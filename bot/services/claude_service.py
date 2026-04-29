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

# 모델 티어: 도구 사용 시 Sonnet, 일반 대화는 Haiku
HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = CLAUDE_MODEL  # claude-sonnet-4-20250514


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

# Gmail 도구 (cloud/all 모드)
GMAIL_TOOLS = [
    {
        "name": "gmail_list",
        "description": "Gmail 받은편지함의 이메일 목록을 조회합니다. 기본적으로 읽지 않은 메일을 보여줍니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "최대 조회 수 (기본 10)", "default": 10},
                "query": {"type": "string", "description": "Gmail 검색 쿼리 (기본 'is:unread'). 예: 'is:unread', 'from:example@gmail.com', 'subject:회의'", "default": "is:unread"}
            }
        }
    },
    {
        "name": "gmail_read",
        "description": "특정 이메일의 전체 내용을 읽습니다. gmail_list나 gmail_search로 찾은 이메일의 ID를 사용합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "읽을 이메일의 ID"}
            },
            "required": ["message_id"]
        }
    },
    {
        "name": "gmail_send",
        "description": "이메일을 전송합니다. 받는 사람, 제목, 본문을 입력합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "받는 사람 이메일 주소"},
                "subject": {"type": "string", "description": "이메일 제목"},
                "body": {"type": "string", "description": "이메일 본문 내용"}
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "gmail_search",
        "description": "Gmail에서 이메일을 검색합니다. Gmail 검색 쿼리 문법을 사용합니다. 예: 'from:user@example.com', 'subject:보고서 after:2026/01/01', 'has:attachment'",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail 검색 쿼리"},
                "max_results": {"type": "integer", "description": "최대 조회 수 (기본 10)", "default": 10}
            },
            "required": ["query"]
        }
    },
]

