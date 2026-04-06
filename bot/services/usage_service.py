"""Claude API 사용량 추적 서비스"""

import aiosqlite
from datetime import datetime, timezone, timedelta
from bot.config import DATABASE_PATH

KST = timezone(timedelta(hours=9))

# Haiku 4.5 가격 (1M 토큰당)
PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-opus-4-20250514": {"input": 15.00, "output": 75.00},
}


async def log_usage(user_id: int, model: str, input_tokens: int, output_tokens: int, tool_calls: int = 0):
    """API 사용량 기록"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO api_usage (user_id, model, input_tokens, output_tokens, tool_calls) VALUES (?, ?, ?, ?, ?)",
            (user_id, model, input_tokens, output_tokens, tool_calls),
        )
        await db.commit()


async def get_usage_today(user_id: int = None) -> dict:
    """오늘 사용량 조회"""
    now = datetime.now(KST)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # SQLite는 UTC로 저장하므로 KST 0시 = UTC 전날 15시
    utc_start = (today_start - timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        if user_id:
            row = await db.execute_fetchall(
                "SELECT COALESCE(SUM(input_tokens),0) as input_tokens, COALESCE(SUM(output_tokens),0) as output_tokens, COUNT(*) as calls, COALESCE(SUM(tool_calls),0) as tool_calls FROM api_usage WHERE user_id = ? AND created_at >= ?",
                (user_id, utc_start),
            )
        else:
            row = await db.execute_fetchall(
                "SELECT COALESCE(SUM(input_tokens),0) as input_tokens, COALESCE(SUM(output_tokens),0) as output_tokens, COUNT(*) as calls, COALESCE(SUM(tool_calls),0) as tool_calls FROM api_usage WHERE created_at >= ?",
                (utc_start,),
            )
        r = row[0]
        return {
            "input_tokens": r[0],
            "output_tokens": r[1],
            "calls": r[2],
            "tool_calls": r[3],
        }


async def get_usage_month(user_id: int = None) -> dict:
    """이번 달 사용량 조회"""
    now = datetime.now(KST)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    utc_start = (month_start - timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        if user_id:
            row = await db.execute_fetchall(
                "SELECT COALESCE(SUM(input_tokens),0) as input_tokens, COALESCE(SUM(output_tokens),0) as output_tokens, COUNT(*) as calls, COALESCE(SUM(tool_calls),0) as tool_calls FROM api_usage WHERE user_id = ? AND created_at >= ?",
                (user_id, utc_start),
            )
        else:
            row = await db.execute_fetchall(
                "SELECT COALESCE(SUM(input_tokens),0) as input_tokens, COALESCE(SUM(output_tokens),0) as output_tokens, COUNT(*) as calls, COALESCE(SUM(tool_calls),0) as tool_calls FROM api_usage WHERE created_at >= ?",
                (utc_start,),
            )
        r = row[0]
        return {
            "input_tokens": r[0],
            "output_tokens": r[1],
            "calls": r[2],
            "tool_calls": r[3],
        }


async def get_daily_breakdown(days: int = 7) -> list[dict]:
    """일별 사용량 조회"""
    now = datetime.now(KST)
    start = now - timedelta(days=days)
    utc_start = (start - timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            """SELECT DATE(created_at, '+9 hours') as day,
                      SUM(input_tokens) as input_tokens,
                      SUM(output_tokens) as output_tokens,
                      COUNT(*) as calls
               FROM api_usage
               WHERE created_at >= ?
               GROUP BY day
               ORDER BY day DESC""",
            (utc_start,),
        )
        return [{"date": r[0], "input_tokens": r[1], "output_tokens": r[2], "calls": r[3]} for r in rows]


def estimate_cost(input_tokens: int, output_tokens: int, model: str = "claude-haiku-4-5-20251001") -> float:
    """예상 비용 계산 (USD)"""
    prices = PRICING.get(model, PRICING["claude-haiku-4-5-20251001"])
    return (input_tokens / 1_000_000 * prices["input"]) + (output_tokens / 1_000_000 * prices["output"])
