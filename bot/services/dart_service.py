"""DART 전자공시 서비스 - 포트폴리오 종목 공시 모니터링 (opendart.fss.or.kr)"""

import asyncio
import logging
import re
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

# === 공시 등급 분류 키워드 ===
# urgent: 즉시 알림 (조용시간에도 발송) / digest: 아침 다이제스트로 묶음 / muted: 알림 안 함
URGENT_KEYWORDS = [
    "주요사항보고서", "합병", "분할", "유상증자", "무상증자", "감자", "최대주주", "공개매수",
    "영업정지", "잠정실적", "영업(잠정)실적", "정정", "회생", "파산", "상장폐지", "관리종목",
    "거래정지", "자기주식", "전환사채", "신주인수권", "대규모내부거래", "소송", "횡령", "배임",
]
DIGEST_KEYWORDS = [
    "사업보고서", "반기보고서", "분기보고서", "감사보고서", "주주총회", "배당",
    "증권신고서", "투자설명서", "특수관계인",
]
MUTED_KEYWORDS = ["소유상황보고서", "기업설명회", "IR", "대량보유"]

TIER_URGENT = "urgent"
TIER_DIGEST = "digest"
TIER_MUTED = "muted"

# 무음 버튼 callback_data 한도 (텔레그램 64바이트): "dart:mute:" + corp_code(8) + ":" + keyword
MUTE_KEYWORD_MAX_CHARS = 12
CALLBACK_DATA_MAX_BYTES = 64


def classify_report(report_nm: str) -> str:
    """보고서명 키워드로 등급 판정. 무음 유형(소유상황보고서 등)은 정정 공시여도 무음 유지."""
    name = (report_nm or "").replace(" ", "")
    if any(k in name for k in MUTED_KEYWORDS):
        return TIER_MUTED
    if any(k in name for k in URGENT_KEYWORDS):
        return TIER_URGENT
    return TIER_DIGEST


def _strip_report_prefix(report_nm: str) -> str:
    """'[기재정정]' 같은 대괄호 접두어와 공백 제거"""
    name = re.sub(r"^(\[[^\]]*\]\s*)+", "", report_nm or "")
    return name.replace(" ", "").strip()


def mute_keyword(report_nm: str, corp_code: str = "") -> str:
    """무음 버튼용 짧은 키워드 (보고서 유형의 앞 12자, callback_data 64바이트 이내로 절단)"""
    base = _strip_report_prefix(report_nm) or (report_nm or "").replace(" ", "")
    keyword = base[:MUTE_KEYWORD_MAX_CHARS]
    prefix_bytes = len(f"dart:mute:{corp_code}:".encode("utf-8"))
    while keyword and prefix_bytes + len(keyword.encode("utf-8")) > CALLBACK_DATA_MAX_BYTES:
        keyword = keyword[:-1]
    return keyword


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


def _search_corp_code_sync(cache: Path, corp_name: str) -> dict | None:
    """전체 기업코드 XML(수만 건) 파싱 — 동기, to_thread로 호출"""
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


async def find_corp_code(corp_name: str) -> dict | None:
    """기업명으로 corp_code 검색. 상장사 우선, 정확 일치 우선."""
    cache = await _ensure_corp_code_cache()
    if not cache:
        return None
    try:
        return await asyncio.to_thread(_search_corp_code_sync, cache, corp_name)
    except Exception as e:
        logger.error(f"기업코드 검색 실패 ({corp_name}): {e}")
        return None


# corp_code → stock_code 캐시 (XML 파싱이 무거워서 프로세스 생존 동안 유지)
_STOCK_CODE_CACHE: dict[str, str] = {}


def _search_stock_code_sync(cache: Path, corp_code: str) -> str:
    """corp_code로 6자리 종목코드 찾기 — 동기, to_thread로 호출. 비상장이면 빈 문자열."""
    with zipfile.ZipFile(cache) as zf:
        xml_data = zf.read(zf.namelist()[0])
    root = ET.fromstring(xml_data)
    for el in root.iter("list"):
        if (el.findtext("corp_code") or "").strip() == corp_code:
            return (el.findtext("stock_code") or "").strip()
    return ""


