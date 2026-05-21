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


def _resolve_prompts_file() -> str:
    env_path = os.environ.get("PROMPTS_FILE")
    if env_path:
        return env_path
    # Railway Volume 가정 /data, 없으면 로컬 ./data
    if os.path.isdir("/data"):
        return "/data/prompts.json"
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "prompts.json")


PROMPTS_FILE = _resolve_prompts_file()


def _default_prompt(doc_name: str) -> str:
    """관리자가 따로 입력하지 않은 서류용 기본 프롬프트."""
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

[결과 규칙]
- 입력 정보 없거나 서류에 해당 정보 없음 → "결과": "SKIP", "비고": "서류에서 확인 불가" 또는 "입력 정보 없음"
- 일치/존재                              → "결과": "OK", "추출값": 서류에서 추출한 실제 값
- 불일치                                 → "결과": "NG", "비고": "입력값 X / 서류값 Y" 형태로 비교
- 판단 어려움 / 부분 충족                → "결과": "WARN", "비고": 이유 설명

[응답 형식 (예시)]
[
  {{{{"항목":"공사명 확인","결과":"OK","추출값":"춘천시 거두리 ...","비고":""}}}},
  {{{{"항목":"준공일자 확인","결과":"OK","추출값":"2025-10-30","비고":""}}}},
  {{{{"항목":"준공금액 확인","결과":"OK","추출값":"3,400,910","비고":""}}}},
  {{{{"항목":"서명/날인 여부","결과":"OK","추출값":"대표이사 인감 확인","비고":""}}}}
]
"""


def load_all() -> dict:
    """저장된 모든 프롬프트 dict 반환 ({doc_id: prompt_text})."""
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


def get_effective_prompt(doc_id: str, doc_name: str) -> str:
    """관리자가 저장한 프롬프트가 있으면 사용, 없으면 기본 프롬프트."""
    saved = load_all().get(doc_id, "").strip()
    return saved if saved else _default_prompt(doc_name)


def list_for_admin() -> list[dict]:
    """관리자 페이지용 — DOCUMENTS 순서대로 (id, name, prompt, is_default) 리스트."""
    saved = load_all()
    out = []
    for d in DOCUMENTS:
        did = d["id"]
        prompt = saved.get(did, "").strip()
        out.append({
            "id": did,
            "num": d["num"],
            "name": d["name"],
            "prompt": prompt or _default_prompt(d["name"]),
            "is_custom": bool(prompt),
        })
    return out


def get_default(doc_name: str) -> str:
    return _default_prompt(doc_name)
