"""DART 전자공시 서비스 - 포트폴리오 종목 공시 모니터링 (opendart.fss.or.kr)"""

import logging
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import httpx

from bot.config import DART_API_KEY, DATABASE_PATH, BASE_DIR

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DISCLOSURE_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

CORP_CODE_CACHE = BASE_DIR / "data" / "corp_codes.zip"
CACHE_MAX_AGE_DAYS = 7


# === 기업 고유번호(corp_code) 조회 ===
async def _ensure_corp_code_cache() -> Path | None:
    """DART 전체 기업코드 ZIP 다운로드 (주 1회 갱신)"""
    if not DART_API_KEY:
        return None

    if CORP_CODE_CACHE.exists():
        age = datetime.now().timestamp() - CORP_CODE_CACHE.stat().st_mtime
        if age < CACHE_MAX_AGE_DAYS * 86400:
            return CORP_CODE_CACHE

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(CORP_CODE_URL, params={"crtfc_key": DART_API_KEY})
            resp.raise_for_status()
        # 에러 시 JSON/XML 에러 메시지가 옴 (ZIP이 아님)
        if not resp.content.startswith(b"PK"):
            logger.error(f"DART 기업코드 다운로드 실패: {resp.text[:200]}")
            return None
        CORP_CODE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        CORP_CODE_CACHE.write_bytes(resp.content)
        logger.info("DART 기업코드 캐시 갱신 완료")
        return CORP_CODE_CACHE
    except Exception as e:
        logger.error(f"DART 기업코드 다운로드 실패: {e}")
        return CORP_CODE_CACHE if CORP_CODE_CACHE.exists() else None


async def find_corp_code(corp_name: str) -> dict | None:
    """기업명으로 corp_code 검색. 상장사 우선, 정확 일치 우선."""
    cache = await _ensure_corp_code_cache()
    if not cache:
        return None

    try:
        with zipfile.ZipFile(cache) as zf:
            xml_data = zf.read(zf.namelist()[0])
        root = ET.fromstring(xml_data)

        exact_listed, exact, partial_listed = None, None, None
        name_lower = corp_name.strip().lower()

        for el in root.iter("list"):
            cn = (el.findtext("corp_name") or "").strip()
            stock = (el.findtext("stock_code") or "").strip()
            code = (el.findtext("corp_code") or "").strip()
            cn_lower = cn.lower()

            if cn_lower == name_lower:
                if stock:
                    exact_listed = {"corp_code": code, "corp_name": cn, "stock_code": stock}
                    break
                elif not exact:
                    exact = {"corp_code": code, "corp_name": cn, "stock_code": ""}
            elif name_lower in cn_lower and stock and not partial_listed:
                partial_listed = {"corp_code": code, "corp_name": cn, "stock_code": stock}

        return exact_listed or exact or partial_listed
    except Exception as e:
        logger.error(f"기업코드 검색 실패 ({corp_name}): {e}")
        return None


# === 공시 조회 ===
async def get_recent_disclosures(corp_code: str, days: int = 2) -> list[dict]:
    """특정 기업의 최근 N일 공시 목록"""
    if not DART_API_KEY:
        return []

    now = datetime.now(KST)
    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bgn_de": (now - timedelta(days=days)).strftime("%Y%m%d"),
        "end_de": now.strftime("%Y%m%d"),
        "page_count": "50",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(DART_LIST_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        status = data.get("status")
        if status == "013":  # 조회 데이터 없음
            return []
        if status != "000":
            logger.warning(f"DART API 오류 (status={status}): {data.get('message')}")
            return []

        return [
            {
                "rcept_no": item.get("rcept_no", ""),
                "corp_name": item.get("corp_name", ""),
                "report_nm": item.get("report_nm", ""),
                "rcept_dt": item.get("rcept_dt", ""),
                "flr_nm": item.get("flr_nm", ""),
                "url": DISCLOSURE_VIEW_URL.format(rcept_no=item.get("rcept_no", "")),
            }
            for item in data.get("list", [])
        ]
    except Exception as e:
        logger.error(f"DART 공시 조회 실패 ({corp_code}): {e}")
        return []


# === 포트폴리오 DB ===
async def add_stock(user_id: int, corp_name: str, corp_code: str | None) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO portfolio (user_id, corp_name, corp_code) VALUES (?, ?, ?)",
            (user_id, corp_name, corp_code),
        )
        await db.commit()
        return cursor.lastrowid


async def list_stocks(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT id, corp_name, corp_code FROM portfolio WHERE user_id = ? ORDER BY id",
            (user_id,),
        )
        return [{"id": r[0], "corp_name": r[1], "corp_code": r[2]} for r in rows]


async def remove_stock(user_id: int, stock_id: int) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM portfolio WHERE id = ? AND user_id = ?", (stock_id, user_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_all_portfolio() -> list[dict]:
    """모든 사용자의 포트폴리오 (공시 체크 잡용)"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT user_id, corp_name, corp_code FROM portfolio WHERE corp_code IS NOT NULL"
        )
        return [{"user_id": r[0], "corp_name": r[1], "corp_code": r[2]} for r in rows]


# === 중복 알림 방지 ===
async def is_seen(rcept_no: str) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT 1 FROM dart_seen WHERE rcept_no = ?", (rcept_no,)
        )
        return len(rows) > 0


async def mark_seen(rcept_no: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO dart_seen (rcept_no) VALUES (?)", (rcept_no,)
        )
        # 30일 이상 된 기록 정리
        await db.execute(
            "DELETE FROM dart_seen WHERE created_at < datetime('now', '-30 days')"
        )
        await db.commit()