async def get_stock_code(corp_code: str) -> str:
    """corp_code → KRX 종목코드 (상장사만). 실패/비상장이면 빈 문자열."""
    if not corp_code:
        return ""
    if corp_code in _STOCK_CODE_CACHE:
        return _STOCK_CODE_CACHE[corp_code]
    cache = await _ensure_corp_code_cache()
    if not cache:
        return ""
    try:
        stock_code = await asyncio.to_thread(_search_stock_code_sync, cache, corp_code)
    except Exception as e:
        logger.error(f"종목코드 검색 실패 ({corp_code}): {e}")
        return ""
    _STOCK_CODE_CACHE[corp_code] = stock_code
    return stock_code


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


# === 사용자별 무음 설정 ===
async def add_mute(user_id: int, corp_code: str | None, report_keyword: str) -> int:
    """특정 기업(None이면 전체)의 보고서 유형 알림 끄기"""
    keyword = (report_keyword or "").replace(" ", "").strip()
    if not keyword:
        return 0
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT id FROM dart_mutes WHERE user_id = ? AND corp_code IS ? AND report_keyword = ?",
            (user_id, corp_code or None, keyword),
        )
        if rows:
            return rows[0][0]
        cursor = await db.execute(
            "INSERT INTO dart_mutes (user_id, corp_code, report_keyword) VALUES (?, ?, ?)",
            (user_id, corp_code or None, keyword),
        )
        await db.commit()
        return cursor.lastrowid


async def list_mutes(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT id, corp_code, report_keyword FROM dart_mutes WHERE user_id = ? ORDER BY id",
            (user_id,),
        )
        return [{"id": r[0], "corp_code": r[1], "report_keyword": r[2]} for r in rows]


async def is_muted(user_id: int, corp_code: str, report_nm: str) -> bool:
    """사용자의 무음 설정에 걸리는 공시인지 (corp_code NULL = 모든 기업)"""
    name = (report_nm or "").replace(" ", "")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT report_keyword FROM dart_mutes "
            "WHERE user_id = ? AND (corp_code IS NULL OR corp_code = ?)",
            (user_id, corp_code),
        )
    return any(r[0] and r[0].replace(" ", "") in name for r in rows)


# === 다이제스트 대기열 ===
async def add_pending(rcept_no: str, user_ids: list[int], disclosure: dict, tier: str):
    """낮은 등급 공시를 다이제스트 발송 전까지 보관"""
    if not rcept_no or not user_ids:
        return
    joined = ",".join(str(u) for u in sorted(set(user_ids)))
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO dart_pending "
            "(rcept_no, user_ids, corp_name, report_nm, rcept_dt, flr_nm, url, tier) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rcept_no, joined, disclosure.get("corp_name", ""), disclosure.get("report_nm", ""),
                disclosure.get("rcept_dt", ""), disclosure.get("flr_nm", ""),
                disclosure.get("url", ""), tier,
            ),
        )
        await db.commit()


async def get_all_pending() -> list[dict]:
    """대기 중인 공시 전체 (user_ids는 int 리스트로 변환)"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT rcept_no, user_ids, corp_name, report_nm, rcept_dt, flr_nm, url, tier "
            "FROM dart_pending ORDER BY corp_name, rcept_dt, rcept_no"
        )
    result = []
    for r in rows:
        user_ids = [int(u) for u in (r[1] or "").split(",") if u.strip().isdigit()]
        result.append({
            "rcept_no": r[0], "user_ids": user_ids, "corp_name": r[2], "report_nm": r[3],
            "rcept_dt": r[4], "flr_nm": r[5], "url": r[6], "tier": r[7],
        })
    return result


async def delete_pending(rcept_nos: list[str]):
    if not rcept_nos:
        return
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executemany(
            "DELETE FROM dart_pending WHERE rcept_no = ?", [(n,) for n in rcept_nos]
        )
        await db.commit()
