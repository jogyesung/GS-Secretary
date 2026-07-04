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


_FORMAT_RULES = (
    "\n\n[출력 형식 - 반드시 지키세요]\n"
    "- 서론, 맺음말, 요약 문장 없이 목록만 출력하세요.\n"
    "- 날짜 줄: 'M/D(요일)' 형태로만 쓰세요.\n"
    "- 각 항목: '• 이벤트명' 한 줄로만. 설명 문장을 붙이지 마세요.\n"
    "- 굵은 글씨(**), 헤딩(#) 등 마크다운 서식을 쓰지 마세요.\n"
    "- 이벤트가 없는 날짜는 아예 표기하지 마세요.\n"
    "- 확인되지 않은 일정은 추측하지 말고 제외하세요."
)


def _build_events_query(now: datetime) -> str:
    today, week_end = _date_range(now)
    return (
        f"오늘은 {today}입니다. "
        f"오늘부터 {week_end}까지 향후 일주일간 한국 및 글로벌 증시에 영향을 줄 "
        f"경제지표·정책 이벤트를 날짜별로 정리해주세요. 포함할 항목:\n"
        f"1. 핵심 경제지표 (미국 CPI/PPI/PCE/고용지표, 한국 소비자물가, 중국 CPI/PPI 등)\n"
        f"2. 중앙은행 (미국 FOMC, 한국은행 금통위, ECB, 일본은행의 금리 결정·의사록)\n"
        f"3. 시장에 큰 영향을 줄 이벤트 (옵션만기, 대형 IPO, 휴장일 등)\n"
        f"중요도 낮은 지표(개별국 무역수지, 부총재급 연설 등)는 빼고 "
        f"시장을 움직일 핵심 이벤트만 20개 이내로 추리세요. 전체 1000자 이내."
        + _FORMAT_RULES
    )


def _build_kr_earnings_query(now: datetime) -> str:
    today, week_end = _date_range(now)
    return (
        f"오늘은 {today}입니다. "
        f"한국 뉴스에서 '이번 주 실적발표 일정', '잠정실적 발표', '2분기 실적 발표 예정' 등을 "
        f"검색해서, 오늘부터 {week_end}까지 향후 일주일간 실적(잠정실적 포함)을 발표하는 "
        f"한국 상장사를 날짜별로 정리해주세요. "
        f"코스피·코스닥 시가총액 상위 대형주 위주로, 중소형주는 제외하세요. "
        f"특히 시총 최상위 기업의 분기 잠정실적 발표 예정일은 뉴스 기사에서 꼭 확인하세요. "
        f"항목 형태: '• 기업명 (잠정/확정, 분기)'. "
        f"이번 주에 발표가 확인된 기업만 포함하세요. "
        f"이번 주 발표가 없으면 목록 대신 딱 한 줄로 "
        f"'이번 주는 국내 대형주 실적발표가 없어요' 형태로 답하세요. "
        f"전체 400자 이내."
        + _FORMAT_RULES
    )


def _build_global_earnings_query(now: datetime) -> str:
    today, week_end = _date_range(now)
    return (
        f"오늘은 {today}입니다. "
        f"'earnings calendar this week' 등으로 미국 실적발표 캘린더를 검색해서, "
        f"오늘부터 {week_end}까지 향후 일주일간 실적을 발표하는 해외 대형주를 "
        f"날짜별로 정리해주세요.\n"
        f"1. 미국: S&P500 대형주, 빅테크, 주요 은행\n"
        f"2. 그 외 글로벌 증시에 영향이 큰 해외 대형주 (TSMC, ASML 등)\n"
        f"중소형주는 제외하고 시장에 영향이 큰 대형주만 포함하세요. "
        f"항목 형태: '• 기업명 (잠정/확정, 분기)'. "
        f"이번 주에 발표가 확인된 기업만 포함하세요. "
        f"이번 주 발표가 없으면 목록 대신 딱 한 줄로 "
        f"'이번 주는 해외 대형주 실적발표가 없어요. 다음 실적 시즌: OOO' 형태로 답하세요. "
        f"전체 400자 이내."
        + _FORMAT_RULES
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
    # Gemini가 형식 지시를 어기고 넣는 마크다운 서식 제거 (텔레그램 파싱 깨짐 방지)
    answer = answer.replace("**", "").replace("__", "")
    answer = "\n".join(
        line for line in (l.rstrip() for l in answer.splitlines())
        if line and not line.lstrip().startswith("#")
    )
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
    events_result, kr_result, global_result = await asyncio.gather(
        _safe_search(_build_events_query(now)),
        _safe_search(_build_kr_earnings_query(now)),
        _safe_search(_build_global_earnings_query(now)),
    )

    events = _clean_answer(events_result)
    kr_earnings = _clean_answer(kr_result)
    global_earnings = _clean_answer(global_result)

    if not events and not kr_earnings and not global_earnings:
        return None

    parts = ["📈 *이번 주 증시 이벤트*"]
    if kr_earnings:
        parts.append(f"\n💰 *실적발표 (한국)*\n{kr_earnings}")
    if global_earnings:
        parts.append(f"\n💰 *실적발표 (미국·글로벌)*\n{global_earnings}")
    if events:
        parts.append(f"\n🗓 *경제지표·중앙은행*\n{events}")

    return "\n".join(parts)
