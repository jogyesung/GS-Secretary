"""Claude AI 대화 핸들러 - 스마트 라우팅: 간단한 질문은 Gemini, 복잡한 업무는 Claude"""

import os
import re
import logging
from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.services import claude_service
from bot.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

# Claude가 필요한 키워드 (Drive, Calendar, PC파일, 메모 등 도구 사용이 필요한 것)
_CLAUDE_KEYWORDS = [
    # Drive 관련
    "드라이브", "drive", "파일", "문서", "보고서", "자료", "엑셀", "워드", "pdf",
    "읽어", "열어", "찾아줘", "검색해줘", "보내줘", "다운",
    # Calendar 관련
    "일정", "캘린더", "스케줄", "회의", "미팅", "약속", "잡아", "등록",
    # 메모 관련
    "메모", "기록", "저장",
    # PC 파일 관련
    "pc", "폴더", "디렉토리",
    # 복잡한 업무
    "요약", "분석", "작성", "번역", "코드", "수정", "만들어",
    "비교", "정리", "계획", "전략", "리뷰",
    # 이전 대화 참조
    "아까", "방금", "이전", "그거", "그것",
]

# Gemini로 처리할 수 있는 간단한 질문 패턴
_SIMPLE_PATTERNS = [
    # 검색/정보 질문
    r"(뭐|무엇|어떤|어디|누구|언제|왜|몇|얼마).*(야|이야|인가|인지|일까|인데|이지)",
    r".*(알려줘|알려주세요|뭐야|뭐지|뭐냐)",
    r".*(날씨|뉴스|주가|환율|시세|가격)",
    r".*(뜻|의미|차이|비교).*",
    r"(추천|좋은|괜찮은|맛집|식당|카페).*",
]


def _needs_claude(message: str) -> bool:
    """Claude API가 필요한 메시지인지 판단"""
    msg_lower = message.lower()

    # Claude 키워드가 포함되어 있으면 Claude 사용
    for kw in _CLAUDE_KEYWORDS:
        if kw in msg_lower:
            return True

    # 대화 히스토리가 있으면 맥락 유지를 위해 Claude 사용
    # (이건 chat handler에서 체크)

    return False


def _is_simple_question(message: str) -> bool:
    """Gemini로 처리할 수 있는 간단한 질문인지 판단"""
    for pattern in _SIMPLE_PATTERNS:
        if re.search(pattern, message):
            return True
    return False


async def _gemini_chat(query: str) -> str:
    """Gemini로 간단한 질문에 답변"""
    from bot.services.gemini_service import _get_client, _gemini_grounding_search, _gemini_regular_search, _duckduckgo_search
    from bot.services.gemini_service import needs_web_search

    client = _get_client()

    # 실시간 정보가 필요한 질문 → grounding 검색
    if needs_web_search(query):
        result = await _gemini_grounding_search(client, query)
        if result:
            return result["answer"]

    # 일반 질문 → Gemini 일반 답변
    result = await _gemini_regular_search(client, query)
    if result:
        # "[Gemini 일반 검색]\n" 접두사 제거
        answer = result["answer"]
        if answer.startswith("[Gemini 일반 검색]\n"):
            answer = answer[len("[Gemini 일반 검색]\n"):]
        return answer

    return None


@authorized_only
async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """스마트 라우팅: 간단한 질문은 Gemini, 복잡한 업무는 Claude"""
    message = update.message.text
    user_id = update.effective_user.id

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # 대화 히스토리가 있으면 맥락 유지를 위해 Claude 사용
        has_history = claude_service.get_history_length(user_id) > 0

        # 라우팅 결정
        use_claude = has_history or _needs_claude(message) or not GEMINI_API_KEY

        if not use_claude and _is_simple_question(message):
            # Gemini로 처리 시도
            logger.info(f"Gemini 라우팅: {message[:50]}")
            try:
                gemini_response = await _gemini_chat(message)
                if gemini_response:
                    if len(gemini_response) > 4000:
                        for i in range(0, len(gemini_response), 4000):
                            await update.message.reply_text(gemini_response[i:i + 4000])
                    else:
                        await update.message.reply_text(gemini_response)
                    return
            except Exception as e:
                logger.warning(f"Gemini 처리 실패, Claude로 폴백: {e}")

        # Claude로 처리
        logger.info(f"Claude 라우팅: {message[:50]}")
        response = await claude_service.chat(user_id, message)

        if len(response) > 4000:
            for i in range(0, len(response), 4000):
                await update.message.reply_text(response[i:i + 4000])
        else:
            await update.message.reply_text(response)

        # 대기 중인 파일 전송
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

    except RuntimeError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ 응답 오류: {e}")


@authorized_only
async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """대화 히스토리 초기화"""
    user_id = update.effective_user.id
    claude_service.clear_history(user_id)
    await update.message.reply_text("🗑️ 대화 히스토리가 초기화되었습니다.")
