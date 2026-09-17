"""스케줄러 핸들러 - 리마인더 체크 및 알림 발송 + 아침 브리핑 + 공시/주가/미팅 알림 + DB 백업"""

import asyncio
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.services import reminder_service
from bot.config import (
    ALLOWED_USER_IDS,
    ANTHROPIC_API_KEY,
    DATABASE_PATH,
    DB_BACKUP_DRIVE_FOLDER,
    MEETING_PREP_MINUTES,
    PRICE_ALERT_THRESHOLD_PCT,
    QUIET_HOURS_END,
    QUIET_HOURS_START,
)
from bot.middleware.auth_middleware import authorized_only
from bot.services.mail_rank_service import extract_sender_name as _extract_sender_name
from bot.utils.jobs import job_guard
from bot.utils.telegram_fmt import reply_html, send_html

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


def _in_quiet_hours(now: datetime | None = None) -> bool:
    """조용시간(KST) 여부. 22~7처럼 자정을 넘기는 구간도 처리."""
    hour = (now or datetime.now(KST)).hour
    if QUIET_HOURS_START == QUIET_HOURS_END:
        return False
    if QUIET_HOURS_START > QUIET_HOURS_END:
        return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END
    return QUIET_HOURS_START <= hour < QUIET_HOURS_END


async def _broadcast(bot, text: str, reply_markup=None, user_ids=None, label: str = "알림"):
    """허용 사용자 전원에게 발송. 개별 실패는 로그만 남기고 계속."""
    for user_id in (user_ids if user_ids is not None else ALLOWED_USER_IDS):
        try:
            await send_html(bot, user_id, text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"{label} 발송 실패 ({user_id}): {e}")


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
        lines.append("📆 **오늘 일정**\n⚠️ 캘린더 연동에 문제가 있어요\n")
    elif events:
        lines.append(f"📆 **오늘 일정** ({len(events)}건)")
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
        lines.append("📆 **오늘 일정**\n오늘은 일정이 없어요 🎉\n")

    # --- 2) 읽지 않은 메일 (최근 하루, 긴급도순 상위 5건) ---
    try:
        from bot.services.gmail_service import list_emails
        emails = await list_emails(max_results=20, query="is:unread newer_than:1d")
    except Exception as e:
        logger.warning(f"Gmail 조회 실패: {e}")
        emails = None

    if emails is None:
        lines.append("📬 **읽지 않은 메일**\n⚠️ 메일 연동에 문제가 있어요\n")
    elif emails:
        from bot.services.mail_rank_service import rank_unread_emails
        usage_user = user_id or (ALLOWED_USER_IDS[0] if ALLOWED_USER_IDS else 0)
        ranked = await rank_unread_emails(emails, user_id=usage_user)
        title = "📬 **읽지 않은 메일**"
        if len(emails) > 3 and ANTHROPIC_API_KEY:
            title += f" (24시간 내 {len(emails)}건, 먼저 볼 것)"
        else:
            title += f" ({len(emails)}건)"
        lines.append(title)
        lines.extend(ranked)
        if len(emails) > len(ranked):
            lines.append(f"  ...외 {len(emails) - len(ranked)}건 더 있어요")
        lines.append("")
    else:
        lines.append("📬 **읽지 않은 메일**\n새 메일이 없어요 ✨\n")

    # --- 3) 오늘 리마인더 (KST 기준) ---
    reminder_items = []
    today_kst = reminder_service.now_kst().date()
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
                    if remind_dt.date() == today_kst:
                        time_part = remind_dt.strftime("%H:%M")
                        reminder_items.append(f"• {time_part} | {r['message']}")
                except (ValueError, TypeError):
                    # 날짜 파싱 실패 시 그냥 포함
                    reminder_items.append(f"• {r['message']}")
        except Exception as e:
            logger.warning(f"리마인더 조회 실패 (user {uid}): {e}")

    if reminder_items:
        lines.append(f"⏰ **오늘 리마인더** ({len(reminder_items)}건)")
        lines.extend(reminder_items)
        lines.append("")
    else:
        lines.append("⏰ **오늘 리마인더**\n설정된 리마인더가 없어요\n")

    lines.append("오늘도 화이팅이에요! 💪✨")

    return "\n".join(lines)


