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


# ── 서류별 프롬프트 ───────────────────────────────────────────────
# 모든 프롬프트 앞에 입력 정보 대조 항목을 자동 추가합니다 (_build_prompt 참조).
PROMPTS = {
    "doc01": """
도시가스 공사 준공계 서류입니다. 다음을 검토하고 JSON 배열로만 응답하세요.
1. 계약금액 숫자와 한글금액 일치 여부
2. 기성/준공액 숫자와 한글금액 일치 여부
3. 금회기성 합계 일치 여부
4. 서명/날인 여부
형식: [{"항목":"...","결과":"OK/NG/WARN/SKIP","추출값":"...","비고":"..."}]
""",
    "doc02": """
하도급 공종별 작업내역서입니다. 다음을 검토하고 JSON 배열로만 응답하세요.
1. 품목·단가·수량·금액 항목 모두 기재 여부
2. 단가 × 수량 = 금액 일치 여부 (샘플 3건)
3. 합계금액 일치 여부
4. 서명/날인 여부
형식: [{"항목":"...","결과":"OK/NG/WARN/SKIP","추출값":"...","비고":"..."}]
""",
    "doc03": """
기밀시험 데이터 서류입니다. 다음을 검토하고 JSON 배열로만 응답하세요.
1. 시험압력 기재 및 기준 적합 여부
2. 시험시간 기재 여부
3. 합격/불합격 결과 기재 여부
4. 담당자 서명 여부
형식: [{"항목":"...","결과":"OK/NG/WARN/SKIP","추출값":"...","비고":"..."}]
""",
}

GENERIC_PROMPT = """
도시가스 공사 준공서류입니다. 다음을 검토하고 JSON 배열로만 응답하세요.
1. 서류 양식 및 내용 기재 여부
2. 필수 항목(날짜·서명·금액 등) 누락 여부
3. 전반적인 서류 적정성
형식: [{"항목":"...","결과":"OK/NG/WARN/SKIP","추출값":"...","비고":"..."}]
"""


def _build_prompt(base_prompt: str, project_info: dict | None) -> str:
    """프롬프트 앞에 입력 정보(공사명/준공일자/준공금액) 대조 지시를 붙임."""
    if not project_info:
        return base_prompt

    name   = project_info.get("name", "").strip()
    date   = project_info.get("date", "").strip()
    amount = project_info.get("amount", "").strip()
    if not any([name, date, amount]):
        return base_prompt

    info_block = "[사용자가 입력한 공사 정보 — 이 정보와 서류 내용을 대조 검증하세요]\n"
    if name:   info_block += f"- 공사명: {name}\n"
    if date:   info_block += f"- 준공일자: {date}\n"
    if amount: info_block += f"- 준공금액: {amount}\n"

    check_items = []
    if name:
        check_items.append('- "공사명 대조": 서류에 표기된 공사명이 입력 공사명과 일치하는지')
    if date:
        check_items.append('- "준공일자 대조": 서류의 준공/완공 일자가 입력 일자와 일치하는지')
    if amount:
        check_items.append('- "준공금액 대조": 서류의 계약/준공/총액 금액이 입력 금액과 일치하는지 (콤마/단위 무시하고 숫자만 비교)')

    info_block += (
        "\n[필수 — 아래 항목을 JSON 배열의 앞쪽에 포함해 응답하세요]\n"
        + "\n".join(check_items)
        + '\n  · 서류에 해당 정보가 없으면 "결과": "SKIP", "비고"에 "서류에서 확인 불가" 기재\n'
        + '  · 일치하면 "결과": "OK", "추출값"에 서류에서 추출한 값 기재\n'
        + '  · 불일치하면 "결과": "NG", "비고"에 입력값/서류값 같이 기재\n\n'
    )

    return info_block + base_prompt

MOCK_RESULT = [
    make_result("서류 양식 확인",    "OK",   "정상",   "양식 확인됨"),
    make_result("필수 항목 기재",    "WARN", "-",      "AI 키 없음 — 수동 확인 필요"),
    make_result("서류 적정성",       "WARN", "-",      "AI 키 없음 — 수동 확인 필요"),
]


# ── 단일 파일 검토 ────────────────────────────────────────────────
def review_file(doc_id: str, file_bytes: bytes, filename: str,
                project_info: dict | None = None) -> list[dict]:
    if config.USE_MOCK:
        return MOCK_RESULT

    mime = _get_mime(filename)
    base_prompt = PROMPTS.get(doc_id, GENERIC_PROMPT)
    prompt = _build_prompt(base_prompt, project_info)
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
        label = f"파일 {i+1} ({filename})" if len(files) > 1 else "내용 검토"
        file_results = review_file(doc_id, file_bytes, filename, project_info)
        for r in file_results:
            r["항목"] = f"{label} — {r['항목']}"
        results.extend(file_results)

    return results
