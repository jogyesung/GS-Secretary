"""증시 이벤트 브리핑 서비스 - 실적발표/경제지표/중앙은행 일정 (Gemini 실시간 검색)"""

import logging
from datetime import datetime, timedelta, timezone

from bot.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


def _build_query(now: datetime) -> str:
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    today = f"{now.strftime('%Y년 %m월 %d일')} {weekdays[now.weekday()]}"
    week_end = (now + timedelta(days=7)).strftime("%Y년 %m월 %d일")

    return (
        f"오늘은 {today}입니다. "
        f"오늘부터 {week_end}까지 향후 일주일간 한국 및 글로벌 증시에 영향을 줄 주요 이벤트를 "
        f"날짜별로 정리해주세요. 반드시 포함할 항목:\n"
        f"1. 주요 기업 실적발표 (한국 대형주, 미국 빅테크/대형주)\n"
        f"2. 물가지수 등 경제지표 발표 (미국 CPI/PPI/PCE/고용지표, 한국 소비자물가, 중국 지표 등)\n"
        f"3. 중앙은행 일정 (미국 FOMC, 한국은행 금통위, ECB, 일본은행 등 금리 결정·의사록·연설)\n"
        f"4. 그 외 시장에 큰 영향을 줄 이벤트 (옵션만기, 대형 IPO, 정치 이벤트 등)\n\n"
        f"형식: 날짜(요일)별로 묶어서 '• 이벤트명 — 한 줄 설명' 형태로 간결하게. "
        f"확인되지 않은 일정은 추측하지 말고 제외하세요. 전체 길이는 2000자 이내로."
    )


async def get_market_events_briefing() -> str | None:
    """향후 일주일 증시 이벤트 요약. 검색 실패 시 None."""
    if not GEMINI_API_KEY:
        return None

    from bot.services.gemini_service import search

    now = datetime.now(KST)
    try:
        result = await search(_build_query(now))
    except Exception as e:
        logger.warning(f"증시 이벤트 검색 실패: {e}")
        return None

    answer = (result.get("answer") or "").strip()
    if not answer:
        return None

    # 폴백 검색 결과 태그 제거
    for tag in ("[Gemini 일반 검색]", "[DuckDuckGo 검색]"):
        if answer.startswith(tag):
            answer = answer[len(tag):].strip()

    if len(answer) > 3500:
        answer = answer[:3500] + "\n...(생략)"

    return f"📈 *이번 주 증시 이벤트*\n\n{answer}"
