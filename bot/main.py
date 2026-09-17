"""GS-Secretary 봇 진입점 - Application 설정 및 핸들러 등록"""

import logging
import traceback
from pathlib import Path
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from bot.config import (
    TELEGRAM_BOT_TOKEN, LOG_LEVEL, TEMP_DOWNLOAD_PATH, RUN_MODE, ANTHROPIC_API_KEY, GEMINI_API_KEY,
    DATABASE_PATH, IS_RAILWAY, RAILWAY_VOLUME_MOUNT_PATH, ALLOWED_USER_IDS,
)
from bot.database import init_db
from bot.handlers.start import start_command, help_command, status_command
from bot.handlers.chat import handle_chat_message, clear_command, action_callback
from bot.handlers.voice import handle_voice_message
from bot.middleware.auth_middleware import authorized_only
from bot.utils.jobs import notify_admin

# 로깅 설정
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
# 라이브러리 잡음 줄이기
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

# 일반 메시지 핸들러는 새 메시지에만 (편집된 메시지는 update.message가 None이라 제외)
_NEW_MSG = filters.UpdateType.MESSAGE


def _check_persistence():
    """Railway에서 볼륨 없이 돌면 재배포 때 DB가 사라짐 — 시작 시 경고"""
    if not IS_RAILWAY:
        return
    db_path = Path(DATABASE_PATH).resolve()
    if RAILWAY_VOLUME_MOUNT_PATH and str(db_path).startswith(str(Path(RAILWAY_VOLUME_MOUNT_PATH).resolve())):
        logger.info(f"DB 영구 저장소 확인: {db_path}")
        return
    logger.error(
        "⚠️ Railway 볼륨이 없거나 DATABASE_PATH가 볼륨 밖입니다. 재배포 시 메모/리마인더/포트폴리오/사용량이 "
        "모두 사라져요. Railway → Volume을 /data에 마운트하고 DATABASE_PATH=/data/gs_secretary.db, "
        "GOOGLE_TOKEN_FILE=/data/token.json 을 설정하세요."
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """전역 에러 핸들러: 로그 + 관리자 DM (조용히 죽는 일이 없도록)"""
    err = context.error
    logger.error("처리되지 않은 예외", exc_info=err)
    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))[-1200:]
    where = ""
    if isinstance(update, Update) and update.effective_message:
        where = f"\n메시지: {(update.effective_message.text or update.effective_message.caption or '')[:100]}"
    await notify_admin(context.bot, f"🚨 봇 오류: {type(err).__name__}: {str(err)[:300]}{where}\n\n{tb}")


