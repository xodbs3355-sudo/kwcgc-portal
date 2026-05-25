"""
준공서류 검토 로직 — Gemini AI 기반 (PDF/이미지 파일 직접 검토)
"""
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

import config
import prompts_store
import usage_store
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
                 max_retries: int = 1,
                 company: str | None = None, doc_id: str | None = None) -> str:
    """Gemini 호출. 429(쿼터 초과) 발생 시 retry_delay 만큼 대기 후 1회 재시도.

    company/doc_id 가 주어지면 usage_store 에 토큰 사용량 기록.
    """
    import google.generativeai as genai
    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(config.GEMINI_MODEL)
    file_part = {"mime_type": mime_type, "data": file_bytes}

    for attempt in range(max_retries + 1):
        try:
            response = model.generate_content([prompt, file_part])
            _record_usage(response, company, doc_id)
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


def _record_usage(response, company: str | None, doc_id: str | None) -> None:
    """Gemini response 의 usage_metadata 를 usage_store 에 기록."""
    if not company or not doc_id:
        return
    try:
        meta = getattr(response, "usage_metadata", None)
        if not meta:
            return
        pt = getattr(meta, "prompt_token_count", 0) or 0
        ct = getattr(meta, "candidates_token_count", 0) or 0
        usage_store.record(company, doc_id, pt, ct)
    except Exception:
        # usage 기록 실패가 검토를 막으면 안 됨
        pass


def _parse_json(raw: str) -> list[dict]:
    start, end = raw.find("["), raw.rfind("]") + 1
    if start == -1 or end == 0:
        raise ValueError("JSON 배열 없음")
    return json.loads(raw[start:end])


def _parse_json_object(raw: str) -> dict:
    """JSON 객체 파싱 — 마크다운 ```json...``` 안에 있어도 추출."""
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("JSON 객체 없음")
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


MOCK_RESULT = [
    make_result("공사명 확인",    "WARN", "-", "AI 키 없음 — 수동 확인 필요"),
    make_result("준공일자 확인",  "WARN", "-", "AI 키 없음 — 수동 확인 필요"),
    make_result("준공금액 확인",  "WARN", "-", "AI 키 없음 — 수동 확인 필요"),
    make_result("서명/날인 여부", "WARN", "-", "AI 키 없음 — 수동 확인 필요"),
]


# ── doc01 하이브리드 — 공사명·일자·금액·서명 검증 헬퍼 ───────────
def _project_name_match(input_name: str, doc_name: str) -> tuple[str, str]:
    """공사명 매칭 룰.

    OK 케이스:
      · 완전 일치
      · 띄어쓰기 차이만 있음 (공백 제거 후 동일)
      · 선두 시·군 단위 누락만 있음 (예: 입력 "효자동 ..." vs 서류 "춘천시 효자동 ...")

    NG: 그 외 모든 차이 (한 글자 오타 포함)
    SKIP: 입력 또는 서류 공사명 없음
    """
    raw_a = (input_name or "").strip()
    raw_b = (doc_name or "").strip()

    if not raw_a or not raw_b:
        return ("SKIP", "입력 또는 서류 공사명 없음")

    # 1. 공백 제거 후 일치
    no_space_a = "".join(raw_a.split())
    no_space_b = "".join(raw_b.split())
    if no_space_a == no_space_b:
        if raw_a == raw_b:
            return ("OK", "입력값과 동일")
        return ("OK", f"띄어쓰기 차이만 있음 (입력 '{raw_a}' / 서류 '{raw_b}')")

    # 2. 선두 시/군 단위 제거 후 비교 (예: "춘천시효자동..." → "효자동...")
    strip_region = lambda s: re.sub(r"^[가-힣]+(시|군)", "", s)
    norm_a = strip_region(no_space_a)
    norm_b = strip_region(no_space_b)
    if norm_a == norm_b:
        return ("OK", f"시·군 단위 누락 차이만 있음 (입력 '{raw_a}' / 서류 '{raw_b}')")

    # 3. 그 외 모두 NG
    return ("NG", f"불일치: 입력 '{raw_a}' / 서류 '{raw_b}'")


