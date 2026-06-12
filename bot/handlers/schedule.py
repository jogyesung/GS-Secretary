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


async def weekly_briefing(context: ContextTypes.DEFAULT_TYPE):
    """매주 월요일 아침 이번 주 일정 브리핑"""
    try:
        from bot.services.calendar_service import list_events
        events = await list_events(days=7, max_results=50)
    except Exception as e:
        logger.warning(f"주간 브리핑 캘린더 조회 실패: {e}")
        return

    now = datetime.now(KST)
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    lines = [f"🗓 *이번 주 일정 브리핑* ({now.strftime('%m/%d')} 주)\n"]

    if not events:
        lines.append("이번 주는 등록된 일정이 없어요. 여유로운 한 주 되세요! 🎉")
    else:
        current_date = ""
        for e in events:
            start = e["start"]
            date_part = start[:10]
            time_part = start[11:16] if "T" in start else "종일"
            if date_part != current_date:
                current_date = date_part
                try:
                    d = datetime.fromisoformat(date_part)
                    lines.append(f"\n*{d.strftime('%m/%d')} ({weekdays[d.weekday()]})*")
                except ValueError:
                    lines.append(f"\n*{date_part}*")
            loc = f" 📍{e['location']}" if e.get("location") else ""
            lines.append(f"• {time_part} | {e['summary']}{loc}")
        lines.append("\n이번 주도 화이팅이에요! 💪")

    text = "\n".join(lines)
    for user_id in ALLOWED_USER_IDS:
        try:
            await context.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"주간 브리핑 발송 실패 ({user_id}): {e}")


# 비용 한도 알림: 하루 1회만 발송
_cost_alert_sent_date: str = ""


async def check_cost_limit(context: ContextTypes.DEFAULT_TYPE):
    """일일 API 비용이 한도를 넘으면 알림 (1시간 주기 체크)"""
    global _cost_alert_sent_date
    from bot.config import DAILY_COST_LIMIT_USD

    today = datetime.now(KST).strftime("%Y-%m-%d")
    if _cost_alert_sent_date == today:
        return

    try:
        from bot.services.usage_service import get_cost_today
        cost = await get_cost_today()
    except Exception as e:
        logger.warning(f"비용 조회 실패: {e}")
        return

    if cost >= DAILY_COST_LIMIT_USD:
        _cost_alert_sent_date = today
        text = (
            f"💸 *API 비용 알림*\n\n"
            f"오늘 사용액이 ${cost:.2f}로 한도(${DAILY_COST_LIMIT_USD:.2f})를 넘었어요!\n"
            f"/usage 로 자세한 내역을 확인해보세요."
        )
        for user_id in ALLOWED_USER_IDS:
            try:
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"비용 알림 발송 실패 ({user_id}): {e}")


async def check_google_token(context: ContextTypes.DEFAULT_TYPE):
    """Google OAuth 토큰 상태 점검 (매일 09:00) - 만료/갱신 실패 시 알림"""
    import asyncio
    from bot.utils.drive_auth import get_drive_credentials

    loop = asyncio.get_event_loop()
    try:
        creds = await loop.run_in_executor(None, get_drive_credentials)
    except Exception as e:
        logger.warning(f"토큰 점검 실패: {e}")
        creds = None

    if creds is not None and creds.valid:
        return  # 정상

    text = (
        "🔑 *Google 토큰 알림*\n\n"
        "Google 인증 토큰이 만료되었거나 갱신에 실패했어요.\n"
        "Drive/Calendar/Gmail 기능이 곧 중단될 수 있으니\n"
        "PC에서 auth\\_local.py를 실행해 재인증해주세요!"
    )
    for user_id in ALLOWED_USER_IDS:
        try:
            await context.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"토큰 알림 발송 실패 ({user_id}): {e}")


async def check_dart_disclosures(context: ContextTypes.DEFAULT_TYPE):
    """포트폴리오 종목의 새 공시 체크 (30분 주기)"""
    from bot.services import dart_service

    try:
        portfolio = await dart_service.get_all_portfolio()
    except Exception as e:
        logger.warning(f"포트폴리오 조회 실패: {e}")
        return

    if not portfolio:
        return

    # corp_code별로 보유 사용자 묶기 (중복 API 호출 방지)
    holders: dict[str, list[int]] = {}
    for p in portfolio:
        holders.setdefault(p["corp_code"], []).append(p["user_id"])

    for corp_code, user_ids in holders.items():
        try:
            disclosures = await dart_service.get_recent_disclosures(corp_code, days=2)
        except Exception as e:
            logger.warning(f"공시 조회 실패 ({corp_code}): {e}")
            continue

        for d in disclosures:
            if not d["rcept_no"] or await dart_service.is_seen(d["rcept_no"]):
                continue
            await dart_service.mark_seen(d["rcept_no"])

            text = (
                f"📢 *새 공시 알림*\n\n"
                f"🏢 {d['corp_name']}\n"
                f"📄 {d['report_nm']}\n"
                f"🗓 {d['rcept_dt']} | 제출: {d['flr_nm']}\n\n"
                f"[공시 원문 보기]({d['url']})"
            )
            for user_id in set(user_ids):
                try:
                    await context.bot.send_message(
                        chat_id=user_id, text=text,
                        parse_mode="Markdown", disable_web_page_preview=True,
                    )
                    logger.info(f"공시 알림 발송: {d['corp_name']} - {d['report_nm']}")
                except Exception as e:
                    logger.error(f"공시 알림 발송 실패 ({user_id}): {e}")


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
