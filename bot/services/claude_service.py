"""Claude AI 대화 서비스 - Anthropic API 연동 + Tool Use"""

import json
import logging
import anthropic
from bot.config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_SYSTEM_PROMPT,
    CLAUDE_MAX_TOKENS, ALLOWED_PATHS, RUN_MODE, GEMINI_API_KEY,
)

logger = logging.getLogger(__name__)

# 사용자별 대화 히스토리 (메모리 내 관리)
_conversations: dict[int, list[dict]] = {}

# 파일 전송 대기열 (user_id → list of file paths)
_pending_files: dict[int, list[str]] = {}

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

# Google Drive 도구 (cloud/all 모드)
DRIVE_TOOLS = [
    {
        "name": "drive_search",
        "description": "Google Drive에서 파일을 검색합니다. 파일 이름과 내용에서 키워드를 검색합니다. 사용자의 Drive 자료를 참고해야 할 때 사용하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색할 키워드 (예: '회의록', '프로젝트 계획서', '매출 보고서')"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "drive_read",
        "description": "Google Drive 파일의 내용을 읽습니다. drive_search로 찾은 파일의 ID를 사용합니다. Google Docs, Sheets, 텍스트 파일 등을 읽을 수 있습니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "읽을 파일의 Google Drive ID"},
                "max_chars": {"type": "integer", "description": "최대 읽을 글자 수 (기본 15000)", "default": 15000}
            },
            "required": ["file_id"]
        }
    },
    {
        "name": "drive_send",
        "description": "Google Drive 파일을 텔레그램으로 전송합니다. 사용자가 파일을 보내달라고 하면 이 도구를 사용하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "전송할 파일의 Google Drive ID"}
            },
            "required": ["file_id"]
        }
    },
    {
        "name": "drive_list",
        "description": "Google Drive 폴더의 파일 목록을 조회합니다. folder_id를 생략하면 루트 폴더를 조회합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder_id": {"type": "string", "description": "폴더 ID (기본: 루트)", "default": "root"},
                "page_size": {"type": "integer", "description": "최대 조회 수 (기본 20)", "default": 20}
            }
        }
    },
]

# Google Calendar 도구 (cloud/all 모드)
CALENDAR_TOOLS = [
    {
        "name": "calendar_list_calendars",
        "description": "등록된 캘린더 목록을 조회합니다. Google Calendar, Outlook 연동 캘린더 등 모든 캘린더를 보여줍니다.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "calendar_list",
        "description": "모든 캘린더에서 일정을 통합 조회합니다. Google Calendar, Outlook 등 연동된 모든 캘린더의 일정을 보여줍니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "오늘부터 몇 일간 조회할지 (기본 1, 이번 주는 7)", "default": 1},
                "max_results": {"type": "integer", "description": "최대 조회 수 (기본 10)", "default": 10}
            }
        }
    },
    {
        "name": "calendar_create",
        "description": "Google Calendar에 새 일정을 만듭니다. 시간은 반드시 ISO 8601 형식으로 입력하세요 (예: 2026-04-07T14:00:00).",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "일정 제목"},
                "start_time": {"type": "string", "description": "시작 시간 (예: 2026-04-07T14:00:00)"},
                "end_time": {"type": "string", "description": "종료 시간 (예: 2026-04-07T15:00:00)"},
                "description": {"type": "string", "description": "일정 설명 (선택)", "default": ""},
                "location": {"type": "string", "description": "장소 (선택)", "default": ""},
                "all_day": {"type": "boolean", "description": "종일 일정 여부 (선택)", "default": False}
            },
            "required": ["summary", "start_time", "end_time"]
        }
    },
    {
        "name": "calendar_update",
        "description": "기존 Google Calendar 일정을 수정합니다. calendar_list로 조회한 일정의 ID를 사용합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "수정할 일정의 ID"},
                "summary": {"type": "string", "description": "새 제목 (변경 시)"},
                "start_time": {"type": "string", "description": "새 시작 시간 (변경 시)"},
                "end_time": {"type": "string", "description": "새 종료 시간 (변경 시)"},
                "description": {"type": "string", "description": "새 설명 (변경 시)"},
                "location": {"type": "string", "description": "새 장소 (변경 시)"}
            },
            "required": ["event_id"]
        }
    },
    {
        "name": "calendar_delete",
        "description": "Google Calendar 일정을 삭제합니다. calendar_list로 조회한 일정의 ID를 사용합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "삭제할 일정의 ID"}
            },
            "required": ["event_id"]
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
        tools.extend(DRIVE_TOOLS)
        tools.extend(CALENDAR_TOOLS)
    if GEMINI_API_KEY:
        tools.extend(SEARCH_TOOLS)
    return tools


