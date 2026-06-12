"""문서 분석 핸들러 - PDF/DOCX/XLSX/텍스트 파일을 Claude로 분석"""

import logging
import os
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import TEMP_DOWNLOAD_PATH

logger = logging.getLogger(__name__)

MAX_CHARS = 30000  # Claude에 보낼 최대 글자 수
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json", ".py", ".log",
}


def is_analyzable(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in SUPPORTED_EXTENSIONS


async def analyze_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """첨부 문서를 다운로드해 텍스트를 추출하고 카리나가 분석"""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    doc = update.message.document
    user_id = update.effective_user.id
    caption = update.message.caption or "이 문서를 요약해주세요."

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("😥 파일이 너무 커요 (20MB 이하만 분석 가능해요)")
        return

    temp_path = None
    try:
        TEMP_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
        file = await doc.get_file()
        temp_path = TEMP_DOWNLOAD_PATH / f"doc_{update.message.message_id}_{doc.file_name}"
        await file.download_to_drive(str(temp_path))

        text = _extract_text(temp_path)
        if not text or not text.strip():
            await update.message.reply_text(
                "😥 문서에서 텍스트를 추출하지 못했어요. 스캔 이미지 PDF라면 사진으로 보내주시면 분석해드릴게요!"
            )
            return

        truncated = len(text) > MAX_CHARS
        if truncated:
            text = text[:MAX_CHARS]

        response = await _analyze_with_claude(user_id, doc.file_name, text, caption, truncated)

        if len(response) > 4000:
            for i in range(0, len(response), 4000):
                await update.message.reply_text(response[i:i + 4000])
        else:
            await update.message.reply_text(response)

    except Exception as e:
        logger.error(f"문서 분석 실패: {e}")
        await update.message.reply_text(f"❌ 문서 분석 중 오류가 발생했어요: {e}")
    finally:
        if temp_path is not None:
            try:
                if temp_path.exists():
                    os.remove(temp_path)
            except OSError:
                pass


def _extract_text(path: Path) -> str | None:
    """파일 형식별 텍스트 추출"""
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
            for row in sheet.iter_rows(max_rows=200, values_only=True):
                cells = [str(c) for c in row if c is not None]
                if cells:
                    parts.append(" | ".join(cells))
        wb.close()
        return "\n".join(parts)

    # 텍스트 계열 (.txt, .md, .csv, .json, .py, .log)
    return path.read_text(encoding="utf-8", errors="replace")


async def _analyze_with_claude(user_id: int, filename: str, text: str, prompt: str, truncated: bool) -> str:
    """추출된 텍스트를 Sonnet으로 분석 (히스토리와 분리된 단발 호출)"""
    from bot.services.claude_service import _get_client, SONNET_MODEL, _log_api_usage
    from bot.config import CLAUDE_SYSTEM_PROMPT, CLAUDE_MAX_TOKENS

    client = _get_client()

    system_text = (
        CLAUDE_SYSTEM_PROMPT
        + "\n\n[문서 분석 지침]"
        "\n사용자가 보낸 문서의 내용을 분석합니다."
        "\n계약서면 핵심 조건(금액, 기간, 의무사항)을 정리해주세요."
        "\n보고서면 주요 결론과 수치를 요약해주세요."
        "\n표 데이터면 핵심 트렌드를 짚어주세요."
    )

    note = "\n(참고: 문서가 길어 앞부분만 발췌됨)" if truncated else ""
    user_content = f"[파일명: {filename}]{note}\n\n{text}\n\n---\n요청: {prompt}"

    response = await client.messages.create(
        model=SONNET_MODEL,
        max_tokens=CLAUDE_MAX_TOKENS,
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )

    if hasattr(response, "usage"):
        await _log_api_usage(user_id, response.usage.input_tokens, response.usage.output_tokens, 0, SONNET_MODEL)

    text_parts = [block.text for block in response.content if hasattr(block, "text") and block.text]
    return "\n".join(text_parts) if text_parts else "문서를 분석할 수 없었어요."
