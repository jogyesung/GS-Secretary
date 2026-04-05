"""Gemini AI 실시간 웹 검색 서비스 - REST API 직접 호출"""

import httpx
from bot.config import GEMINI_API_KEY, GEMINI_MODEL

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


async def search(query: str) -> dict:
    """Google 검색 grounding을 활용한 실시간 웹 검색

    Returns:
        dict: {"answer": str, "sources": list[str]}
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")

    url = API_URL.format(model=GEMINI_MODEL)

    payload = {
        "system_instruction": {
            "parts": [{"text": (
                "당신은 실시간 웹 검색 리서치 어시스턴트입니다. "
                "한국어로 답변하세요. "
                "검색 결과를 바탕으로 정확하고 최신 정보를 정리해서 제공하세요. "
                "출처가 있으면 반드시 포함하세요."
            )}]
        },
        "contents": [{"parts": [{"text": query}]}],
        "tools": [{"google_search": {}}],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            params={"key": GEMINI_API_KEY},
            json=payload,
        )

        if resp.status_code != 200:
            error_msg = resp.json().get("error", {}).get("message", resp.text)
            raise RuntimeError(f"Gemini API 오류 ({resp.status_code}): {error_msg}")

        data = resp.json()

    # 응답 텍스트 추출
    answer = ""
    try:
        parts = data["candidates"][0]["content"]["parts"]
        answer = "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError):
        answer = "검색 결과를 가져올 수 없습니다."

    # 출처 추출
    sources = []
    try:
        metadata = data["candidates"][0].get("groundingMetadata", {})
        chunks = metadata.get("groundingChunks", [])
        for chunk in chunks:
            web = chunk.get("web", {})
            uri = web.get("uri", "")
            title = web.get("title", "")
            if uri:
                sources.append(f"[{title}]({uri})" if title else uri)
    except (KeyError, IndexError):
        pass

    return {"answer": answer, "sources": sources}
