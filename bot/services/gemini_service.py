"""Gemini AI 실시간 웹 검색 서비스 - 3단계 폴백: Gemini grounding → Gemini 일반 → DuckDuckGo"""

import asyncio
import logging
from google import genai
from google.genai import types
from bot.config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

# 웹 검색이 필요한 키워드 목록
_SEARCH_KEYWORDS = [
    "오늘", "현재", "지금", "최신", "실시간", "최근",
    "날씨", "뉴스", "주가", "코스피", "코스닥", "환율", "비트코인",
    "검색", "찾아", "알려줘", "알아봐", "조사", "리서치",
    "몇시", "몇도", "얼마", "가격", "시세",
    "today", "now", "current", "latest", "recent", "news",
    "weather", "price", "stock", "search",
]


def needs_web_search(query: str) -> bool:
    """쿼리에 웹 검색이 필요한 키워드가 포함되어 있는지 확인"""
    query_lower = query.lower()
    return any(kw in query_lower for kw in _SEARCH_KEYWORDS)


def _get_client() -> genai.Client:
    """Gemini 클라이언트 생성"""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
    return genai.Client(api_key=GEMINI_API_KEY)


async def _gemini_grounding_search(client: genai.Client, query: str) -> dict | None:
    """1단계: Gemini Google Search grounding (실시간 검색)"""
    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=GEMINI_MODEL,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                system_instruction=(
                    "당신은 실시간 웹 검색 리서치 어시스턴트입니다. "
                    "한국어로 답변하세요. "
                    "검색 결과를 바탕으로 정확하고 최신 정보를 정리해서 제공하세요. "
                    "출처가 있으면 반드시 포함하세요. "
                    "위치 정보가 없는 날씨, 맛집 등의 질문은 서울 기준으로 답변하세요. "
                    "절대 '위치를 알 수 없다'거나 '지역을 알려달라'고 하지 말고, 바로 검색 결과를 제공하세요."
                ),
            ),
        )

        answer = response.text or ""
        if not answer:
            return None

        sources = []
        try:
            if response.candidates and response.candidates[0].grounding_metadata:
                metadata = response.candidates[0].grounding_metadata
                if metadata.grounding_chunks:
                    for chunk in metadata.grounding_chunks:
                        if chunk.web and chunk.web.uri:
                            title = chunk.web.title or ""
                            sources.append(f"[{title}]({chunk.web.uri})" if title else chunk.web.uri)
        except Exception:
            pass

        logger.info("Gemini grounding 검색 성공")
        return {"answer": answer, "sources": sources}

    except Exception as e:
        logger.warning(f"Gemini grounding 검색 실패: {e}")
        return None


async def _gemini_regular_search(client: genai.Client, query: str) -> dict | None:
    """2단계: Gemini 일반 검색 (grounding 없이)"""
    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=GEMINI_MODEL,
            contents=f"다음에 대해 알고 있는 최신 정보를 정리해주세요: {query}",
            config=types.GenerateContentConfig(
                system_instruction=(
                    "당신은 지식이 풍부한 어시스턴트입니다. "
                    "한국어로 답변하세요. "
                    "알고 있는 정보를 정확하게 정리해서 제공하세요."
                ),
            ),
        )

        answer = response.text or ""
        if not answer:
            return None

        logger.info("Gemini 일반 검색 성공")
        return {"answer": f"[Gemini 일반 검색]\n{answer}", "sources": []}

    except Exception as e:
        logger.warning(f"Gemini 일반 검색 실패: {e}")
        return None


async def _duckduckgo_search(query: str) -> dict | None:
    """3단계: DuckDuckGo 검색 (최종 폴백)"""
    try:
        from duckduckgo_search import DDGS

        def _do_search():
            with DDGS() as ddgs:
                results = list(ddgs.text(query, region="kr-kr", max_results=5))
            return results

        results = await asyncio.to_thread(_do_search)

        if not results:
            return None

        answer_parts = []
        sources = []
        for r in results:
            answer_parts.append(f"• {r.get('title', '')}: {r.get('body', '')}")
            if r.get("href"):
                sources.append(f"[{r.get('title', '')}]({r['href']})")

        answer = "\n".join(answer_parts)
        logger.info("DuckDuckGo 검색 성공")
        return {"answer": f"[DuckDuckGo 검색]\n{answer}", "sources": sources}

    except Exception as e:
        logger.warning(f"DuckDuckGo 검색 실패: {e}")
        return None


async def search(query: str) -> dict:
    """3단계 폴백 웹 검색: Gemini grounding → Gemini 일반 → DuckDuckGo

    Returns:
        dict: {"answer": str, "sources": list[str]}
    """
    client = None
    if GEMINI_API_KEY:
        client = _get_client()

    # 1단계: Gemini Google Search grounding
    if client:
        result = await _gemini_grounding_search(client, query)
        if result:
            return result

    # 2단계: Gemini 일반 검색 (grounding 없이)
    if client:
        result = await _gemini_regular_search(client, query)
        if result:
            return result

    # 3단계: DuckDuckGo 폴백
    result = await _duckduckgo_search(query)
    if result:
        return result

    raise RuntimeError("모든 검색 방법이 실패했습니다.")
