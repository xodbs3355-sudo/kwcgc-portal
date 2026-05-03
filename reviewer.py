"""
준공서류 검토 로직 (Gemini AI + 첨부여부 자동 확인)
"""
import base64
import io
import json
from typing import Any

from PIL import Image

import config


def make_result(item: str, status: str, extracted: str, note: str) -> dict:
    """status: 'OK' | 'NG' | 'WARN' | 'SKIP'"""
    return {"항목": item, "결과": status, "추출값": extracted, "비고": note}


# ── 워크시트 분석 헬퍼 ────────────────────────────────────────────
NA_TEXTS = {'해당없음', '해당 없음', '해당없음.', '해당없음 '}
PLACEHOLDER_TEXTS = {'그림 삽입', '그림삽입', '파일 삽입', '파일삽입'}


def _has_images(ws) -> bool:
    try:
        return len(ws._images) > 0
    except Exception:
        return False


def _has_na_text(ws) -> bool:
    for row in ws.iter_rows(max_row=10, values_only=True):
        for cell in row:
            if cell and str(cell).strip() in NA_TEXTS:
                return True
    return False


def _has_placeholder(ws) -> bool:
    for row in ws.iter_rows(max_row=10, values_only=True):
        for cell in row:
            if cell and str(cell).strip() in PLACEHOLDER_TEXTS:
                return True
    return False


def _check_attachment(ws, sheet_name: str, conditional: bool = False) -> list[dict]:
    """서류 첨부 여부를 확인하는 기본 검토 결과 반환"""
    if _has_na_text(ws):
        status = 'SKIP' if conditional else 'WARN'
        note = '해당없음 처리' if conditional else '필수 서류인데 해당없음 표시 — 확인 필요'
        return [make_result('서류 첨부 여부', status, '해당없음', note)]

    if _has_images(ws):
        return [make_result('서류 첨부 여부', 'OK', f'이미지 {len(ws._images)}개 첨부', '첨부 확인')]

    if _has_placeholder(ws):
        return [make_result('서류 첨부 여부', 'NG', '미첨부', '그림/파일이 삽입되지 않음')]

    # 이미지도 없고 placeholder도 없으면 내용 있는 것으로 간주
    return [make_result('서류 첨부 여부', 'WARN', '이미지 없음', '첨부 이미지 미감지 — 수동 확인 필요')]


# ── Gemini 호출 ───────────────────────────────────────────────────
def _image_to_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _call_gemini(prompt: str, image: Image.Image) -> str:
    import google.generativeai as genai
    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(config.GEMINI_MODEL)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    response = model.generate_content([prompt, {"mime_type": "image/png", "data": buf.read()}])
    return response.text


def _gemini_review(prompt: str, image: Image.Image) -> list[dict]:
    try:
        raw = _call_gemini(prompt, image)
        start, end = raw.find("["), raw.rfind("]") + 1
        if start == -1 or end == 0:
            raise ValueError("JSON 배열 없음")
        return json.loads(raw[start:end])
    except Exception as e:
        return [make_result("AI 검토 오류", "WARN", "-", str(e))]


# ── 준공계 (1번 시트) ──────────────────────────────────────────────
PROMPT_준공계 = """
당신은 도시가스 공사 준공서류를 검토하는 전문가입니다.
아래 이미지는 준공계 서류입니다. 다음 항목을 정확히 검토하고 JSON 배열로만 응답하세요.

검토 항목:
1. 계약금액 숫자 추출 (아라비아 숫자)
2. 계약금액 한글금액 추출 후 숫자 변환 → 1번과 일치 여부
3. 기성/준공액 숫자 추출
4. 기성/준공액 한글금액 추출 후 숫자 변환 → 3번과 일치 여부
5. 테이블의 금회기성 합계가 기재 금액과 일치하는지
6. 위 모든 금액이 전체적으로 일치하는지 최종 확인

응답 형식 (JSON 배열만, 다른 텍스트 없이):
[{"항목": "계약금액 숫자 추출", "결과": "OK", "추출값": "123,456,789", "비고": "정상"}, ...]
결과값은 반드시 "OK", "NG", "WARN", "SKIP" 중 하나입니다.
"""

