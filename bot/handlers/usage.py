"""API 사용량 조회 핸들러 - /usage 명령"""

from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.services.usage_service import (
    get_usage_today,
    get_usage_month,
    get_daily_breakdown,
    estimate_cost,
)
from bot.config import CLAUDE_MODEL


@authorized_only
async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """API 사용량 및 비용 조회"""
    user_id = update.effective_user.id

    today = await get_usage_today(user_id)
    month = await get_usage_month(user_id)
    breakdown = await get_daily_breakdown(7)

    today_cost = estimate_cost(today["input_tokens"], today["output_tokens"], CLAUDE_MODEL)
    month_cost = estimate_cost(month["input_tokens"], month["output_tokens"], CLAUDE_MODEL)

    lines = [
        "📊 *Claude API 사용량*\n",
        f"*오늘*",
        f"  호출: {today['calls']}회 (도구: {today['tool_calls']}회)",
        f"  입력: {today['input_tokens']:,} 토큰",
        f"  출력: {today['output_tokens']:,} 토큰",
        f"  비용: ${today_cost:.4f}\n",
        f"*이번 달*",
        f"  호출: {month['calls']}회 (도구: {month['tool_calls']}회)",
        f"  입력: {month['input_tokens']:,} 토큰",
        f"  출력: {month['output_tokens']:,} 토큰",
        f"  비용: ${month_cost:.4f}\n",
        f"*모델*: `{CLAUDE_MODEL}`",
    ]

    if breakdown:
        lines.append("\n*최근 7일*")
        for day in breakdown:
            day_cost = estimate_cost(day["input_tokens"], day["output_tokens"], CLAUDE_MODEL)
            lines.append(f"  {day['date']}: {day['calls']}회, ${day_cost:.4f}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
