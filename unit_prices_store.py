"""
연간단가표 저장/로드.

저장 위치:
- Railway: /data/unit_prices.json (Volume 영구 보존)
- 로컬: ./data/unit_prices.json
- 환경변수 UNIT_PRICES_FILE 로 경로 오버라이드 가능

데이터 구조:
{
  "2026": {
    "ASP_CONC":   {"1m": 1000000, ..., "10m": 4000000, "PLP옵션": 500000},
    "보도블럭":    {"1m": 800000,  ..., "10m": 3500000, "PLP옵션": 400000}
  },
  "2025": { ... }
}
"""
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
MATERIAL_KEYS = ["ASP_CONC", "보도블럭"]
MATERIAL_DISPLAY = {
    "ASP_CONC": "ASP / CON`C",
    "보도블럭": "보도블럭",
}
PLP_KEY = "PLP옵션"


def _empty_year() -> dict:
    return {
        mat: {**{lk: 0 for lk in LENGTH_KEYS}, PLP_KEY: 0}
        for mat in MATERIAL_KEYS
    }


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
    """특정 연도 데이터. 없으면 빈 구조 반환."""
    data = load_all()
    return _ensure_shape(data.get(year))


def _ensure_shape(year_data: dict | None) -> dict:
    """저장된 데이터에 빠진 필드가 있으면 0으로 채움."""
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
            out[mat][PLP_KEY] = int(src.get(PLP_KEY) or 0)
        except (TypeError, ValueError):
            out[mat][PLP_KEY] = 0
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


def lookup_price(year: str, road_material: str, extension_m: int, plp: bool) -> int | None:
    """검토 단계에서 최종 공사비 산출용 — 단가 + (PLP 옵션) 조회.

    road_material: "ASP", "CON`C", "보도블럭" 중 하나 (입력 탭의 콤보박스 값)
    extension_m: 1~10 정수 (소수점은 반올림하여 사용)
    plp: True 시 PLP 옵션 가산
    """
    # 콤보 값을 단가표 키로 매핑
    if road_material in ("ASP", "CON`C"):
        mat_key = "ASP_CONC"
    elif road_material == "보도블럭":
        mat_key = "보도블럭"
    else:
        return None
    if extension_m < 1 or extension_m > 10:
        return None
    year_data = get_year(year)
    base = year_data[mat_key][f"{extension_m}m"]
    if plp:
        base += year_data[mat_key][PLP_KEY]
    return base
