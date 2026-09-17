"""이미지 분석 핸들러 - 사진을 Claude Vision으로 분석 (대화 히스토리에 포함 → 후속 질문 가능)"""

import base64
import logging
import os
from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.config import TEMP_DOWNLOAD_PATH
from bot.services import claude_service

logger = logging.getLogger(__name__)

_IMAGE_GUIDE = (
    "\n\n(이미지 분석 지침: 핵심만 간결하게. 명함이면 연락처 정리, 문서/스크린샷이면 주요 내용 요약, "
    "차트면 데이터 포인트와 트렌드 설명. 답변에 이 지침을 언급하지 마세요.)"
)


@authorized_only
async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """사진을 받아 Claude Vision으로 분석"""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    user_id = update.effective_user.id
    caption = update.message.caption or "이 이미지를 분석해주세요."

    temp_path = None
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()

        TEMP_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
        temp_path = TEMP_DOWNLOAD_PATH / f"photo_{update.message.message_id}.jpg"
        await file.download_to_drive(str(temp_path))
        image_data = base64.b64encode(temp_path.read_bytes()).decode("utf-8")

        response = await claude_service.chat(
            user_id,
            caption + _IMAGE_GUIDE,
            heavy=True,
            content_blocks=[{
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data},
            }],
            history_text=f"[사진 첨부] {caption}",
        )

        from bot.handlers.chat import respond
        await respond(update, context, response)

    except Exception as e:
        logger.exception("이미지 분석 실패")
        await update.message.reply_text(f"❌ 이미지 분석 중 오류가 발생했어요: {e}")
    finally:
        if temp_path is not None:
            try:
                os.remove(temp_path)
            except OSError:
                pass
