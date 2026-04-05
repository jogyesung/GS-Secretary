"""인증 미들웨어 - 데코레이터로 핸들러에 인증 적용"""

import functools
from telegram import Update
from telegram.ext import ContextTypes
from bot.utils.auth import is_authorized


def authorized_only(func):
    """허용된 사용자만 접근 가능하도록 하는 데코레이터"""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            await update.message.reply_text("⛔ 접근 권한이 없습니다. 관리자에게 문의하세요.")
            return
        return await func(update, context, *args, **kwargs)

    return wrapper
