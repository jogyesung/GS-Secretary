"""스케줄 잡 공통 유틸 - 실행 결과 기록(job_runs) + 실패 시 관리자 DM"""

import functools
import logging
import traceback
from datetime import datetime, timezone, timedelta

import aiosqlite

from bot.config import ALLOWED_USER_IDS, DATABASE_PATH

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


async def record_job_result(job_name: str, ok: bool, error: str = ""):
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                "INSERT INTO job_runs (job_name, last_run_at, last_status, last_error) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(job_name) DO UPDATE SET last_run_at=excluded.last_run_at, "
                "last_status=excluded.last_status, last_error=excluded.last_error",
                (job_name, datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "ok" if ok else "error", error[:500]),
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"job_runs 기록 실패 ({job_name}): {e}")


async def get_job_status() -> list[dict]:
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            rows = await db.execute_fetchall(
                "SELECT job_name, last_run_at, last_status, last_error FROM job_runs ORDER BY job_name"
            )
        return [{"job": r[0], "last_run": r[1], "status": r[2], "error": r[3]} for r in rows]
    except Exception:
        return []


async def notify_admin(bot, text: str):
    """첫 번째 허용 사용자에게 DM (운영 알림용)"""
    if not ALLOWED_USER_IDS:
        return
    try:
        await bot.send_message(chat_id=ALLOWED_USER_IDS[0], text=text[:4000])
    except Exception as e:
        logger.error(f"관리자 알림 실패: {e}")


def job_guard(job_name: str, notify: bool = True):
    """잡 래퍼: 예외를 삼키지 않고 기록 + 관리자 DM. 잡 자체가 죽어 다음 실행이 취소되는 일을 막는다."""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(context, *args, **kwargs):
            try:
                result = await func(context, *args, **kwargs)
                await record_job_result(job_name, True)
                return result
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                logger.error(f"잡 실패 [{job_name}]: {err}\n{traceback.format_exc()}")
                await record_job_result(job_name, False, err)
                if notify:
                    await notify_admin(context.bot, f"⚠️ 스케줄 잡 실패: {job_name}\n{err[:300]}")
        return wrapper

    return decorator
