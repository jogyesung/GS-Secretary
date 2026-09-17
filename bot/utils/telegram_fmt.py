"""텔레그램 메시지 포맷 유틸 - Markdown → HTML 변환, 긴 메시지 분할, 파싱 실패 시 plain 폴백

Claude/Gemini 응답은 Markdown(**굵게**, `코드`, [링크](url), # 제목, - 불릿)으로 오고,
텔레그램 legacy Markdown은 파일명의 `_` 같은 문자에 자주 깨진다.
모든 발송을 HTML 모드로 통일하고, 그래도 실패하면 plain text로 재전송한다.
"""

import html
import logging
import re

from telegram import Bot, Update
from telegram.error import BadRequest

logger = logging.getLogger(__name__)

TELEGRAM_MAX = 4096
CHUNK_LIMIT = 3800  # HTML 태그 오버헤드 여유

_CODE_BLOCK = re.compile(r"```(?:[\w+-]*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_BOLD_UNDER = re.compile(r"__(.+?)__")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_BULLET = re.compile(r"^(\s*)[-*]\s+", re.MULTILINE)
_HR = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)


def md_to_html(text: str) -> str:
    """Markdown 흔한 문법만 텔레그램 HTML로 변환. 나머지 문자는 전부 escape."""
    if not text:
        return ""

    placeholders: list[str] = []

    def _stash(s: str) -> str:
        placeholders.append(s)
        return f"\x00{len(placeholders) - 1}\x00"

    # 코드 블록/인라인 코드는 먼저 빼두고 (내부는 변환하지 않음)
    text = _CODE_BLOCK.sub(lambda m: _stash(f"<pre>{html.escape(m.group(1).rstrip())}</pre>"), text)
    text = _INLINE_CODE.sub(lambda m: _stash(f"<code>{html.escape(m.group(1))}</code>"), text)
    # 링크도 URL이 escape되지 않도록 먼저 처리
    text = _LINK.sub(
        lambda m: _stash(f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1))}</a>'),
        text,
    )

    text = html.escape(text, quote=False)

    text = _HR.sub("", text)
    text = _HEADING.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _BOLD_UNDER.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _BULLET.sub(lambda m: f"{m.group(1)}• ", text)

    # 빈 줄 3개 이상 → 2개
    text = re.sub(r"\n{3,}", "\n\n", text)

    for i, ph in enumerate(placeholders):
        text = text.replace(f"\x00{i}\x00", ph)
    return text.strip()


def split_chunks(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """문단 → 줄 → 글자 순으로 경계를 찾아 분할"""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


async def send_html(bot: Bot, chat_id: int, text: str, reply_markup=None, **kwargs):
    """Markdown 텍스트를 HTML로 변환해 발송. 길면 분할, 파싱 실패 시 plain 재전송.
    reply_markup은 마지막 조각에만 붙는다. 마지막 메시지 객체를 반환."""
    last = None
    chunks = split_chunks(text or "")
    for i, chunk in enumerate(chunks):
        markup = reply_markup if i == len(chunks) - 1 else None
        try:
            last = await bot.send_message(
                chat_id=chat_id, text=md_to_html(chunk), parse_mode="HTML",
                reply_markup=markup, disable_web_page_preview=True, **kwargs,
            )
        except BadRequest as e:
            logger.warning(f"HTML 파싱 실패, plain 재전송: {e}")
            last = await bot.send_message(
                chat_id=chat_id, text=chunk[:TELEGRAM_MAX], reply_markup=markup,
                disable_web_page_preview=True, **kwargs,
            )
    return last


async def reply_html(update: Update, text: str, reply_markup=None, **kwargs):
    """update에 대한 답장을 HTML로. send_html과 동일한 폴백."""
    msg = update.effective_message
    return await send_html(msg.get_bot(), msg.chat_id, text, reply_markup=reply_markup, **kwargs)