def _build_system_prompt() -> str:
    """허용 경로 정보를 포함한 시스템 프롬프트 생성"""
    from datetime import datetime, timezone, timedelta
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    today = f"{now.strftime('%Y년 %m월 %d일')} {weekdays[now.weekday()]}"

    # 이번 주 + 다음 주 날짜 목록 생성 (날짜 계산 오류 방지)
    monday = now - timedelta(days=now.weekday())

    week_info = []
    for i in range(7):
        d = monday + timedelta(days=i)
        marker = " ← 오늘" if d.date() == now.date() else ""
        week_info.append(f"  {weekdays[i]}: {d.strftime('%Y-%m-%d')}{marker}")

    next_week_info = []
    next_monday = monday + timedelta(days=7)
    for i in range(7):
        d = next_monday + timedelta(days=i)
        next_week_info.append(f"  {weekdays[i]}: {d.strftime('%Y-%m-%d')}")

    extra = (
        f"\n\n오늘: {today} (현재 시각: {now.strftime('%H:%M')} KST)"
        f"\n이번 주:\n" + "\n".join(week_info)
        + f"\n다음 주:\n" + "\n".join(next_week_info)
        + "\n\n[중요] 사용자가 '이번주 수요일'이라고 하면 위 표에서 해당 날짜를 정확히 사용하세요. 절대 날짜를 추측하지 마세요."
    )
    if ALLOWED_PATHS:
        paths_list = ", ".join(str(p) for p in ALLOWED_PATHS)
        extra += f"\n\n접근 가능한 PC 경로: {paths_list}"

    if RUN_MODE in ("cloud", "all"):
        extra += (
            "\n\n[Google Drive 활용 지침]"
            "\n사용자가 질문하면, 관련 자료가 Google Drive에 있을 수 있습니다."
            "\n1. drive_search로 관련 파일을 검색하세요."
            "\n2. 찾은 파일의 ID로 drive_read를 사용해 내용을 읽으세요."
            "\n3. 파일 내용을 바탕으로 정확하게 답변하세요."
            "\n사용자가 '내 자료', '내 문서', '보고서', '파일' 등을 언급하면 Drive를 먼저 검색하세요."
        "\n\n[Google Calendar 활용 지침]"
        "\n사용자가 일정, 스케줄, 회의, 약속 등을 언급하면 Calendar 도구를 사용하세요."
        "\n오늘 날짜를 기준으로 시간을 계산하세요."
        "\n일정 생성 시 종료 시간이 없으면 시작 시간 + 1시간으로 설정하세요."
        )

    extra += (
        "\n\n[중요 규칙]"
        "\n- 도구 실행이 실패하더라도 절대 '할 수 없습니다', '검색이 안 됩니다'라고만 답하지 마세요."
        "\n- 웹 검색이 실패하면, 반드시 당신이 알고 있는 지식으로 직접 답변하세요."
        "\n- '다른 사이트에서 확인하세요', '직접 검색해보세요' 같은 떠넘기기는 절대 하지 마세요."
        "\n- 사용자는 당신에게 답을 원하는 것이지, 다른 곳으로 안내받길 원하는 게 아닙니다."
    )

    return CLAUDE_SYSTEM_PROMPT + extra


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

        elif tool_name == "drive_search":
            from bot.services.drive_service import fulltext_search
            files = await fulltext_search(tool_input["query"])
            if not files:
                return f"'{tool_input['query']}'에 대한 Drive 검색 결과가 없습니다."
            result = []
            for f in files:
                mime = f.get("mimeType", "")
                modified = f.get("modifiedTime", "")[:16]
                size = f.get("size", "")
                size_str = f" ({size} bytes)" if size else ""
                result.append(f"• {f['name']} [ID: {f['id']}] ({mime}){size_str} 수정: {modified}")
            return f"Drive 검색 결과 ({len(files)}개):\n" + "\n".join(result)

        elif tool_name == "drive_send":
            from bot.services.drive_service import download_file, get_file_info
            from bot.config import TEMP_DOWNLOAD_PATH
            file_info = await get_file_info(tool_input["file_id"])
            file_name = file_info["name"]
            dest = str(TEMP_DOWNLOAD_PATH / file_name)
            await download_file(tool_input["file_id"], dest)
            # 전송 대기열에 추가
            if user_id not in _pending_files:
                _pending_files[user_id] = []
            _pending_files[user_id].append(dest)
            return f"파일 '{file_name}' 전송 준비 완료. 텔레그램으로 보내드리겠습니다."

        elif tool_name == "drive_read":
            from bot.services.drive_service import read_file_content
            max_chars = tool_input.get("max_chars", 15000)
            file_data = await read_file_content(tool_input["file_id"], max_chars=max_chars)
            return f"파일: {file_data['name']} ({file_data['mime_type']})\n\n{file_data['content']}"

        elif tool_name == "drive_list":
            from bot.services.drive_service import list_files
            folder_id = tool_input.get("folder_id", "root")
            page_size = tool_input.get("page_size", 20)
            files = await list_files(folder_id, page_size)
            if not files:
                return "폴더가 비어있습니다."
            result = []
            for f in files:
                is_folder = f.get("mimeType") == "application/vnd.google-apps.folder"
                icon = "📁" if is_folder else "📄"
                result.append(f"{icon} {f['name']} [ID: {f['id']}]")
            return f"Drive 파일 목록 ({len(files)}개):\n" + "\n".join(result)

        elif tool_name == "calendar_list_calendars":
            from bot.services.calendar_service import list_calendars
            calendars = await list_calendars()
            if not calendars:
                return "등록된 캘린더가 없습니다."
            result = []
            for c in calendars:
                primary = " (기본)" if c["primary"] else ""
                result.append(f"• {c['summary']}{primary} [ID: {c['id']}]")
            return f"캘린더 목록 ({len(calendars)}개):\n" + "\n".join(result)

        elif tool_name == "calendar_list":
            from bot.services.calendar_service import list_events
            days = tool_input.get("days", 1)
            max_results = tool_input.get("max_results", 10)
            events = await list_events(days=days, max_results=max_results)
            if not events:
                return f"향후 {days}일 내 일정이 없습니다."
            result = []
            for e in events:
                time_str = e["start"]
                if "T" in time_str:
                    time_str = time_str[:16].replace("T", " ")
                loc = f" 📍{e['location']}" if e["location"] else ""
                cal = f" [{e.get('calendar', '')}]" if e.get("calendar") else ""
                result.append(f"• {time_str} | {e['summary']}{loc}{cal} [ID: {e['id']}]")
            return f"일정 ({len(events)}개):\n" + "\n".join(result)

        elif tool_name == "calendar_create":
            from bot.services.calendar_service import create_event
            event = await create_event(
                summary=tool_input["summary"],
                start_time=tool_input["start_time"],
                end_time=tool_input["end_time"],
                description=tool_input.get("description", ""),
                location=tool_input.get("location", ""),
                all_day=tool_input.get("all_day", False),
            )
            return f"일정 생성 완료: {event['summary']} ({event['start']})"

        elif tool_name == "calendar_update":
            from bot.services.calendar_service import update_event
            event = await update_event(
                event_id=tool_input["event_id"],
                summary=tool_input.get("summary"),
                start_time=tool_input.get("start_time"),
                end_time=tool_input.get("end_time"),
                description=tool_input.get("description"),
                location=tool_input.get("location"),
            )
            return f"일정 수정 완료: {event['summary']} ({event['start']})"

        elif tool_name == "calendar_delete":
            from bot.services.calendar_service import delete_event
            await delete_event(tool_input["event_id"])
            return "일정이 삭제되었습니다."

        elif tool_name == "web_search":
            from bot.services.gemini_service import search
            try:
                search_result = await search(tool_input["query"])
                text = search_result["answer"]
                if search_result["sources"]:
                    text += "\n\n출처:\n" + "\n".join(search_result["sources"][:5])
                logger.info(f"웹 검색 결과 (앞 200자): {text[:200]}")
                return text
            except Exception as e:
                logger.error(f"웹 검색 전체 실패: {e}")
                return "검색 엔진에 일시적 문제가 있습니다. 반드시 당신이 알고 있는 지식으로 직접 답변하세요. 절대 '검색할 수 없다'거나 '다른 곳에서 확인하라'고 하지 마세요."

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
    max_tool_rounds = 10
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
                    logger.info(f"Claude 도구 호출: {block.name}({json.dumps(block.input, ensure_ascii=False)})")
                    result = await _execute_tool(block.name, block.input, user_id)
                    logger.info(f"도구 결과 ({len(result)}자): {result[:300]}")
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


def get_pending_files(user_id: int) -> list[str]:
    """전송 대기 중인 파일 경로 목록 반환 후 대기열 비우기"""
    return _pending_files.pop(user_id, [])


def clear_history(user_id: int):
    """대화 히스토리 초기화"""
    _conversations.pop(user_id, None)


def get_history_length(user_id: int) -> int:
    """현재 대화 히스토리 길이"""
    return len(_conversations.get(user_id, []))