def _date_match(input_date: str, doc_date: str) -> tuple[str, str]:
    """일자 매칭 룰 — YYYYMMDD 정규화 후 정확 비교."""
    raw_a = (input_date or "").strip()
    raw_b = (doc_date or "").strip()

    if not raw_b:
        return ("SKIP", "서류에서 일자 추출 실패")
    if not raw_a:
        return ("SKIP", f"입력 준공일자 없음 (서류값: {raw_b})")

    def _to_yyyymmdd(s: str) -> str:
        nums = re.findall(r"\d+", s)
        if len(nums) >= 3:
            y = nums[0]
            if len(y) == 2:        # "26-04-17" → "2026-04-17"
                y = "20" + y
            return f"{y}{nums[1].zfill(2)}{nums[2].zfill(2)}"
        return s

    norm_a = _to_yyyymmdd(raw_a)
    norm_b = _to_yyyymmdd(raw_b)
    if norm_a == norm_b:
        return ("OK", f"입력값 {raw_a} 과 서류값 {raw_b} 일치")
    return ("NG", f"불일치: 입력 {raw_a} / 서류 {raw_b}")


def _amount_match(final_cost: int | None,
                  doc_amounts,
                  final_cost_meta: str | None) -> tuple[str, str, str]:
    """준공금액 매칭 — 2단계 검증.
    1차: 서류 내 모든 금액 표기가 동일한지
    2차: 그 값이 계산값(단가표+일시점용료+영구신청수수료)과 일치하는지

    doc_amounts: [{"라벨": str, "원본": str, "값": int|null}, ...]  또는 None
    반환: (status, extracted, note)
    """
    if not doc_amounts or not isinstance(doc_amounts, list):
        return ("SKIP", "-", "서류에서 준공금액 추출 실패")

    items: list[tuple[str, str, int]] = []  # (라벨, 원본, 값)
    for it in doc_amounts:
        if not isinstance(it, dict):
            continue
        v = it.get("값")
        if v is None:
            continue
        try:
            v_int = int(v)
        except (ValueError, TypeError):
            continue
        items.append((
            str(it.get("라벨") or "?"),
            str(it.get("원본") or ""),
            v_int,
        ))

    if not items:
        return ("SKIP", "-", "서류에서 유효한 금액 추출 실패")

    values = [v for (_, _, v) in items]

    # 1차: 서류 내부 일치 확인
    if len(set(values)) > 1:
        breakdown = " / ".join(f"{lab} {v:,}원" for (lab, _, v) in items)
        return ("NG", f"{values[0]:,}",
                f"서류 내 금액 불일치 — {breakdown} (계산값 비교 생략)")

    # 내부 일치 → 그 값이 곧 서류값
    doc_value = values[0]
    extracted = f"{doc_value:,}"
    count = len(items)

    if final_cost is None:
        return ("SKIP", extracted,
                f"서류 내 금액 {count}건 모두 {_won(doc_value)} 일치 — "
                f"단가표 또는 입력 정보 부족으로 계산값 산출 불가, 비교 생략")

    if int(final_cost) == doc_value:
        return ("OK", extracted,
                f"서류 내 금액 {count}건 모두 {_won(doc_value)} 일치 + "
                f"계산값과 일치 ({final_cost_meta or '계산'})")

    return ("NG", extracted,
            f"서류 내 금액 {count}건 모두 {_won(doc_value)} 일치, "
            f"그러나 계산값 {_won(final_cost)}({final_cost_meta or '계산'})과 불일치")


def _signature_check(data: dict) -> tuple[str, str, str]:
    """서명/날인 체크."""
    sig = data.get("서명_날인")
    if sig is True:
        return ("OK", "있음", "서명 또는 날인 확인됨")
    if sig is False:
        return ("NG", "없음", "서명 또는 날인 누락")
    return ("SKIP", "-", "서명/날인 여부 추출 실패")