@job_guard("daily_briefing")
async def daily_briefing(context: ContextTypes.DEFAULT_TYPE):
    """매일 아침 오늘 일정 브리핑 (자동 스케줄)"""
    text = await _build_briefing_text()

    # 증시 이벤트 (실적발표/경제지표/중앙은행) - 검색 실패 시 생략
    market_text = None
    try:
        from bot.services.market_service import get_market_events_briefing
        market_text = await get_market_events_briefing()
    except Exception as e:
        logger.warning(f"증시 이벤트 브리핑 실패: {e}")

    for user_id in ALLOWED_USER_IDS:
        try:
            await send_html(context.bot, user_id, text)
            if market_text:
                await send_html(context.bot, user_id, market_text)
            logger.info(f"아침 브리핑 발송 완료: {user_id}")
        except Exception as e:
            logger.error(f"아침 브리핑 발송 실패 ({user_id}): {e}")


@authorized_only
async def briefing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """수동 브리핑 요청 (/briefing)"""
    user_id = update.effective_user.id
    try:
        text = await _build_briefing_text(user_id=user_id)
        await reply_html(update, text)
    except Exception as e:
        logger.error(f"수동 브리핑 실패 ({user_id}): {e}")
        await update.message.reply_text("😥 브리핑 생성 중 오류가 발생했어요. 잠시 후 다시 시도해주세요.")
        return

    # 증시 이벤트도 이어서 발송 (검색에 몇 초 걸릴 수 있음)
    from bot.config import GEMINI_API_KEY
    if not GEMINI_API_KEY:
        await update.message.reply_text(
            "📈 증시 이벤트 브리핑은 GEMINI_API_KEY 설정 후 이용할 수 있어요."
        )
        return

    try:
        from bot.services.market_service import get_market_events_briefing
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        market_text = await get_market_events_briefing()
        if market_text:
            await send_html(context.bot, update.effective_chat.id, market_text)
        else:
            await update.message.reply_text(
                "📈 증시 이벤트를 가져오지 못했어요 😥 검색이 일시적으로 실패한 것 같아요. 잠시 후 다시 시도해주세요."
            )
    except Exception as e:
        logger.warning(f"증시 이벤트 브리핑 실패 ({user_id}): {e}")
        await update.message.reply_text(
            "📈 증시 이벤트를 가져오는 중 오류가 발생했어요 😥 잠시 후 다시 시도해주세요."
        )


async def _weekly_cost_section() -> list[str]:
    """지난주 API 비용 요약 줄들. 조회 실패 시 빈 리스트."""
    try:
        from bot.services.usage_service import get_usage_week
        usage = await get_usage_week()
    except Exception as e:
        logger.warning(f"주간 비용 조회 실패: {e}")
        return []

    if not usage or not usage.get("calls"):
        return []

    lines = [
        f"\n**💸 지난주 API 비용** ${usage.get('cost', 0.0):.2f} "
        f"({usage.get('calls', 0)}회, 캐시 히트 {usage.get('cache_hit_rate', 0.0):.0%})"
    ]
    by_model = usage.get("by_model") or {}
    for model, m in sorted(by_model.items(), key=lambda kv: -kv[1].get("cost", 0.0)):
        lines.append(f"• {model}: ${m.get('cost', 0.0):.2f} ({m.get('calls', 0)}회)")
    return lines


@job_guard("weekly_briefing")
async def weekly_briefing(context: ContextTypes.DEFAULT_TYPE):
    """매주 월요일 아침 이번 주 일정 브리핑 + 지난주 API 비용"""
    try:
        from bot.services.calendar_service import list_events
        events = await list_events(days=7, max_results=50)
    except Exception as e:
        logger.warning(f"주간 브리핑 캘린더 조회 실패: {e}")
        events = None

    now = datetime.now(KST)
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    lines = [f"🗓 **이번 주 일정 브리핑** ({now.strftime('%m/%d')} 주)\n"]

    if events is None:
        lines.append("⚠️ 캘린더 연동에 문제가 있어요")
    elif not events:
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
                    lines.append(f"\n**{d.strftime('%m/%d')} ({weekdays[d.weekday()]})**")
                except ValueError:
                    lines.append(f"\n**{date_part}**")
            loc = f" 📍{e['location']}" if e.get("location") else ""
            lines.append(f"• {time_part} | {e['summary']}{loc}")

    lines.extend(await _weekly_cost_section())
    lines.append("\n이번 주도 화이팅이에요! 💪")

    await _broadcast(context.bot, "\n".join(lines), label="주간 브리핑")


