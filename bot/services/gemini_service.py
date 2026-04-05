"""Gemini AI 실시간 웹 검색 서비스 - google-genai SDK + Google Search grounding"""

import asyncio
from google import genai
from google.genai import types
from bot.config import GEMINI_API_KEY, GEMINI_MODEL


def _get_client() -> genai.Client:
    """Gemini 클라이언트 생성"""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
    return genai.Client(api_key=GEMINI_API_KEY)


async def search(query: str) -> dict:
    """Google 검색 grounding을 활용한 실시간 웹 검색 (할당량 초과 시 재시도)

    Returns:
        dict: {"answer": str, "sources": list[str]}
    """
    client = _get_client()

    # 할당량 초과 시 최대 2회 재시도 (30초 간격)
    last_error = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=query,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    system_instruction=(
                        "당신은 실시간 웹 검색 리서치 어시스턴트입니다. "
                        "한국어로 답변하세요. "
                        "검색 결과를 바탕으로 정확하고 최신 정보를 정리해서 제공하세요. "
                        "출처가 있으면 반드시 포함하세요."
                    ),
                ),
            )

            answer = response.text or "검색 결과를 가져올 수 없습니다."

            # 출처 추출
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

            return {"answer": answer, "sources": sources}

        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            if "quota" in error_str or "resource_exhausted" in error_str or "429" in error_str:
                if attempt < 2:
                    await asyncio.sleep(30)  # 30초 대기 후 재시도
                    continue
            # 할당량 외 에러는 바로 중단
            break

    # 모든 재시도 실패 → 에러 전달
    raise RuntimeError(f"웹 검색 실패: {last_error}")