MOCK_준공계 = [
    make_result("계약금액 숫자 추출", "OK", "123,456,789", "정상 추출"),
    make_result("계약금액 한글금액 일치 여부", "OK", "일억이천삼백사십오만육천칠백팔십구원 → 123,456,789", "일치"),
    make_result("기성/준공액 숫자 추출", "OK", "98,765,432", "정상 추출"),
    make_result("기성/준공액 한글금액 일치 여부", "NG", "구천팔백칠십육만오천사백삼십이원 → 98,765,432 ≠ 98,765,430", "2원 불일치"),
    make_result("금회기성 합계 일치", "OK", "98,765,432 = 98,765,432", "일치"),
    make_result("전체 금액 최종 일치", "NG", "-", "한글금액 불일치로 최종 NG"),
]


# ── 하도급 작업내역 (2번 시트) ────────────────────────────────────
PROMPT_하도급 = """
당신은 도시가스 공사 준공서류 검토 전문가입니다.
아래 이미지는 하도급 공종별 작업내역서입니다. 다음 항목을 검토하고 JSON 배열로만 응답하세요.

검토 항목:
1. 서류 제목 및 양식 확인 (하도급 작업내역서 양식인지)
2. 품목별 단가 × 수량 = 금액 일치 여부 (샘플 3건 이상)
3. 합계금액 계산 일치 여부
4. 필수 항목(품목명, 단가, 수량, 금액) 모두 기재 여부

응답 형식 (JSON 배열만):
[{"항목": "...", "결과": "OK/NG/WARN/SKIP", "추출값": "...", "비고": "..."}, ...]
"""

MOCK_하도급 = [
    make_result("양식 확인", "OK", "하도급 작업내역서", "정상"),
    make_result("단가×수량=금액 일치", "WARN", "-", "이미지 해상도로 일부 항목 확인 불가"),
    make_result("합계금액 일치", "OK", "확인됨", "정상"),
    make_result("필수항목 기재", "OK", "품목/단가/수량/금액 모두 기재", "정상"),
]


# ── 하자보수보증 (8번 시트) ───────────────────────────────────────
PROMPT_하자보수 = """
당신은 도시가스 공사 준공서류 검토 전문가입니다.
아래 이미지는 하자보수보증서입니다. 다음 항목을 검토하고 JSON 배열로만 응답하세요.

검토 항목:
1. 보증금액 기재 여부 및 숫자 추출
2. 보증기간 기재 여부 (3년 이상인지)
3. 발주처/수급인 정보 기재 여부
4. 서명/날인 여부

응답 형식 (JSON 배열만):
[{"항목": "...", "결과": "OK/NG/WARN/SKIP", "추출값": "...", "비고": "..."}, ...]
"""

MOCK_하자보수 = [
    make_result("보증금액 기재", "OK", "5,000,000원", "정상"),
    make_result("보증기간 확인", "OK", "3년", "기준 충족"),
    make_result("발주처/수급인 정보", "OK", "기재 확인", "정상"),
    make_result("서명/날인", "WARN", "-", "확인 필요"),
]


# ── 기밀/내압 (6번 시트) ─────────────────────────────────────────
PROMPT_기밀내압 = """
당신은 도시가스 공사 준공서류 검토 전문가입니다.
아래 이미지는 기밀/내압 시험 데이터입니다. 다음 항목을 검토하고 JSON 배열로만 응답하세요.

검토 항목:
1. 시험압력 기재 여부 및 수치 추출
2. 시험시간 기재 여부
3. 시험결과(합격/불합격) 기재 여부
4. 담당자 확인 서명 여부

응답 형식 (JSON 배열만):
[{"항목": "...", "결과": "OK/NG/WARN/SKIP", "추출값": "...", "비고": "..."}, ...]
"""

