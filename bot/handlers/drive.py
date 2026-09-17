"""Google Drive 핸들러 - 인증 안내, 파일 목록, 업로드, 다운로드, 검색 등"""

import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.utils.drive_auth import is_authenticated_async, AUTH_HELP_TEXT
from bot.utils.formatting import format_file_size
from bot.utils.telegram_fmt import reply_html, md_to_html
from bot.services import drive_service
from bot.services.file_service import safe_filename
from bot.config import TEMP_DOWNLOAD_PATH

_CALLBACK_MAX_BYTES = 64
_REAUTH_HINT = "❌ Google 인증이 필요해요. PC에서 auth_local.py로 재인증 후 GOOGLE_TOKEN_B64를 갱신해주세요. (/drive_auth 참고)"


def _build_listing(files: list[dict], include_parent: bool = False) -> tuple[str, InlineKeyboardMarkup | None]:
    """파일 목록 텍스트 + 버튼 생성 (콜백 데이터 64바이트 제한 준수)"""
    buttons = []
    if include_parent:
        buttons.append([InlineKeyboardButton("⬆️ 상위 폴더", callback_data="drive_cd:root")])
    lines = ["📂 **파일 목록**\n"]

    for f in files:
        is_folder = f["mimeType"] == "application/vnd.google-apps.folder"
        icon = "📁" if is_folder else "📄"
        size_str = "" if is_folder else f" ({format_file_size(int(f.get('size', 0)))})"
        lines.append(f"{icon} {f['name']}{size_str}")

        prefix = "drive_cd:" if is_folder else "drive_dl:"
        callback_data = f"{prefix}{f['id']}"
        if len(callback_data.encode()) > _CALLBACK_MAX_BYTES:
            continue
        buttons.append([InlineKeyboardButton(f"{icon} {f['name']}", callback_data=callback_data)])

    return "\n".join(lines), (InlineKeyboardMarkup(buttons) if buttons else None)


async def _edit_html(query, text: str, reply_markup=None):
    try:
        await query.edit_message_text(md_to_html(text), parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        await query.edit_message_text(text, reply_markup=reply_markup)


@authorized_only
async def drive_auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Google 인증 상태 확인 및 인증 안내"""
    if await is_authenticated_async():
        await update.message.reply_text("✅ 이미 Google에 연결되어 있어요")
        return
    await reply_html(update, AUTH_HELP_TEXT)


@authorized_only
async def drive_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Drive 파일 목록 (루트 또는 지정 폴더)"""
    folder_id = context.args[0] if context.args else "root"

    try:
        files = await drive_service.list_files(folder_id)
        if not files:
            await update.message.reply_text("📂 폴더가 비어있어요.")
            return
        text, reply_markup = _build_listing(files)
        await reply_html(update, text, reply_markup=reply_markup)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


@authorized_only
async def drive_search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Drive 파일 검색"""
    if not context.args:
        await update.message.reply_text("사용법: /drive_search <검색어>")
        return

    query = " ".join(context.args)
    try:
        files = await drive_service.search_files(query)
        if not files:
            await update.message.reply_text(f"🔍 '{query}'에 대한 검색 결과가 없어요.")
            return

        lines = [f"🔍 **'{query}' 검색 결과**\n"]
        for f in files:
            is_folder = f["mimeType"] == "application/vnd.google-apps.folder"
            icon = "📁" if is_folder else "📄"
            lines.append(f"{icon} {f['name']} (ID: `{f['id']}`)")

        await reply_html(update, "\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


@authorized_only
async def drive_mkdir_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Drive에 폴더 생성"""
    if not context.args:
        await update.message.reply_text("사용법: /drive_mkdir <폴더이름>")
        return

    name = " ".join(context.args)
    try:
        folder = await drive_service.create_folder(name)
        await reply_html(update, f"✅ 폴더 생성 완료: {folder['name']} (ID: `{folder['id']}`)")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


@authorized_only
async def drive_share_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """공유 링크 생성"""
    if not context.args:
        await update.message.reply_text("사용법: /drive_share <파일ID>")
        return

    file_id = context.args[0]
    try:
        link = await drive_service.get_share_link(file_id)
        await update.message.reply_text(f"🔗 공유 링크:\n{link}")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


@authorized_only
async def drive_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """인라인 버튼 콜백 처리 (폴더 이동, 파일 다운로드)"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("drive_cd:"):
        folder_id = data.split(":", 1)[1]
        try:
            files = await drive_service.list_files(folder_id)
            if not files:
                await query.edit_message_text("📂 폴더가 비어있어요.")
                return
            text, reply_markup = _build_listing(files, include_parent=True)
            await _edit_html(query, text, reply_markup=reply_markup)
        except Exception as e:
            await query.edit_message_text(f"❌ 오류: {e}")

    elif data.startswith("drive_dl:"):
        file_id = data.split(":", 1)[1]
        dest = None
        try:
            info = await drive_service.get_file_info(file_id)
            file_name = safe_filename(info.get("name"))
            TEMP_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
            dest = str(TEMP_DOWNLOAD_PATH / file_name)
            await drive_service.download_file(file_id, dest)
            with open(dest, "rb") as fh:
                await query.message.reply_document(document=fh, filename=file_name)
        except Exception as e:
            await query.edit_message_text(f"❌ 다운로드 실패: {e}")
        finally:
            if dest and os.path.exists(dest):
                os.remove(dest)


@authorized_only
async def handle_drive_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """텔레그램에서 받은 파일을 Drive에 업로드"""
    if not await is_authenticated_async():
        await update.message.reply_text(_REAUTH_HINT)
        return

    document = update.message.document
    if not document:
        return

    if not context.user_data.get("drive_upload_mode"):
        return

    await update.message.reply_text("⏳ Drive에 업로드 중...")
    temp_path = None
    try:
        TEMP_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
        file_name = safe_filename(document.file_name)
        file = await document.get_file()
        temp_path = str(TEMP_DOWNLOAD_PATH / file_name)
        await file.download_to_drive(temp_path)

        folder_id = context.user_data.get("drive_upload_folder", "root")
        result = await drive_service.upload_file(temp_path, folder_id, file_name)

        context.user_data["drive_upload_mode"] = False
        await update.message.reply_text(
            f"✅ 업로드 완료!\n"
            f"파일명: {result['name']}\n"
            f"링크: {result.get('webViewLink', 'N/A')}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ 업로드 실패: {e}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@authorized_only
async def drive_upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Drive 업로드 모드 활성화"""
    if not await is_authenticated_async():
        await update.message.reply_text(_REAUTH_HINT)
        return

    context.user_data["drive_upload_mode"] = True
    context.user_data["drive_upload_folder"] = context.args[0] if context.args else "root"

    await reply_html(
        update,
        "📤 **Drive 업로드 모드**\n\n"
        "파일을 보내주시면 Google Drive에 업로드해요.\n"
        "업로드 후 자동으로 모드가 해제돼요.",
    )
