"""포트폴리오 핸들러 - 종목 등록/조회/삭제 + DART 공시 알림 대상 관리 + 공시 무음 버튼"""

import logging
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import DART_API_KEY
from bot.middleware.auth_middleware import authorized_only
from bot.services import dart_service
from bot.utils.telegram_fmt import reply_html

logger = logging.getLogger(__name__)

MUTED_MARK = "🔕 무음 처리됨"


@authorized_only
async def portfolio_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/portfolio_add <종목명> - 포트폴리오에 종목 추가"""
    user_id = update.effective_user.id
    corp_name = " ".join(context.args).strip() if context.args else ""

    if not corp_name:
        await reply_html(update, "사용법: /portfolio_add <종목명>\n예: /portfolio_add 삼성전자")
        return

    if not DART_API_KEY:
        await reply_html(
            update,
            "⚠️ DART_API_KEY가 설정되지 않았어요.\n"
            "opendart.fss.or.kr에서 무료 API 키를 발급받아 환경변수에 추가해주세요."
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # 중복 확인
    existing = await dart_service.list_stocks(user_id)
    if any(s["corp_name"] == corp_name for s in existing):
        await reply_html(update, f"이미 등록된 종목이에요: {corp_name}")
        return

    match = await dart_service.find_corp_code(corp_name)
    if not match:
        await reply_html(
            update,
            f"😥 '{corp_name}' 기업을 DART에서 찾지 못했어요.\n정확한 법인명으로 다시 시도해주세요."
        )
        return

    stock_id = await dart_service.add_stock(user_id, match["corp_name"], match["corp_code"])
    stock_info = f" (종목코드 {match['stock_code']})" if match.get("stock_code") else " (비상장)"
    await reply_html(
        update,
        f"✅ 포트폴리오에 추가했어요! [#{stock_id}]\n"
        f"📈 {match['corp_name']}{stock_info}\n"
        f"새 공시가 올라오면 바로 알려드릴게요 🔔"
    )


@authorized_only
async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/portfolio - 포트폴리오 목록"""
    user_id = update.effective_user.id
    stocks = await dart_service.list_stocks(user_id)

    if not stocks:
        await reply_html(
            update,
            "포트폴리오가 비어있어요.\n/portfolio_add <종목명> 으로 추가해보세요!"
        )
        return

    lines = [f"📊 **내 포트폴리오** ({len(stocks)}종목)\n"]
    for s in stocks:
        lines.append(f"• #{s['id']} {s['corp_name']}")
    lines.append("\n긴급 공시는 바로, 정기보고서 등은 아침 다이제스트로 알려드려요 🔔")
    await reply_html(update, "\n".join(lines))


@authorized_only
async def portfolio_del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/portfolio_del <ID> - 종목 삭제"""
    user_id = update.effective_user.id

    if not context.args or not context.args[0].isdigit():
        await reply_html(update, "사용법: /portfolio_del <ID>\n(/portfolio 에서 ID 확인)")
        return

    stock_id = int(context.args[0])
    if await dart_service.remove_stock(user_id, stock_id):
        await reply_html(update, f"🗑️ #{stock_id} 종목을 삭제했어요.")
    else:
        await reply_html(update, f"#{stock_id} 종목을 찾지 못했어요.")


@authorized_only
async def portfolio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """공시 알림의 인라인 버튼 처리 (dart:mute:{corp_code}:{keyword})"""
    query = update.callback_query
    data = query.data or ""
    parts = data.split(":", 3)
    if len(parts) < 4 or parts[0] != "dart" or parts[1] != "mute":
        await query.answer()
        return

    corp_code, keyword = parts[2].strip(), parts[3].strip()
    if not keyword:
        await query.answer("무음 처리할 유형을 알 수 없어요 😥", show_alert=True)
        return

    user_id = update.effective_user.id
    try:
        await dart_service.add_mute(user_id, corp_code or None, keyword)
    except Exception as e:
        logger.error(f"공시 무음 설정 실패 ({user_id}, {corp_code}, {keyword}): {e}")
        await query.answer("무음 설정에 실패했어요 😥", show_alert=True)
        return

    await query.answer("이 유형 알림을 끌게요")

    msg = query.message
    if msg is None or not msg.text or MUTED_MARK in msg.text:
        return

    # 무음 버튼은 빼고 원문 링크 버튼만 남김
    keep_rows = []
    if msg.reply_markup:
        for row in msg.reply_markup.inline_keyboard:
            kept = [b for b in row if b.url]
            if kept:
                keep_rows.append(kept)
    markup = InlineKeyboardMarkup(keep_rows) if keep_rows else None

    try:
        await query.edit_message_text(
            text=f"{msg.text_html}\n\n{MUTED_MARK}", parse_mode="HTML",
            reply_markup=markup, disable_web_page_preview=True,
        )
    except Exception as e:
        logger.debug(f"공시 알림 메시지 편집 실패 (HTML): {e}")
        try:
            await query.edit_message_text(
                text=f"{msg.text}\n\n{MUTED_MARK}", reply_markup=markup,
                disable_web_page_preview=True,
            )
        except Exception as e2:
            logger.warning(f"공시 알림 메시지 편집 실패: {e2}")