def _doc01_apply_rules(data: dict, project_info: dict | None,
                       final_cost: int | None,
                       final_cost_meta: str | None) -> list[dict]:
    """doc01 추출 데이터에 Python 룰 적용 → 4개 카드 반환."""
    pi = project_info or {}
    results: list[dict] = []

    # 공사명 확인
    status, note = _project_name_match(
        pi.get("name") or "",
        data.get("공사명") or "",
    )
    extracted = (data.get("공사명") or "").strip() or "-"
    results.append(make_result("공사명 확인", status, extracted, note))

    # 준공일자 확인
    status, note = _date_match(
        pi.get("date") or "",
        data.get("준공일자") or "",
    )
    extracted = (data.get("준공일자") or "").strip() or "-"
    results.append(make_result("준공일자 확인", status, extracted, note))

    # 준공금액 확인 — 1차: 서류 내부 일치, 2차: 계산값과 일치
    status, extracted, note = _amount_match(
        final_cost, data.get("준공금액_목록"), final_cost_meta
    )
    results.append(make_result("준공금액 확인", status, extracted, note))

    # 서명/날인 여부
    status, extracted, note = _signature_check(data)
    results.append(make_result("서명/날인 여부", status, extracted, note))

    return results


def _review_doc01_combined(files: list,
                            project_info: dict | None,
                            final_cost: int | None,
                            final_cost_meta: str | None,
                            company: str | None = None) -> list[dict]:
    """준공계 — 하이브리드 방식 (AI 추출 + Python 룰)."""
    if config.USE_MOCK:
        return MOCK_RESULT
    if not files:
        return [make_result("서류 첨부 여부", "NG", "미첨부", "파일이 업로드되지 않음")]

    # Step 1: AI 추출 (멀티파일 호출 — 1파일이어도 호환)
    prompt = _build_prompt("doc01", "준공계", project_info)
    file_parts = []
    for filename, file_bytes in files:
        mime = _get_mime(filename)
        file_parts.append({"mime_type": mime, "data": file_bytes})

    try:
        raw = _call_gemini_multifile(prompt, file_parts,
                                     company=company, doc_id="doc01")
        data = _parse_json_object(raw)
    except Exception as e:
        return [make_result(
            "AI 데이터 추출 오류", "WARN", "-", str(e)[:300]
        )]

    # Step 2: Python 룰 적용
    return _doc01_apply_rules(data, project_info, final_cost, final_cost_meta)


# ── 단일 파일 검토 ────────────────────────────────────────────────
def review_file(doc_id: str, doc_name: str, file_bytes: bytes, filename: str,
                project_info: dict | None = None,
                company: str | None = None) -> list[dict]:
    if config.USE_MOCK:
        return MOCK_RESULT

    mime = _get_mime(filename)
    prompt = _build_prompt(doc_id, doc_name, project_info)
    try:
        raw = _call_gemini(prompt, file_bytes, mime,
                           company=company, doc_id=doc_id)
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
    "산업안전보건관리비 사용내역서",
    "항목 별 사용내역",
    "사진대지",
    "세금계산서",
    "거래명세서",
    "지급대장",
]


def _normalize_text(s: str) -> str:
    """매칭 비교용 정규화 — 공백/탭/줄바꿈을 전부 제거.
    "산업안전보건관리비 사용내역서" / "산업안전보건관리비사용내역서" /
    "산업 안전 보건관리비 사용내역서" 모두 동일하게 처리.
    """
    if not s:
        return ""
    return "".join(s.split())

# 서류 유형 식별 방식 적용 대상 (각 파일이 어떤 유형인지 AI가 식별 + 누락 집계)
# doc06은 멀티파일 통합 호출 (_review_doc06_combined)로 별도 처리.
_TYPE_ID_DOCS = {"doc05"}


def _required_types_for(doc_id: str) -> list[str]:
    if doc_id == "doc05":
        return _DOC05_REQUIRED_TYPES
    if doc_id == "doc06":
        return _DOC06_REQUIRED_TYPES
    return []


