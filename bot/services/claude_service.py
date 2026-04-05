"""Claude AI 대화 서비스 - Anthropic API 연동 + Tool Use"""

import json
import anthropic
from bot.config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_SYSTEM_PROMPT,
    CLAUDE_MAX_TOKENS, ALLOWED_PATHS, RUN_MODE, GEMINI_API_KEY,
)

# 사용자별 대화 히스토리 (메모리 내 관리)
_conversations: dict[int, list[dict]] = {}

MAX_HISTORY = 20


def _get_client() -> anthropic.AsyncAnthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
    return anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


# === Claude가 사용할 수 있는 도구 정의 ===
TOOLS = []

# PC 파일 관련 도구 (local/all 모드)
PC_TOOLS = [
    {
        "name": "list_directory",
        "description": "로컬 PC의 디렉토리 내용을 조회합니다. 파일명, 크기, 폴더 여부를 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "조회할 디렉토리 경로 (예: C:\\Users\\jogye\\Documents)"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "read_file",
        "description": "로컬 PC의 텍스트 파일 내용을 읽습니다. (txt, csv, json, py, md 등 텍스트 파일만 가능)",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "읽을 파일 경로"},
                "max_lines": {"type": "integer", "description": "최대 읽을 줄 수 (기본 200)", "default": 200}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "로컬 PC에 텍스트 파일을 생성하거나 덮어씁니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "저장할 파일 경로"},
                "content": {"type": "string", "description": "파일에 쓸 내용"}
            },
            "required": ["path", "content"]
        }
    },
]

# 메모 도구 (cloud/all 모드)
MEMO_TOOLS = [
    {
        "name": "save_memo",
        "description": "메모를 저장합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "메모 내용"}
            },
            "required": ["content"]
        }
    },
    {
        "name": "list_memos",
        "description": "저장된 메모 목록을 조회합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "최대 조회 수 (기본 10)", "default": 10}
            }
        }
    },
    {
        "name": "search_memos",
        "description": "메모를 키워드로 검색합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "검색할 키워드"}
            },
            "required": ["keyword"]
        }
    },
]

# 웹 검색 도구 (Gemini API 키가 있을 때만)
SEARCH_TOOLS = [
    {
        "name": "web_search",
        "description": "실시간 웹 검색을 수행합니다. 최신 뉴스, 날씨, 주가, 실시간 정보, 최근 이벤트 등 실시간 데이터가 필요할 때만 사용하세요. 일반 지식 질문에는 사용하지 마세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색할 내용 (예: '오늘 코스피 지수', '최신 AI 뉴스')"}
            },
            "required": ["query"]
        }
    },
]


def _build_tools() -> list[dict]:
    """현재 모드에 따라 사용 가능한 도구 목록 생성"""
    tools = []
    if RUN_MODE in ("local", "all"):
        tools.extend(PC_TOOLS)
    if RUN_MODE in ("cloud", "all"):
        tools.extend(MEMO_TOOLS)
    if GEMINI_API_KEY:
        tools.extend(SEARCH_TOOLS)
    return tools


def _build_system_prompt() -> str:
    """허용 경로 정보를 포함한 시스템 프롬프트 생성"""
    paths_info = ""
    if ALLOWED_PATHS:
        paths_list = ", ".join(str(p) for p in ALLOWED_PATHS)
        paths_info = f"\n\n접근 가능한 PC 경로: {paths_list}"

    return CLAUDE_SYSTEM_PROMPT + paths_info


