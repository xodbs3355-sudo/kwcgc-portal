import os

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# ── Gemini 모델 (서류별 차등 적용) ──────────────────────────────
# 기본(경량) 모델 — 단순 4항목 판독 서류. env GEMINI_MODEL 로 재정의 가능.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
# 상위 모델 — 스캔·수기·상세내역 등 난이도 높은 서류. env GEMINI_MODEL_HEAVY 로 재정의.
GEMINI_MODEL_HEAVY = os.environ.get("GEMINI_MODEL_HEAVY", "gemini-3.6-flash")

# 상위 모델을 적용할 서류 id
#   doc09 화재위험작업허가서 (스캔·수기·서명 판독)
#   doc02 하도급 공종별 작업내역서 (품목·단가·수량 상세)
#   doc06 산업안전보건관리비 내역서 (금액·날인 검증)
HEAVY_DOC_IDS = {"doc09", "doc02", "doc06"}


def model_for(doc_id: str | None) -> str:
    """서류별 모델 선택 — 난이도 높은 서류는 상위 모델, 그 외 기본 모델."""
    if doc_id in HEAVY_DOC_IDS:
        return GEMINI_MODEL_HEAVY
    return GEMINI_MODEL


USE_MOCK = not bool(GEMINI_API_KEY)
