"""Claude AI 대화 서비스 - Anthropic API 연동"""

import anthropic
from bot.config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_SYSTEM_PROMPT, CLAUDE_MAX_TOKENS

# 사용자별 대화 히스토리 (메모리 내 관리)
_conversations: dict[int, list[dict]] = {}

MAX_HISTORY = 20  # 최대 대화 기록 수 (이전 메시지 유지)


def _get_client() -> anthropic.AsyncAnthropic:
    """Anthropic 클라이언트 생성"""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
    return anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


async def chat(user_id: int, message: str) -> str:
    """Claude와 대화. 사용자별 대화 히스토리를 유지합니다."""
    client = _get_client()

    # 대화 히스토리 가져오기
    if user_id not in _conversations:
        _conversations[user_id] = []

    history = _conversations[user_id]
    history.append({"role": "user", "content": message})

    # 히스토리가 너무 길면 오래된 것부터 제거
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
        _conversations[user_id] = history

    response = await client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=CLAUDE_MAX_TOKENS,
        system=CLAUDE_SYSTEM_PROMPT,
        messages=history,
    )

    assistant_message = response.content[0].text

    # 응답을 히스토리에 추가
    history.append({"role": "assistant", "content": assistant_message})

    return assistant_message


def clear_history(user_id: int):
    """대화 히스토리 초기화"""
    _conversations.pop(user_id, None)


def get_history_length(user_id: int) -> int:
    """현재 대화 히스토리 길이"""
    return len(_conversations.get(user_id, []))