async def post_init(application: Application):
    """봇 시작 후 초기화"""
    await init_db()
    TEMP_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
    logger.info("데이터베이스 초기화 완료")
    _check_persistence()

    from datetime import time as dt_time, timezone, timedelta
    kst = timezone(timedelta(hours=9))
    from bot.config import DART_API_KEY, DART_DIGEST_TIME, PRICE_ALERT_THRESHOLD_PCT, MEETING_PREP_MINUTES
    jq = application.job_queue

    if RUN_MODE in ("cloud", "all"):
        from bot.handlers.schedule import (
            check_reminders, daily_briefing, weekly_briefing,
            check_google_token, check_dart_disclosures, send_dart_digest,
            backup_database, meeting_prep_check, check_price_alerts,
        )
        jq.run_repeating(check_reminders, interval=30, first=5)
        logger.info("리마인더 스케줄러 시작됨")

        jq.run_daily(daily_briefing, time=dt_time(hour=7, minute=0, tzinfo=kst))
        logger.info("아침 브리핑 스케줄러 시작됨 (매일 07:00 KST)")

        # days: 0=일요일 ~ 6=토요일 (PTB v20+)
        jq.run_daily(weekly_briefing, time=dt_time(hour=7, minute=30, tzinfo=kst), days=(1,))
        logger.info("주간 브리핑 스케줄러 시작됨 (매주 월 07:30 KST)")

        jq.run_daily(check_google_token, time=dt_time(hour=9, minute=0, tzinfo=kst))
        logger.info("Google 토큰 점검 스케줄러 시작됨 (매일 09:00 KST)")

        # 야간 DB 백업 → Drive (볼륨이 있어도 배포 사고 대비)
        jq.run_daily(backup_database, time=dt_time(hour=3, minute=0, tzinfo=kst))
        logger.info("DB 백업 스케줄러 시작됨 (매일 03:00 KST)")

        if DART_API_KEY:
            jq.run_repeating(check_dart_disclosures, interval=1800, first=60)
            try:
                _h, _m = (int(x) for x in DART_DIGEST_TIME.split(":"))
            except ValueError:
                _h, _m = 8, 0
            jq.run_daily(send_dart_digest, time=dt_time(hour=_h, minute=_m, tzinfo=kst))
            logger.info(f"DART 공시 모니터링 시작됨 (30분 주기, 다이제스트 {_h:02d}:{_m:02d} KST)")
            if PRICE_ALERT_THRESHOLD_PCT > 0:
                jq.run_repeating(check_price_alerts, interval=900, first=120)
                logger.info(f"포트폴리오 가격 변동 알림 시작됨 (15분 주기, ±{PRICE_ALERT_THRESHOLD_PCT}%)")
        else:
            logger.info("DART_API_KEY 미설정 - 공시 모니터링 비활성화")

        if MEETING_PREP_MINUTES > 0 and ANTHROPIC_API_KEY:
            jq.run_repeating(meeting_prep_check, interval=300, first=90)
            logger.info(f"미팅 준비 브리핑 시작됨 (5분 주기, {MEETING_PREP_MINUTES}분 전)")

    # 아침 종목 리포트 (평일, KST)
    from bot.config import STOCK_REPORT_TIME, STOCK_REPORT_MODE
    if STOCK_REPORT_MODE != "off":
        from bot.handlers.stock_report import morning_stock_report
        try:
            _hour, _minute = (int(x) for x in STOCK_REPORT_TIME.split(":"))
            report_time = dt_time(hour=_hour, minute=_minute, tzinfo=kst)
        except ValueError:
            report_time = dt_time(hour=7, minute=20, tzinfo=kst)
        jq.run_daily(morning_stock_report, time=report_time, days=(1, 2, 3, 4, 5))
        logger.info(f"아침 종목 리포트 스케줄러 시작됨 (평일 {report_time.strftime('%H:%M')} KST, 모드: {STOCK_REPORT_MODE})")
    else:
        logger.info("아침 종목 리포트 자동 발송 비활성화 (STOCK_REPORT_MODE=off)")

    if ANTHROPIC_API_KEY:
        from bot.handlers.schedule import check_cost_limit
        jq.run_repeating(check_cost_limit, interval=3600, first=300)
        logger.info("API 비용 한도 알림 시작됨 (1시간 주기)")

    # 재시작 알림 — 크래시 루프를 가장 싸게 감지하는 방법
    await notify_admin(application.bot, "🔄 카리나 재시작됐어요.")


def _register_cloud_handlers(app: Application):
    """클라우드 모드 핸들러: Google Drive + 메모 + 리마인더"""
    from bot.handlers.drive import (
        drive_auth_command,
        drive_list_command,
        drive_search_command,
        drive_mkdir_command,
        drive_share_command,
        drive_upload_command,
        drive_callback,
    )
    from bot.handlers.memo import (
        memo_command,
        memos_command,
        memo_search_command,
        memo_del_command,
    )
    from bot.handlers.reminder import (
        remind_command,
        remind_daily_command,
        reminders_command,
        remind_del_command,
    )

    app.add_handler(CommandHandler("drive_auth", drive_auth_command))
    app.add_handler(CommandHandler("drive", drive_list_command))
    app.add_handler(CommandHandler("drive_search", drive_search_command))
    app.add_handler(CommandHandler("drive_mkdir", drive_mkdir_command))
    app.add_handler(CommandHandler("drive_share", drive_share_command))
    app.add_handler(CommandHandler("drive_upload", drive_upload_command))
    app.add_handler(CallbackQueryHandler(drive_callback, pattern=r"^drive_"))

    app.add_handler(CommandHandler("memo", memo_command))
    app.add_handler(CommandHandler("memos", memos_command))
    app.add_handler(CommandHandler("memo_search", memo_search_command))
    app.add_handler(CommandHandler("memo_del", memo_del_command))

    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("remind_daily", remind_daily_command))
    app.add_handler(CommandHandler("reminders", reminders_command))
    app.add_handler(CommandHandler("remind_del", remind_del_command))

    logger.info("클라우드 핸들러 등록: Drive, 메모, 리마인더")


def _register_local_handlers(app: Application):
    """로컬 모드 핸들러: PC 파일 관리"""
    from bot.handlers.files import (
        pc_command,
        pc_ls_command,
        pc_get_command,
        pc_save_command,
        pc_delete_command,
        pc_callback,
    )

    app.add_handler(CommandHandler("pc", pc_command))
    app.add_handler(CommandHandler("pc_ls", pc_ls_command))
    app.add_handler(CommandHandler("pc_get", pc_get_command))
    app.add_handler(CommandHandler("pc_save", pc_save_command))
    app.add_handler(CommandHandler("pc_delete", pc_delete_command))
    app.add_handler(CallbackQueryHandler(pc_callback, pattern=r"^pc_"))

    logger.info("로컬 핸들러 등록: PC 파일 관리")


