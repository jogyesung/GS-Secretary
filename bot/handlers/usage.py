"""API 사용량 조회 핸들러 - /usage 명령"""

from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.services.usage_service import get_usage_today, get_usage_month, get_daily_breakdown
from bot.utils.telegram_fmt import reply_html


def _model_label(model: str) -> str:
    if "haiku" in model:
        return "Haiku"
    if "sonnet" in model:
        return "Sonnet"
    if "opus" in model:
        return "Opus"
    return model


def _section(title: str, s: dict) -> list[str]:
    lines = [
        f"**{title}** — ${s['cost']:.3f}",
        f"  호출 {s['calls']}회 (도구 {s['tool_calls']}회)",
        f"  입력 {s['input_tokens']:,} / 출력 {s['output_tokens']:,} 토큰",
    ]
    if s["cache_read_tokens"] or s["cache_creation_tokens"]:
        lines.append(f"  캐시 히트율 {s['cache_hit_rate'] * 100:.0f}% (읽기 {s['cache_read_tokens']:,})")
    for model, m in sorted(s["by_model"].items(), key=lambda kv: -kv[1]["cost"]):
        lines.append(f"  · {_model_label(model)}: {m['calls']}회, ${m['cost']:.3f}")
    return lines


@authorized_only
async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """API 사용량 및 비용 조회 (모델별 단가, 캐시 반영)"""
    user_id = update.effective_user.id

    today = await get_usage_today(user_id)
    month = await get_usage_month(user_id)
    breakdown = await get_daily_breakdown(7)

    lines = ["📊 **Claude API 사용량**", ""]
    lines += _section("오늘", today)
    lines.append("")
    lines += _section("이번 달", month)

    if breakdown:
        lines.append("")
        lines.append("**최근 7일**")
        for day in breakdown:
            lines.append(f"  {day['date']}: {day['calls']}회, ${day['cost']:.3f}")

    await reply_html(update, "\n".join(lines))
