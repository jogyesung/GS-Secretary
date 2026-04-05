"""GS-Secretary 봇 진입점 - Application 설정 및 핸들러 등록"""

import logging
from pathlib import Path
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from bot.config import TELEGRAM_BOT_TOKEN, LOG_LEVEL, TEMP_DOWNLOAD_PATH, RUN_MODE, ANTHROPIC_API_KEY
from bot.database import init_db
from bot.handlers.start import start_command, help_command, status_command
from bot.handlers.chat import handle_chat_message, clear_command

# 로깅 설정
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logger = logging.getLogger(__name__)


async def post_init(application: Application):
    """봇 시작 후 초기화"""
    await init_db()
    TEMP_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
    logger.info("데이터베이스 초기화 완료")

    if RUN_MODE in ("cloud", "all"):
        from bot.handlers.schedule import check_reminders
        application.job_queue.run_repeating(check_reminders, interval=30, first=5)
        logger.info("리마인더 스케줄러 시작됨")


def _register_cloud_handlers(app: Application):
    """클라우드 모드 핸들러: Google Drive + 메모 + 리마인더"""
    from bot.handlers.drive import (
        get_drive_auth_handler,
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

    # Google Drive
    app.add_handler(get_drive_auth_handler())
    app.add_handler(CommandHandler("drive", drive_list_command))
    app.add_handler(CommandHandler("drive_search", drive_search_command))
    app.add_handler(CommandHandler("drive_mkdir", drive_mkdir_command))
    app.add_handler(CommandHandler("drive_share", drive_share_command))
    app.add_handler(CommandHandler("drive_upload", drive_upload_command))
    app.add_handler(CallbackQueryHandler(drive_callback, pattern=r"^drive_"))

    # 메모
    app.add_handler(CommandHandler("memo", memo_command))
    app.add_handler(CommandHandler("memos", memos_command))
    app.add_handler(CommandHandler("memo_search", memo_search_command))
    app.add_handler(CommandHandler("memo_del", memo_del_command))

    # 리마인더
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("remind_daily", remind_daily_command))
    app.add_handler(CommandHandler("reminders", reminders_command))
    app.add_handler(CommandHandler("remind_del", remind_del_command))

    logger.info("클라우드 핸들러 등록: Drive, 메모, 리마인더")


def _register_local_handlers(app: Application):
    """로컬 모드 핸들러: PC 파일 관리 + 파일 감시"""
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

    async def handle_file_received(update, context):
        """파일 수신 시 모드에 따라 처리"""
        if RUN_MODE in ("cloud", "all") and context.user_data.get("drive_upload_mode"):
            from bot.handlers.drive import handle_drive_upload
            await handle_drive_upload(update, context)
        elif RUN_MODE in ("local", "all") and context.user_data.get("pc_save_dir"):
            from bot.handlers.files import handle_pc_file_save
            await handle_pc_file_save(update, context)
        else:
            options = []
            if RUN_MODE in ("cloud", "all"):
                options.append("• /drive\\_upload - Google Drive에 업로드")
            if RUN_MODE in ("local", "all"):
                options.append("• /pc\\_save <경로> - PC에 저장")
            await update.message.reply_text(
                "📎 파일을 받았습니다. 어디에 저장할까요?\n\n" + "\n".join(options)
            )

    app.add_handler(MessageHandler(filters.Document.ALL, handle_file_received))


def main():
    """봇 실행"""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")
        return

    logger.info(f"실행 모드: {RUN_MODE}")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # === 기본 명령 (항상 등록) ===
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))

    # === 모드별 핸들러 등록 ===
    if RUN_MODE in ("cloud", "all"):
        _register_cloud_handlers(app)

    if RUN_MODE in ("local", "all"):
        _register_local_handlers(app)

    _register_file_handler(app)

    # === Claude AI 대화 ===
    if ANTHROPIC_API_KEY:
        app.add_handler(CommandHandler("clear", clear_command))
        # 일반 텍스트 메시지 → Claude 대화 (가장 낮은 우선순위)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat_message))
        logger.info("Claude AI 대화 핸들러 등록")
    else:
        logger.warning("ANTHROPIC_API_KEY 미설정 - Claude 대화 기능 비활성화")

    mode_label = {"cloud": "☁️ 클라우드", "local": "💻 로컬", "all": "☁️+💻 전체"}
    logger.info(f"GS-Secretary 봇 시작! [{mode_label.get(RUN_MODE, RUN_MODE)}]")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
