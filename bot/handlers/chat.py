"""Claude AI 대화 핸들러 - 3단계 스마트 라우팅: Gemini → Haiku → Sonnet"""

import os
import re
import logging
from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.services import claude_service
from bot.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

# 도구가 필요한 키워드 → Sonnet (도구 호출 품질이 중요)
_TOOL_KEYWORDS = [
    # Drive / 문서 검색 관련
    "드라이브", "drive", "구글 파일", "구글파일",
    "읽어", "열어", "찾아줘", "검색해줘", "보내줘", "다운로드",
    "문서", "보고서", "자료", "파일", "계약서", "offer",
    "엑셀", "워드", "pdf", "첨부",
    # Calendar 관련
    "일정", "캘린더", "스케줄", "회의", "미팅", "약속", "잡아", "등록",
    # Gmail 관련
    "이메일", "메일", "받은편지함", "gmail",
    # 메모 관련
    "메모",
    # PC 파일 관련
    "pc 파일", "pc파일", "로컬 파일", "로컬파일",
    # 슬라이드/PPT
    "슬라이드", "slide", "ppt", "프레젠테이션", "덱",
    # 웹 검색 (도구)
    "검색해", "찾아봐", "알아봐",
]

# Gemini로 처리할 수 있는 패턴 (무료, 가장 저렴)
_GEMINI_PATTERNS = [
    r"(뭐|무엇|어떤|어디|누구|언제|왜|몇|얼마).*(야|이야|인가|인지|일까|인데|이지)",
    r".*(알려줘|알려주세요|뭐야|뭐지|뭐냐)",
    r".*(날씨|뉴스|주가|환율|시세|가격)",
    r".*(뜻|의미|차이).*",
    r"(추천|좋은|괜찮은|맛집|식당|카페).*",
    r"^.{0,50}$",  # 50자 이하 짧은 메시지
]


def _needs_tools(message: str) -> bool:
    """도구 호출이 필요한 메시지인지 판단 → Sonnet 사용"""
    msg_lower = message.lower()
    for kw in _TOOL_KEYWORDS:
        if kw in msg_lower:
            return True
    return False


def _can_use_gemini(message: str) -> bool:
    """Gemini로 처리 가능한 간단한 질문인지 판단"""
    for pattern in _GEMINI_PATTERNS:
        if re.search(pattern, message):
            return True
    return False


async def _gemini_chat(query: str) -> str:
    """Gemini로 간단한 질문에 답변"""
    from bot.services.gemini_service import _get_client, _gemini_grounding_search, _gemini_regular_search
    from bot.services.gemini_service import needs_web_search

    client = _get_client()

    if needs_web_search(query):
        result = await _gemini_grounding_search(client, query)
        if result:
            return result["answer"]

    result = await _gemini_regular_search(client, query)
    if result:
        answer = result["answer"]
        if answer.startswith("[Gemini 일반 검색]\n"):
            answer = answer[len("[Gemini 일반 검색]\n"):]
        return answer

    return None


async def _send_response(update: Update, text: str):
    """긴 응답을 분할 전송"""
    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await update.message.reply_text(text[i:i + 4000])
    else:
        await update.message.reply_text(text)


@authorized_only
async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """3단계 스마트 라우팅: Gemini(무료) → Haiku(저가) → Sonnet(도구용)"""
    message = update.message.text or ""
    user_id = update.effective_user.id

    if not message.strip():
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        has_history = claude_service.get_history_length(user_id) > 0
        needs_tool = _needs_tools(message)

        # === 1단계: 도구가 필요하면 Sonnet (히스토리 있어도 도구 키워드면 Sonnet) ===
        if needs_tool:
            logger.info(f"[Sonnet+도구] {message[:50]}")
            response = await claude_service.chat(user_id, message, use_tools=True)
            await _send_response(update, response)
            await _send_pending_files(update, context, user_id)
            return

        # === 2단계: 히스토리 없고 Gemini 가능하면 Gemini (무료) ===
        if not has_history and GEMINI_API_KEY and _can_use_gemini(message):
            logger.info(f"[Gemini] {message[:50]}")
            try:
                gemini_response = await _gemini_chat(message)
                if gemini_response:
                    await _send_response(update, gemini_response)
                    return
            except Exception as e:
                logger.warning(f"Gemini 실패, Haiku 폴백: {e}")

        # === 3단계: 나머지는 Haiku (도구 없이 가볍게) ===
        logger.info(f"[Haiku] {message[:50]}")
        response = await claude_service.chat(user_id, message, use_tools=False)
        await _send_response(update, response)

    except RuntimeError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ 응답 오류: {e}")


async def _send_pending_files(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """대기 중인 파일 전송"""
    pending_files = claude_service.get_pending_files(user_id)
    for file_path in pending_files:
        try:
            with open(file_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=os.path.basename(file_path),
                )
        except Exception as e:
            await update.message.reply_text(f"❌ 파일 전송 실패: {e}")
        finally:
            try:
                os.remove(file_path)
            except OSError:
                pass


@authorized_only
async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """대화 히스토리 초기화"""
    user_id = update.effective_user.id
    await claude_service.clear_history(user_id)
    await update.message.reply_text("🗑️ 대화 히스토리가 초기화되었습니다.")
