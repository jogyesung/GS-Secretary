"""아침 종목 리포트 핸들러 - 증권사 리포트 + 종목 뉴스 취합 → AI 상세검토 → 브리핑 발송"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot.config import ALLOWED_USER_IDS
from bot.middleware.auth_middleware import authorized_only
from bot.services import stock_research_service
from bot.utils.jobs import job_guard
from bot.utils.telegram_fmt import send_html

logger = logging.getLogger(__name__)


async def _build_report_text() -> str:
    """수집 → AI 검토 → 리포트 원문 PDF Drive 아카이빙까지 수행한 브리핑 텍스트 생성"""
    collected = await stock_research_service.collect_watchlist_data()
    if not any(s["reports"] or s["news"] for s in collected):
        raise RuntimeError("수집된 리포트/뉴스가 없습니다.")
    text = await stock_research_service.build_morning_report(collected)

    # 리포트 원문 PDF를 Drive에 아카이빙 (실패해도 브리핑 발송은 계속)
    try:
        from datetime import datetime
        from bot.config import (
            STOCK_REPORT_SAVE_PDF, STOCK_REPORT_DRIVE_FOLDER, TEMP_DOWNLOAD_PATH,
        )
        if STOCK_REPORT_SAVE_PDF and STOCK_REPORT_DRIVE_FOLDER:
            # 서버 디스크는 휘발성이므로 임시 폴더에 받고 Drive 업로드가 목적
            dest = TEMP_DOWNLOAD_PATH / "stock_reports" / datetime.now(
                stock_research_service.KST
            ).strftime("%Y-%m-%d")
            text += await stock_research_service.archive_report_pdfs(
                collected, dest, STOCK_REPORT_DRIVE_FOLDER
            )
    except Exception as e:
        logger.warning(f"리포트 PDF 아카이빙 실패: {e}")

    return text


@job_guard("morning_stock_report")
async def morning_stock_report(context: ContextTypes.DEFAULT_TYPE):
    """평일 아침 종목 리포트 브리핑 (자동 스케줄)"""
    text = await _build_report_text()

    for user_id in ALLOWED_USER_IDS:
        try:
            await send_html(context.bot, user_id, text)
            logger.info(f"아침 종목 리포트 발송 완료: {user_id}")
        except Exception as e:
            logger.error(f"아침 종목 리포트 발송 실패 ({user_id}): {e}")


@authorized_only
async def stock_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stock_report - 수동 종목 리포트 요청"""
    names = ", ".join(s["name"] for s in stock_research_service.get_watchlist())
    await update.message.reply_text(
        f"📈 {names} 리포트와 뉴스를 모아서 검토할게요. 1분 정도만 기다려주세요!"
    )
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        text = await _build_report_text()
    except Exception as e:
        logger.error(f"종목 리포트 생성 실패: {e}")
        await update.message.reply_text("😥 리포트/뉴스 수집에 실패했어요. 잠시 후 다시 시도해주세요.")
        return

    await send_html(context.bot, update.effective_chat.id, text)
