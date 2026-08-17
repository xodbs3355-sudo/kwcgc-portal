"""
Gemini API 사용량 / 비용 기록 + 집계.

저장 형식:
- /data/usage.jsonl (Railway Volume) — 한 줄 = 한 호출
- 로컬: ./data/usage.jsonl

요금 단가는 서류별로 실제 적용된 모델(config.model_for)에 따라 계산.
(대략값 — 정밀 회계 아님. 환율 1 USD = 1,500 KRW 고정)
"""
import datetime
import json
import os
from collections import defaultdict

import config


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

# 모델별 단가 (USD per 1M tokens) — (input, output). 대략값(정밀 회계 아님).
MODEL_PRICING = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.6-flash":      (0.50, 3.00),
    "gemini-2.5-flash":      (0.15, 1.25),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}
DEFAULT_PRICING = (0.25, 1.50)   # 미등록 모델 fallback
USD_TO_KRW = 1500   # 약 1,500원 기준 (사용자 정책)


def _price_for(doc_id: str | None) -> tuple[float, float]:
    """해당 서류에 실제 적용된 모델의 (input, output) 단가."""
    model = config.model_for(doc_id)
    return MODEL_PRICING.get(model, DEFAULT_PRICING)


def _calc_cost_krw(prompt_tokens: int, completion_tokens: int,
                   doc_id: str | None = None) -> float:
    price_in, price_out = _price_for(doc_id)
    cost_usd = (prompt_tokens * price_in
                + completion_tokens * price_out) / 1_000_000
    return round(cost_usd * USD_TO_KRW, 4)


def record(company: str, doc_id: str,
           prompt_tokens: int, completion_tokens: int) -> None:
    """Gemini 호출 1회 기록 — append-only JSONL."""
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "company": company or "",
        "doc": doc_id or "",
        "model": config.model_for(doc_id),
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "cost_krw": _calc_cost_krw(prompt_tokens or 0, completion_tokens or 0, doc_id),
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


def compute_admin_metrics(daily_days: int = 30, period: str = "month") -> dict:
    """관리자 페이지 한 번 진입 = 1회 파일 read 로 모든 집계 계산.

    기존 summary/daily/by_doc/by_company 를 각각 호출하면 _load_all() 가 4번
    실행되어 jsonl 누적 시 비효율. 본 함수는 단일 패스로 4종 집계 동시 산출.

    반환: {summary, daily, by_doc, by_company} — 형태는 기존 함수들과 동일.
    """
    rows = _load_all()
    today = _today_str()
    month = _month_str()
    today_obj = datetime.date.today()

    # summary 누적
    s_today_cnt = s_today_cost = 0
    s_month_cnt = s_month_cost = 0
    s_total_cnt = s_total_cost = 0

    # daily 버킷 초기화
    daily_buckets: dict[str, dict] = {}
    for i in range(daily_days):
        d = (today_obj - datetime.timedelta(days=daily_days - 1 - i)).isoformat()
        daily_buckets[d] = {
            "date": d, "count": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "cost_krw": 0.0,
        }

    # by_doc / by_company 집계 (period 필터 동일 적용)
    by_doc_agg = defaultdict(lambda: {"count": 0, "cost_krw": 0.0})
    by_company_agg = defaultdict(lambda: {"count": 0, "cost_krw": 0.0})

    for r in rows:
        ts = r.get("ts", "")
        cost = r.get("cost_krw", 0)

        # summary
        s_total_cnt += 1
        s_total_cost += cost
        if ts.startswith(month):
            s_month_cnt += 1
            s_month_cost += cost
        if ts.startswith(today):
            s_today_cnt += 1
            s_today_cost += cost

        # daily
        ts_date = ts[:10]
        if ts_date in daily_buckets:
            b = daily_buckets[ts_date]
            b["count"] += 1
            b["prompt_tokens"] += r.get("prompt_tokens", 0)
            b["completion_tokens"] += r.get("completion_tokens", 0)
            b["cost_krw"] += cost

        # by_doc / by_company (period 필터)
        in_period = (
            period == "all"
            or (period == "today" and ts.startswith(today))
            or (period == "month" and ts.startswith(month))
        )
        if in_period:
            doc = r.get("doc", "") or "(미분류)"
            comp = r.get("company", "") or "(미분류)"
            by_doc_agg[doc]["count"] += 1
            by_doc_agg[doc]["cost_krw"] += cost
            by_company_agg[comp]["count"] += 1
            by_company_agg[comp]["cost_krw"] += cost

    return {
        "summary": {
            "today_count": s_today_cnt, "today_cost": round(s_today_cost, 2),
            "month_count": s_month_cnt, "month_cost": round(s_month_cost, 2),
            "total_count": s_total_cnt, "total_cost": round(s_total_cost, 2),
        },
        "daily": [{**v, "cost_krw": round(v["cost_krw"], 2)}
                  for v in daily_buckets.values()],
        "by_doc": sorted(
            [{"doc": k, "count": v["count"], "cost_krw": round(v["cost_krw"], 2)}
             for k, v in by_doc_agg.items()],
            key=lambda x: x["cost_krw"], reverse=True,
        ),
        "by_company": sorted(
            [{"company": k, "count": v["count"], "cost_krw": round(v["cost_krw"], 2)}
             for k, v in by_company_agg.items()],
            key=lambda x: x["cost_krw"], reverse=True,
        ),
    }
