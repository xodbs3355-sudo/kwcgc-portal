"""
Gemini API 사용량 / 비용 기록 + 집계.

저장 형식:
- /data/usage.jsonl (Railway Volume) — 한 줄 = 한 호출
- 로컬: ./data/usage.jsonl

요금 단가 (Flash Lite, 2026년 5월 기준):
- Input:  $0.075 / 1M tokens
- Output: $0.30  / 1M tokens
환율은 1 USD = 1,350 KRW 고정 (대략값 — 정밀 회계 아님)
"""
import datetime
import json
import os
from collections import defaultdict


def _resolve_file() -> str:
    env_path = os.environ.get("USAGE_FILE")
    if env_path:
        return env_path
    if os.path.isdir("/data"):
        return "/data/usage.jsonl"
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "usage.jsonl")


USAGE_FILE = _resolve_file()

# Flash Lite 단가 (USD per 1M tokens)
PRICE_INPUT_PER_M = 0.075
PRICE_OUTPUT_PER_M = 0.30
USD_TO_KRW = 1350


def _calc_cost_krw(prompt_tokens: int, completion_tokens: int) -> float:
    cost_usd = (prompt_tokens * PRICE_INPUT_PER_M
                + completion_tokens * PRICE_OUTPUT_PER_M) / 1_000_000
    return round(cost_usd * USD_TO_KRW, 4)


def record(company: str, doc_id: str,
           prompt_tokens: int, completion_tokens: int) -> None:
    """Gemini 호출 1회 기록 — append-only JSONL."""
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "company": company or "",
        "doc": doc_id or "",
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "cost_krw": _calc_cost_krw(prompt_tokens or 0, completion_tokens or 0),
    }
    try:
        with open(USAGE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # 사용량 기록 실패가 검토 자체를 막으면 안 됨
        pass


def _load_all() -> list[dict]:
    if not os.path.isfile(USAGE_FILE):
        return []
    rows = []
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    return rows


def _today_str() -> str:
    return datetime.date.today().isoformat()


def _month_str() -> str:
    return datetime.date.today().strftime("%Y-%m")


def summary() -> dict:
    """오늘/이번 달/전체 호출 수 + 비용 요약."""
    rows = _load_all()
    today = _today_str()
    month = _month_str()

    today_cnt = today_cost = 0
    month_cnt = month_cost = 0
    total_cnt = total_cost = 0

    for r in rows:
        ts = r.get("ts", "")
        cost = r.get("cost_krw", 0)
        total_cnt += 1
        total_cost += cost
        if ts.startswith(month):
            month_cnt += 1
            month_cost += cost
        if ts.startswith(today):
            today_cnt += 1
            today_cost += cost

    return {
        "today_count": today_cnt,
        "today_cost": round(today_cost, 2),
        "month_count": month_cnt,
        "month_cost": round(month_cost, 2),
        "total_count": total_cnt,
        "total_cost": round(total_cost, 2),
    }


def daily(days: int = 30) -> list[dict]:
    """최근 N일 일별 집계 (오늘 포함, 과거 → 현재 순)."""
    rows = _load_all()
    today = datetime.date.today()
    buckets: dict[str, dict] = {}
    for i in range(days):
        d = (today - datetime.timedelta(days=days - 1 - i)).isoformat()
        buckets[d] = {
            "date": d, "count": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "cost_krw": 0.0,
        }
    for r in rows:
        ts = r.get("ts", "")[:10]
        if ts in buckets:
            b = buckets[ts]
            b["count"] += 1
            b["prompt_tokens"] += r.get("prompt_tokens", 0)
            b["completion_tokens"] += r.get("completion_tokens", 0)
            b["cost_krw"] += r.get("cost_krw", 0)
    return [{**v, "cost_krw": round(v["cost_krw"], 2)} for v in buckets.values()]


def by_doc(period: str = "month") -> list[dict]:
    """서류별 집계. period: 'today' / 'month' / 'all'."""
    rows = _load_all()
    today = _today_str()
    month = _month_str()
    agg = defaultdict(lambda: {"count": 0, "cost_krw": 0.0})
    for r in rows:
        ts = r.get("ts", "")
        if period == "today" and not ts.startswith(today):
            continue
        if period == "month" and not ts.startswith(month):
            continue
        doc = r.get("doc", "") or "(미분류)"
        agg[doc]["count"] += 1
        agg[doc]["cost_krw"] += r.get("cost_krw", 0)
    return sorted(
        [{"doc": k, "count": v["count"], "cost_krw": round(v["cost_krw"], 2)}
         for k, v in agg.items()],
        key=lambda x: x["cost_krw"], reverse=True,
    )


def by_company(period: str = "month") -> list[dict]:
    """회사별 집계."""
    rows = _load_all()
    today = _today_str()
    month = _month_str()
    agg = defaultdict(lambda: {"count": 0, "cost_krw": 0.0})
    for r in rows:
        ts = r.get("ts", "")
        if period == "today" and not ts.startswith(today):
            continue
        if period == "month" and not ts.startswith(month):
            continue
        company = r.get("company", "") or "(미분류)"
        agg[company]["count"] += 1
        agg[company]["cost_krw"] += r.get("cost_krw", 0)
    return sorted(
        [{"company": k, "count": v["count"], "cost_krw": round(v["cost_krw"], 2)}
         for k, v in agg.items()],
        key=lambda x: x["cost_krw"], reverse=True,
    )
