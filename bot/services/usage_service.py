"""Claude API 사용량 추적 서비스 - 모델별 단가, 캐시 토큰 반영"""

import aiosqlite
from datetime import datetime, timezone, timedelta
from bot.config import DATABASE_PATH

KST = timezone(timedelta(hours=9))

# 모델별 가격 (1M 토큰당, USD). 캐시 읽기 0.1x, 캐시 생성 1.25x
PRICING = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    # 과거 기록 조회용 (2026-06-15 retire된 구 모델)
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00},
}
_DEFAULT_PRICE_KEY = "claude-sonnet-5"

CACHE_READ_RATE = 0.1
CACHE_WRITE_RATE = 1.25


def _prices(model: str) -> dict:
    if model in PRICING:
        return PRICING[model]
    # 날짜 접미사 등 변형 ID 대응
    for key, val in PRICING.items():
        if model and model.startswith(key):
            return val
    return PRICING[_DEFAULT_PRICE_KEY]


def estimate_cost(
    input_tokens: int, output_tokens: int, model: str = _DEFAULT_PRICE_KEY,
    cache_read_tokens: int = 0, cache_creation_tokens: int = 0,
) -> float:
    """예상 비용 계산 (USD). input_tokens는 캐시되지 않은 입력 토큰."""
    p = _prices(model)
    return (
        input_tokens / 1_000_000 * p["input"]
        + cache_read_tokens / 1_000_000 * p["input"] * CACHE_READ_RATE
        + cache_creation_tokens / 1_000_000 * p["input"] * CACHE_WRITE_RATE
        + output_tokens / 1_000_000 * p["output"]
    )


async def log_usage(
    user_id: int, model: str, input_tokens: int, output_tokens: int, tool_calls: int = 0,
    cache_read_tokens: int = 0, cache_creation_tokens: int = 0,
):
    """API 사용량 기록"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO api_usage (user_id, model, input_tokens, output_tokens, tool_calls, "
            "cache_read_tokens, cache_creation_tokens) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, model, input_tokens, output_tokens, tool_calls, cache_read_tokens, cache_creation_tokens),
        )
        await db.commit()


def _utc_start_of(kst_dt: datetime) -> str:
    """SQLite CURRENT_TIMESTAMP는 UTC이므로 KST 시각을 UTC 문자열로"""
    return (kst_dt - timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


def _today_start() -> datetime:
    return datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start() -> datetime:
    return datetime.now(KST).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _summary_since(utc_start: str, user_id: int = None) -> dict:
    """기간 합계 + 모델별 단가로 계산한 비용 + 캐시 히트율"""
    where = "created_at >= ?"
    params: tuple = (utc_start,)
    if user_id:
        where += " AND user_id = ?"
        params = (utc_start, user_id)

    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            f"SELECT model, COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            f"COUNT(*), COALESCE(SUM(tool_calls),0), COALESCE(SUM(cache_read_tokens),0), "
            f"COALESCE(SUM(cache_creation_tokens),0) FROM api_usage WHERE {where} GROUP BY model",
            params,
        )

    summary = {
        "input_tokens": 0, "output_tokens": 0, "calls": 0, "tool_calls": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost": 0.0, "by_model": {},
    }
    for model, inp, out, calls, tools, cr, cc in rows:
        cost = estimate_cost(inp, out, model, cr, cc)
        summary["input_tokens"] += inp
        summary["output_tokens"] += out
        summary["calls"] += calls
        summary["tool_calls"] += tools
        summary["cache_read_tokens"] += cr
        summary["cache_creation_tokens"] += cc
        summary["cost"] += cost
        summary["by_model"][model] = {"calls": calls, "cost": cost, "input": inp, "output": out}

    total_in = summary["input_tokens"] + summary["cache_read_tokens"] + summary["cache_creation_tokens"]
    summary["cache_hit_rate"] = (summary["cache_read_tokens"] / total_in) if total_in else 0.0
    return summary


async def get_usage_today(user_id: int = None) -> dict:
    """오늘 사용량 조회 (모델별 비용 포함)"""
    return await _summary_since(_utc_start_of(_today_start()), user_id)


async def get_usage_month(user_id: int = None) -> dict:
    """이번 달 사용량 조회 (모델별 비용 포함)"""
    return await _summary_since(_utc_start_of(_month_start()), user_id)


async def get_usage_week() -> dict:
    """최근 7일 사용량 (주간 다이제스트용)"""
    start = datetime.now(KST) - timedelta(days=7)
    return await _summary_since(_utc_start_of(start))


async def get_daily_breakdown(days: int = 7) -> list[dict]:
    """일별 사용량 + 비용 (모델별 단가 반영)"""
    start = datetime.now(KST) - timedelta(days=days)

    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            """SELECT DATE(created_at, '+9 hours') as day, model,
                      SUM(input_tokens), SUM(output_tokens), COUNT(*),
                      SUM(cache_read_tokens), SUM(cache_creation_tokens)
               FROM api_usage
               WHERE created_at >= ?
               GROUP BY day, model
               ORDER BY day DESC""",
            (_utc_start_of(start),),
        )

    days_map: dict[str, dict] = {}
    for day, model, inp, out, calls, cr, cc in rows:
        d = days_map.setdefault(day, {"date": day, "input_tokens": 0, "output_tokens": 0, "calls": 0, "cost": 0.0})
        d["input_tokens"] += inp or 0
        d["output_tokens"] += out or 0
        d["calls"] += calls
        d["cost"] += estimate_cost(inp or 0, out or 0, model, cr or 0, cc or 0)
    return list(days_map.values())


async def get_cost_today() -> float:
    """오늘 총 비용 (USD)"""
    return (await get_usage_today())["cost"]