def _aggregate_missing_types(per_file_results: list[list[dict]],
                             required: list[str]) -> list[dict]:
    """필수 유형 중 빠진 것만 NG 행으로 반환 (OK는 파일별 표시로 갈음).
    공백 무관 매칭 — 띄어쓰기 달라도 같은 유형으로 인식.
    """
    # 정규화된 키 → canonical name 매핑
    norm_to_canonical = {_normalize_text(req): req for req in required}
    found = set()  # canonical names
    for file_results in per_file_results:
        for r in file_results:
            extracted = (r.get("추출값") or "").strip()
            norm = _normalize_text(extracted)
            if norm in norm_to_canonical:
                found.add(norm_to_canonical[norm])

    missing = []
    for req in required:
        if req not in found:
            missing.append(make_result(
                f"(미첨부) {req}", "NG", "",
                f"'{req}' 유형 파일이 식별되지 않음"
            ))
    return missing


def _review_doc06_combined(files: list, project_info: dict | None,
                            company: str | None = None) -> list[dict]:
    """산업안전보건관리비 — 하이브리드 방식.

    Step 1: AI가 멀티파일 호출로 구조화된 데이터 추출 (비교/판정 X)
    Step 2: Python(_doc06_apply_rules)이 추출된 데이터로 룰 적용 (정확)
    """
    if config.USE_MOCK:
        return MOCK_RESULT

    if not files:
        return [make_result("서류 첨부 여부", "NG", "미첨부", "파일이 업로드되지 않음")]

    # Step 1: 멀티파일 호출 — 프롬프트 + 모든 파일
    prompt = _build_prompt("doc06", "산업안전보건관리비 내역서", project_info)
    file_parts = []
    for filename, file_bytes in files:
        mime = _get_mime(filename)
        file_parts.append({"mime_type": mime, "data": file_bytes})

    try:
        raw = _call_gemini_multifile(prompt, file_parts,
                                     company=company, doc_id="doc06")
        data = _parse_json_object(raw)
    except Exception as e:
        return [make_result(
            "AI 데이터 추출 오류", "WARN", "-", str(e)[:300]
        )]

    # Step 2: Python에서 룰 적용
    return _doc06_apply_rules(data)


def _normalize_date_for_match(s) -> str:
    """일자 정규화 — MM/DD, YYYY-MM-DD, YYYY.MM.DD 등 → MMDD 4자리.
    매칭 비교용. 형식 차이 흡수.
    """
    s = (s or "").strip()
    nums = re.findall(r"\d+", s)
    if len(nums) >= 2:
        return nums[-2].zfill(2) + nums[-1].zfill(2)
    return s


def _normalize_item_for_match(s) -> str:
    """품목명 정규화 — 대괄호/괄호 안 규격 제거 + 공백 제거.
    예: "안전화 [6인치]" → "안전화"
    """
    s = (s or "").strip()
    s = re.sub(r"\s*\[.*?\]\s*", "", s)   # [규격] 제거
    s = re.sub(r"\s*\(.*?\)\s*", "", s)   # (규격) 제거
    return "".join(s.split())


