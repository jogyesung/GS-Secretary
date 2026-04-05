"""메모 CRUD 서비스"""

from bot.database import get_db


async def create_memo(user_id: int, content: str) -> int:
    """메모 생성, 생성된 메모 ID 반환"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO memos (user_id, content) VALUES (?, ?)",
            (user_id, content),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_memos(user_id: int, limit: int = 20) -> list[dict]:
    """사용자의 메모 목록 조회"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, content, created_at FROM memos WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [{"id": row[0], "content": row[1], "created_at": row[2]} for row in rows]
    finally:
        await db.close()


async def search_memos(user_id: int, keyword: str) -> list[dict]:
    """메모 검색"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, content, created_at FROM memos WHERE user_id = ? AND content LIKE ? ORDER BY created_at DESC",
            (user_id, f"%{keyword}%"),
        )
        rows = await cursor.fetchall()
        return [{"id": row[0], "content": row[1], "created_at": row[2]} for row in rows]
    finally:
        await db.close()


async def delete_memo(user_id: int, memo_id: int) -> bool:
    """메모 삭제"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM memos WHERE id = ? AND user_id = ?",
            (memo_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()
