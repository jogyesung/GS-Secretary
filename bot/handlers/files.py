"""PC 로컬 파일 관리 핸들러"""

import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.services import file_service
from bot.utils.formatting import format_file_size
from bot.config import TEMP_DOWNLOAD_PATH


@authorized_only
async def pc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """허용된 루트 디렉토리 목록"""
    roots = file_service.get_allowed_roots()
    if not roots:
        await update.message.reply_text(
            "❌ 허용된 경로가 설정되지 않았습니다.\n"
            ".env 파일에서 ALLOWED_PATHS를 설정하세요."
        )
        return

    buttons = [
        [InlineKeyboardButton(f"📁 {r['name']}", callback_data=f"pc_ls:{r['path']}")]
        for r in roots
    ]
    await update.message.reply_text(
        "💻 *허용된 디렉토리 목록*\n\n폴더를 선택하세요:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@authorized_only
async def pc_ls_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """디렉토리 내용 보기"""
    if not context.args:
        await update.message.reply_text("사용법: /pc\\_ls <경로>")
        return

    dir_path = " ".join(context.args)
    try:
        items = file_service.list_directory(dir_path)
        if not items:
            await update.message.reply_text("📂 비어있는 디렉토리입니다.")
            return

        lines = [f"📂 *{dir_path}*\n"]
        buttons = []

        for item in items[:30]:  # 최대 30개
            if item["is_dir"]:
                lines.append(f"📁 {item['name']}/")
                buttons.append([InlineKeyboardButton(
                    f"📁 {item['name']}", callback_data=f"pc_ls:{item['path']}"
                )])
            else:
                lines.append(f"📄 {item['name']} ({format_file_size(item['size'])})")
                buttons.append([InlineKeyboardButton(
                    f"📄 {item['name']}", callback_data=f"pc_get:{item['path']}"
                )])

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )
    except (PermissionError, FileNotFoundError) as e:
        await update.message.reply_text(f"❌ {e}")


@authorized_only
async def pc_get_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """로컬 파일을 텔레그램으로 전송"""
    if not context.args:
        await update.message.reply_text("사용법: /pc\\_get <파일경로>")
        return

    file_path = " ".join(context.args)
    try:
        path = file_service.get_file_path(file_path)
        await update.message.reply_document(
            document=open(path, "rb"),
            filename=path.name,
        )
    except (PermissionError, FileNotFoundError) as e:
        await update.message.reply_text(f"❌ {e}")


@authorized_only
async def pc_save_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """텔레그램 파일을 로컬에 저장 - 저장 경로 설정"""
    if not context.args:
        await update.message.reply_text("사용법: /pc\\_save <저장할\\_디렉토리\\_경로>\n그 후 파일을 보내주세요.")
        return

    dest_dir = " ".join(context.args)
    context.user_data["pc_save_dir"] = dest_dir
    await update.message.reply_text(
        f"📥 저장 경로: `{dest_dir}`\n\n파일을 보내주시면 해당 경로에 저장합니다.",
        parse_mode="Markdown",
    )


@authorized_only
async def pc_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """로컬 파일/폴더 삭제"""
    if not context.args:
        await update.message.reply_text("사용법: /pc\\_delete <경로>")
        return

    file_path = " ".join(context.args)
    try:
        file_service.delete_file(file_path)
        await update.message.reply_text(f"✅ 삭제 완료: {file_path}")
    except (PermissionError, FileNotFoundError) as e:
        await update.message.reply_text(f"❌ {e}")


async def handle_pc_file_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """파일 수신 시 로컬에 저장 (pc_save 모드일 때)"""
    save_dir = context.user_data.get("pc_save_dir")
    if not save_dir:
        return

    document = update.message.document
    if not document:
        return

    try:
        TEMP_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
        file = await document.get_file()
        temp_path = str(TEMP_DOWNLOAD_PATH / document.file_name)
        await file.download_to_drive(temp_path)

        with open(temp_path, "rb") as f:
            content = f.read()
        os.remove(temp_path)

        saved_path = file_service.save_file(save_dir, document.file_name, content)
        context.user_data["pc_save_dir"] = None
        await update.message.reply_text(f"✅ 파일 저장 완료: {saved_path}")
    except (PermissionError, FileNotFoundError) as e:
        await update.message.reply_text(f"❌ {e}")


async def pc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PC 파일 관련 인라인 버튼 콜백"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("pc_ls:"):
        dir_path = data[6:]
        try:
            items = file_service.list_directory(dir_path)
            if not items:
                await query.edit_message_text("📂 비어있는 디렉토리입니다.")
                return

            lines = [f"📂 *{dir_path}*\n"]
            buttons = []

            for item in items[:30]:
                if item["is_dir"]:
                    lines.append(f"📁 {item['name']}/")
                    buttons.append([InlineKeyboardButton(
                        f"📁 {item['name']}", callback_data=f"pc_ls:{item['path']}"
                    )])
                else:
                    lines.append(f"📄 {item['name']} ({format_file_size(item['size'])})")
                    buttons.append([InlineKeyboardButton(
                        f"📄 {item['name']}", callback_data=f"pc_get:{item['path']}"
                    )])

            await query.edit_message_text(
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            )
        except Exception as e:
            await query.edit_message_text(f"❌ {e}")

    elif data.startswith("pc_get:"):
        file_path = data[7:]
        try:
            path = file_service.get_file_path(file_path)
            await query.message.reply_document(
                document=open(path, "rb"),
                filename=path.name,
            )
        except Exception as e:
            await query.edit_message_text(f"❌ {e}")
