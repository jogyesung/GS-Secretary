"""파일 변경 감시 서비스 - watchdog 기반"""

import asyncio
import logging
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from bot.config import ALLOWED_PATHS
from bot.database import get_db

logger = logging.getLogger(__name__)

# 전역 Observer 인스턴스
_observer: Observer | None = None
_watches: dict[str, any] = {}  # path -> watch handle


class _ChangeHandler(FileSystemEventHandler):
    """파일 변경 이벤트 핸들러"""

    def __init__(self, callback):
        self.callback = callback

    def on_created(self, event):
        if not event.is_directory:
            self.callback("생성", event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.callback("수정", event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self.callback("삭제", event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self.callback("이동", f"{event.src_path} → {event.dest_path}")


def _is_path_allowed(path: str) -> bool:
    resolved = Path(path).resolve()
    return any(
        str(resolved).startswith(str(allowed.resolve()))
        for allowed in ALLOWED_PATHS
    )


def start_observer():
    """Observer 시작"""
    global _observer
    if _observer is None:
        _observer = Observer()
        _observer.start()
        logger.info("파일 감시 Observer 시작됨")


def stop_observer():
    """Observer 중지"""
    global _observer
    if _observer:
        _observer.stop()
        _observer.join()
        _observer = None
        _watches.clear()
        logger.info("파일 감시 Observer 중지됨")


def add_watch(path: str, callback) -> bool:
    """디렉토리 감시 추가"""
    if not _is_path_allowed(path):
        raise PermissionError(f"접근이 허용되지 않은 경로입니다: {path}")

    if not Path(path).is_dir():
        raise FileNotFoundError(f"디렉토리를 찾을 수 없습니다: {path}")

    if path in _watches:
        return False  # 이미 감시 중

    start_observer()
    handler = _ChangeHandler(callback)
    watch = _observer.schedule(handler, path, recursive=True)
    _watches[path] = watch
    return True


def remove_watch(path: str) -> bool:
    """디렉토리 감시 제거"""
    if path not in _watches:
        return False

    if _observer:
        _observer.unschedule(_watches[path])
    del _watches[path]
    return True


def get_active_watches() -> list[str]:
    """현재 감시 중인 경로 목록"""
    return list(_watches.keys())


async def save_watch_to_db(user_id: int, path: str) -> int:
    """감시 경로를 DB에 저장"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO watches (user_id, path) VALUES (?, ?)",
            (user_id, path),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_user_watches(user_id: int) -> list[dict]:
    """사용자의 감시 경로 목록 조회"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, path, is_active FROM watches WHERE user_id = ? AND is_active = 1",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [{"id": row[0], "path": row[1], "is_active": bool(row[2])} for row in rows]
    finally:
        await db.close()


async def deactivate_watch_in_db(user_id: int, watch_id: int) -> bool:
    """DB에서 감시 비활성화"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE watches SET is_active = 0 WHERE id = ? AND user_id = ?",
            (watch_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()