# CIP 슬라이드 생성 도구
SLIDE_TOOLS = [
    {
        "name": "slide_list_types",
        "description": "사용 가능한 CIP 브랜드 슬라이드 템플릿 종류를 조회합니다.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "slide_generate_cover",
        "description": "CIP 브랜드 표지(Cover) 슬라이드를 생성합니다. 프로젝트명, 부제목, 날짜를 입력받아 HTML 파일을 생성합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "프로젝트/문서 제목 (예: 'Project Albatross')"},
                "subtitle": {"type": "string", "description": "부제목 (예: 'Investment Memorandum')", "default": "Investment Memorandum"},
                "date": {"type": "string", "description": "날짜 (예: 'July 2025'). 비우면 현재 날짜", "default": ""}
            },
            "required": ["title"]
        }
    },
    {
        "name": "slide_generate_twocol",
        "description": "CIP 브랜드 2단 레이아웃 슬라이드를 생성합니다. 좌우 컬럼에 각각 제목과 본문을 입력합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_label": {"type": "string", "description": "섹션 라벨 (예: '2-1. Market Position')"},
                "title": {"type": "string", "description": "슬라이드 제목"},
                "tagline": {"type": "string", "description": "부제/태그라인"},
                "left_title": {"type": "string", "description": "왼쪽 컬럼 제목"},
                "left_body": {"type": "string", "description": "왼쪽 컬럼 본문"},
                "right_title": {"type": "string", "description": "오른쪽 컬럼 제목"},
                "right_body": {"type": "string", "description": "오른쪽 컬럼 본문"},
                "page_num": {"type": "integer", "description": "페이지 번호", "default": 1}
            },
            "required": ["section_label", "title", "tagline", "left_title", "left_body", "right_title", "right_body"]
        }
    },
    {
        "name": "slide_generate_divider",
        "description": "CIP 브랜드 섹션 구분 슬라이드를 생성합니다. IFC 서울 배경 + 큰 번호 + 섹션 제목.",
        "input_schema": {
            "type": "object",
            "properties": {
                "number": {"type": "string", "description": "섹션 번호 (예: '1.', '2.')"},
                "title": {"type": "string", "description": "섹션 제목 (예: 'Executive Summary')"},
                "page_num": {"type": "integer", "description": "페이지 번호", "default": 1}
            },
            "required": ["number", "title"]
        }
    },
    {
        "name": "slide_generate_closing",
        "description": "CIP 브랜드 종료(Thank you) 슬라이드를 생성합니다.",
        "input_schema": {
            "type": "object",
            "properties": {}
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
        tools.extend(GMAIL_TOOLS)
    tools.extend(SLIDE_TOOLS)
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
            "\n\n[문서/자료 검색 지침 — 가장 중요]"
            "\n사용자가 문서, 계약서, 보고서, 자료, 파일, offer letter 등을 찾으면:"
            "\n1. drive_search와 gmail_search를 **동시에** 호출하세요 (한 번의 응답에 두 도구를 함께 사용)."
            "\n2. 두 결과를 종합해서 답변하세요."
            "\n3. 날짜 힌트가 있으면 Gmail에서 after:/before: 쿼리를 활용하세요."
            "\n예: '24년 2월 계약서 찾아줘' → drive_search('계약서') + gmail_search('계약서 OR offer after:2024/02/01 before:2024/05/01') 동시 호출."
            "\n절대 한쪽만 검색하고 기다리지 마세요. 항상 Drive + Gmail 병렬 검색!"
            "\n\n[Google Drive 활용 지침]"
            "\ndrive_search로 키워드 검색 → drive_read로 내용 확인."
            "\n사용자가 '내 자료', '내 문서', '보고서', '파일' 등을 언급하면 Drive를 검색하세요."
        "\n\n[Google Calendar 활용 지침]"
        "\n사용자가 일정, 스케줄, 회의, 약속 등을 언급하면 Calendar 도구를 사용하세요."
        "\n오늘 날짜를 기준으로 시간을 계산하세요."
        "\n일정 생성 시 종료 시간이 없으면 시작 시간 + 1시간으로 설정하세요."
        "\n\n[Gmail 활용 지침]"
        "\n사용자가 이메일, 메일, 받은편지함 등을 언급하면 Gmail 도구를 사용하세요."
        "\ngmail_list로 메일 목록을 먼저 조회하고, gmail_read로 상세 내용을 읽으세요."
        "\n이메일 전송 시 받는 사람 주소를 반드시 확인하세요."
        "\nGmail 검색 쿼리 문법: from:, to:, subject:, has:attachment, after:, before:, is:unread 등을 사용할 수 있습니다."
        )

    extra += (
        "\n\n[CIP 슬라이드 생성 지침]"
        "\n사용자가 슬라이드, PPT, 프레젠테이션, 표지, 덱 등을 언급하면 슬라이드 도구를 사용하세요."
        "\n사용 가능한 슬라이드: cover(표지), twocol(2단 레이아웃), divider(섹션 구분), closing(종료)."
        "\nCentroid Investment Partners 브랜드 가이드라인에 맞춘 HTML 슬라이드를 생성합니다."
        "\n생성된 파일은 텔레그램으로 자동 전송됩니다."
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

            # 날짜별로 그룹화하여 명확히 구분
            from datetime import datetime as dt, timezone as tz, timedelta as td
            kst = tz(td(hours=9))
            today_str = dt.now(kst).strftime("%Y-%m-%d")
            tomorrow_str = (dt.now(kst) + td(days=1)).strftime("%Y-%m-%d")

            result = []
            current_date_label = ""
            for e in events:
                # 이벤트 날짜 추출
                start_str = e["start"]
                if "T" in start_str:
                    event_date = start_str[:10]
                    time_str = start_str[11:16]  # HH:MM
                else:
                    event_date = start_str[:10]
                    time_str = "종일"

                # 날짜 라벨 추가
                if event_date == today_str:
                    date_label = f"📅 [{event_date} 오늘]"
                elif event_date == tomorrow_str:
                    date_label = f"📅 [{event_date} 내일]"
                else:
                    date_label = f"📅 [{event_date}]"

                if date_label != current_date_label:
                    result.append(f"\n{date_label}")
                    current_date_label = date_label

                loc = f" 📍{e['location']}" if e["location"] else ""
                cal = f" [{e.get('calendar', '')}]" if e.get("calendar") else ""
                result.append(f"  • {time_str} | {e['summary']}{loc}{cal} [ID: {e['id']}]")
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

        elif tool_name == "gmail_list":
            from bot.services.gmail_service import list_emails
            max_results = tool_input.get("max_results", 10)
            query = tool_input.get("query", "is:unread")
            emails = await list_emails(max_results=max_results, query=query)
            if not emails:
                return f"'{query}' 조건에 맞는 이메일이 없습니다."
            result = []
            for e in emails:
                unread = "🔵" if "UNREAD" in e.get("label_ids", []) else "⚪"
                result.append(f"{unread} {e['date']} | {e['from']}\n  제목: {e['subject']}\n  미리보기: {e['snippet'][:80]}...\n  [ID: {e['id']}]")
            return f"이메일 목록 ({len(emails)}개):\n\n" + "\n\n".join(result)

        elif tool_name == "gmail_read":
            from bot.services.gmail_service import read_email
            email = await read_email(tool_input["message_id"])
            body = email["body"]
            if len(body) > 10000:
                body = body[:10000] + "\n\n...(본문이 잘렸습니다)"
            return (
                f"제목: {email['subject']}\n"
                f"보낸 사람: {email['from']}\n"
                f"받는 사람: {email['to']}\n"
                f"날짜: {email['date']}\n\n"
                f"본문:\n{body}"
            )

        elif tool_name == "gmail_send":
            from bot.services.gmail_service import send_email
            sent = await send_email(
                to=tool_input["to"],
                subject=tool_input["subject"],
                body=tool_input["body"],
            )
            return f"이메일 전송 완료 (ID: {sent['id']})\n받는 사람: {tool_input['to']}\n제목: {tool_input['subject']}"

        elif tool_name == "gmail_search":
            from bot.services.gmail_service import search_emails
            query = tool_input["query"]
            max_results = tool_input.get("max_results", 10)
            emails = await search_emails(query=query, max_results=max_results)
            if not emails:
                return f"'{query}' 검색 결과가 없습니다."
            result = []
            for e in emails:
                unread = "🔵" if "UNREAD" in e.get("label_ids", []) else "⚪"
                result.append(f"{unread} {e['date']} | {e['from']}\n  제목: {e['subject']}\n  미리보기: {e['snippet'][:80]}...\n  [ID: {e['id']}]")
            return f"검색 결과 ({len(emails)}개, 쿼리: {query}):\n\n" + "\n\n".join(result)

        elif tool_name == "slide_list_types":
            from bot.services.slide_service import list_slide_types
            types = list_slide_types()
            result = []
            for t in types:
                status = "✅" if t["exists"] else "❌"
                result.append(f"{status} {t['type']}: {t['label']}")
            return "CIP 슬라이드 템플릿:\n" + "\n".join(result)

        elif tool_name == "slide_generate_cover":
            from bot.services.slide_service import generate_cover_slide, save_generated_slide
            html = generate_cover_slide(
                title=tool_input["title"],
                subtitle=tool_input.get("subtitle", "Investment Memorandum"),
                date=tool_input.get("date", ""),
            )
            path = save_generated_slide(html, "cover.html")
            if user_id not in _pending_files:
                _pending_files[user_id] = []
            _pending_files[user_id].append(str(path))
            return f"표지 슬라이드 생성 완료: {tool_input['title']}. 파일을 텔레그램으로 전송합니다."

        elif tool_name == "slide_generate_twocol":
            from bot.services.slide_service import generate_twocol_slide, save_generated_slide
            html = generate_twocol_slide(
                section_label=tool_input["section_label"],
                title=tool_input["title"],
                tagline=tool_input["tagline"],
                left_title=tool_input["left_title"],
                left_body=tool_input["left_body"],
                right_title=tool_input["right_title"],
                right_body=tool_input["right_body"],
                page_num=tool_input.get("page_num", 1),
            )
            filename = f"twocol_{tool_input['section_label'][:10].replace(' ', '_')}.html"
            path = save_generated_slide(html, filename)
            if user_id not in _pending_files:
                _pending_files[user_id] = []
            _pending_files[user_id].append(str(path))
            return f"2단 레이아웃 슬라이드 생성 완료: {tool_input['title']}. 파일을 텔레그램으로 전송합니다."

        elif tool_name == "slide_generate_divider":
            from bot.services.slide_service import generate_section_divider, save_generated_slide
            html = generate_section_divider(
                number=tool_input["number"],
                title=tool_input["title"],
                page_num=tool_input.get("page_num", 1),
            )
            filename = f"divider_{tool_input['number'].replace('.', '')}.html"
            path = save_generated_slide(html, filename)
            if user_id not in _pending_files:
                _pending_files[user_id] = []
            _pending_files[user_id].append(str(path))
            return f"섹션 구분 슬라이드 생성 완료: {tool_input['number']} {tool_input['title']}. 파일을 텔레그램으로 전송합니다."

        elif tool_name == "slide_generate_closing":
            from bot.services.slide_service import generate_closing_slide, save_generated_slide
            html = generate_closing_slide()
            path = save_generated_slide(html, "closing.html")
            if user_id not in _pending_files:
                _pending_files[user_id] = []
            _pending_files[user_id].append(str(path))
            return "종료 슬라이드 생성 완료. 파일을 텔레그램으로 전송합니다."

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
    except RuntimeError as e:
        err_msg = str(e)
        if "인증" in err_msg or "drive_auth" in err_msg:
            return "Google 인증 토큰이 만료되었습니다. 관리자가 토큰을 재발급해야 합니다. 사용자에게 '현재 Google 서비스 토큰이 만료되어 접근할 수 없다'고 알려주세요."
        return f"도구 실행 오류: {e}"
    except Exception as e:
        err_msg = str(e).lower()
        if "invalid_grant" in err_msg or "token" in err_msg and "expired" in err_msg:
            return "Google OAuth 토큰이 만료되었습니다. 관리자가 토큰을 재발급해야 합니다. 사용자에게 '토큰 만료로 Google 서비스 접근 불가'라고 알려주세요."
        return f"도구 실행 오류: {e}"


async def _log_api_usage(user_id: int, input_tokens: int, output_tokens: int, tool_calls: int, model: str = None):
    """API 사용량을 DB에 기록"""
    used_model = model or CLAUDE_MODEL
    try:
        from bot.services.usage_service import log_usage
        await log_usage(user_id, used_model, input_tokens, output_tokens, tool_calls)
        from bot.services.usage_service import estimate_cost
        cost = estimate_cost(input_tokens, output_tokens, used_model)
        model_label = "Haiku" if "haiku" in used_model else "Sonnet"
        logger.info(f"API [{model_label}]: input={input_tokens}, output={output_tokens}, tools={tool_calls}, cost=${cost:.4f}")
    except Exception as e:
        logger.error(f"사용량 기록 실패: {e}")


async def chat(user_id: int, message: str, use_tools: bool = True) -> str:
    """Claude와 대화. Tool use를 지원하며 사용자별 히스토리를 유지합니다.
    use_tools=False이면 도구 없이 Haiku로 가볍게 응답합니다.
    """
    client = _get_client()
    tools = _build_tools() if use_tools else []
    system_text = _build_system_prompt()

    # 프롬프트 캐싱: 시스템 프롬프트에 cache_control 적용
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]

    # 모델 선택: 도구 사용 시 Sonnet, 일반 대화는 Haiku
    model = SONNET_MODEL if use_tools else HAIKU_MODEL

    if user_id not in _conversations:
        _conversations[user_id] = []

    history = _conversations[user_id]

    if not message or not message.strip():
        return "메시지를 입력해주세요."

    history.append({"role": "user", "content": message})

    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
        _conversations[user_id] = history

    # Claude API 호출 (tool use 루프)
    max_tool_rounds = 10
    total_input_tokens = 0
    total_output_tokens = 0
    total_tool_calls = 0
    used_model = model

    # 히스토리에서 빈 content 제거 (이전 버그로 인한 잔여 데이터 방어)
    history[:] = [
        msg for msg in history
        if not (isinstance(msg.get("content"), str) and not msg["content"].strip())
    ]

    for round_num in range(max_tool_rounds):
        kwargs = {
            "model": model,
            "max_tokens": CLAUDE_MAX_TOKENS,
            "system": system,
            "messages": history,
        }
        if tools:
            # 프롬프트 캐싱: 마지막 도구에 cache_control 적용
            cached_tools = [t.copy() for t in tools]
            cached_tools[-1]["cache_control"] = {"type": "ephemeral"}
            kwargs["tools"] = cached_tools

        response = await client.messages.create(**kwargs)

        # 캐시 히트 로깅
        if hasattr(response, "usage"):
            cache_read = getattr(response.usage, "cache_read_input_tokens", 0)
            cache_create = getattr(response.usage, "cache_creation_input_tokens", 0)
            if cache_read > 0:
                logger.info(f"프롬프트 캐시 히트: {cache_read} tokens (절감)")
            elif cache_create > 0:
                logger.info(f"프롬프트 캐시 생성: {cache_create} tokens")

        # 토큰 사용량 누적
        if hasattr(response, "usage"):
            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

        # 응답에서 텍스트와 tool_use 분리
        if response.stop_reason == "tool_use":
            # 전체 content를 히스토리에 추가
            history.append({"role": "assistant", "content": response.content})

            # 각 tool_use에 대해 실행
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    total_tool_calls += 1
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
            # 사용량 기록
            await _log_api_usage(user_id, total_input_tokens, total_output_tokens, total_tool_calls, used_model)

            # 최종 텍스트 응답
            text_parts = [block.text for block in response.content if hasattr(block, "text") and block.text]
            assistant_message = "\n".join(text_parts) if text_parts else "처리가 완료되었습니다."
            history.append({"role": "assistant", "content": assistant_message})
            return assistant_message

    # 도구 호출 초과 시에도 사용량 기록
    await _log_api_usage(user_id, total_input_tokens, total_output_tokens, total_tool_calls, used_model)
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