# 비용 한도 알림: 하루 1회만 발송
_cost_alert_sent_date: str = ""


@job_guard("check_cost_limit")
async def check_cost_limit(context: ContextTypes.DEFAULT_TYPE):
    """일일 API 비용이 한도를 넘으면 알림 (1시간 주기 체크, 조용시간에는 다음 시간으로 미룸)"""
    global _cost_alert_sent_date
    from bot.config import DAILY_COST_LIMIT_USD

    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    if _cost_alert_sent_date == today or _in_quiet_hours(now):
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
            f"💸 **API 비용 알림**\n\n"
            f"오늘 사용액이 ${cost:.2f}로 한도(${DAILY_COST_LIMIT_USD:.2f})를 넘었어요!\n"
            f"/usage 로 자세한 내역을 확인해보세요."
        )
        await _broadcast(context.bot, text, label="비용 알림")


@job_guard("check_google_token")
async def check_google_token(context: ContextTypes.DEFAULT_TYPE):
    """Google OAuth 토큰 상태 점검 (매일 09:00) - 만료/갱신 실패 시 알림"""
    from bot.utils.drive_auth import get_drive_credentials

    try:
        creds = await asyncio.to_thread(get_drive_credentials)
    except Exception as e:
        logger.warning(f"토큰 점검 실패: {e}")
        creds = None

    if creds is not None and creds.valid:
        return  # 정상

    text = (
        "🔑 **Google 토큰 알림**\n\n"
        "Google 인증 토큰이 만료되었거나 갱신에 실패했어요.\n"
        "Drive/Calendar/Gmail 기능이 곧 중단될 수 있으니\n"
        "PC에서 `auth_local.py`를 실행해 재인증해주세요!"
    )
    await _broadcast(context.bot, text, label="토큰 알림")


# === DART 공시 ===
def _dart_alert_markup(corp_code: str, disclosure: dict) -> InlineKeyboardMarkup:
    from bot.services import dart_service

    rows = [[InlineKeyboardButton("공시 원문", url=disclosure["url"])]]
    keyword = dart_service.mute_keyword(disclosure.get("report_nm", ""), corp_code)
    if keyword:
        rows.append([InlineKeyboardButton(
            "🔕 이 유형 무음", callback_data=f"dart:mute:{corp_code}:{keyword}"
        )])
    return InlineKeyboardMarkup(rows)


def _fmt_rcept_dt(rcept_dt: str) -> str:
    """YYYYMMDD → M/D"""
    if rcept_dt and len(rcept_dt) >= 8 and rcept_dt[:8].isdigit():
        return f"{int(rcept_dt[4:6])}/{int(rcept_dt[6:8])}"
    return rcept_dt or ""


@job_guard("check_dart_disclosures")
async def check_dart_disclosures(context: ContextTypes.DEFAULT_TYPE):
    """포트폴리오 종목의 새 공시 체크 (30분 주기). 긴급은 즉시, 나머지는 다이제스트로 보류."""
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

            tier = dart_service.classify_report(d["report_nm"])
            if tier == dart_service.TIER_MUTED:
                logger.info(f"공시 무음 유형 건너뜀: {d['corp_name']} - {d['report_nm']}")
                continue

            recipients = []
            for user_id in sorted(set(user_ids)):
                try:
                    if not await dart_service.is_muted(user_id, corp_code, d["report_nm"]):
                        recipients.append(user_id)
                except Exception as e:
                    logger.warning(f"무음 설정 조회 실패 ({user_id}): {e}")
                    recipients.append(user_id)
            if not recipients:
                continue

            if tier != dart_service.TIER_URGENT:
                await dart_service.add_pending(d["rcept_no"], recipients, d, tier)
                logger.info(f"공시 다이제스트 보류: {d['corp_name']} - {d['report_nm']}")
                continue

            text = (
                f"**📢 새 공시**\n"
                f"🏢 {d['corp_name']}\n"
                f"📄 {d['report_nm']}\n"
                f"🗓 {d['rcept_dt']} | 제출: {d['flr_nm']}"
            )
            await _broadcast(
                context.bot, text, reply_markup=_dart_alert_markup(corp_code, d),
                user_ids=recipients, label="공시 알림",
            )
            logger.info(f"공시 알림 발송: {d['corp_name']} - {d['report_nm']}")


