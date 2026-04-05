"""메모 핸들러 - 저장, 조회, 검색, 삭제"""

from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.services import memo_service
from bot.utils.formatting import truncate


@authorized_only
async def memo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """메모 저장"""
    if not context.args:
        await update.message.reply_text("사용법: /memo <메모 내용>")
        return

    content = " ".join(context.args)
    user_id = update.effective_user.id
    memo_id = await memo_service.create_memo(user_id, content)
    await update.message.reply_text(f"📝 메모 저장 완료! (ID: {memo_id})")


@authorized_only
async def memos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """메모 목록 조회"""
    user_id = update.effective_user.id
    memos = await memo_service.get_memos(user_id)

    if not memos:
        await update.message.reply_text("📝 저장된 메모가 없습니다.")
        return

    lines = ["📝 *메모 목록*\n"]
    for m in memos:
        lines.append(f"**#{m['id']}** | {m['created_at'][:16]}\n{truncate(m['content'], 80)}\n")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized_only
async def memo_search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """메모 검색"""
    if not context.args:
        await update.message.reply_text("사용법: /memo\\_search <키워드>")
        return

    keyword = " ".join(context.args)
    user_id = update.effective_user.id
    memos = await memo_service.search_memos(user_id, keyword)

    if not memos:
        await update.message.reply_text(f"🔍 '{keyword}'에 대한 검색 결과가 없습니다.")
        return

    lines = [f"🔍 *'{keyword}' 검색 결과*\n"]
    for m in memos:
        lines.append(f"**#{m['id']}** | {m['created_at'][:16]}\n{truncate(m['content'], 80)}\n")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized_only
async def memo_del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """메모 삭제"""
    if not context.args:
        await update.message.reply_text("사용법: /memo\\_del <메모ID>")
        return

    try:
        memo_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ 올바른 메모 ID를 입력하세요.")
        return

    user_id = update.effective_user.id
    deleted = await memo_service.delete_memo(user_id, memo_id)
    if deleted:
        await update.message.reply_text(f"✅ 메모 #{memo_id} 삭제 완료")
    else:
        await update.message.reply_text(f"❌ 메모 #{memo_id}를 찾을 수 없습니다.")
