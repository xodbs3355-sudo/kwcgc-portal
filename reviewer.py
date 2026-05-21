"""
준공서류 검토 로직 — Gemini AI 기반 (PDF/이미지 파일 직접 검토)
"""
import base64
import io
import json

from PIL import Image
import config


def make_result(item: str, status: str, extracted: str, note: str) -> dict:
    return {"항목": item, "결과": status, "추출값": extracted, "비고": note}


# ── Gemini 호출 ───────────────────────────────────────────────────
def _call_gemini(prompt: str, file_bytes: bytes, mime_type: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(config.GEMINI_MODEL)
    file_part = {"mime_type": mime_type, "data": file_bytes}
    response = model.generate_content([prompt, file_part])
    return response.text


def _parse_json(raw: str) -> list[dict]:
    start, end = raw.find("["), raw.rfind("]") + 1
    if start == -1 or end == 0:
        raise ValueError("JSON 배열 없음")
    return json.loads(raw[start:end])


def _get_mime(filename: str) -> str:
    ext = filename.lower().split(".")[-1]
    return {
        "pdf":  "application/pdf",
        "jpg":  "image/jpeg",
        "jpeg": "image/jpeg",
        "png":  "image/png",
    }.get(ext, "application/octet-stream")


# ── 검토 프롬프트 — 모든 서류에 동일하게 4개 항목 ────────────────
# (서류 첨부 여부는 review_document 에서 Python 으로 별도 처리)
PROMPTS = {}  # 서류별 차이 제거. 모두 GENERIC_PROMPT + _build_prompt 사용.


def _build_prompt(_base_prompt: str, project_info: dict | None) -> str:
    """입력 정보(공사명/준공일자/준공금액) 기반으로 검토 프롬프트 구성.

    검토 항목 (4개, 서류 종류 무관 동일):
      1. 공사명 확인
      2. 준공일자 확인
      3. 준공금액 확인
      4. 서명/날인 여부
    """
    info = project_info or {}
    name   = info.get("name", "").strip()
    date   = info.get("date", "").strip()
    amount = info.get("amount", "").strip()

    info_lines = []
    if name:   info_lines.append(f"- 공사명: {name}")
    if date:   info_lines.append(f"- 준공일자: {date}")
    if amount: info_lines.append(f"- 준공금액: {amount}")
    info_block = "\n".join(info_lines) if info_lines else "(입력 정보 없음)"

    return f"""
도시가스 공사 준공서류입니다. 첨부된 서류를 분석하여 다음 4가지 항목을 검토하고,
**JSON 배열만** 응답하세요. (다른 설명/마크다운 금지)

[사용자가 입력한 공사 정보 — 이 정보와 서류 내용을 대조 검증]
{info_block}

[검토 항목 — 항상 정확히 아래 4개만, 같은 순서로]
1. "공사명 확인"     : 입력 공사명과 서류 표기 공사명 일치 여부
2. "준공일자 확인"   : 입력 준공일자와 서류의 준공/완공 일자 일치 여부
3. "준공금액 확인"   : 입력 준공금액과 서류의 계약/준공/총액/합계 등 금액 일치 여부
                       (콤마/단위/VAT 별도 무시. 서류에 여러 금액이 있어도 종합 판단)
4. "서명/날인 여부"  : 서명 또는 인감 날인이 있는지

[결과 규칙]
- 입력 정보 없거나 서류에 해당 정보 없음 → "결과": "SKIP", "비고": "서류에서 확인 불가" 또는 "입력 정보 없음"
- 일치/존재                              → "결과": "OK", "추출값": 서류에서 추출한 실제 값
- 불일치                                 → "결과": "NG", "비고": "입력값 X / 서류값 Y" 형태로 비교
- 판단 어려움 / 부분 충족                → "결과": "WARN", "비고": 이유 설명

[응답 형식 (예시)]
[
  {{"항목":"공사명 확인","결과":"OK","추출값":"춘천시 거두리 ...","비고":""}},
  {{"항목":"준공일자 확인","결과":"NG","추출값":"2025-10-30","비고":"입력값 2025-10-31 / 서류값 2025-10-30"}},
  {{"항목":"준공금액 확인","결과":"OK","추출값":"3,400,910","비고":""}},
  {{"항목":"서명/날인 여부","결과":"OK","추출값":"대표이사 정인철/문만영 인감 확인","비고":""}}
]
""".strip()


# 기존 코드 호환용 (사용 안 함)
GENERIC_PROMPT = ""

MOCK_RESULT = [
    make_result("공사명 확인",    "WARN", "-", "AI 키 없음 — 수동 확인 필요"),
    make_result("준공일자 확인",  "WARN", "-", "AI 키 없음 — 수동 확인 필요"),
    make_result("준공금액 확인",  "WARN", "-", "AI 키 없음 — 수동 확인 필요"),
    make_result("서명/날인 여부", "WARN", "-", "AI 키 없음 — 수동 확인 필요"),
]


# ── 단일 파일 검토 ────────────────────────────────────────────────
def review_file(doc_id: str, file_bytes: bytes, filename: str,
                project_info: dict | None = None) -> list[dict]:
    if config.USE_MOCK:
        return MOCK_RESULT

    mime = _get_mime(filename)
    prompt = _build_prompt("", project_info)
    try:
        raw = _call_gemini(prompt, file_bytes, mime)
        return _parse_json(raw)
    except Exception as e:
        return [make_result("AI 검토 오류", "WARN", "-", str(e))]


# ── 서류 전체 검토 (여러 파일) ────────────────────────────────────
def review_document(doc_id: str, doc_name: str, files: list,
                    project_info: dict | None = None) -> list[dict]:
    """
    files: list of (filename, bytes) tuples
    project_info: {"name": ..., "date": ..., "amount": ...}
    """
    if not files:
        return [make_result("서류 첨부 여부", "NG", "미첨부", "파일이 업로드되지 않음")]

    results = [make_result("서류 첨부 여부", "OK", f"{len(files)}개 파일 첨부", "첨부 확인")]

    for i, (filename, file_bytes) in enumerate(files):
        file_results = review_file(doc_id, file_bytes, filename, project_info)
        # 다중 파일일 때만 항목명 앞에 파일 표시
        if len(files) > 1:
            for r in file_results:
                r["항목"] = f"[파일 {i+1}] {r['항목']}"
        results.extend(file_results)

    return results