@job_guard("send_dart_digest")
async def send_dart_digest(context: ContextTypes.DEFAULT_TYPE):
    """보류된 낮은 등급 공시를 사용자별로 묶어 하루 한 번 발송"""
    from bot.services import dart_service

    pending = await dart_service.get_all_pending()
    if not pending:
        return

    per_user: dict[int, list[dict]] = {}
    for row in pending:
        for uid in row["user_ids"]:
            per_user.setdefault(uid, []).append(row)

    _now = datetime.now(KST)
    today = f"{_now.month}/{_now.day}"
    delivered: set[str] = set()

    for uid, rows in per_user.items():
        lines = [f"**📋 공시 다이제스트 ({today})**\n"]
        current_corp = None
        for r in rows:
            if r["corp_name"] != current_corp:
                current_corp = r["corp_name"]
                lines.append(f"**{current_corp}**")
            lines.append(f"• {r['report_nm']} ({_fmt_rcept_dt(r['rcept_dt'])}) — [원문]({r['url']})")
        try:
            await send_html(context.bot, uid, "\n".join(lines))
            delivered.update(r["rcept_no"] for r in rows)
            logger.info(f"공시 다이제스트 발송: {uid} ({len(rows)}건)")
        except Exception as e:
            logger.error(f"공시 다이제스트 발송 실패 ({uid}): {e}")

    await dart_service.delete_pending(sorted(delivered))


# === 포트폴리오 주가 변동 ===
@job_guard("check_price_alerts")
async def check_price_alerts(context: ContextTypes.DEFAULT_TYPE):
    """장중 15분 주기로 포트폴리오 종목의 등락률 체크, 임계값 이상이면 방향별 하루 1회 알림"""
    from bot.services import dart_service, price_service

    if PRICE_ALERT_THRESHOLD_PCT <= 0:
        return
    now = datetime.now(KST)
    if not price_service.is_market_hours(now) or _in_quiet_hours(now):
        return

    try:
        portfolio = await dart_service.get_all_portfolio()
    except Exception as e:
        logger.warning(f"포트폴리오 조회 실패: {e}")
        return
    if not portfolio:
        return

    holders: dict[str, dict] = {}
    for p in portfolio:
        entry = holders.setdefault(p["corp_code"], {"corp_name": p["corp_name"], "user_ids": set()})
        entry["user_ids"].add(p["user_id"])

    today = now.strftime("%Y-%m-%d")
    for corp_code, info in holders.items():
        try:
            stock_code = await dart_service.get_stock_code(corp_code)
            if not stock_code:
                continue
            quote = await price_service.fetch_quote(stock_code)
            if not quote:
                continue
            pct = quote["pct"]
            if abs(pct) < PRICE_ALERT_THRESHOLD_PCT:
                continue
            direction = "up" if pct > 0 else "down"
            if await price_service.has_alerted(corp_code, today, direction):
                continue

            emoji = "📈" if direction == "up" else "📉"
            text = f"**{emoji} {info['corp_name']} {pct:+.1f}%** (현재가 {int(quote['price']):,}원)"
            await _broadcast(context.bot, text, user_ids=sorted(info["user_ids"]), label="주가 알림")
            await price_service.record_alert(corp_code, today, direction)
            logger.info(f"주가 알림 발송: {info['corp_name']} {pct:+.1f}%")
        except Exception as e:
            logger.warning(f"주가 체크 실패 ({info['corp_name']}): {e}")
            continue