def _register_file_handler(app: Application):
    """파일 수신 핸들러 등록 (모드에 따라 분기)"""

    @authorized_only
    async def handle_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """파일 수신 시 모드에 따라 처리"""
        if RUN_MODE in ("cloud", "all") and context.user_data.get("drive_upload_mode"):
            from bot.handlers.drive import handle_drive_upload
            await handle_drive_upload(update, context)
        elif RUN_MODE in ("local", "all") and context.user_data.get("pc_save_dir"):
            from bot.handlers.files import handle_pc_file_save
            await handle_pc_file_save(update, context)
        else:
            from bot.handlers.document import is_analyzable, analyze_document
            doc = update.message.document
            if ANTHROPIC_API_KEY and doc and is_analyzable(doc.file_name):
                await analyze_document(update, context)
                return
            options = []
            if RUN_MODE in ("cloud", "all"):
                options.append("• /drive_upload - Google Drive에 업로드")
            if RUN_MODE in ("local", "all"):
                options.append("• /pc_save <경로> - PC에 저장")
            await update.message.reply_text(
                "📎 파일을 받았습니다. 어디에 저장할까요?\n\n" + "\n".join(options)
            )

    app.add_handler(MessageHandler(filters.Document.ALL & _NEW_MSG, handle_file_received))


def main():
    """봇 실행"""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")
        return
    if not ALLOWED_USER_IDS:
        logger.error("ALLOWED_USER_IDS가 비어 있습니다. 아무도 봇을 쓸 수 없어요 (fail-closed).")

    logger.info(f"실행 모드: {RUN_MODE}")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_error_handler(on_error)

    # === 기본 명령 (항상 등록) ===
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))

    if RUN_MODE in ("cloud", "all"):
        _register_cloud_handlers(app)
    if RUN_MODE in ("local", "all"):
        _register_local_handlers(app)

    _register_file_handler(app)

    if GEMINI_API_KEY:
        from bot.handlers.search import search_command
        app.add_handler(CommandHandler("search", search_command))
        logger.info("Gemini 실시간 검색 핸들러 등록")
    else:
        logger.warning("GEMINI_API_KEY 미설정 - 실시간 검색 기능 비활성화")

    if ANTHROPIC_API_KEY:
        from bot.handlers.usage import usage_command
        app.add_handler(CommandHandler("usage", usage_command))

    if RUN_MODE in ("cloud", "all"):
        from bot.handlers.schedule import briefing_command
        app.add_handler(CommandHandler("briefing", briefing_command))

    from bot.handlers.stock_report import stock_report_command
    app.add_handler(CommandHandler("stock_report", stock_report_command))

    from bot.handlers.portfolio import (
        portfolio_command, portfolio_add_command, portfolio_del_command, portfolio_callback,
    )
    app.add_handler(CommandHandler("portfolio", portfolio_command))
    app.add_handler(CommandHandler("portfolio_add", portfolio_add_command))
    app.add_handler(CommandHandler("portfolio_del", portfolio_del_command))
    app.add_handler(CallbackQueryHandler(portfolio_callback, pattern=r"^dart:"))

    # === Claude AI 대화 ===
    if ANTHROPIC_API_KEY:
        from bot.handlers.photo import handle_photo_message
        app.add_handler(MessageHandler(filters.PHOTO & _NEW_MSG, handle_photo_message))
        app.add_handler(CommandHandler("clear", clear_command))
        app.add_handler(CallbackQueryHandler(action_callback, pattern=r"^act:"))
        app.add_handler(MessageHandler((filters.VOICE | filters.AUDIO) & _NEW_MSG, handle_voice_message))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & _NEW_MSG, handle_chat_message))
        logger.info("Claude AI 대화 핸들러 등록 (텍스트/음성/사진/문서, 확인 버튼)")
    else:
        logger.warning("ANTHROPIC_API_KEY 미설정 - Claude 대화 기능 비활성화")

    mode_label = {"cloud": "☁️ 클라우드", "local": "💻 로컬", "all": "☁️+💻 전체"}
    logger.info(f"GS-Secretary 봇 시작! [{mode_label.get(RUN_MODE, RUN_MODE)}]")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
