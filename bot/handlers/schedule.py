"""스케줄러 핸들러 - 리마인더 체크 및 알림 발송"""

import logging
from datetime import datetime, timedelta
from telegram.ext import ContextTypes
from bot.services import reminder_service

logger = logging.getLogger(__name__)


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
                    # 반복 리마인더: 다음 실행 시간 계산 (간단한 daily cron만 지원)
                    parts = reminder["cron_expr"].split()
                    if len(parts) >= 2:
                        minute, hour = int(parts[0]), int(parts[1])
                        now = datetime.now()
                        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                        if next_run <= now:
                            next_run += timedelta(days=1)
                        await reminder_service.update_next_run(reminder["id"], next_run)
                else:
                    # 일회성 리마인더: 비활성화
                    await reminder_service.deactivate_reminder(reminder["id"])

            except Exception as e:
                logger.error(f"리마인더 #{reminder['id']} 발송 실패: {e}")

    except Exception as e:
        logger.error(f"리마인더 체크 실패: {e}")
