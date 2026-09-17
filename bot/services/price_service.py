"""국내 주가 조회 서비스 - 포트폴리오 종목의 장중 등락률 체크 (네이버 금융)

1순위: polling.finance.naver.com JSON API (필드: closePrice, fluctuationsRatio, compareToPreviousClosePrice)
2순위: finance.naver.com 종목 페이지 HTML 파싱 (cp949)
"""

import logging
import re
from datetime import datetime, timedelta, timezone

import aiosqlite
import httpx

from bot.config import DATABASE_PATH

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

NAVER_POLLING_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
NAVER_ITEM_URL = "https://finance.naver.com/item/main.naver?code={code}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}

# 정규장 09:00 ~ 15:30, 동시호가 반영 여유로 15:40까지 체크
MARKET_OPEN = (9, 0)
MARKET_CLOSE = (15, 40)


def is_market_hours(now: datetime | None = None) -> bool:
    """KST 평일 장중 여부 (공휴일은 체크하지 않음 — 그 날은 등락이 0이라 알림이 안 나감)"""
    now = now or datetime.now(KST)
    if now.weekday() >= 5:
        return False
    hm = (now.hour, now.minute)
    return MARKET_OPEN <= hm <= MARKET_CLOSE


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("+", "").strip())
    except ValueError:
        return None


def _parse_polling(data: dict) -> dict | None:
    datas = data.get("datas") or data.get("result", {}).get("areas", [{}])[0].get("datas") or []
    if not datas:
        return None
    item = datas[0]
    price = _to_float(item.get("closePrice") or item.get("nv"))
    pct = _to_float(item.get("fluctuationsRatio") or item.get("cr"))
    change = _to_float(item.get("compareToPreviousClosePrice") or item.get("cv"))
    if price is None or pct is None:
        return None
    # 하락인데 부호가 빠져 오는 경우 방어 (compareToPreviousPrice.code: 2=상승, 5=하락)
    direction_code = str((item.get("compareToPreviousPrice") or {}).get("code", ""))
    if direction_code == "5" and pct > 0:
        pct = -pct
        change = -abs(change) if change is not None else None
    return {"price": price, "pct": pct, "change": change}


async def _fetch_polling(client: httpx.AsyncClient, stock_code: str) -> dict | None:
    resp = await client.get(NAVER_POLLING_URL.format(code=stock_code))
    resp.raise_for_status()
    return _parse_polling(resp.json())


_HTML_PRICE_RE = re.compile(
    r'<p class="no_today">.*?<span class="blind">([\d,]+)</span>', re.DOTALL
)
_HTML_EXDAY_RE = re.compile(
    r'<p class="no_exday">.*?<em class="no_(?P<dir>up|down|nochange)?[^"]*">.*?'
    r'<span class="blind">(?P<change>[\d,]+)</span>.*?'
    r'<span class="blind">(?P<pct>[\d.]+)</span>',
    re.DOTALL,
)


def _parse_item_html(html: str) -> dict | None:
    m_price = _HTML_PRICE_RE.search(html)
    m_ex = _HTML_EXDAY_RE.search(html)
    if not m_price or not m_ex:
        return None
    price = _to_float(m_price.group(1))
    pct = _to_float(m_ex.group("pct"))
    change = _to_float(m_ex.group("change"))
    if price is None or pct is None:
        return None
    if m_ex.group("dir") == "down":
        pct, change = -pct, -(change or 0)
    elif m_ex.group("dir") == "nochange":
        pct, change = 0.0, 0.0
    return {"price": price, "pct": pct, "change": change}


async def _fetch_item_page(client: httpx.AsyncClient, stock_code: str) -> dict | None:
    resp = await client.get(NAVER_ITEM_URL.format(code=stock_code))
    resp.raise_for_status()
    return _parse_item_html(resp.content.decode("cp949", errors="replace"))


async def fetch_quote(stock_code: str) -> dict | None:
    """현재가/전일대비 등락률 조회. {"price": float, "pct": float, "change": float|None} 또는 None"""
    if not stock_code:
        return None
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=HEADERS) as client:
        try:
            quote = await _fetch_polling(client, stock_code)
            if quote:
                return quote
        except Exception as e:
            logger.debug(f"네이버 polling API 실패 ({stock_code}): {e}")
        try:
            return await _fetch_item_page(client, stock_code)
        except Exception as e:
            logger.warning(f"네이버 종목 페이지 파싱 실패 ({stock_code}): {e}")
            return None


# === 하루 1회(방향별) 알림 기록 ===
async def has_alerted(corp_code: str, alert_date: str, direction: str) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT 1 FROM price_alerts WHERE corp_code = ? AND alert_date = ? AND direction = ?",
            (corp_code, alert_date, direction),
        )
        return len(rows) > 0


async def record_alert(corp_code: str, alert_date: str, direction: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO price_alerts (corp_code, alert_date, direction) VALUES (?, ?, ?)",
            (corp_code, alert_date, direction),
        )
        await db.execute(
            "DELETE FROM price_alerts WHERE created_at < datetime('now', '-30 days')"
        )
        await db.commit()