MOCK_기밀내압 = [
    make_result("시험압력 기재", "OK", "8.4 kPa", "기재 확인"),
    make_result("시험시간 기재", "OK", "24시간", "기재 확인"),
    make_result("시험결과 기재", "OK", "합격", "정상"),
    make_result("담당자 서명", "WARN", "-", "확인 필요"),
]


# ── 시트명 → (prompt, mock, conditional) 매핑 ────────────────────
SHEET_CONFIG: dict[str, dict] = {
    '1.준공계':                  {'prompt': PROMPT_준공계,  'mock': MOCK_준공계,    'conditional': False},
    '2. 하도급 작업내역':         {'prompt': PROMPT_하도급,  'mock': MOCK_하도급,    'conditional': False},
    '3.감리필증':                 {'prompt': None,           'mock': None,           'conditional': False},
    '4.자재하역':                 {'prompt': None,           'mock': None,           'conditional': False},
    '4.안전작업허가(계획)서':     {'prompt': None,           'mock': None,           'conditional': True},
    '5.저심도배관':               {'prompt': None,           'mock': None,           'conditional': True},
    '6.기밀, 내압':               {'prompt': PROMPT_기밀내압,'mock': MOCK_기밀내압,  'conditional': False},
    '8.하자보수보증':             {'prompt': PROMPT_하자보수,'mock': MOCK_하자보수,  'conditional': True},
    '9.건설기계대여보증':         {'prompt': None,           'mock': None,           'conditional': True},
    '10.교육일지':                {'prompt': None,           'mock': None,           'conditional': False},
    '11.융착데이터':              {'prompt': None,           'mock': None,           'conditional': True},
    '12.자재 및 비파괴성적':      {'prompt': None,           'mock': None,           'conditional': False},
    '13.산업안전보건관리비':      {'prompt': None,           'mock': None,           'conditional': False},
    '14.폐기물':                  {'prompt': None,           'mock': None,           'conditional': False},
    '15.FCN':                     {'prompt': None,           'mock': None,           'conditional': True},
    '16.보호구지급대장':          {'prompt': None,           'mock': None,           'conditional': True},
    '17.현장점검표':              {'prompt': None,           'mock': None,           'conditional': True},
    '18.기타':                    {'prompt': None,           'mock': None,           'conditional': True},
}

# 하위 호환: 기존 REVIEWERS 키도 유지
REVIEWERS = {k: True for k in SHEET_CONFIG}


def review_sheet(sheet_name: str, image: Image.Image, ws=None) -> list[dict]:
    cfg = SHEET_CONFIG.get(sheet_name)
    if cfg is None:
        return [make_result('미지원 시트', 'SKIP', '-', f"'{sheet_name}' 검토 기준 미정의")]

    conditional = cfg['conditional']

    # 1단계: 첨부 여부 확인
    if ws is not None:
        attachment_results = _check_attachment(ws, sheet_name, conditional)
        attached = attachment_results[0]['결과'] == 'OK'
        is_na = attachment_results[0]['추출값'] == '해당없음'
    else:
        attachment_results = [make_result('서류 첨부 여부', 'WARN', '-', 'ws 없음 — 수동 확인')]
        attached = False
        is_na = False

    # 해당없음이면 첨부 결과만 반환
    if is_na:
        return attachment_results

    # prompt가 없으면 첨부 여부만 반환
    if cfg['prompt'] is None:
        return attachment_results

    # 2단계: AI 내용 검토 (이미지 첨부된 경우만)
    if not attached:
        return attachment_results + [
            make_result('내용 검토', 'SKIP', '-', '서류 미첨부로 내용 검토 불가')
        ]

    if config.USE_MOCK:
        content_results = cfg['mock'] or [make_result('내용 검토', 'SKIP', '-', 'Mock 데이터 없음')]
    else:
        content_results = _gemini_review(cfg['prompt'], image)

    return attachment_results + content_results
