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
from bot.config import TELEGRAM_BOT_TOKEN, LOG_LEVEL, TEMP_DOWNLOAD_PATH
from bot.database import init_db
from bot.handlers.start import start_command, help_command, status_command
from bot.handlers.drive import (
    get_drive_auth_handler,
    drive_list_command,
    drive_search_command,
    drive_mkdir_command,
    drive_share_command,
    drive_upload_command,
    drive_callback,
    handle_drive_upload,
)
from bot.handlers.files import (
    pc_command,
    pc_ls_command,
    pc_get_command,
    pc_save_command,
    pc_delete_command,
    pc_callback,
    handle_pc_file_save,
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
from bot.handlers.schedule import check_reminders

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

    # 리마인더 체크 Job (30초마다)
    application.job_queue.run_repeating(check_reminders, interval=30, first=5)
    logger.info("리마인더 스케줄러 시작됨")


def main():
    """봇 실행"""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # === 기본 명령 ===
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))

    # === Google Drive ===
    app.add_handler(get_drive_auth_handler())  # ConversationHandler (우선순위 높음)
    app.add_handler(CommandHandler("drive", drive_list_command))
    app.add_handler(CommandHandler("drive_search", drive_search_command))
    app.add_handler(CommandHandler("drive_mkdir", drive_mkdir_command))
    app.add_handler(CommandHandler("drive_share", drive_share_command))
    app.add_handler(CommandHandler("drive_upload", drive_upload_command))
    app.add_handler(CallbackQueryHandler(drive_callback, pattern=r"^drive_"))

    # === PC 파일 관리 ===
    app.add_handler(CommandHandler("pc", pc_command))
    app.add_handler(CommandHandler("pc_ls", pc_ls_command))
    app.add_handler(CommandHandler("pc_get", pc_get_command))
    app.add_handler(CommandHandler("pc_save", pc_save_command))
    app.add_handler(CommandHandler("pc_delete", pc_delete_command))
    app.add_handler(CallbackQueryHandler(pc_callback, pattern=r"^pc_"))

    # === 메모 ===
    app.add_handler(CommandHandler("memo", memo_command))
    app.add_handler(CommandHandler("memos", memos_command))
    app.add_handler(CommandHandler("memo_search", memo_search_command))
    app.add_handler(CommandHandler("memo_del", memo_del_command))

    # === 리마인더 ===
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("remind_daily", remind_daily_command))
    app.add_handler(CommandHandler("reminders", reminders_command))
    app.add_handler(CommandHandler("remind_del", remind_del_command))

    # === 파일 수신 핸들러 (Drive 업로드 + PC 저장) ===
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file_received))

    logger.info("GS-Secretary 봇 시작!")
    app.run_polling(drop_pending_updates=True)


async def handle_file_received(update, context):
    """파일 수신 시 Drive 업로드 또는 PC 저장 처리"""
    if context.user_data.get("drive_upload_mode"):
        await handle_drive_upload(update, context)
    elif context.user_data.get("pc_save_dir"):
        await handle_pc_file_save(update, context)
    else:
        await update.message.reply_text(
            "📎 파일을 받았습니다. 어디에 저장할까요?\n\n"
            "• /drive\\_upload - Google Drive에 업로드\n"
            "• /pc\\_save <경로> - PC에 저장"
        )


if __name__ == "__main__":
    main()
