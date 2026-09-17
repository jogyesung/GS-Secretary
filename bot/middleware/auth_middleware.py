"""인증 미들웨어 - 데코레이터로 핸들러에 인증 적용 (메시지·콜백·편집 메시지 모두 처리)"""

import functools
from telegram import Update
from telegram.ext import ContextTypes
from bot.utils.auth import is_authorized


def authorized_only(func):
    """허용된 사용자만 접근 가능하도록 하는 데코레이터"""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if user is None or not is_authorized(user.id):
            if update.callback_query:
                await update.callback_query.answer("⛔ 접근 권한이 없어요.", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text("⛔ 접근 권한이 없습니다. 관리자에게 문의하세요.")
            return
        # 편집된 메시지 등 update.message가 None인 경우 핸들러가 터지지 않게
        if update.message is None and update.callback_query is None:
            return
        return await func(update, context, *args, **kwargs)

    return wrapper
