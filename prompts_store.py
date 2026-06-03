"""
서류별 검토 프롬프트 저장/로드.

저장 위치:
- Railway 환경: /data/prompts.json (Volume 마운트 가정)
- 로컬: ./data/prompts.json (자동 생성)
- 환경변수 PROMPTS_FILE 로 경로 오버라이드 가능

관리자가 빈 값으로 저장하거나 파일이 없으면 빌트인 기본 프롬프트(_default_prompt) 사용.
"""
import json
import os
from typing import Optional

from documents import DOCUMENTS
from prompt_defaults import PROMPTS as PER_DOC_DEFAULTS
from prompt_defaults import default_version


def _resolve_prompts_file() -> str:
    env_path = os.environ.get("PROMPTS_FILE")
    if env_path:
        return env_path
    # Railway Volume 가정 /data, 없으면 로컬 ./data
    if os.path.isdir("/data"):
        return "/data/prompts.json"
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "prompts.json")


PROMPTS_FILE = _resolve_prompts_file()


def _generic_default_prompt(doc_name: str) -> str:
    """서류별 전용 프롬프트가 없을 때 쓰는 generic 템플릿."""
    return f"""\
도시가스 공사 준공서류 - 「{doc_name}」 검토.
첨부된 서류를 분석하여 다음 항목을 검토하고 **JSON 배열만** 응답하세요. (다른 설명/마크다운 금지)

[사용자 입력 공사 정보 — 서류와 대조 검증]
{{project_info}}

[검토 항목 — 항상 정확히 아래 4개만, 같은 순서로]
1. "공사명 확인"     : 입력 공사명과 서류 표기 공사명 일치 여부
2. "준공일자 확인"   : 입력 준공일자와 서류의 준공/완공 일자 일치 여부
3. "준공금액 확인"   : 입력 준공금액과 서류의 계약/준공/총액/합계 등 금액 일치 여부
                       (콤마/단위/VAT 별도 무시. 서류에 여러 금액이 있어도 종합 판단)
4. "서명/날인 여부"  : 서명 또는 인감 날인이 있는지

[엄격 매칭 원칙 — 절대 원칙]
✅ 같은 값 = OK
❌ 다른 값 = NG (어디가 다른지 비고에 명시)
⚠️ 부분 일치 / 판단 모호 = WARN
- "비슷해 보임"을 OK로 적지 말 것
- 공사명의 공사 종류 키워드는 글자 그대로 일치해야 OK
  · 도시가스 공사 종류 예: "인입공급관", "주위공급관", "공급관", "본관", "사용자공급관"
  · 이들은 서로 완전히 다른 공사 종류. 의미상 비슷하다고 동일 취급 금지.
  · 예: 입력 "주위공급관" vs 서류 "인입공급관" → 반드시 NG

[결과 규칙]
- 입력 정보 없거나 서류에 해당 정보 없음 → "결과": "SKIP", "비고": "서류에서 확인 불가" 또는 "입력 정보 없음"
- 모든 부분 일치/존재                    → "결과": "OK", "추출값": 서류에서 추출한 실제 값
- 명백한 불일치                          → "결과": "NG", "비고": "입력값 X / 서류값 Y" 형태로 비교
- 부분 일치 / 판단 어려움                → "결과": "WARN", "비고": 이유 설명

[응답 형식 (예시)]
[
  {{{{"항목":"공사명 확인","결과":"OK","추출값":"춘천시 거두리 ...","비고":""}}}},
  {{{{"항목":"준공일자 확인","결과":"OK","추출값":"2025-10-30","비고":""}}}},
  {{{{"항목":"준공금액 확인","결과":"OK","추출값":"3,400,910","비고":""}}}},
  {{{{"항목":"서명/날인 여부","결과":"OK","추출값":"대표이사 인감 확인","비고":""}}}}
]
"""


def _default_prompt(doc_id: str, doc_name: str) -> str:
    """서류 기본 프롬프트 — 서류별 전용이 있으면 그것, 없으면 generic."""
    if doc_id in PER_DOC_DEFAULTS:
        return PER_DOC_DEFAULTS[doc_id]
    return _generic_default_prompt(doc_name)


def load_all() -> dict:
    """저장된 원본 dict 반환.

    저장 형식 두 가지를 모두 허용:
      · 신형: {doc_id: {"text": "...", "base_version": N}}
      · 구형(하위호환): {doc_id: "프롬프트 문자열"}  → base_version 0 으로 간주
    """
    if not os.path.exists(PROMPTS_FILE):
        return {}
    try:
        with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_all(prompts: dict) -> None:
    """전체 prompts dict 저장."""
    os.makedirs(os.path.dirname(PROMPTS_FILE), exist_ok=True)
    with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
        json.dump(prompts, f, ensure_ascii=False, indent=2)


def _parse_entry(entry) -> tuple[str, int]:
    """저장 엔트리 → (text, base_version). 구형 문자열은 버전 0."""
    if isinstance(entry, str):
        return entry.strip(), 0
    if isinstance(entry, dict):
        return (entry.get("text") or "").strip(), int(entry.get("base_version", 0))
    return "", 0


def _effective_saved_text(doc_id: str) -> str:
    """버전을 반영한 '유효한' 저장 프롬프트.

    저장본이 있어도 base_version 이 현재 기본값 버전보다 낮으면 (낡은 저장본)
    빈 문자열을 반환 → 호출부가 기본값을 쓰게 함.
    """
    text, base_v = _parse_entry(load_all().get(doc_id))
    if text and base_v >= default_version(doc_id):
        return text
    return ""


def get_effective_prompt(doc_id: str, doc_name: str) -> str:
    """유효한 저장 프롬프트가 있으면 사용, 없으면(또는 낡았으면) 서류별 기본값."""
    saved = _effective_saved_text(doc_id)
    return saved if saved else _default_prompt(doc_id, doc_name)


def save_prompt(doc_id: str, text: str) -> None:
    """관리자 저장 — 현재 기본값 버전을 함께 기록. 빈 값이면 저장본 제거(기본값 복원)."""
    all_prompts = load_all()
    if text and text.strip():
        all_prompts[doc_id] = {
            "text": text,
            "base_version": default_version(doc_id),
        }
    else:
        all_prompts.pop(doc_id, None)
    save_all(all_prompts)


def reset_prompt(doc_id: str) -> None:
    """저장본 제거 → 기본값으로 복원."""
    all_prompts = load_all()
    all_prompts.pop(doc_id, None)
    save_all(all_prompts)


def list_for_admin() -> list[dict]:
    """관리자 페이지용 — DOCUMENTS 순서대로 (id, name, prompt, is_custom) 리스트.
    낡은(버전 미달) 저장본은 custom 으로 보지 않고 기본값을 노출한다."""
    out = []
    for d in DOCUMENTS:
        did = d["id"]
        prompt = _effective_saved_text(did)
        out.append({
            "id": did,
            "num": d["num"],
            "name": d["name"],
            "prompt": prompt or _default_prompt(did, d["name"]),
            "is_custom": bool(prompt),
        })
    return out


def get_default(doc_name: str, doc_id: str = "") -> str:
    """하위 호환 — 가능한 경우 doc_id로 서류별 기본값 반환, 아니면 generic."""
    return _default_prompt(doc_id, doc_name)
