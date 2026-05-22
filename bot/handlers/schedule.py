"""스케줄러 핸들러 - 리마인더 체크 및 알림 발송 + 아침 브리핑"""

import logging
import re
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import ContextTypes
from bot.services import reminder_service
from bot.config import ALLOWED_USER_IDS
from bot.middleware.auth_middleware import authorized_only

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


def _extract_sender_name(from_str: str) -> str:
    """이메일 From 헤더에서 발신자 이름만 추출 (없으면 이메일 주소 사용)"""
    if not from_str:
        return "알 수 없음"
    # "이름 <email>" 형태에서 이름 추출
    match = re.match(r'^"?([^"<]+)"?\s*<', from_str)
    if match:
        return match.group(1).strip()
    # 이메일만 있는 경우
    match = re.match(r'<(.+)>', from_str)
    if match:
        return match.group(1)
    return from_str.strip()


async def _build_briefing_text(user_id: int = None) -> str:
    """브리핑 메시지 텍스트 생성 (캘린더 + 메일 + 리마인더)"""
    now = datetime.now(KST)
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    today_str = f"{now.strftime('%Y년 %m월 %d일')} {weekdays[now.weekday()]}"

    lines = [
        "☀️ 좋은 아침이에요! 오늘의 브리핑이에요\n",
        f"📅 {today_str}\n",
    ]

    # --- 1) 캘린더 일정 ---
    try:
        from bot.services.calendar_service import list_events
        events = await list_events(days=1, max_results=20)
    except Exception as e:
        logger.warning(f"캘린더 조회 실패: {e}")
        events = None

    if events is None:
        lines.append("📆 *오늘 일정*\n⚠️ 캘린더 연동에 문제가 있어요\n")
    elif events:
        lines.append(f"📆 *오늘 일정* ({len(events)}건)")
        for e in events:
            time_str = e["start"]
            if "T" in time_str:
                time_str = time_str[11:16]
            else:
                time_str = "종일"
            loc = f" 📍{e['location']}" if e.get("location") else ""
            lines.append(f"• {time_str} | {e['summary']}{loc}")
        lines.append("")
    else:
        lines.append("📆 *오늘 일정*\n오늘은 일정이 없어요 🎉\n")

    # --- 2) 읽지 않은 메일 ---
    try:
        from bot.services.gmail_service import list_emails
        emails = await list_emails(max_results=5, query="is:unread")
    except Exception as e:
        logger.warning(f"Gmail 조회 실패: {e}")
        emails = None

    if emails is None:
        lines.append("📬 *읽지 않은 메일*\n⚠️ 메일 연동에 문제가 있어요\n")
    elif emails:
        lines.append(f"📬 *읽지 않은 메일* ({len(emails)}건)")
        for em in emails[:3]:
            sender = _extract_sender_name(em.get("from", ""))
            subject = em.get("subject", "(제목 없음)")
            lines.append(f"• [{sender}] {subject}")
        if len(emails) > 3:
            lines.append(f"  ...외 {len(emails) - 3}건 더 있어요")
        lines.append("")
    else:
        lines.append("📬 *읽지 않은 메일*\n새 메일이 없어요 ✨\n")

    # --- 3) 오늘 리마인더 ---
    reminder_items = []
    target_user_ids = [user_id] if user_id else ALLOWED_USER_IDS
    for uid in target_user_ids:
        try:
            from bot.services.reminder_service import get_active_reminders
            reminders = await get_active_reminders(uid)
            for r in reminders:
                remind_at_str = r.get("remind_at", "")
                try:
                    remind_dt = datetime.fromisoformat(remind_at_str)
                    # 오늘 날짜에 해당하는 리마인더만
                    if remind_dt.date() == now.date():
                        time_part = remind_dt.strftime("%H:%M")
                        reminder_items.append(f"• {time_part} | {r['message']}")
                except (ValueError, TypeError):
                    # 날짜 파싱 실패 시 그냥 포함
                    reminder_items.append(f"• {r['message']}")
        except Exception as e:
            logger.warning(f"리마인더 조회 실패 (user {uid}): {e}")

    if reminder_items:
        lines.append(f"⏰ *오늘 리마인더* ({len(reminder_items)}건)")
        lines.extend(reminder_items)
        lines.append("")
    else:
        lines.append("⏰ *오늘 리마인더*\n설정된 리마인더가 없어요\n")

    lines.append("오늘도 화이팅이에요! 💪✨")

    return "\n".join(lines)


async def daily_briefing(context: ContextTypes.DEFAULT_TYPE):
    """매일 아침 오늘 일정 브리핑 (자동 스케줄)"""
    try:
        text = await _build_briefing_text()

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


@authorized_only
async def briefing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """수동 브리핑 요청 (/briefing)"""
    user_id = update.effective_user.id
    try:
        text = await _build_briefing_text(user_id=user_id)
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"수동 브리핑 실패 ({user_id}): {e}")
        await update.message.reply_text("😥 브리핑 생성 중 오류가 발생했어요. 잠시 후 다시 시도해주세요.")


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
