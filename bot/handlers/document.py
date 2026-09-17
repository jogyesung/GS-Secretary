"""문서 분석 핸들러 - PDF/DOCX/XLSX/텍스트 파일을 Claude로 분석 (대화 히스토리에 포함 → 후속 질문 가능)"""

import asyncio
import logging
import os
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import TEMP_DOWNLOAD_PATH
from bot.middleware.auth_middleware import authorized_only
from bot.services import claude_service

logger = logging.getLogger(__name__)

MAX_CHARS = 30000  # Claude에 보낼 최대 글자 수
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json", ".py", ".log",
}

_DOC_GUIDE = (
    "\n\n(문서 분석 지침: 계약서면 핵심 조건(금액, 기간, 의무사항), 보고서면 주요 결론과 수치, "
    "표 데이터면 핵심 트렌드를 정리. 답변에 이 지침을 언급하지 마세요.)"
)


def is_analyzable(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in SUPPORTED_EXTENSIONS


@authorized_only
async def analyze_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """첨부 문서를 다운로드해 텍스트를 추출하고 카리나가 분석"""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    doc = update.message.document
    user_id = update.effective_user.id
    caption = update.message.caption or "이 문서를 요약해주세요."
    safe_name = Path(doc.file_name or "file").name or "file"

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("😥 파일이 너무 커요 (20MB 이하만 분석 가능해요)")
        return

    temp_path = None
    try:
        TEMP_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
        file = await doc.get_file()
        temp_path = TEMP_DOWNLOAD_PATH / f"doc_{update.message.message_id}_{safe_name}"
        await file.download_to_drive(str(temp_path))

        # PDF/DOCX 파싱은 CPU 작업 → 스레드에서 (이벤트 루프 안 막음)
        text = await asyncio.to_thread(_extract_text, temp_path)
        if not text or not text.strip():
            await update.message.reply_text(
                "😥 문서에서 텍스트를 추출하지 못했어요. 스캔 이미지 PDF라면 사진으로 보내주시면 분석해드릴게요!"
            )
            return

        truncated = len(text) > MAX_CHARS
        if truncated:
            text = text[:MAX_CHARS]
        note = "\n(참고: 문서가 길어 앞부분만 발췌됨)" if truncated else ""

        # 문서 본문을 대화 턴에 포함 → "여기서 EBITDA 얼마야?" 같은 후속 질문 가능 (캐시로 재전송 비용 0.1x)
        message = f"[파일명: {safe_name}]{note}\n\n{text}\n\n---\n요청: {caption}{_DOC_GUIDE}"
        response = await claude_service.chat(user_id, message, heavy=True)

        from bot.handlers.chat import respond
        await respond(update, context, response)

    except Exception as e:
        logger.exception("문서 분석 실패")
        await update.message.reply_text(f"❌ 문서 분석 중 오류가 발생했어요: {e}")
    finally:
        if temp_path is not None:
            try:
                if temp_path.exists():
                    os.remove(temp_path)
            except OSError:
                pass


def _extract_text(path: Path) -> str | None:
    """파일 형식별 텍스트 추출 (동기 — to_thread로 호출)"""
    ext = path.suffix.lower()

    if ext == ".pdf":
        from PyPDF2 import PdfReader
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if ext == ".docx":
        from docx import Document
        d = Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return "\n".join(parts)

    if ext == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(str(path), read_only=True, data_only=True)
        parts = []
        for sheet in wb.worksheets:
            parts.append(f"[시트: {sheet.title}]")
            for row in sheet.iter_rows(max_row=200, values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    parts.append(" | ".join(cells))
        wb.close()
        return "\n".join(parts)

    return path.read_text(encoding="utf-8", errors="replace")