def _items_match(a: str, b: str) -> bool:
    """품목 매칭 — 정규화 후 정확 일치 또는 포함 관계.
    "안전화 [6인치]" ↔ "안전화" 매칭 OK.
    """
    na, nb = _normalize_item_for_match(a), _normalize_item_for_match(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _won(n) -> str:
    """숫자를 원 단위 문자열로 (예: 95000 → '95,000원')."""
    try:
        return f"{int(n):,}원"
    except (TypeError, ValueError):
        return str(n) if n is not None else "-"


def _addr_token(name: str) -> str | None:
    """공사명에서 지번 토큰 추출 — 예: '172-159번지' → '172-159'."""
    if not name:
        return None
    m = re.search(r"\d+-\d+(?=\s*번지)", name)
    if m:
        return m.group(0)
    m = re.search(r"\d+-\d+", name)
    return m.group(0) if m else None


def _name_loose_match(name_a: str, name_b: str) -> tuple[bool, str]:
    """공사명 관대 매칭 — 완전 일치 > 부분 포함 > 지번 일치 순으로 검사.
    번지·지번이 같으면 뒤에 붙는 '인입공급관'/'일원' 등 표기 차이는 허용.
    반환: (일치 여부, 매칭 사유)
    """
    a = (name_a or "").strip()
    b = (name_b or "").strip()
    if not a or not b:
        return (False, "")
    if a == b:
        return (True, "완전 일치")
    if a in b or b in a:
        return (True, "부분 포함")
    addr_a = _addr_token(a)
    addr_b = _addr_token(b)
    if addr_a and addr_b and addr_a == addr_b:
        return (True, f"지번 {addr_a} 일치")
    return (False, "")


def _doc06_apply_rules(data: dict) -> list[dict]:
    """추출된 구조화 데이터에 검증 룰 적용 → 서류별 통합 결과 카드 반환.

    카드 구조 (최대 6개 + 누락 카드):
      · (미첨부) X         — 필수 유형 중 식별 안 된 서류
      1) 산업안전보건관리비 사용내역서  — 기준=합계 + 안전관리자 날인 + 현장소장 날인
      2) 항목별 사용내역                — 사용내역서와 7개 항목 금액 비교
      3) 사진대지                       — 공사명 + 품목 매칭
      4) 세금계산서                     — 보호구 품목과 매핑
      5) 거래명세서                     — 세금계산서와 매칭
      6) 지급대장                       — 수량 매칭

    각 카드: 모든 sub-check OK → OK / 하나라도 NG → NG (비고에 어디가 어긋났는지)
    "파일 1" 같은 식별 요약 카드는 결과에서 제외 (내부 디버그 정보).
    """
    results: list[dict] = []

    # 조건부 SKIP: 비용 사용 없음
    if not data.get("비용_사용", True):
        results.append(make_result(
            "비용 사용 없음", "OK", "사용내역서 합계 0원", "증빙 서류 불필요"
        ))
        return results

    사용내역서 = data.get("사용내역서") or None
    항목별 = data.get("항목별_사용내역") or None
    사진대지 = data.get("사진대지") or None
    세금계산서들 = data.get("세금계산서") or []
    거래명세서들 = data.get("거래명세서") or []
    지급대장 = data.get("지급대장") or []
    식별_요약 = data.get("식별_요약") or []

    # ── 누락 유형 (필수 유형 중 식별 안 된 것) ────────────────────
    # 식별 요약 자체는 결과 카드에 포함 X (파일 1, 파일 2 같은 표현 제거)
    found_types = {entry.get("유형") for entry in 식별_요약 if entry.get("유형") in _DOC06_REQUIRED_TYPES}
    for req in _DOC06_REQUIRED_TYPES:
        if req not in found_types:
            results.append(make_result(
                f"(미첨부) {req}", "NG", "",
                f"'{req}' 유형 파일이 식별되지 않음"
            ))

    # ── 카드 1: 산업안전보건관리비 사용내역서 ─────────────────────
    # 룰 1-가 (기준=합계) + 1-나 (안전관리자 날인) + 1-다 (현장소장 날인) 통합
    if 사용내역서:
        sub_results: list[str] = []
        ng_reasons: list[str] = []

        # 기준 vs 합계
        a = 사용내역서.get("기준금액")
        b = 사용내역서.get("합계금액")
        if a is None or b is None:
            sub_results.append("기준/합계 추출 실패")
            ng_reasons.append("기준금액 또는 합계금액 추출 실패")
        elif a == b:
            sub_results.append(f"기준 {_won(a)} = 합계 {_won(b)}")
        else:
            sub_results.append(f"기준 {_won(a)} ≠ 합계 {_won(b)}")
            ng_reasons.append(f"기준 vs 합계 불일치 (기준 {_won(a)} vs 합계 {_won(b)})")

        # 안전관리자 날인
        if 사용내역서.get("안전관리자_날인"):
            sub_results.append("안전관리자 날인 ✓")
        else:
            sub_results.append("안전관리자 날인 ✗")
            ng_reasons.append("안전관리자 날인 없음")

        # 현장소장 날인
        if 사용내역서.get("현장소장_날인"):
            sub_results.append("현장소장 날인 ✓")
        else:
            sub_results.append("현장소장 날인 ✗")
            ng_reasons.append("현장소장 날인 없음")

        if not ng_reasons:
            results.append(make_result(
                "산업안전보건관리비 사용내역서", "OK",
                " / ".join(sub_results), "모든 항목 일치"
            ))
        else:
            results.append(make_result(
                "산업안전보건관리비 사용내역서", "NG",
                " / ".join(sub_results), " · ".join(ng_reasons)
            ))
    else:
        results.append(make_result(
            "산업안전보건관리비 사용내역서", "SKIP", "", "사용내역서 미첨부"
        ))

    # ── 카드 2: 항목별 사용내역 (룰 2-가) ────────────────────────
    if 항목별 and 사용내역서:
        a_amounts = 사용내역서.get("항목금액") or []
        b_amounts = 항목별.get("항목소계") or []
        if len(a_amounts) == 7 and len(b_amounts) == 7:
            mismatches = []
            for i in range(7):
                if a_amounts[i] != b_amounts[i]:
                    mismatches.append(
                        f"항목 {i+1} (사용내역서 {_won(a_amounts[i])} vs 항목별 {_won(b_amounts[i])})"
                    )
            if not mismatches:
                results.append(make_result(
                    "항목별 사용내역", "OK",
                    "전 7개 항목 사용내역서와 일치", "모든 항목 일치"
                ))
            else:
                results.append(make_result(
                    "항목별 사용내역", "NG",
                    f"{len(mismatches)}개 항목 불일치",
                    " · ".join(mismatches)
                ))
        else:
            results.append(make_result(
                "항목별 사용내역", "SKIP", "",
                f"항목 금액 추출 부족 (사용내역서 {len(a_amounts)}개 / 항목별 {len(b_amounts)}개)"
            ))
    elif 항목별 and not 사용내역서:
        results.append(make_result(
            "항목별 사용내역", "SKIP", "",
            "사용내역서 미첨부 (비교 대상 부족)"
        ))
    else:
        results.append(make_result(
            "항목별 사용내역", "SKIP", "", "항목별 사용내역 미첨부"
        ))

    # ── 카드 3: 사진대지 (룰 3-가) ───────────────────────────────
    if 사진대지:
        ph_name = (사진대지.get("공사명_표기") or "").strip()
        ref_name = (사용내역서.get("공사명") if 사용내역서 else "") or ""
        ref_name = ref_name.strip()
        name_match, name_match_reason = _name_loose_match(ph_name, ref_name)
        ph_items = [s.strip() for s in (사진대지.get("품목_표기") or []) if s and s.strip()]
        보호구_품명들 = [
            (item.get("품명") or "").strip()
            for item in (항목별.get("보호구_품목") if 항목별 else []) or []
        ]
        item_match = True if not ph_items else any(
            any((ph in pn) or (pn in ph) for pn in 보호구_품명들 if pn)
            for ph in ph_items
        )
        ph_items_str = ", ".join(ph_items) if ph_items else "(없음)"
        ext_summary = f"공사명 '{ph_name}' / 품목 {ph_items_str}"

        if name_match and item_match:
            note_parts = []
            if name_match_reason and name_match_reason != "완전 일치":
                note_parts.append(
                    f"공사명 {name_match_reason} (사진 '{ph_name}' vs 사용내역서 '{ref_name}')"
                )
            else:
                note_parts.append("공사명 일치")
            note_parts.append("품목 일치")
            results.append(make_result(
                "사진대지", "OK", ext_summary, " · ".join(note_parts)
            ))
        else:
            reasons = []
            if not name_match:
                reasons.append(f"공사명 불일치 (사진 '{ph_name}' vs 사용내역서 '{ref_name}')")
            if not item_match:
                reasons.append(f"품목 매칭 실패 (사진 {ph_items} / 보호구 {보호구_품명들})")
            results.append(make_result(
                "사진대지", "NG", ext_summary, " · ".join(reasons)
            ))
    else:
        results.append(make_result(
            "사진대지", "SKIP", "", "사진대지 미첨부"
        ))

    # ── 카드 4: 세금계산서 (룰 4-가, 보호구 품목 매핑) ──────────
    if 세금계산서들:
        if 항목별:
            보호구 = 항목별.get("보호구_품목") or []
            보호구_set = {(item.get("품명") or "").strip() for item in 보호구}
            mismatches = []
            for tx in 세금계산서들:
                tx_품목 = (tx.get("품목") or "").strip()
                matched = any(
                    tx_품목 == pn or tx_품목 in pn or pn in tx_품목
                    for pn in 보호구_set if pn
                )
                if not matched:
                    mismatches.append(
                        f"'{tx_품목}' (일자 {tx.get('일자', '?')}, {_won(tx.get('공급가액'))})"
                    )
            if not mismatches:
                results.append(make_result(
                    "세금계산서", "OK",
                    f"{len(세금계산서들)}건 — 항목별 보호구와 모두 매핑 OK",
                    "전 건 매핑 완료"
                ))
            else:
                results.append(make_result(
                    "세금계산서", "NG",
                    f"{len(세금계산서들)}건 중 {len(mismatches)}건 매핑 실패",
                    " · ".join(mismatches)
                ))
        else:
            results.append(make_result(
                "세금계산서", "SKIP", f"{len(세금계산서들)}건 첨부",
                "항목별 사용내역 미첨부 (매핑 대상 부족)"
            ))
    else:
        results.append(make_result(
            "세금계산서", "SKIP", "", "세금계산서 미첨부"
        ))

    # ── 카드 5: 거래명세서 (룰 5-가, 세금계산서와 매칭) ─────────
    # 매칭 규칙: 금액 정확 일치 + 일자 형식 정규화 + 품목 정규화/포함 매칭
    if 거래명세서들:
        if 세금계산서들:
            mismatches = []
            for tm in 거래명세서들:
                tm_일자 = _normalize_date_for_match(tm.get("일자"))
                tm_금액 = tm.get("공급가액")
                tm_품목 = tm.get("품목") or ""
                found_pair = any(
                    _normalize_date_for_match(sx.get("일자")) == tm_일자
                    and sx.get("공급가액") == tm_금액
                    and _items_match(tm_품목, sx.get("품목") or "")
                    for sx in 세금계산서들
                )
                if not found_pair:
                    mismatches.append(
                        f"'{tm_품목.strip()}' (일자 {(tm.get('일자') or '').strip()}, "
                        f"{_won(tm_금액)}) — 세금계산서와 매칭 X"
                    )
            if not mismatches:
                results.append(make_result(
                    "거래명세서", "OK",
                    f"{len(거래명세서들)}건 — 세금계산서와 모두 일치",
                    "전 건 매칭 완료"
                ))
            else:
                results.append(make_result(
                    "거래명세서", "NG",
                    f"{len(거래명세서들)}건 중 {len(mismatches)}건 매칭 실패",
                    " · ".join(mismatches)
                ))
        else:
            results.append(make_result(
                "거래명세서", "SKIP", f"{len(거래명세서들)}건 첨부",
                "세금계산서 미첨부 (매칭 대상 부족)"
            ))
    else:
        results.append(make_result(
            "거래명세서", "SKIP", "", "거래명세서 미첨부"
        ))

    # ── 카드 6: 지급대장 (룰 6-가, 수량 매칭) ───────────────────
    if 항목별:
        보호구 = 항목별.get("보호구_품목") or []
        if not 보호구:
            results.append(make_result(
                "지급대장", "SKIP", "",
                "항목별 사용내역에 개인보호구 구입비 없음 (지급대장 검증 불필요)"
            ))
        elif not 지급대장:
            results.append(make_result(
                "지급대장", "NG", "",
                "개인보호구 구입비 있는데 지급대장 미첨부"
            ))
        else:
            ledger_by_name = {(item.get("품명") or "").strip(): item.get("수량") for item in 지급대장}
            mismatches = []
            ok_pairs = []
            for item in 보호구:
                품명 = (item.get("품명") or "").strip()
                use_qty = item.get("수량")
                pay_qty = None
                for pn, pq in ledger_by_name.items():
                    if pn == 품명 or pn in 품명 or 품명 in pn:
                        pay_qty = pq
                        break
                if pay_qty is None:
                    mismatches.append(f"{품명} 사용내역 {use_qty}개 vs 지급대장 (해당 품명 없음)")
                elif use_qty == pay_qty:
                    ok_pairs.append(f"{품명} {use_qty}/{pay_qty}")
                else:
                    mismatches.append(f"{품명} 사용내역 {use_qty}개 vs 지급대장 {pay_qty}개")
            if not mismatches:
                results.append(make_result(
                    "지급대장", "OK",
                    ", ".join(ok_pairs), "전 품목 수량 일치"
                ))
            else:
                results.append(make_result(
                    "지급대장", "NG",
                    f"{len(mismatches)}개 품목 불일치",
                    " · ".join(mismatches)
                ))
    else:
        results.append(make_result(
            "지급대장", "SKIP", "", "항목별 사용내역 미첨부 (수량 매칭 대상 부족)"
        ))

    return results


def _call_gemini_multifile(prompt: str, file_parts: list[dict],
                            max_retries: int = 1,
                            company: str | None = None,
                            doc_id: str | None = None) -> str:
    """다중 파일을 한 번에 보내는 Gemini 호출. 429 시 재시도."""
    import google.generativeai as genai
    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(config.GEMINI_MODEL)
    content = [prompt] + file_parts

    for attempt in range(max_retries + 1):
        try:
            response = model.generate_content(content)
            _record_usage(response, company, doc_id)
            return response.text
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg and attempt < max_retries:
                m = re.search(r"retry_delay\s*\{?\s*seconds:\s*(\d+)", error_msg)
                wait_sec = (int(m.group(1)) + 2) if m else 60
                time.sleep(min(wait_sec, 90))
                continue
            raise


# ── 서류 전체 검토 (여러 파일) ────────────────────────────────────
def review_document(doc_id: str, doc_name: str, files: list,
                    project_info: dict | None = None,
                    company: str | None = None,
                    final_cost: int | None = None,
                    final_cost_meta: str | None = None) -> list[dict]:
    """
    files: list of (filename, bytes) tuples
    project_info: {"name": ..., "date": ..., "amount": ...}
    final_cost / final_cost_meta: doc01 준공금액 검증용 (단가표 기반 계산값)
    """
    if not files:
        return [make_result("서류 첨부 여부", "NG", "미첨부", "파일이 업로드되지 않음")]

    # attachment_only 서류 — AI 호출 생략, 첨부만 확인
    if _is_attachment_only(doc_id):
        return [make_result(
            "서류 첨부 여부", "OK", f"{len(files)}개 파일 첨부",
            "첨부 확인 (AI 검토 생략 — 첨부만 확인하는 서류)"
        )]

    # doc01 — 하이브리드 (AI 추출 + Python 룰)
    if doc_id == "doc01":
        return _review_doc01_combined(
            files, project_info, final_cost, final_cost_meta, company=company
        )

    # doc06 — 6개 파일 멀티파일 통합 호출 (특수 처리)
    if doc_id == "doc06":
        return _review_doc06_combined(files, project_info, company=company)

    # doc05: 서류 첨부 여부 행 제외 (각 파일 유형 식별로 갈음)
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
            review_file(doc_id, doc_name, file_bytes, filename, project_info, company=company)
        )
    else:
        with ThreadPoolExecutor(max_workers=len(files)) as executor:
            future_to_idx = {
                executor.submit(review_file, doc_id, doc_name, fb, fn, project_info, company): idx
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
        # 유형 식별 행: 식별된 유형 이름을 항목으로 사용, 추출값은 비움
        for idx, file_results in enumerate(per_file_results):
            for r in file_results:
                item = (r.get("항목") or "").strip()
                if "유형 식별" in item:
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
    # 빠진 유형이 있으면 NG로 표시
    results.extend(missing_rows)

    return results
