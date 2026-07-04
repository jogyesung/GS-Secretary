"""종목 리서치 서비스 - 증권사 리포트 + 종목 뉴스 수집 및 AI 아침 브리핑 생성

수집 소스:
- 증권사 리포트: 네이버 금융 리서치 (finance.naver.com/research) - 국내 종목
- 종목 뉴스: Google News RSS (한국어) - 국내/해외 종목 공통
- 해외 종목(티커)은 리포트 대신 애널리스트/목표주가 관련 뉴스로 대체
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from bot.config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    STOCK_WATCHLIST,
    STOCK_REPORT_MODE,
    ALLOWED_USER_IDS,
)

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

NAVER_RESEARCH_LIST_URL = "https://finance.naver.com/research/company_list.naver"
NAVER_RESEARCH_READ_URL = "https://finance.naver.com/research/company_read.naver?nid={nid}"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
}

REPORT_LOOKBACK_DAYS = 4  # 증권사 리포트 조회 기간 (주말 끼면 금요일 리포트까지 커버)
NEWS_LOOKBACK = "2d"      # 뉴스 조회 기간 (Google News when: 문법)
MAX_REPORTS_PER_STOCK = 5
MAX_NEWS_PER_STOCK = 8


def get_watchlist() -> list[dict]:
    """STOCK_WATCHLIST 환경변수 파싱. '이름:코드' 쉼표 구분, 6자리 숫자면 국내 종목."""
    stocks = []
    for entry in STOCK_WATCHLIST.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        name, code = entry.rsplit(":", 1)
        name, code = name.strip(), code.strip()
        if not name or not code:
            continue
        market = "KR" if (code.isdigit() and len(code) == 6) else "US"
        stocks.append({"name": name, "code": code, "market": market})
    return stocks


# === 증권사 리포트 (네이버 금융 리서치) ===
# 목록 테이블 행: 종목명 | 제목(company_read 링크) | 증권사 | 첨부 | 작성일(yy.mm.dd) | 조회수
_REPORT_ROW_RE = re.compile(
    r'company_read\.naver\?nid=(?P<nid>\d+)[^"]*"[^>]*>(?P<title>.*?)</a>\s*</td>\s*'
    r"<td[^>]*>(?P<broker>[^<]*)</td>"
    r'(?P<rest>.*?)(?P<date>\d{2}\.\d{2}\.\d{2})',
    re.DOTALL,
)


async def fetch_broker_reports(
    item_code: str, days: int = REPORT_LOOKBACK_DAYS, limit: int = MAX_REPORTS_PER_STOCK
) -> list[dict]:
    """네이버 금융 리서치에서 특정 종목의 최근 증권사 리포트 목록 조회"""
    params = {"searchType": "itemCode", "itemCode": item_code}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=HEADERS) as client:
        resp = await client.get(NAVER_RESEARCH_LIST_URL, params=params)
        resp.raise_for_status()
    html = resp.content.decode("cp949", errors="replace")

    cutoff = (datetime.now(KST) - timedelta(days=days)).date()
    reports = []
    for m in _REPORT_ROW_RE.finditer(html):
        title = re.sub(r"<[^>]+>", "", m.group("title")).strip()
        broker = m.group("broker").strip()
        date_str = m.group("date")
        if not title or not broker:
            continue
        try:
            report_date = datetime.strptime(date_str, "%y.%m.%d").date()
        except ValueError:
            continue
        if report_date < cutoff:
            continue
        # 첨부 셀에서 리포트 원문 PDF 링크 추출 (없는 리포트도 있음)
        pdf_match = re.search(r'href="(https?://[^"]+\.pdf[^"]*)"', m.group("rest"))
        reports.append(
            {
                "title": title,
                "broker": broker,
                "date": report_date.strftime("%m/%d"),
                "url": NAVER_RESEARCH_READ_URL.format(nid=m.group("nid")),
                "pdf_url": pdf_match.group(1) if pdf_match else "",
            }
        )
        if len(reports) >= limit:
            break
    return reports


# === 종목 뉴스 (Google News RSS) ===
async def fetch_stock_news(query: str, limit: int = MAX_NEWS_PER_STOCK) -> list[dict]:
    """Google News RSS에서 최근 뉴스 조회 (한국어)"""
    params = {
        "q": f"{query} when:{NEWS_LOOKBACK}",
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    }
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=HEADERS) as client:
        resp = await client.get(GOOGLE_NEWS_RSS_URL, params=params)
        resp.raise_for_status()

    root = ET.fromstring(resp.content)
    news = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "").strip()
        # 제목 끝의 " - 언론사" 중복 제거
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()
        published = ""
        try:
            pub_dt = parsedate_to_datetime(item.findtext("pubDate") or "")
            published = pub_dt.astimezone(KST).strftime("%m/%d %H:%M")
        except Exception:
            pass
        if not title:
            continue
        news.append({"title": title, "link": link, "source": source, "published": published})
        if len(news) >= limit:
            break
    return news


# === 수집 오케스트레이션 ===
async def collect_watchlist_data() -> list[dict]:
    """워치리스트 전 종목의 증권사 리포트 + 뉴스 수집"""
    collected = []
    for stock in get_watchlist():
        reports: list[dict] = []
        if stock["market"] == "KR":
            try:
                reports = await fetch_broker_reports(stock["code"])
            except Exception as e:
                logger.warning(f"증권사 리포트 수집 실패 ({stock['name']}): {e}")
            news_query = stock["name"]
        else:
            news_query = f"{stock['name']} OR {stock['code']}"

        # 리포트가 없으면(해외 종목 포함) 애널리스트/목표주가 뉴스로 대체
        if not reports:
            try:
                analyst_news = await fetch_stock_news(
                    f"{stock['name']} 목표주가 OR 투자의견 OR 실적 전망", limit=4
                )
                reports = [
                    {
                        "title": n["title"],
                        "broker": n["source"] or "뉴스",
                        "date": n["published"][:5],
                        "url": n["link"],
                    }
                    for n in analyst_news
                ]
            except Exception as e:
                logger.warning(f"애널리스트 뉴스 수집 실패 ({stock['name']}): {e}")

        news: list[dict] = []
        try:
            news = await fetch_stock_news(news_query)
        except Exception as e:
            logger.warning(f"뉴스 수집 실패 ({stock['name']}): {e}")

        collected.append({**stock, "reports": reports, "news": news})
        logger.info(
            f"수집 완료: {stock['name']} - 리포트 {len(reports)}건, 뉴스 {len(news)}건"
        )
    return collected


def _serialize_for_prompt(collected: list[dict]) -> str:
    """수집 데이터를 Claude 프롬프트용 텍스트로 변환"""
    lines = []
    for s in collected:
        label = "증권사 리포트" if s["market"] == "KR" else "애널리스트/실적 관련 뉴스"
        lines.append(f"### {s['name']} ({s['code']})")
        lines.append(f"[{label}]")
        if s["reports"]:
            for r in s["reports"]:
                lines.append(f"- {r['date']} | {r['broker']} | {r['title']} | {r['url']}")
        else:
            lines.append("- (최근 자료 없음)")
        lines.append("[뉴스]")
        if s["news"]:
            for n in s["news"]:
                lines.append(f"- {n['published']} | {n['source']} | {n['title']} | {n['link']}")
        else:
            lines.append("- (최근 뉴스 없음)")
        lines.append("")
    return "\n".join(lines)


def format_raw_digest(collected: list[dict]) -> str:
    """AI 분석 없이 수집 결과를 그대로 정리한 다이제스트 (API 키 없거나 분석 실패 시 폴백)"""
    now = datetime.now(KST)
    lines = [f"📈 *아침 종목 리포트* ({now.strftime('%m/%d %H:%M')})\n"]
    for s in collected:
        lines.append(f"*■ {s['name']}*")
        if s["reports"]:
            lines.append("📄 리포트")
            for r in s["reports"][:3]:
                lines.append(f"• {r['date']} {r['broker']} | [{r['title']}]({r['url']})")
        if s["news"]:
            lines.append("📰 뉴스")
            for n in s["news"][:4]:
                lines.append(f"• [{n['title']}]({n['link']}) ({n['source']})")
        if not s["reports"] and not s["news"]:
            lines.append("• 최근 리포트/뉴스가 없어요")
        lines.append("")
    return "\n".join(lines)


# === AI 상세검토 ===
ANALYST_SYSTEM_PROMPT = (
    "당신의 이름은 '카리나'이고, GS Secretary의 증권 리서치 어시스턴트예요.\n"
    "매일 아침 사용자의 관심 종목에 대해 밤사이 수집된 증권사 리포트와 뉴스를 "
    "상세히 검토해서 브리핑을 작성해요.\n\n"
    "[말투]\n"
    "- 밝고 친근한 '~요' 체를 사용해요. '~습니다' 체는 쓰지 않아요.\n"
    "- 이모지를 적절히 사용해요 (📈📉📄📰💡 등). 도배는 금지예요.\n\n"
    "[작성 규칙]\n"
    "- 종목별 섹션으로 나눠 작성해요: *■ 종목명* 형식의 제목으로 시작.\n"
    "- 각 종목마다: ① 증권사 리포트 핵심 (제목에 목표주가/투자의견이 보이면 명시, "
    "리포트가 여러 건이면 공통 논조 정리) ② 주요 뉴스 요약 (같은 이슈의 중복 기사는 하나로 묶기) "
    "③ 💡 오늘의 체크포인트 1~2개.\n"
    "- 마지막에 *📌 오늘의 한 줄 정리* 로 4개 종목 전체를 종합해요.\n"
    "- 텔레그램 Markdown 형식: 굵게는 *별표*, 링크는 [제목](URL). "
    "밑줄(_), 표, 헤딩(#)은 사용 금지.\n"
    "- 링크는 종목당 가장 중요한 리포트/기사 1~2개만 걸어요.\n"
    "- 전체 3,000자 이내로 밀도 있게 작성해요.\n"
    "- 제공된 데이터에 없는 수치(목표주가, 실적 등)를 지어내지 마세요. "
    "데이터가 부족하면 '오늘은 관련 자료가 적었어요'라고 솔직하게 말하세요."
)


async def build_morning_report(collected: list[dict]) -> str:
    """수집 데이터를 Claude로 상세 검토하여 아침 브리핑 생성. 실패 시 원본 다이제스트 폴백.

    STOCK_REPORT_MODE=digest이면 API를 호출하지 않고 무료 다이제스트만 반환합니다.
    """
    if STOCK_REPORT_MODE == "digest" or not ANTHROPIC_API_KEY:
        return format_raw_digest(collected)

    now = datetime.now(KST)
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    prompt = (
        f"오늘은 {now.strftime('%Y년 %m월 %d일')} {weekdays[now.weekday()]} 아침이에요.\n"
        f"아래는 밤사이 수집한 관심 종목의 증권사 리포트 목록과 최신 뉴스예요.\n"
        f"이 자료를 상세히 검토해서 아침 브리핑을 작성해주세요.\n\n"
        f"{_serialize_for_prompt(collected)}"
    )

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            system=ANALYST_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        # 사용량 기록 (스케줄 잡은 대표 사용자 기준)
        try:
            from bot.services.usage_service import log_usage

            usage_user = ALLOWED_USER_IDS[0] if ALLOWED_USER_IDS else 0
            await log_usage(
                usage_user, CLAUDE_MODEL,
                response.usage.input_tokens, response.usage.output_tokens, 0,
            )
        except Exception as e:
            logger.warning(f"종목 리포트 사용량 기록 실패: {e}")

        text_parts = [b.text for b in response.content if hasattr(b, "text") and b.text]
        report = "\n".join(text_parts).strip()
        if not report:
            return format_raw_digest(collected)

        header = f"📈 *아침 종목 리포트* ({now.strftime('%m/%d')} {weekdays[now.weekday()]})\n\n"
        return header + report
    except Exception as e:
        logger.error(f"종목 리포트 AI 분석 실패: {e}")
        return format_raw_digest(collected)
