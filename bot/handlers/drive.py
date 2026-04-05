"""Google Drive 핸들러 - 인증, 파일 목록, 업로드, 다운로드, 검색 등"""

import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.middleware.auth_middleware import authorized_only
from bot.utils.drive_auth import get_auth_url, exchange_code, is_authenticated
from bot.utils.formatting import format_file_size
from bot.services import drive_service
from bot.config import TEMP_DOWNLOAD_PATH

# ConversationHandler 상태
WAITING_AUTH_CODE = 0
WAITING_UPLOAD_FOLDER = 1


@authorized_only
async def drive_auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Google Drive 인증 시작"""
    if is_authenticated():
        await update.message.reply_text("✅ 이미 Google Drive에 연결되어 있습니다.")
        return ConversationHandler.END

    auth_url = get_auth_url()
    if not auth_url:
        await update.message.reply_text(
            "❌ credentials.json 파일이 없습니다.\n"
            "Google Cloud Console에서 OAuth 클라이언트 자격증명을 다운로드하고\n"
            "프로젝트 루트에 credentials.json으로 저장하세요."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🔐 *Google Drive 인증*\n\n"
        "1. 아래 링크를 클릭하여 Google 계정으로 로그인하세요\n"
        "2. 권한을 승인하세요\n"
        "3. 표시되는 인증 코드를 여기에 붙여넣기 하세요\n\n"
        f"[인증 링크]({auth_url})",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )
    return WAITING_AUTH_CODE


async def receive_auth_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """인증 코드 수신 및 토큰 교환"""
    code = update.message.text.strip()
    try:
        creds = exchange_code(code)
        if creds:
            await update.message.reply_text("✅ Google Drive 인증 완료! /drive 로 파일을 확인하세요.")
        else:
            await update.message.reply_text("❌ 인증에 실패했습니다. /drive_auth 로 다시 시도하세요.")
    except Exception as e:
        await update.message.reply_text(f"❌ 인증 오류: {e}")
    return ConversationHandler.END


async def auth_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """인증 취소"""
    await update.message.reply_text("인증이 취소되었습니다.")
    return ConversationHandler.END


@authorized_only
async def drive_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Drive 파일 목록 (루트 또는 지정 폴더)"""
    folder_id = "root"
    if context.args:
        folder_id = context.args[0]

    try:
        files = await drive_service.list_files(folder_id)
        if not files:
            await update.message.reply_text("📂 폴더가 비어있습니다.")
            return

        buttons = []
        text_lines = ["📂 *파일 목록*\n"]

        for f in files:
            is_folder = f["mimeType"] == "application/vnd.google-apps.folder"
            icon = "📁" if is_folder else "📄"
            size_str = "" if is_folder else f" ({format_file_size(int(f.get('size', 0)))})"
            text_lines.append(f"{icon} {f['name']}{size_str}")

            if is_folder:
                buttons.append([InlineKeyboardButton(
                    f"📁 {f['name']}",
                    callback_data=f"drive_cd:{f['id']}",
                )])
            else:
                buttons.append([InlineKeyboardButton(
                    f"📄 {f['name']}",
                    callback_data=f"drive_dl:{f['id']}:{f['name']}",
                )])

        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
        await update.message.reply_text(
            "\n".join(text_lines),
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
    except RuntimeError as e:
        await update.message.reply_text(f"❌ {e}")


@authorized_only
async def drive_search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Drive 파일 검색"""
    if not context.args:
        await update.message.reply_text("사용법: /drive\\_search <검색어>")
        return

    query = " ".join(context.args)
    try:
        files = await drive_service.search_files(query)
        if not files:
            await update.message.reply_text(f"🔍 '{query}'에 대한 검색 결과가 없습니다.")
            return

        lines = [f"🔍 *'{query}' 검색 결과*\n"]
        for f in files:
            is_folder = f["mimeType"] == "application/vnd.google-apps.folder"
            icon = "📁" if is_folder else "📄"
            lines.append(f"{icon} {f['name']} (ID: `{f['id']}`)")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except RuntimeError as e:
        await update.message.reply_text(f"❌ {e}")


@authorized_only
async def drive_mkdir_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Drive에 폴더 생성"""
    if not context.args:
        await update.message.reply_text("사용법: /drive\\_mkdir <폴더이름>")
        return

    name = " ".join(context.args)
    try:
        folder = await drive_service.create_folder(name)
        await update.message.reply_text(f"✅ 폴더 생성 완료: {folder['name']} (ID: `{folder['id']}`)", parse_mode="Markdown")
    except RuntimeError as e:
        await update.message.reply_text(f"❌ {e}")


@authorized_only
async def drive_share_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """공유 링크 생성"""
    if not context.args:
        await update.message.reply_text("사용법: /drive\\_share <파일ID>")
        return

    file_id = context.args[0]
    try:
        link = await drive_service.get_share_link(file_id)
        await update.message.reply_text(f"🔗 공유 링크:\n{link}")
    except RuntimeError as e:
        await update.message.reply_text(f"❌ {e}")


async def drive_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """인라인 버튼 콜백 처리 (폴더 이동, 파일 다운로드)"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("drive_cd:"):
        folder_id = data.split(":")[1]
        try:
            files = await drive_service.list_files(folder_id)
            if not files:
                await query.edit_message_text("📂 폴더가 비어있습니다.")
                return

            buttons = [[InlineKeyboardButton("⬆️ 상위 폴더", callback_data="drive_cd:root")]]
            text_lines = ["📂 *파일 목록*\n"]

            for f in files:
                is_folder = f["mimeType"] == "application/vnd.google-apps.folder"
                icon = "📁" if is_folder else "📄"
                size_str = "" if is_folder else f" ({format_file_size(int(f.get('size', 0)))})"
                text_lines.append(f"{icon} {f['name']}{size_str}")

                if is_folder:
                    buttons.append([InlineKeyboardButton(
                        f"📁 {f['name']}", callback_data=f"drive_cd:{f['id']}"
                    )])
                else:
                    buttons.append([InlineKeyboardButton(
                        f"📄 {f['name']}", callback_data=f"drive_dl:{f['id']}:{f['name']}"
                    )])

            await query.edit_message_text(
                "\n".join(text_lines),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 오류: {e}")

    elif data.startswith("drive_dl:"):
        parts = data.split(":", 2)
        file_id = parts[1]
        file_name = parts[2] if len(parts) > 2 else "file"

        try:
            TEMP_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
            dest = str(TEMP_DOWNLOAD_PATH / file_name)
            await drive_service.download_file(file_id, dest)
            await query.message.reply_document(document=open(dest, "rb"), filename=file_name)
            os.remove(dest)
        except Exception as e:
            await query.edit_message_text(f"❌ 다운로드 실패: {e}")


@authorized_only
async def handle_drive_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """텔레그램에서 받은 파일을 Drive에 업로드"""
    if not is_authenticated():
        await update.message.reply_text("❌ Google Drive 인증이 필요합니다. /drive_auth 를 먼저 실행하세요.")
        return

    document = update.message.document
    if not document:
        return

    # context.user_data에서 업로드 모드 확인
    if not context.user_data.get("drive_upload_mode"):
        return

    await update.message.reply_text("⏳ Drive에 업로드 중...")
    try:
        TEMP_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
        file = await document.get_file()
        temp_path = str(TEMP_DOWNLOAD_PATH / document.file_name)
        await file.download_to_drive(temp_path)

        folder_id = context.user_data.get("drive_upload_folder", "root")
        result = await drive_service.upload_file(temp_path, folder_id, document.file_name)
        os.remove(temp_path)

        context.user_data["drive_upload_mode"] = False
        await update.message.reply_text(
            f"✅ 업로드 완료!\n"
            f"파일명: {result['name']}\n"
            f"링크: {result.get('webViewLink', 'N/A')}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ 업로드 실패: {e}")


@authorized_only
async def drive_upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Drive 업로드 모드 활성화"""
    if not is_authenticated():
        await update.message.reply_text("❌ Google Drive 인증이 필요합니다. /drive_auth 를 먼저 실행하세요.")
        return

    context.user_data["drive_upload_mode"] = True
    context.user_data["drive_upload_folder"] = "root"
    if context.args:
        context.user_data["drive_upload_folder"] = context.args[0]

    await update.message.reply_text(
        "📤 *Drive 업로드 모드*\n\n"
        "파일을 보내주시면 Google Drive에 업로드합니다.\n"
        "업로드 후 자동으로 모드가 해제됩니다.",
        parse_mode="Markdown",
    )


def get_drive_auth_handler() -> ConversationHandler:
    """Drive 인증 ConversationHandler"""
    return ConversationHandler(
        entry_points=[CommandHandler("drive_auth", drive_auth_command)],
        states={
            WAITING_AUTH_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_auth_code)],
        },
        fallbacks=[CommandHandler("cancel", auth_cancel)],
    )
