"""이미지 분석 핸들러 - 사진을 Claude Vision으로 분석"""

import base64
import logging
import os
from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.config import TEMP_DOWNLOAD_PATH

logger = logging.getLogger(__name__)


@authorized_only
async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """사진을 받아 Claude Vision으로 분석"""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    user_id = update.effective_user.id
    caption = update.message.caption or "이 이미지를 분석해주세요."

    try:
        # Get the largest photo
        photo = update.message.photo[-1]  # highest resolution
        file = await photo.get_file()

        # Download
        TEMP_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
        temp_path = TEMP_DOWNLOAD_PATH / f"photo_{update.message.message_id}.jpg"
        await file.download_to_drive(str(temp_path))

        # Base64 encode
        with open(temp_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        # Clean up temp file
        os.remove(temp_path)

        # Send to Claude Vision
        response = await _analyze_image(user_id, image_data, caption)

        # Send response (split if too long)
        if len(response) > 4000:
            for i in range(0, len(response), 4000):
                await update.message.reply_text(response[i:i + 4000])
        else:
            await update.message.reply_text(response)

    except Exception as e:
        logger.error(f"이미지 분석 실패: {e}")
        await update.message.reply_text(f"❌ 이미지 분석 중 오류가 발생했어요: {e}")


async def _analyze_image(user_id: int, image_base64: str, prompt: str) -> str:
    """Claude Vision API로 이미지 분석"""
    from bot.services.claude_service import _get_client, SONNET_MODEL, CLAUDE_MAX_TOKENS, _log_api_usage
    from bot.config import CLAUDE_SYSTEM_PROMPT

    client = _get_client()

    system_text = (
        CLAUDE_SYSTEM_PROMPT
        + "\n\n[이미지 분석 지침]"
        "\n이미지를 분석할 때는 핵심 내용을 간결하게 전달해주세요."
        "\n명함이면 연락처 정보를 정리해주세요."
        "\n문서/스크린샷이면 주요 내용을 요약해주세요."
        "\n차트/그래프면 데이터 포인트와 트렌드를 설명해주세요."
    )

    response = await client.messages.create(
        model=SONNET_MODEL,
        max_tokens=CLAUDE_MAX_TOKENS,
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_base64,
                    }
                },
                {
                    "type": "text",
                    "text": prompt,
                }
            ]
        }]
    )

    # Log usage
    if hasattr(response, "usage"):
        await _log_api_usage(user_id, response.usage.input_tokens, response.usage.output_tokens, 0, SONNET_MODEL)

    text_parts = [block.text for block in response.content if hasattr(block, "text") and block.text]
    return "\n".join(text_parts) if text_parts else "이미지를 분석할 수 없었어요."
