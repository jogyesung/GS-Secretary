"""스케줄러 핸들러 - 리마인더 체크 및 알림 발송 + 아침 브리핑"""

import logging
from datetime import datetime, timezone, timedelta
from telegram.ext import ContextTypes
from bot.services import reminder_service
from bot.config import ALLOWED_USER_IDS

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


async def daily_briefing(context: ContextTypes.DEFAULT_TYPE):
    """매일 아침 오늘 일정 브리핑"""
    try:
        from bot.services.calendar_service import list_events

        events = await list_events(days=1, max_results=20)

        now = datetime.now(KST)
        weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
        today_str = f"{now.strftime('%Y년 %m월 %d일')} {weekdays[now.weekday()]}"

        if events:
            lines = [f"☀️ *굿모닝! 오늘 일정 브리핑*\n📅 {today_str}\n"]
            for e in events:
                time_str = e["start"]
                if "T" in time_str:
                    # 시간 추출 (HH:MM)
                    time_str = time_str[11:16]
                else:
                    time_str = "종일"
                loc = f" 📍{e['location']}" if e.get("location") else ""
                cal = f" ({e.get('calendar', '')})" if e.get("calendar") else ""
                lines.append(f"• {time_str} | *{e['summary']}*{loc}{cal}")
            lines.append(f"\n총 {len(events)}개의 일정이 있습니다. 오늘도 화이팅! 💪")
            text = "\n".join(lines)
        else:
            text = f"☀️ *굿모닝!*\n📅 {today_str}\n\n오늘은 일정이 없어요. 여유로운 하루 보내세요! 😊"

        for user_id in ALLOWED_USER_IDS:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="Markdown",
                )
                logger.info(f"아침 브리핑 발송 완료: {user_id}")
            except Exception as e:
                logger.error(f"아침 브리핑 발송 실패 ({user_id}): {e}")

    except Exception as e:
        logger.error(f"아침 브리핑 실패: {e}")


async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    """만료된 리마인더를 체크하고 알림을 발송하는 Job (주기적 실행)"""
    try:
        due_reminders = await reminder_service.get_due_reminders()

        for reminder in due_reminders:
            try:
                await context.bot.send_message(
                    chat_id=reminder["user_id"],
                    text=f"⏰ *리마인더 알림*\n\n{reminder['message']}",
                    parse_mode="Markdown",
                )

                if reminder["is_recurring"] and reminder["cron_expr"]:
                    parts = reminder["cron_expr"].split()
                    if len(parts) >= 2:
                        minute, hour = int(parts[0]), int(parts[1])
                        now = datetime.now()
                        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                        if next_run <= now:
                            next_run += timedelta(days=1)
                        await reminder_service.update_next_run(reminder["id"], next_run)
                else:
                    await reminder_service.deactivate_reminder(reminder["id"])

            except Exception as e:
                logger.error(f"리마인더 #{reminder['id']} 발송 실패: {e}")

    except Exception as e:
        logger.error(f"리마인더 체크 실패: {e}")
