"""리마인더 핸들러 - 설정, 조회, 삭제 (KST 기준)"""

from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.services import reminder_service
from bot.utils.formatting import format_datetime
from bot.utils.telegram_fmt import reply_html


@authorized_only
async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """일회성 리마인더 설정 (예: /remind 30m 회의 시작)"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "사용법: /remind <시간> <메시지>\n"
            "시간 형식: 30m(분), 2h(시간), 1d(일)\n"
            "예: /remind 30m 회의 시작\n\n"
            "자연어도 돼요: '내일 9시에 세무사 통화 알려줘'"
        )
        return

    time_str = context.args[0]
    message = " ".join(context.args[1:])

    delta = reminder_service.parse_time_string(time_str)
    if not delta:
        await update.message.reply_text("❌ 시간 형식이 올바르지 않습니다. (예: 30m, 2h, 1d)")
        return

    remind_at = reminder_service.now_kst() + delta
    user_id = update.effective_user.id
    reminder_id = await reminder_service.create_reminder(user_id, message, remind_at)

    await update.message.reply_text(
        f"⏰ 리마인더 설정 완료! (ID: {reminder_id})\n"
        f"시간: {format_datetime(remind_at)} KST\n"
        f"내용: {message}"
    )


@authorized_only
async def remind_daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """매일 반복 리마인더 (예: /remind_daily 09:00 출근 체크)"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "사용법: /remind_daily <HH:MM> <메시지>\n"
            "예: /remind_daily 09:00 출근 체크"
        )
        return

    time_str = context.args[0]
    message = " ".join(context.args[1:])

    try:
        hour, minute = map(int, time_str.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ 시간 형식이 올바르지 않습니다. (예: 09:00, 14:30)")
        return

    next_run = reminder_service.next_daily_run(hour, minute)
    cron_expr = f"{minute} {hour} * * *"
    user_id = update.effective_user.id
    reminder_id = await reminder_service.create_recurring_reminder(user_id, message, cron_expr, next_run)

    await update.message.reply_text(
        f"🔄 매일 반복 리마인더 설정 완료! (ID: {reminder_id})\n"
        f"시간: 매일 {hour:02d}:{minute:02d} KST\n"
        f"내용: {message}"
    )


@authorized_only
async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """활성 리마인더 목록"""
    user_id = update.effective_user.id
    reminders = await reminder_service.get_active_reminders(user_id)

    if not reminders:
        await update.message.reply_text("⏰ 활성 리마인더가 없습니다.")
        return

    lines = ["⏰ **리마인더 목록**", ""]
    for r in reminders:
        if r["is_recurring"]:
            minute, hour = r["cron_expr"].split()[:2]
            time_str = f"매일 {int(hour):02d}:{int(minute):02d}"
            icon = "🔄"
        else:
            time_str = r["remind_at"][:16].replace("T", " ")
            icon = "⏳"
        lines.append(f"{icon} **#{r['id']}** | {time_str}\n{r['message']}\n")

    await reply_html(update, "\n".join(lines))


@authorized_only
async def remind_del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """리마인더 삭제"""
    if not context.args:
        await update.message.reply_text("사용법: /remind_del <리마인더ID>")
        return

    try:
        reminder_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ 올바른 리마인더 ID를 입력하세요.")
        return

    user_id = update.effective_user.id
    deleted = await reminder_service.delete_reminder(user_id, reminder_id)
    if deleted:
        await update.message.reply_text(f"✅ 리마인더 #{reminder_id} 삭제 완료")
    else:
        await update.message.reply_text(f"❌ 리마인더 #{reminder_id}를 찾을 수 없습니다.")
