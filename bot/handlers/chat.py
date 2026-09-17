"""Claude AI 대화 핸들러 - 도구는 항상 켜고, 모델만 작업 무게로 고른다 (Haiku 기본 / Sonnet 무거운 작업)

이전 3단계 라우팅(Gemini → Haiku 도구 없음 → Sonnet)은 후속 질문("그럼 화요일로 바꿔줘")이
도구 없는 Haiku로 빠져 동작하지 않는 문제가 있었다. 지금은 모든 메시지가 도구를 쓸 수 있고,
Gemini는 web_search 도구와 /search 뒤에서만 쓴다.
"""

import logging
import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot.middleware.auth_middleware import authorized_only
from bot.services import claude_service
from bot.utils.telegram_fmt import reply_html, send_html

logger = logging.getLogger(__name__)

# Sonnet(무거운 모델)이 필요한 작업 — 도구 호출 품질·긴 글 품질이 중요한 경우
_HEAVY_KEYWORDS = [
    # Drive/Gmail 병렬 검색·문서 읽기 (여러 도구 조합)
    "드라이브", "drive", "구글 파일", "구글파일",
    "찾아줘", "검색해줘", "보내줘", "다운로드",
    "문서", "보고서", "자료", "계약서", "offer", "첨부",
    "엑셀", "워드", "pdf",
    "이메일", "메일", "받은편지함", "gmail",
    "일정", "캘린더", "스케줄", "회의", "미팅", "약속",
    # 슬라이드/PPT
    "슬라이드", "slide", "ppt", "프레젠테이션", "덱",
    # 긴 글 생성·분석
    "작성해", "써줘", "초안", "정리해줘", "요약해", "분석해", "검토해", "번역", "비교해",
]


def needs_heavy_model(message: str) -> bool:
    """Sonnet이 필요한 메시지인지 판단. 나머지는 Haiku + 도구."""
    msg_lower = message.lower()
    return any(kw in msg_lower for kw in _HEAVY_KEYWORDS)


async def _send_pending_files(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """대기 중인 파일 전송"""
    for file_path in claude_service.get_pending_files(user_id):
        try:
            with open(file_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=os.path.basename(file_path),
                )
        except Exception as e:
            await update.effective_message.reply_text(f"❌ 파일 전송 실패: {e}")
        finally:
            try:
                os.remove(file_path)
            except OSError:
                pass


async def _send_pending_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """확인이 필요한 작업이 있으면 버튼 표시"""
    action = claude_service.get_pending_action(user_id)
    if not action:
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 실행", callback_data=f"act:ok:{action['token']}"),
        InlineKeyboardButton("❌ 취소", callback_data=f"act:no:{action['token']}"),
    ]])
    await send_html(
        context.bot, update.effective_chat.id,
        "**실행 전 확인이 필요해요**\n\n" + action["summary"],
        reply_markup=keyboard,
    )


async def respond(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """카리나 답변 + 대기 파일 + 확인 버튼을 한 번에 처리"""
    user_id = update.effective_user.id
    await reply_html(update, text)
    await _send_pending_files(update, context, user_id)
    await _send_pending_confirmation(update, context, user_id)


async def handle_text_query(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """텍스트/음성 공용 진입점"""
    user_id = update.effective_user.id
    heavy = needs_heavy_model(text)
    logger.info(f"[{'Sonnet' if heavy else 'Haiku'}+도구] {text[:50]}")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    response = await claude_service.chat(user_id, text, heavy=heavy)
    await respond(update, context, response)


@authorized_only
async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """일반 텍스트 메시지 → 카리나"""
    message = update.message.text or ""
    if not message.strip():
        return
    try:
        await handle_text_query(update, context, message)
    except RuntimeError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        logger.exception("채팅 처리 실패")
        await update.message.reply_text(f"❌ 응답 오류: {e}")


@authorized_only
async def action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """위험 작업 확인/취소 버튼"""
    query = update.callback_query
    await query.answer()
    _, decision, token = query.data.split(":", 2)
    user_id = update.effective_user.id

    action = claude_service.pop_pending_action(user_id, token)
    if not action:
        await query.edit_message_text("⌛ 이미 처리됐거나 만료된 요청이에요. 다시 요청해주세요.")
        return

    if decision != "ok":
        await query.edit_message_text("❌ 취소했어요.")
        return

    try:
        result = await claude_service.execute_confirmed_action(user_id, action)
        await query.edit_message_text(f"✅ 실행 완료\n\n{result[:3500]}")
    except Exception as e:
        logger.exception("확인 작업 실행 실패")
        await query.edit_message_text(f"❌ 실행 실패: {e}")


@authorized_only
async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """대화 히스토리 초기화"""
    user_id = update.effective_user.id
    await claude_service.clear_history(user_id)
    await update.message.reply_text("🗑️ 대화 히스토리가 초기화되었습니다.")
