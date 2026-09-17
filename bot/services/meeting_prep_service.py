"""미팅 준비 브리핑 서비스 - 곧 시작하는 일정의 참석자 메일·관련 문서·메모를 모아 Haiku로 요약"""

import logging
import re
from datetime import datetime, timedelta, timezone

import aiosqlite
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import ANTHROPIC_API_KEY, DATABASE_PATH, MEETING_PREP_MINUTES

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

MAX_ATTENDEES = 3
MAX_MAILS_PER_ATTENDEE = 5
MAX_DRIVE_FILES = 5
MAX_DRIVE_BUTTONS = 3
MAX_MEMOS = 5

# 회의실·그룹 캘린더 주소는 참석자로 취급하지 않음
_NON_PERSON_SUFFIXES = ("calendar.google.com",)
# 제목에서 검색 키워드로 쓰기엔 의미 없는 단어
_STOPWORDS = {
    "미팅", "회의", "면담", "통화", "콜", "call", "meeting", "sync", "with", "w/", "mtg",
    "및", "관련", "the", "and", "re", "re:", "fw", "fw:", "1:1", "주간", "정기", "kick-off", "킥오프",
}

PREP_SYSTEM = (
    "당신은 사모펀드 운용역의 업무 비서 '카리나'예요. 밝고 간결한 '~요' 체를 써요.\n"
    "제공된 자료에 없는 사실은 절대 지어내지 말고, 자료가 없으면 '확인된 내용이 없어요'라고 말해요."
)
PREP_PROMPT = (
    "다음 미팅 준비 브리핑을 5줄 이내로: 마지막 연락 내용, 열린 이슈, 참고할 문서. "
    "확인되지 않은 건 추측하지 말 것. 각 줄은 '• '로 시작하고 서론·결론 없이 줄만 출력."
)

_primary_email: str | None = None


async def _get_primary_email() -> str:
    """사용자 본인 주소 (primary 캘린더 ID). 조회 실패 시 빈 문자열, 다음에 재시도."""
    global _primary_email
    if _primary_email is not None:
        return _primary_email
    try:
        from bot.services.calendar_service import list_calendars

        cals = await list_calendars()
        _primary_email = next((c["id"] for c in cals if c.get("primary")), "")
    except Exception as e:
        logger.debug(f"primary 캘린더 조회 실패: {e}")
        return ""
    return _primary_email


def parse_start(start: str) -> datetime | None:
    """캘린더 start 문자열(ISO, KST) → aware datetime. 종일 일정이면 None."""
    if not start or "T" not in start:
        return None
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _has_prep_material(event: dict) -> bool:
    return bool(
        event.get("attendees")
        or (event.get("description") or "").strip()
        or (event.get("location") or "").strip()
    )


def upcoming_events(events: list[dict], now: datetime, minutes: int = MEETING_PREP_MINUTES) -> list[dict]:
    """[now, now+minutes] 안에 시작하는, 준비 자료가 있을 만한 일정만"""
    window_end = now + timedelta(minutes=minutes)
    result = []
    for ev in events:
        start = parse_start(ev.get("start", ""))
        if start is None or not (now <= start <= window_end):
            continue
        if (ev.get("status") or "") == "cancelled":
            continue
        if not _has_prep_material(ev):
            continue
        result.append(ev)
    return result


def first_meaningful_word(summary: str) -> str:
    """제목에서 메모 검색용 첫 의미 단어 (2자 이상, 불용어 제외)"""
    tokens = re.split(r"[\s\[\]()<>/,:;|\-–—·]+", summary or "")
    for tok in tokens:
        t = tok.strip().strip("\"'")
        if len(t) >= 2 and t.lower() not in _STOPWORDS:
            return t
    return (summary or "").strip()[:20]


def _external_attendees(event: dict, primary_email: str) -> list[str]:
    own = {primary_email.lower(), (event.get("calendar_id") or "").lower()}
    result = []
    for email in event.get("attendees") or []:
        e = (email or "").strip().lower()
        if not e or e in own or e.endswith(_NON_PERSON_SUFFIXES):
            continue
        if e not in result:
            result.append(e)
    return result


# === 발송 기록 ===
async def is_sent(event_id: str) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT 1 FROM meeting_prep_sent WHERE event_id = ?", (event_id,)
        )
        return len(rows) > 0


async def mark_sent(event_id: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO meeting_prep_sent (event_id) VALUES (?)", (event_id,)
        )
        await db.execute(
            "DELETE FROM meeting_prep_sent WHERE created_at < datetime('now', '-14 days')"
        )
        await db.commit()


# === 자료 수집 ===
async def gather_context(event: dict, user_ids: list[int]) -> dict:
    """참석자별 최근 메일 + Drive 전문검색 + 메모. 각 소스는 실패해도 나머지 진행."""
    primary = await _get_primary_email()
    attendees = _external_attendees(event, primary)
    summary = (event.get("summary") or "").strip()

    mails: list[tuple[str, list[dict]]] = []
    try:
        from bot.services.gmail_service import search_emails

        for email in attendees[:MAX_ATTENDEES]:
            try:
                found = await search_emails(
                    f"from:{email} OR to:{email} newer_than:60d", MAX_MAILS_PER_ATTENDEE
                )
            except Exception as e:
                logger.warning(f"미팅 준비 메일 검색 실패 ({email}): {e}")
                found = []
            if found:
                mails.append((email, found))
    except Exception as e:
        logger.warning(f"미팅 준비 Gmail 사용 불가: {e}")

    files: list[dict] = []
    if summary:
        try:
            from bot.services.drive_service import fulltext_search

            safe_query = summary.replace("'", " ").replace("\\", " ")[:80]
            files = await fulltext_search(safe_query, MAX_DRIVE_FILES)
        except Exception as e:
            logger.warning(f"미팅 준비 Drive 검색 실패: {e}")

    memos: list[dict] = []
    keyword = first_meaningful_word(summary)
    if keyword:
        try:
            from bot.services.memo_service import search_memos

            seen_ids = set()
            for uid in user_ids:
                for m in await search_memos(uid, keyword):
                    if m["id"] not in seen_ids:
                        seen_ids.add(m["id"])
                        memos.append(m)
                    if len(memos) >= MAX_MEMOS:
                        break
                if len(memos) >= MAX_MEMOS:
                    break
        except Exception as e:
            logger.warning(f"미팅 준비 메모 검색 실패: {e}")

    return {"attendees": attendees, "mails": mails, "files": files, "memos": memos}


