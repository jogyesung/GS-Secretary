"""리마인더 서비스 - DB CRUD 및 스케줄러 관리 (모든 시각은 KST, naive ISO 문자열로 저장)"""

import re
from datetime import datetime, timedelta, timezone
from bot.database import get_db

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """KST 현재 시각 (tz 없는 naive datetime — DB 저장 형식과 맞춤)"""
    return datetime.now(KST).replace(tzinfo=None)


def next_daily_run(hour: int, minute: int) -> datetime:
    """오늘 또는 내일의 HH:MM (KST)"""
    now = now_kst()
    run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run <= now:
        run += timedelta(days=1)
    return run


def parse_time_string(time_str: str) -> timedelta | None:
    """시간 문자열 파싱 (예: '30m', '2h', '1d')"""
    match = re.match(r"^(\d+)([mhd])$", time_str.strip().lower())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    if unit == "m":
        return timedelta(minutes=value)
    elif unit == "h":
        return timedelta(hours=value)
    elif unit == "d":
        return timedelta(days=value)
    return None


def _iso(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(KST).replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat()


async def create_reminder(user_id: int, message: str, remind_at: datetime) -> int:
    """일회성 리마인더 생성"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO reminders (user_id, message, remind_at) VALUES (?, ?, ?)",
            (user_id, message, _iso(remind_at)),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def create_recurring_reminder(
    user_id: int, message: str, cron_expr: str, next_run: datetime
) -> int:
    """반복 리마인더 생성"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO reminders (user_id, message, remind_at, is_recurring, cron_expr) VALUES (?, ?, ?, 1, ?)",
            (user_id, message, _iso(next_run), cron_expr),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_active_reminders(user_id: int) -> list[dict]:
    """활성 리마인더 목록 조회"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, message, remind_at, is_recurring, cron_expr FROM reminders WHERE user_id = ? AND is_active = 1 ORDER BY remind_at",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "message": row[1],
                "remind_at": row[2],
                "is_recurring": bool(row[3]),
                "cron_expr": row[4],
            }
            for row in rows
        ]
    finally:
        await db.close()


async def delete_reminder(user_id: int, reminder_id: int) -> bool:
    """리마인더 삭제 (비활성화)"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE reminders SET is_active = 0 WHERE id = ? AND user_id = ?",
            (reminder_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def get_due_reminders() -> list[dict]:
    """만료된 리마인더 조회 (KST 기준)"""
    now = _iso(now_kst())
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, user_id, message, is_recurring, cron_expr FROM reminders WHERE is_active = 1 AND remind_at <= ?",
            (now,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "user_id": row[1],
                "message": row[2],
                "is_recurring": bool(row[3]),
                "cron_expr": row[4],
            }
            for row in rows
        ]
    finally:
        await db.close()


async def deactivate_reminder(reminder_id: int):
    """리마인더 비활성화"""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE reminders SET is_active = 0 WHERE id = ?",
            (reminder_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def update_next_run(reminder_id: int, next_run: datetime):
    """반복 리마인더의 다음 실행 시간 업데이트"""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE reminders SET remind_at = ? WHERE id = ?",
            (_iso(next_run), reminder_id),
        )
        await db.commit()
    finally:
        await db.close()