# === 도구 실행 함수 ===
async def _execute_tool(tool_name: str, tool_input: dict, user_id: int) -> str:
    """도구를 실행하고 결과를 반환"""
    try:
        if tool_name == "list_directory":
            from bot.services.file_service import list_directory
            items = list_directory(tool_input["path"])
            result = []
            for item in items[:50]:
                icon = "📁" if item["is_dir"] else "📄"
                size = f" ({item['size']} bytes)" if not item["is_dir"] else ""
                result.append(f"{icon} {item['name']}{size}")
            return "\n".join(result) if result else "빈 디렉토리입니다."

        elif tool_name == "read_file":
            from bot.services.file_service import get_file_path
            path = get_file_path(tool_input["path"])
            max_lines = tool_input.get("max_lines", 200)
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[:max_lines]
            content = "".join(lines)
            if len(content) > 10000:
                content = content[:10000] + "\n...(잘림)"
            return content

        elif tool_name == "write_file":
            from bot.services.file_service import save_file
            from pathlib import Path
            file_path = Path(tool_input["path"])
            save_file(str(file_path.parent), file_path.name, tool_input["content"].encode("utf-8"))
            return f"파일 저장 완료: {tool_input['path']}"

        elif tool_name == "save_memo":
            from bot.services.memo_service import create_memo
            memo_id = await create_memo(user_id, tool_input["content"])
            return f"메모 저장 완료 (ID: {memo_id})"

        elif tool_name == "list_memos":
            from bot.services.memo_service import get_memos
            limit = tool_input.get("limit", 10)
            memos = await get_memos(user_id, limit)
            if not memos:
                return "저장된 메모가 없습니다."
            result = []
            for m in memos:
                result.append(f"#{m['id']} [{m['created_at'][:16]}] {m['content']}")
            return "\n".join(result)

        elif tool_name == "search_memos":
            from bot.services.memo_service import search_memos
            memos = await search_memos(user_id, tool_input["keyword"])
            if not memos:
                return f"'{tool_input['keyword']}'에 대한 검색 결과가 없습니다."
            result = []
            for m in memos:
                result.append(f"#{m['id']} [{m['created_at'][:16]}] {m['content']}")
            return "\n".join(result)

        elif tool_name == "web_search":
            from bot.services.gemini_service import search
            try:
                search_result = await search(tool_input["query"])
                text = search_result["answer"]
                if search_result["sources"]:
                    text += "\n\n출처:\n" + "\n".join(search_result["sources"][:5])
                return text
            except Exception as e:
                # DuckDuckGo까지 모두 실패한 경우
                return f"웹 검색을 수행할 수 없습니다 ({e}). 검색 없이 알고 있는 정보로 답변해주세요."

        else:
            return f"알 수 없는 도구: {tool_name}"

    except (PermissionError, FileNotFoundError) as e:
        return f"오류: {e}"
    except Exception as e:
        return f"도구 실행 오류: {e}"


async def chat(user_id: int, message: str) -> str:
    """Claude와 대화. Tool use를 지원하며 사용자별 히스토리를 유지합니다."""
    client = _get_client()
    tools = _build_tools()
    system = _build_system_prompt()

    if user_id not in _conversations:
        _conversations[user_id] = []

    history = _conversations[user_id]
    history.append({"role": "user", "content": message})

    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
        _conversations[user_id] = history

    # Claude API 호출 (tool use 루프)
    max_tool_rounds = 5
    for _ in range(max_tool_rounds):
        kwargs = {
            "model": CLAUDE_MODEL,
            "max_tokens": CLAUDE_MAX_TOKENS,
            "system": system,
            "messages": history,
        }
        if tools:
            kwargs["tools"] = tools

        response = await client.messages.create(**kwargs)

        # 응답에서 텍스트와 tool_use 분리
        if response.stop_reason == "tool_use":
            # 전체 content를 히스토리에 추가
            history.append({"role": "assistant", "content": response.content})

            # 각 tool_use에 대해 실행
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await _execute_tool(block.name, block.input, user_id)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            history.append({"role": "user", "content": tool_results})
            continue  # Claude가 도구 결과를 보고 다시 응답하도록

        else:
            # 최종 텍스트 응답
            text_parts = [block.text for block in response.content if hasattr(block, "text")]
            assistant_message = "\n".join(text_parts)
            history.append({"role": "assistant", "content": assistant_message})
            return assistant_message

    return "도구 호출이 너무 많습니다. 다시 시도해주세요."


def clear_history(user_id: int):
    """대화 히스토리 초기화"""
    _conversations.pop(user_id, None)


def get_history_length(user_id: int) -> int:
    """현재 대화 히스토리 길이"""
    return len(_conversations.get(user_id, []))
