"""
준공서류 검토 로직 — Gemini AI 기반 (PDF/이미지 파일 직접 검토)
"""
import base64
import io
import json

from PIL import Image
import config
import prompts_store


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


# ── 프롬프트 — 관리자가 입력한 것 우선, 없으면 prompts_store 의 기본값 ──

def _build_prompt(doc_id: str, doc_name: str, project_info: dict | None) -> str:
    """관리자 프롬프트(또는 기본 프롬프트) 에 입력 정보를 끼워넣어 최종 프롬프트 생성."""
    info = project_info or {}
    name   = info.get("name", "").strip()
    date   = info.get("date", "").strip()
    amount = info.get("amount", "").strip()

    info_lines = []
    if name:   info_lines.append(f"- 공사명: {name}")
    if date:   info_lines.append(f"- 준공일자: {date}")
    if amount: info_lines.append(f"- 준공금액: {amount}")
    info_block = "\n".join(info_lines) if info_lines else "(입력 정보 없음)"

    template = prompts_store.get_effective_prompt(doc_id, doc_name)
    if "{project_info}" in template:
        return template.replace("{project_info}", info_block)
    # placeholder 없는 옛 프롬프트는 그대로 사용
    return template


# 기존 호환용 (사용 안 함)
PROMPTS = {}
GENERIC_PROMPT = ""

MOCK_RESULT = [
    make_result("공사명 확인",    "WARN", "-", "AI 키 없음 — 수동 확인 필요"),
    make_result("준공일자 확인",  "WARN", "-", "AI 키 없음 — 수동 확인 필요"),
    make_result("준공금액 확인",  "WARN", "-", "AI 키 없음 — 수동 확인 필요"),
    make_result("서명/날인 여부", "WARN", "-", "AI 키 없음 — 수동 확인 필요"),
]


# ── 단일 파일 검토 ────────────────────────────────────────────────
def review_file(doc_id: str, doc_name: str, file_bytes: bytes, filename: str,
                project_info: dict | None = None) -> list[dict]:
    if config.USE_MOCK:
        return MOCK_RESULT

    mime = _get_mime(filename)
    prompt = _build_prompt(doc_id, doc_name, project_info)
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
        file_results = review_file(doc_id, doc_name, file_bytes, filename, project_info)
        # 다중 파일일 때만 항목명 앞에 파일 표시
        if len(files) > 1:
            for r in file_results:
                r["항목"] = f"[파일 {i+1}] {r['항목']}"
        results.extend(file_results)

    return results
