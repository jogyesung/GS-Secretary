"""증시 이벤트 브리핑 서비스 - 실적발표/경제지표/중앙은행 일정 (Gemini 실시간 검색)"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from bot.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

_SECTION_MAX_CHARS = 1700


def _date_range(now: datetime) -> tuple[str, str]:
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    today = f"{now.strftime('%Y년 %m월 %d일')} {weekdays[now.weekday()]}"
    week_end = (now + timedelta(days=7)).strftime("%Y년 %m월 %d일")
    return today, week_end


def _build_events_query(now: datetime) -> str:
    today, week_end = _date_range(now)
    return (
        f"오늘은 {today}입니다. "
        f"오늘부터 {week_end}까지 향후 일주일간 한국 및 글로벌 증시에 영향을 줄 "
        f"경제지표·정책 이벤트를 날짜별로 정리해주세요. 반드시 포함할 항목:\n"
        f"1. 물가지수 등 경제지표 발표 (미국 CPI/PPI/PCE/고용지표, 한국 소비자물가, 중국 지표 등)\n"
        f"2. 중앙은행 일정 (미국 FOMC, 한국은행 금통위, ECB, 일본은행 등 금리 결정·의사록·연설)\n"
        f"3. 그 외 시장에 큰 영향을 줄 이벤트 (옵션만기, 대형 IPO, 휴장일, 정치 이벤트 등)\n\n"
        f"형식: 날짜(요일)별로 묶어서 '• 이벤트명 — 한 줄 설명' 형태로 간결하게. "
        f"확인되지 않은 일정은 추측하지 말고 제외하세요. 전체 길이는 1500자 이내로."
    )


def _build_earnings_query(now: datetime) -> str:
    today, week_end = _date_range(now)
    return (
        f"오늘은 {today}입니다. "
        f"오늘부터 {week_end}까지 향후 일주일간 실적을 발표하는 주요 대형주 일정을 "
        f"날짜별로 정리해주세요.\n"
        f"1. 한국: 삼성전자·LG에너지솔루션 등 코스피 시가총액 상위 기업의 잠정실적/확정실적 발표\n"
        f"2. 미국: S&P500 대형주, 빅테크(애플·마이크로소프트·엔비디아 등), 주요 은행(JPMorgan 등)\n"
        f"3. 어닝시즌 시작/종료 등 실적 관련 주요 흐름\n\n"
        f"형식: 날짜(요일)별로 '• 기업명 — 발표 내용' 형태로 간결하게. "
        f"이번 주에 발표가 확인된 기업만 포함하고, 확인되지 않으면 추측하지 마세요. "
        f"해당 주에 대형주 실적발표가 없으면 '이번 주는 주요 대형주 실적발표가 없어요. "
        f"본격 어닝시즌은 O월 O주부터 시작돼요' 형태로 다음 어닝시즌 시작 시점을 알려주세요. "
        f"전체 길이는 1000자 이내로."
    )


def _clean_answer(result: dict | None) -> str | None:
    if not result:
        return None
    answer = (result.get("answer") or "").strip()
    if not answer:
        return None
    for tag in ("[Gemini 일반 검색]", "[DuckDuckGo 검색]"):
        if answer.startswith(tag):
            answer = answer[len(tag):].strip()
    if len(answer) > _SECTION_MAX_CHARS:
        answer = answer[:_SECTION_MAX_CHARS] + "\n...(생략)"
    return answer


async def _safe_search(query: str) -> dict | None:
    from bot.services.gemini_service import search
    try:
        return await search(query)
    except Exception as e:
        logger.warning(f"증시 이벤트 검색 실패: {e}")
        return None


async def get_market_events_briefing() -> str | None:
    """향후 일주일 증시 이벤트 + 주요 실적발표 요약. 전부 실패 시 None."""
    if not GEMINI_API_KEY:
        return None

    now = datetime.now(KST)
    events_result, earnings_result = await asyncio.gather(
        _safe_search(_build_events_query(now)),
        _safe_search(_build_earnings_query(now)),
    )

    events = _clean_answer(events_result)
    earnings = _clean_answer(earnings_result)

    if not events and not earnings:
        return None

    parts = ["📈 *이번 주 증시 이벤트*"]
    if earnings:
        parts.append(f"\n💰 *주요 실적발표*\n{earnings}")
    if events:
        parts.append(f"\n🗓 *경제지표·중앙은행*\n{events}")

    return "\n".join(parts)
