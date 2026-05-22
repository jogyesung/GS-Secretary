"""음성 메시지 핸들러 - 음성 → 텍스트 변환 → 카리나 응답"""

import logging
import os
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from bot.middleware.auth_middleware import authorized_only
from bot.config import GEMINI_API_KEY, GEMINI_MODEL, TEMP_DOWNLOAD_PATH
from bot.services import claude_service

logger = logging.getLogger(__name__)


@authorized_only
async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """음성 메시지를 텍스트로 변환 후 카리나에게 전달"""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    temp_path = None
    try:
        # 1. Download voice file
        TEMP_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
        file = await voice.get_file()
        temp_path = TEMP_DOWNLOAD_PATH / f"voice_{update.message.message_id}.ogg"
        await file.download_to_drive(str(temp_path))

        # 2. Transcribe using Gemini (primary), OpenAI Whisper (fallback)
        transcribed_text = await _transcribe_with_gemini(temp_path)

        if transcribed_text is None:
            transcribed_text = await _transcribe_with_whisper(temp_path)

        # 3. Clean up temp file
        if temp_path.exists():
            os.remove(temp_path)
            temp_path = None

        if not transcribed_text:
            await update.message.reply_text("🎤 음성을 인식하지 못했어요. 다시 말씀해주세요!")
            return

        # 4. Show transcription
        await update.message.reply_text(f"🎤 *인식된 음성:* {transcribed_text}", parse_mode="Markdown")

        # 5. Route through the same 3-tier chat routing (Gemini → Haiku → Sonnet)
        from bot.handlers.chat import _needs_tools, _can_use_gemini, _gemini_chat, _send_response, _send_pending_files

        user_id = update.effective_user.id
        has_history = claude_service.get_history_length(user_id) > 0
        needs_tool = _needs_tools(transcribed_text)

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

        if needs_tool:
            logger.info(f"[Voice→Sonnet+도구] {transcribed_text[:50]}")
            response = await claude_service.chat(user_id, transcribed_text, use_tools=True)
            await _send_response(update, response)
            await _send_pending_files(update, context, user_id)
        elif not has_history and GEMINI_API_KEY and _can_use_gemini(transcribed_text):
            logger.info(f"[Voice→Gemini] {transcribed_text[:50]}")
            try:
                gemini_response = await _gemini_chat(transcribed_text)
                if gemini_response:
                    await _send_response(update, gemini_response)
                else:
                    response = await claude_service.chat(user_id, transcribed_text, use_tools=False)
                    await _send_response(update, response)
            except Exception as e:
                logger.warning(f"Gemini 실패, Haiku 폴백: {e}")
                response = await claude_service.chat(user_id, transcribed_text, use_tools=False)
                await _send_response(update, response)
        else:
            logger.info(f"[Voice→Haiku] {transcribed_text[:50]}")
            response = await claude_service.chat(user_id, transcribed_text, use_tools=False)
            await _send_response(update, response)

    except RuntimeError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        logger.error(f"음성 메시지 처리 실패: {e}")
        await update.message.reply_text(f"❌ 음성 처리 중 오류가 발생했어요: {e}")
    finally:
        # Clean up on error
        if temp_path is not None:
            try:
                if temp_path.exists():
                    os.remove(temp_path)
            except OSError:
                pass


async def _transcribe_with_gemini(audio_path: Path) -> str | None:
    """Gemini API로 음성 파일을 텍스트로 변환"""
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY 없음 - Gemini 음성 변환 불가")
        return None

    try:
        import base64
        from google import genai

        client = genai.Client(api_key=GEMINI_API_KEY)

        audio_bytes = audio_path.read_bytes()
        audio_b64 = base64.b64encode(audio_bytes).decode()

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                {
                    "parts": [
                        {
                            "text": (
                                "이 음성 파일의 내용을 정확하게 받아적어주세요. "
                                "음성 내용만 출력하고, 설명이나 추가 텍스트는 넣지 마세요."
                            )
                        },
                        {
                            "inline_data": {
                                "mime_type": "audio/ogg",
                                "data": audio_b64,
                            }
                        },
                    ]
                }
            ],
        )

        text = response.text.strip() if response.text else None
        logger.info(f"Gemini 음성 변환 결과: {text[:100] if text else 'None'}")
        return text

    except Exception as e:
        logger.error(f"Gemini 음성 변환 실패: {e}")
        return None


async def _transcribe_with_whisper(audio_path: Path) -> str | None:
    """OpenAI Whisper API로 음성 파일을 텍스트로 변환 (폴백)"""
    try:
        import openai
    except ImportError:
        logger.debug("openai 패키지 미설치 - Whisper 폴백 불가")
        return None

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        logger.debug("OPENAI_API_KEY 없음 - Whisper 폴백 불가")
        return None

    try:
        client = openai.OpenAI(api_key=api_key)

        with open(audio_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="ko",
            )

        text = transcript.text.strip() if transcript.text else None
        logger.info(f"Whisper 음성 변환 결과: {text[:100] if text else 'None'}")
        return text

    except Exception as e:
        logger.error(f"Whisper 음성 변환 실패: {e}")
        return None
