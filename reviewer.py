"""
준공서류 검토 로직 — Gemini AI 기반 (PDF/이미지 파일 직접 검토)
"""
import base64
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
import config
import prompts_store
from documents import DOCUMENTS


def make_result(item: str, status: str, extracted: str, note: str) -> dict:
    return {"항목": item, "결과": status, "추출값": extracted, "비고": note}


def _is_attachment_only(doc_id: str) -> bool:
    """첨부 여부만 확인하고 AI 호출 생략하는 서류인지."""
    for d in DOCUMENTS:
        if d["id"] == doc_id:
            return d.get("attachment_only", False)
    return False


# ── Gemini 호출 (429 안전망 — 1회 재시도) ────────────────────────
def _call_gemini(prompt: str, file_bytes: bytes, mime_type: str,
                 max_retries: int = 1) -> str:
    """Gemini 호출. 429(쿼터 초과) 발생 시 retry_delay 만큼 대기 후 1회 재시도."""
    import google.generativeai as genai
    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(config.GEMINI_MODEL)
    file_part = {"mime_type": mime_type, "data": file_bytes}

    for attempt in range(max_retries + 1):
        try:
            response = model.generate_content([prompt, file_part])
            return response.text
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg and attempt < max_retries:
                # 에러 메시지에서 retry_delay seconds 파싱 (없으면 60초)
                m = re.search(r"retry_delay\s*\{?\s*seconds:\s*(\d+)", error_msg)
                wait_sec = (int(m.group(1)) + 2) if m else 60
                time.sleep(min(wait_sec, 90))  # 최대 90초 cap
                continue
            raise


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


# ── doc05/doc06 — 파일별 서류 유형 식별 + 누락 유형만 집계 ───────
_DOC05_REQUIRED_TYPES = [
    "작업일보",
    "안전보건환경 교육 및 TBM일지",
    "교육 및 TBM 사진",
    "스케치도면",
]

_DOC06_REQUIRED_TYPES = [
    "산업안전보건관리비 갑지",
    "항목 별 사용내역",
    "사진대지",
    "세금계산서",
    "거래명세서",
    "지급대장",
]

# 서류 유형 식별 방식 적용 대상 (각 파일이 어떤 유형인지 AI가 식별 + 누락 집계)
_TYPE_ID_DOCS = {"doc05", "doc06"}


def _required_types_for(doc_id: str) -> list[str]:
    if doc_id == "doc05":
        return _DOC05_REQUIRED_TYPES
    if doc_id == "doc06":
        return _DOC06_REQUIRED_TYPES
    return []


def _aggregate_missing_types(per_file_results: list[list[dict]],
                             required: list[str]) -> list[dict]:
    """필수 유형 중 빠진 것만 NG 행으로 반환 (OK는 파일별 표시로 갈음)."""
    found = set()
    for file_results in per_file_results:
        for r in file_results:
            extracted = (r.get("추출값") or "").strip()
            if extracted in required:
                found.add(extracted)

    missing = []
    for req in required:
        if req not in found:
            missing.append(make_result(
                f"(미첨부) {req}", "NG", "",
                f"'{req}' 유형 파일이 식별되지 않음"
            ))
    return missing


# ── 서류 전체 검토 (여러 파일) ────────────────────────────────────
def review_document(doc_id: str, doc_name: str, files: list,
                    project_info: dict | None = None) -> list[dict]:
    """
    files: list of (filename, bytes) tuples
    project_info: {"name": ..., "date": ..., "amount": ...}
    """
    if not files:
        return [make_result("서류 첨부 여부", "NG", "미첨부", "파일이 업로드되지 않음")]

    # attachment_only 서류 — AI 호출 생략, 첨부만 확인
    if _is_attachment_only(doc_id):
        return [make_result(
            "서류 첨부 여부", "OK", f"{len(files)}개 파일 첨부",
            "첨부 확인 (AI 검토 생략 — 첨부만 확인하는 서류)"
        )]

    # doc05/doc06: 서류 첨부 여부 행 제외 (각 파일 유형 식별로 갈음)
    is_type_id_doc = doc_id in _TYPE_ID_DOCS
    if is_type_id_doc:
        results = []
    else:
        results = [make_result("서류 첨부 여부", "OK", f"{len(files)}개 파일 첨부", "첨부 확인")]

    # 파일별 검토 — 다중 파일은 병렬 처리 (단일 파일은 그대로)
    per_file_results: list[list[dict]] = []
    if len(files) == 1:
        filename, file_bytes = files[0]
        per_file_results.append(
            review_file(doc_id, doc_name, file_bytes, filename, project_info)
        )
    else:
        with ThreadPoolExecutor(max_workers=len(files)) as executor:
            future_to_idx = {
                executor.submit(review_file, doc_id, doc_name, fb, fn, project_info): idx
                for idx, (fn, fb) in enumerate(files)
            }
            # 순서 보존 — 인덱스로 정렬
            indexed = [(future_to_idx[f], f.result()) for f in future_to_idx]
            indexed.sort(key=lambda x: x[0])
            per_file_results = [r for _, r in indexed]

    # 누락 유형 집계 — 라벨 변경 전 원본 추출값을 사용해야 매칭 가능
    missing_rows: list[dict] = []
    if is_type_id_doc:
        missing_rows = _aggregate_missing_types(
            per_file_results, _required_types_for(doc_id)
        )

    # 라벨 처리
    if is_type_id_doc:
        # 식별된 유형 이름을 항목으로 사용, 추출값은 비움 (중복 제거)
        for file_results in per_file_results:
            for r in file_results:
                extracted = (r.get("추출값") or "").strip()
                if extracted:
                    r["항목"] = extracted
                    r["추출값"] = ""
    elif len(files) > 1:
        # 일반 다중 파일: [파일 N] prefix
        for i, file_results in enumerate(per_file_results):
            for r in file_results:
                r["항목"] = f"[파일 {i+1}] {r['항목']}"

    # 결과 통합
    for file_results in per_file_results:
        results.extend(file_results)
    # 빠진 유형이 있으면 그것만 NG로 표시 (OK 유형은 파일별 행에서 이미 보임)
    results.extend(missing_rows)

    return results
