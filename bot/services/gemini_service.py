"""Gemini AI 실시간 웹 검색 서비스 - Google Search grounding 활용"""

import google.generativeai as genai
from bot.config import GEMINI_API_KEY, GEMINI_MODEL


def _configure():
    """Gemini API 설정"""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
    genai.configure(api_key=GEMINI_API_KEY)


async def search(query: str) -> dict:
    """Google 검색 grounding을 활용한 실시간 웹 검색

    Returns:
        dict: {"answer": str, "sources": list[str]}
    """
    _configure()

    google_search_tool = genai.protos.Tool(
        google_search=genai.protos.GoogleSearch()
    )

    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=(
            "당신은 실시간 웹 검색 리서치 어시스턴트입니다. "
            "한국어로 답변하세요. "
            "검색 결과를 바탕으로 정확하고 최신 정보를 정리해서 제공하세요. "
            "출처가 있으면 반드시 포함하세요."
        ),
        tools=[google_search_tool],
    )

    response = await model.generate_content_async(query)

    answer = response.text or "검색 결과를 가져올 수 없습니다."

    # 검색 출처(grounding metadata) 추출
    sources = []
    try:
        if response.candidates and response.candidates[0].grounding_metadata:
            metadata = response.candidates[0].grounding_metadata
            if hasattr(metadata, "grounding_chunks") and metadata.grounding_chunks:
                for chunk in metadata.grounding_chunks:
                    if hasattr(chunk, "web") and chunk.web:
                        uri = chunk.web.uri if hasattr(chunk.web, "uri") else ""
                        title = chunk.web.title if hasattr(chunk.web, "title") else ""
                        if uri:
                            sources.append(f"[{title}]({uri})" if title else uri)
    except Exception:
        pass

    return {"answer": answer, "sources": sources}
