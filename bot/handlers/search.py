"""실시간 웹 검색 핸들러 - Gemini Google Search grounding"""

from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.services import gemini_service
from bot.utils.telegram_fmt import reply_html


@authorized_only
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/search <질문> - 실시간 웹 검색"""
    if not context.args:
        await update.message.reply_text("사용법: /search <검색할 내용>\n예: /search 오늘 코스피 지수")
        return

    query = " ".join(context.args)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        result = await gemini_service.search(query)

        text = f"🔍 **검색: {query}**\n\n{result['answer']}"
        if result["sources"]:
            text += "\n\n📎 **출처:**\n" + "\n".join(f"- {src}" for src in result["sources"][:5])

        await reply_html(update, text)

    except RuntimeError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ 검색 오류: {e}")
