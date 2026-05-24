"""
연간단가표 저장/로드.

저장 위치:
- Railway: /data/unit_prices.json (Volume 영구 보존)
- 로컬: ./data/unit_prices.json
- 환경변수 UNIT_PRICES_FILE 로 경로 오버라이드 가능

데이터 구조:
{
  "2026": {
    "ASP":        {"1m": 1000000, ..., "10m": 4000000},
    "CONC_BLOCK": {"1m": 800000,  ..., "10m": 3500000},
    "PLP옵션":    500000                              ← 연도 최상위 (도로재질·연장 무관)
  },
  "2025": { ... }
}

도로재질 매핑 (사용자 입력 콤보박스 → 단가표 키):
  ASP        → ASP
  CON`C      → CONC_BLOCK
  보도블럭   → CONC_BLOCK
"""
import datetime
import json
import os


def _resolve_file() -> str:
    env_path = os.environ.get("UNIT_PRICES_FILE")
    if env_path:
        return env_path
    if os.path.isdir("/data"):
        return "/data/unit_prices.json"
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data", "unit_prices.json",
    )


UNIT_PRICES_FILE = _resolve_file()

LENGTH_KEYS = ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "10m"]
MATERIAL_KEYS = ["ASP", "CONC_BLOCK"]
MATERIAL_DISPLAY = {
    "ASP": "ASP",
    "CONC_BLOCK": "CON`C 및 보도블럭(ASP 外)",
}
PLP_KEY = "PLP옵션"


def default_applicable_year() -> int:
    """오늘 기준 단가 적용 연도. 단가계약 기간은 당해년도 5/1 ~ 익년도 4/30."""
    today = datetime.date.today()
    return today.year if today.month >= 5 else today.year - 1

# 사용자 콤보박스 값 → 단가표 카테고리 키 매핑
ROAD_MATERIAL_MAP = {
    "ASP": "ASP",
    "CON`C": "CONC_BLOCK",
    "보도블럭": "CONC_BLOCK",
}


def _empty_year() -> dict:
    """빈 연도 데이터 골격."""
    data = {mat: {lk: 0 for lk in LENGTH_KEYS} for mat in MATERIAL_KEYS}
    data[PLP_KEY] = 0
    return data


def load_all() -> dict:
    if not os.path.exists(UNIT_PRICES_FILE):
        return {}
    try:
        with open(UNIT_PRICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_all(data: dict) -> None:
    os.makedirs(os.path.dirname(UNIT_PRICES_FILE), exist_ok=True)
    with open(UNIT_PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_years() -> list[str]:
    """등록된 연도 리스트 (최신순)."""
    return sorted(load_all().keys(), reverse=True)


def get_year(year: str) -> dict:
    data = load_all()
    return _ensure_shape(data.get(year))


def _ensure_shape(year_data: dict | None) -> dict:
    """저장된 데이터에 빠진 필드가 있으면 0으로 채움. 신구 구조 모두 대응."""
    out = _empty_year()
    if not year_data:
        return out
    for mat in MATERIAL_KEYS:
        src = year_data.get(mat) or {}
        for lk in LENGTH_KEYS:
            try:
                out[mat][lk] = int(src.get(lk) or 0)
            except (TypeError, ValueError):
                out[mat][lk] = 0
    try:
        out[PLP_KEY] = int(year_data.get(PLP_KEY) or 0)
    except (TypeError, ValueError):
        out[PLP_KEY] = 0
    return out


def set_year(year: str, prices: dict) -> None:
    data = load_all()
    data[year] = _ensure_shape(prices)
    save_all(data)


def add_year(year: str) -> bool:
    """새 연도 추가. 이미 있으면 False, 추가하면 True."""
    data = load_all()
    if year in data:
        return False
    data[year] = _empty_year()
    save_all(data)
    return True


def delete_year(year: str) -> None:
    data = load_all()
    data.pop(year, None)
    save_all(data)


def reset_year(year: str) -> bool:
    """해당 연도의 모든 단가를 0으로 리셋 (연도 자체는 유지).
    존재하지 않는 연도면 False.
    """
    data = load_all()
    if year not in data:
        return False
    data[year] = _empty_year()
    save_all(data)
    return True


def lookup_price(year: str, road_material: str, extension_m: int, plp: bool) -> int | None:
    """검토 단계 최종 공사비 산출용 — 단가 + (PLP 옵션) 조회.

    road_material: 사용자 콤보박스 값 ("ASP" / "CON`C" / "보도블럭")
    extension_m: 1~10 정수
    plp: True 시 PLP 옵션 가산 (연도별 단일 값)
    """
    mat_key = ROAD_MATERIAL_MAP.get(road_material)
    if mat_key is None or extension_m < 1 or extension_m > 10:
        return None
    year_data = get_year(year)
    base = year_data[mat_key][f"{extension_m}m"]
    if plp:
        base += year_data[PLP_KEY]
    return base


def compute_final_cost(project_info: dict,
                       adjustment_required: bool | None) -> tuple[int | None, str | None]:
    """최종 공사비 계산 — app.py / reviewer.py 공통 사용.

    단가 적용 연장 룰:
      · 정산 대상(True)  → 준공연장(extension) ceiling
      · 정산 비대상/미정 → 발주연장(order_extension) 그대로

    반환: (final_cost, meta_text) — 계산 가능하면 금액과 산식 메타,
          계산 불가하면 (None, None).
    """
    import math
    try:
        year = (project_info.get("year") or "").strip()
        road_material = (project_info.get("road_material") or "").strip()
        plp = bool(project_info.get("plp"))
        land_fee_raw = (project_info.get("land_fee") or "").strip()

        ext_m: int | None = None
        ext_basis = ""
        if adjustment_required is True:
            try:
                done_ext = float((project_info.get("extension") or "").strip())
                ext_m = math.ceil(done_ext)
                ext_basis = f"준공 {ext_m}m(올림)"
            except (ValueError, TypeError):
                ext_m = None
        else:
            try:
                order_ext = float((project_info.get("order_extension") or "").strip())
                ext_m = int(round(order_ext))
                ext_basis = f"발주 {ext_m}m"
            except (ValueError, TypeError):
                ext_m = None

        if not (year and road_material and ext_m and 1 <= ext_m <= 10):
            return (None, None)

        base = lookup_price(year, road_material, ext_m, plp)
        if base is None or base <= 0:
            return (None, None)

        land_fee = 0
        if land_fee_raw:
            try:
                land_fee = int(round(float(land_fee_raw.replace(",", ""))))
            except (ValueError, TypeError):
                land_fee = 0

        final_cost = base + land_fee
        parts = [f"{ext_basis} 단가"] if ext_basis else [f"{ext_m}m 단가"]
        if plp:
            parts.append("PLP")
        if land_fee > 0:
            parts.append("일시점용료")
        return (final_cost, " + ".join(parts))
    except Exception:
        return (None, None)
