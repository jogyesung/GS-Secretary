from datetime import timedelta

import pytest

from bot.services import reminder_service as rs
from bot.services.file_service import safe_filename


def test_now_kst_is_naive_and_kst():
    from datetime import datetime, timezone
    now = rs.now_kst()
    assert now.tzinfo is None
    utc = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((now - utc) - timedelta(hours=9)) < timedelta(minutes=1)


def test_next_daily_run_is_in_future():
    now = rs.now_kst()
    run = rs.next_daily_run(now.hour, now.minute)
    assert run > now
    assert run - now <= timedelta(days=1)


@pytest.mark.asyncio
async def test_due_reminders_use_kst():
    past = rs.now_kst() - timedelta(minutes=1)
    future = rs.now_kst() + timedelta(hours=1)
    rid_past = await rs.create_reminder(1, "past", past)
    rid_future = await rs.create_reminder(1, "future", future)
    due_ids = {r["id"] for r in await rs.get_due_reminders()}
    assert rid_past in due_ids
    assert rid_future not in due_ids
    await rs.deactivate_reminder(rid_past)
    await rs.delete_reminder(1, rid_future)


@pytest.mark.parametrize("raw,expected", [
    ("report.pdf", "report.pdf"),
    ("..\\..\\startup.bat", "startup.bat"),
    ("../../etc/passwd", "passwd"),
    ("dir/sub/파일 이름.xlsx", "파일 이름.xlsx"),
    ("", "file"),
    (None, "file"),
    ("..", "file"),
])
def test_safe_filename_strips_directories(raw, expected):
    assert safe_filename(raw) == expected