# === 미팅 준비 브리핑 ===
@job_guard("meeting_prep_check")
async def meeting_prep_check(context: ContextTypes.DEFAULT_TYPE):
    """5분 주기: 곧 시작하는 미팅의 관련 메일·문서·메모를 모아 준비 브리핑 발송"""
    from bot.services import meeting_prep_service

    if MEETING_PREP_MINUTES <= 0 or not ALLOWED_USER_IDS:
        return

    now = datetime.now(KST)
    try:
        from bot.services.calendar_service import list_events
        events = await list_events(days=2, max_results=30)
    except Exception as e:
        logger.warning(f"미팅 준비 캘린더 조회 실패: {e}")
        return

    for ev in meeting_prep_service.upcoming_events(events, now, MEETING_PREP_MINUTES):
        event_id = ev.get("id", "")
        if not event_id or await meeting_prep_service.is_sent(event_id):
            continue
        # 실패해도 5분마다 재시도하며 메일/LLM을 반복 호출하지 않도록 먼저 기록
        await meeting_prep_service.mark_sent(event_id)
        try:
            text, markup = await meeting_prep_service.build_prep_message(ev, ALLOWED_USER_IDS, now)
        except Exception as e:
            logger.error(f"미팅 준비 브리핑 생성 실패 ({ev.get('summary')}): {e}")
            continue
        await _broadcast(context.bot, text, reply_markup=markup, label="미팅 준비 브리핑")
        logger.info(f"미팅 준비 브리핑 발송: {ev.get('summary')}")


# === DB 백업 ===
_BACKUP_NAME_RE = re.compile(r"^gs_secretary_\d{4}-\d{2}-\d{2}\.db$")
MAX_BACKUPS = 14


def _sqlite_backup(src_path: str, dst_path: str):
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


@job_guard("backup_database")
async def backup_database(context: ContextTypes.DEFAULT_TYPE):
    """매일 03:00 SQLite 스냅샷을 Drive 백업 폴더에 올리고 최근 14개만 유지"""
    if not DB_BACKUP_DRIVE_FOLDER:
        return

    from bot.services.drive_service import delete_file, list_files, upload_file
    from bot.services.stock_research_service import _find_or_create_drive_folder

    name = f"gs_secretary_{datetime.now(KST).strftime('%Y-%m-%d')}.db"
    tmp_dir = tempfile.mkdtemp(prefix="gs_backup_")
    tmp_path = os.path.join(tmp_dir, name)
    try:
        await asyncio.to_thread(_sqlite_backup, DATABASE_PATH, tmp_path)

        folder_id = await _find_or_create_drive_folder(DB_BACKUP_DRIVE_FOLDER)
        existing = await list_files(folder_id, page_size=100)

        # 같은 날 재실행 시 이전 파일 교체
        for f in existing:
            if f.get("name") == name:
                await delete_file(f["id"])

        await upload_file(tmp_path, folder_id=folder_id, file_name=name)

        backups = sorted(
            (f for f in existing if _BACKUP_NAME_RE.match(f.get("name", "")) and f.get("name") != name),
            key=lambda f: f["name"],
            reverse=True,
        )
        for f in backups[MAX_BACKUPS - 1:]:
            try:
                await delete_file(f["id"])
            except Exception as e:
                logger.warning(f"오래된 백업 삭제 실패 ({f.get('name')}): {e}")

        logger.info(f"DB 백업 완료: {DB_BACKUP_DRIVE_FOLDER}/{name}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# === 리마인더 ===
@job_guard("check_reminders", notify=False)
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    """만료된 리마인더를 체크하고 알림을 발송하는 Job (30초 주기, KST)"""
    due_reminders = await reminder_service.get_due_reminders()

    for reminder in due_reminders:
        try:
            await send_html(
                context.bot, reminder["user_id"],
                f"**⏰ 리마인더 알림**\n\n{reminder['message']}",
            )
        except Exception as e:
            logger.error(f"리마인더 #{reminder['id']} 발송 실패: {e}")
        finally:
            # 발송 실패와 무관하게 다음 실행/비활성화 처리 — 안 그러면 30초마다 무한 재시도
            try:
                next_run = None
                if reminder["is_recurring"] and reminder["cron_expr"]:
                    parts = reminder["cron_expr"].split()
                    if len(parts) >= 2:
                        minute, hour = int(parts[0]), int(parts[1])
                        next_run = reminder_service.next_daily_run(hour, minute)
                if next_run is not None:
                    await reminder_service.update_next_run(reminder["id"], next_run)
                else:
                    await reminder_service.deactivate_reminder(reminder["id"])
            except Exception as e:
                logger.error(f"리마인더 #{reminder['id']} 상태 갱신 실패: {e}")
                try:
                    await reminder_service.deactivate_reminder(reminder["id"])
                except Exception:
                    pass