def _serialize_context(event: dict, ctx: dict) -> str:
    lines = [f"[미팅] {event.get('summary', '')}"]
    if event.get("location"):
        lines.append(f"[장소] {event['location']}")
    if (event.get("description") or "").strip():
        lines.append(f"[일정 설명] {event['description'].strip()[:600]}")
    if ctx["attendees"]:
        lines.append(f"[참석자] {', '.join(ctx['attendees'])}")

    lines.append("\n[참석자별 최근 메일 (최신순)]")
    if ctx["mails"]:
        for email, found in ctx["mails"]:
            lines.append(f"- {email}")
            for em in found:
                snippet = (em.get("snippet") or "").replace("\n", " ")[:200]
                lines.append(f"  · {em.get('date', '')} | {em.get('subject', '')} | {snippet}")
    else:
        lines.append("- (없음)")

    lines.append("\n[관련 Drive 문서]")
    if ctx["files"]:
        for f in ctx["files"]:
            lines.append(f"- {f.get('name', '')} (수정 {str(f.get('modifiedTime', ''))[:10]})")
    else:
        lines.append("- (없음)")

    lines.append("\n[내 메모]")
    if ctx["memos"]:
        for m in ctx["memos"]:
            lines.append(f"- {str(m.get('created_at', ''))[:10]} | {m.get('content', '')[:300]}")
    else:
        lines.append("- (없음)")
    return "\n".join(lines)


def _format_raw(ctx: dict) -> str:
    lines = []
    if ctx["mails"]:
        lines.append("📧 최근 메일")
        for email, found in ctx["mails"]:
            for em in found[:2]:
                lines.append(f"• [{email}] {em.get('date', '')[:10]} {em.get('subject', '')}")
    if ctx["files"]:
        lines.append("📁 관련 문서")
        for f in ctx["files"][:MAX_DRIVE_BUTTONS]:
            lines.append(f"• {f.get('name', '')}")
    if ctx["memos"]:
        lines.append("📝 메모")
        for m in ctx["memos"][:3]:
            lines.append(f"• {m.get('content', '')[:80]}")
    return "\n".join(lines) if lines else "관련 메일·문서·메모를 찾지 못했어요."


async def summarize(event: dict, ctx: dict, user_id: int = 0) -> str:
    """Haiku 요약. 키 없거나 실패하면 원본 목록."""
    has_material = ctx["mails"] or ctx["files"] or ctx["memos"] or (event.get("description") or "").strip()
    if not ANTHROPIC_API_KEY or not has_material:
        return _format_raw(ctx)
    try:
        from bot.services.claude_service import simple_completion

        prompt = f"{PREP_PROMPT}\n\n{_serialize_context(event, ctx)}"
        text = await simple_completion(prompt, system=PREP_SYSTEM, max_tokens=600, user_id=user_id)
        return text.strip() or _format_raw(ctx)
    except Exception as e:
        logger.warning(f"미팅 준비 요약 실패, 원본 목록으로 대체: {e}")
        return _format_raw(ctx)


def _drive_keyboard(files: list[dict]) -> InlineKeyboardMarkup | None:
    rows = []
    for f in files[:MAX_DRIVE_BUTTONS]:
        if not f.get("id"):
            continue
        label = f"📄 {f.get('name', '문서')}"[:40]
        rows.append([InlineKeyboardButton(label, url=f"https://drive.google.com/file/d/{f['id']}/view")])
    return InlineKeyboardMarkup(rows) if rows else None


async def build_prep_message(event: dict, user_ids: list[int], now: datetime) -> tuple[str, InlineKeyboardMarkup | None]:
    """미팅 준비 브리핑 텍스트(Markdown) + Drive 문서 버튼"""
    ctx = await gather_context(event, user_ids)
    body = await summarize(event, ctx, user_ids[0] if user_ids else 0)

    start = parse_start(event.get("start", ""))
    mins = max(1, round((start - now).total_seconds() / 60)) if start else MEETING_PREP_MINUTES
    time_str = start.strftime("%H:%M") if start else ""
    meta = ", ".join(p for p in (time_str, (event.get("location") or "").strip()) if p)

    lines = [f"**🗓 {mins}분 후 미팅: {event.get('summary', '(제목 없음)')}** ({meta})" if meta
             else f"**🗓 {mins}분 후 미팅: {event.get('summary', '(제목 없음)')}**"]
    if ctx["attendees"]:
        shown = ctx["attendees"][:5]
        more = f" 외 {len(ctx['attendees']) - 5}명" if len(ctx["attendees"]) > 5 else ""
        lines.append(f"참석: {', '.join(shown)}{more}")
    lines.append("")
    lines.append(body)
    return "\n".join(lines), _drive_keyboard(ctx["files"])
