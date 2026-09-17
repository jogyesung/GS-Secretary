"""PC 로컬 파일 관리 핸들러"""

import hashlib
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.services import file_service
from bot.services.file_service import safe_filename
from bot.utils.formatting import format_file_size
from bot.utils.telegram_fmt import reply_html, md_to_html
from bot.config import TEMP_DOWNLOAD_PATH

_EXPIRED_TEXT = "⌛ 목록이 만료됐어요. /pc 로 다시 열어주세요."
MAX_ITEMS = 30


def _path_key(context: ContextTypes.DEFAULT_TYPE, path: str) -> str:
    """전체 경로를 짧은 키로 바꿔 user_data에 보관 (콜백 데이터 64바이트 제한 대응)"""
    key = hashlib.sha1(path.encode()).hexdigest()[:10]
    context.user_data.setdefault("pc_paths", {})[key] = path
    return key


def _resolve_key(context: ContextTypes.DEFAULT_TYPE, key: str) -> str | None:
    return (context.user_data.get("pc_paths") or {}).get(key)


def _build_listing(context: ContextTypes.DEFAULT_TYPE, dir_path: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """디렉토리 목록 텍스트 + 버튼 생성. 비어 있으면 버튼 없이 안내문."""
    items = file_service.list_directory(dir_path)
    if not items:
        return "📂 비어있는 디렉토리예요.", None

    lines = [f"📂 **{dir_path}**\n"]
    buttons = []
    for item in items[:MAX_ITEMS]:
        key = _path_key(context, item["path"])
        if item["is_dir"]:
            lines.append(f"📁 {item['name']}/")
            buttons.append([InlineKeyboardButton(f"📁 {item['name']}", callback_data=f"pc_ls:{key}")])
        else:
            lines.append(f"📄 {item['name']} ({format_file_size(item['size'])})")
            buttons.append([InlineKeyboardButton(f"📄 {item['name']}", callback_data=f"pc_get:{key}")])
    if len(items) > MAX_ITEMS:
        lines.append(f"\n… 외 {len(items) - MAX_ITEMS}개")

    return "\n".join(lines), (InlineKeyboardMarkup(buttons) if buttons else None)


async def _edit_html(query, text: str, reply_markup=None):
    try:
        await query.edit_message_text(md_to_html(text), parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        await query.edit_message_text(text, reply_markup=reply_markup)


@authorized_only
async def pc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """허용된 루트 디렉토리 목록"""
    roots = file_service.get_allowed_roots()
    if not roots:
        await update.message.reply_text(
            "❌ 허용된 경로가 설정되지 않았어요.\n"
            ".env 파일에서 ALLOWED_PATHS를 설정해주세요."
        )
        return

    buttons = [
        [InlineKeyboardButton(f"📁 {r['name']}", callback_data=f"pc_ls:{_path_key(context, r['path'])}")]
        for r in roots
    ]
    await reply_html(
        update,
        "💻 **허용된 디렉토리 목록**\n\n폴더를 선택해주세요:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@authorized_only
async def pc_ls_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """디렉토리 내용 보기"""
    if not context.args:
        await update.message.reply_text("사용법: /pc_ls <경로>")
        return

    dir_path = " ".join(context.args)
    try:
        text, reply_markup = _build_listing(context, dir_path)
        await reply_html(update, text, reply_markup=reply_markup)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


@authorized_only
async def pc_get_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """로컬 파일을 텔레그램으로 전송"""
    if not context.args:
        await update.message.reply_text("사용법: /pc_get <파일경로>")
        return

    file_path = " ".join(context.args)
    try:
        path = file_service.get_file_path(file_path)
        with open(path, "rb") as fh:
            await update.message.reply_document(document=fh, filename=path.name)
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


@authorized_only
async def pc_save_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """텔레그램 파일을 로컬에 저장 - 저장 경로 설정"""
    if not context.args:
        await update.message.reply_text("사용법: /pc_save <저장할 디렉토리 경로>\n그 후 파일을 보내주세요.")
        return

    dest_dir = " ".join(context.args)
    context.user_data["pc_save_dir"] = dest_dir
    await reply_html(update, f"📥 저장 경로: `{dest_dir}`\n\n파일을 보내주시면 해당 경로에 저장해요.")


@authorized_only
async def pc_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """로컬 파일/폴더 삭제"""
    if not context.args:
        await update.message.reply_text("사용법: /pc_delete <경로>")
        return

    file_path = " ".join(context.args)
    try:
        file_service.delete_file(file_path)
        await update.message.reply_text(f"✅ 삭제 완료: {file_path}")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


@authorized_only
async def handle_pc_file_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """파일 수신 시 로컬에 저장 (pc_save 모드일 때)"""
    save_dir = context.user_data.get("pc_save_dir")
    if not save_dir:
        return

    document = update.message.document
    if not document:
        return

    temp_path = None
    try:
        TEMP_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
        file_name = safe_filename(document.file_name)
        file = await document.get_file()
        temp_path = str(TEMP_DOWNLOAD_PATH / file_name)
        await file.download_to_drive(temp_path)

        with open(temp_path, "rb") as f:
            content = f.read()

        saved_path = file_service.save_file(save_dir, file_name, content)
        context.user_data["pc_save_dir"] = None
        await update.message.reply_text(f"✅ 파일 저장 완료: {saved_path}")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@authorized_only
async def pc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PC 파일 관련 인라인 버튼 콜백"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("pc_ls:"):
        dir_path = _resolve_key(context, data[6:])
        if not dir_path:
            await query.edit_message_text(_EXPIRED_TEXT)
            return
        try:
            text, reply_markup = _build_listing(context, dir_path)
            await _edit_html(query, text, reply_markup=reply_markup)
        except Exception as e:
            await query.edit_message_text(f"❌ {e}")

    elif data.startswith("pc_get:"):
        file_path = _resolve_key(context, data[7:])
        if not file_path:
            await query.edit_message_text(_EXPIRED_TEXT)
            return
        try:
            path = file_service.get_file_path(file_path)
            with open(path, "rb") as fh:
                await query.message.reply_document(document=fh, filename=path.name)
        except Exception as e:
            await query.edit_message_text(f"❌ {e}")
