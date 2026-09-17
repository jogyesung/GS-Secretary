"""Claude AI 대화 서비스 - Anthropic API 연동 + Tool Use

구조:
- 히스토리(_conversations)에는 텍스트 턴만 남긴다. 도구 왕복(tool_use/tool_result)은 턴 안에서만 쓰고
  턴이 끝나면 최종 답변 + <tool_log> 요약으로 접는다 → 슬라이스해도 API 메시지 규칙이 깨지지 않는다.
- 프롬프트 캐싱: tools(마지막) + system(안정 블록) + 마지막 user 메시지에 cache_control.
  시스템 프롬프트에는 분 단위 시각을 넣지 않는다 (넣으면 매 분 캐시가 깨짐).
- 위험 도구(메일 발송, 일정 삭제)는 바로 실행하지 않고 확인 버튼을 거친다.
"""

import json
import logging
import secrets
import time
from datetime import datetime, timezone, timedelta

import anthropic

from bot.config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_LIGHT_MODEL, CLAUDE_SYSTEM_PROMPT,
    CLAUDE_MAX_TOKENS, CLAUDE_EFFORT, CLAUDE_HEAVY_EFFORT,
    ALLOWED_PATHS, RUN_MODE, GEMINI_API_KEY, DART_API_KEY,
)

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 사용자별 대화 히스토리 (텍스트 턴만, 메모리 내 관리)
_conversations: dict[int, list[dict]] = {}

# 파일 전송 대기열 (user_id → list of file paths)
_pending_files: dict[int, list[str]] = {}

# 확인 대기 중인 위험 작업 (user_id → action)
_pending_actions: dict[int, dict] = {}

MAX_HISTORY = 20          # 메시지 수 (user+assistant 합산)
MAX_TOOL_ROUNDS = 10
TOOL_LOG_RESULT_CHARS = 500
TOOL_LOG_MAX_CHARS = 3000
PENDING_ACTION_TTL = 600  # 초

# 모델 티어
HAIKU_MODEL = CLAUDE_LIGHT_MODEL
SONNET_MODEL = CLAUDE_MODEL

# 실행 전 사용자 확인이 필요한 도구
CONFIRM_TOOLS = {"gmail_send", "gmail_reply", "calendar_delete"}


def _get_client() -> anthropic.AsyncAnthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
    return anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


def _model_params(model: str, heavy: bool) -> dict:
    """모델별 thinking/effort 파라미터. Haiku 4.5는 adaptive thinking 미지원."""
    if "haiku" in model:
        return {}
    return {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": CLAUDE_HEAVY_EFFORT if heavy else CLAUDE_EFFORT},
    }


# === Claude가 사용할 수 있는 도구 정의 ===

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
        "description": "메모를 키워드로 검색합니다. 결과가 없으면 유의어 2~3개로 다시 검색해보세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "검색할 키워드"}
            },
            "required": ["keyword"]
        }
    },
]

# 리마인더 도구 (cloud/all 모드)
REMINDER_TOOLS = [
    {
        "name": "create_reminder",
        "description": "리마인더(알림)를 만듭니다. '내일 9시에 세무사 통화 알려줘', '30분 뒤에 알려줘' 같은 요청에 사용하세요. 일정(캘린더)이 아니라 텔레그램 알림입니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "알림 내용"},
                "remind_at": {"type": "string", "description": "알림 시각, KST, 형식 YYYY-MM-DDTHH:MM (예: 2026-09-17T09:00). 시스템 프롬프트의 날짜표와 현재 시각을 기준으로 계산"},
                "repeat_daily": {"type": "boolean", "description": "매일 같은 시각 반복 여부 (기본 false)", "default": False}
            },
            "required": ["message", "remind_at"]
        }
    },
    {
        "name": "list_reminders",
        "description": "설정된 리마인더 목록을 조회합니다.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "delete_reminder",
        "description": "리마인더를 삭제합니다. list_reminders로 조회한 ID를 사용합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "integer", "description": "삭제할 리마인더 ID"}
            },
            "required": ["reminder_id"]
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
        "input_schema": {"type": "object", "properties": {}}
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
        "description": "Google Calendar 일정을 삭제합니다. 실행 전 사용자 확인 버튼을 거칩니다. calendar_list로 조회한 일정의 ID를 사용합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "삭제할 일정의 ID"},
                "summary": {"type": "string", "description": "삭제할 일정 제목 (확인 메시지 표시용)", "default": ""}
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
        "description": "새 이메일을 전송합니다. 실행 전 사용자가 확인 버튼을 눌러야 실제로 발송됩니다. 기존 메일에 답장할 때는 gmail_reply를 쓰세요.",
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
        "name": "gmail_reply",
        "description": "기존 이메일에 같은 스레드로 답장합니다 (받는 사람·제목은 원본에서 자동). 실행 전 사용자 확인 버튼을 거칩니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "답장할 원본 이메일의 ID"},
                "body": {"type": "string", "description": "답장 본문"}
            },
            "required": ["message_id", "body"]
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

# 포트폴리오 / DART 공시 도구
PORTFOLIO_TOOLS = [
    {
        "name": "list_portfolio",
        "description": "사용자가 공시 알림용으로 등록한 포트폴리오 종목 목록을 조회합니다.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "recent_disclosures",
        "description": "특정 기업의 최근 DART 전자공시 목록을 조회합니다. '삼성전자 최근 공시 뭐야?' 같은 질문에 사용하세요 (웹 검색보다 정확).",
        "input_schema": {
            "type": "object",
            "properties": {
                "corp_name": {"type": "string", "description": "기업명 (예: 삼성전자)"},
                "days": {"type": "integer", "description": "최근 며칠 (기본 7)", "default": 7}
            },
            "required": ["corp_name"]
        }
    },
]

# CIP 슬라이드 생성 도구
SLIDE_TOOLS = [
    {
        "name": "slide_list_types",
        "description": "사용 가능한 CIP 브랜드 슬라이드 템플릿 종류를 조회합니다.",
        "input_schema": {"type": "object", "properties": {}}
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
        "input_schema": {"type": "object", "properties": {}}
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
    """현재 모드에 따라 사용 가능한 도구 목록 생성 (순서 고정 → 캐시 안정)"""
    tools = []
    if RUN_MODE in ("local", "all"):
        tools.extend(PC_TOOLS)
    if RUN_MODE in ("cloud", "all"):
        tools.extend(MEMO_TOOLS)
        tools.extend(REMINDER_TOOLS)
        tools.extend(DRIVE_TOOLS)
        tools.extend(CALENDAR_TOOLS)
        tools.extend(GMAIL_TOOLS)
    if DART_API_KEY:
        tools.extend(PORTFOLIO_TOOLS)
    tools.extend(SLIDE_TOOLS)
    if GEMINI_API_KEY:
        tools.extend(SEARCH_TOOLS)
    return tools


def _build_system_prompt() -> str:
    """시스템 프롬프트 (하루 단위로만 바뀜 → 프롬프트 캐시 유지. 분 단위 시각은 user 메시지에 붙임)"""
    now = datetime.now(KST)
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    today = f"{now.strftime('%Y년 %m월 %d일')} {weekdays[now.weekday()]}"

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
        f"\n\n오늘: {today} (KST). 현재 시각은 사용자 메시지 끝에 붙어 있어요."
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
            "\n\n[리마인더 지침]"
            "\n'~에 알려줘', '리마인드', '까먹지 않게' 같은 요청은 캘린더가 아니라 create_reminder를 쓰세요."
            "\n\n[Gmail 활용 지침]"
            "\n사용자가 이메일, 메일, 받은편지함 등을 언급하면 Gmail 도구를 사용하세요."
            "\ngmail_list로 메일 목록을 먼저 조회하고, gmail_read로 상세 내용을 읽으세요."
            "\n받은 메일에 답장할 때는 gmail_reply(같은 스레드), 새 메일은 gmail_send."
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
        "\n\n[확인이 필요한 작업]"
        "\n메일 발송(gmail_send, gmail_reply)과 일정 삭제(calendar_delete)는 도구를 호출해도 바로 실행되지 않고"
        " 사용자에게 확인 버튼이 표시돼요. 도구 결과에 '[확인 필요]'가 오면 내용을 짧게 요약해서 보여주고"
        " '아래 버튼으로 확인해주세요'라고만 안내하세요. 이미 보냈다고 말하지 마세요."
        "\n\n[내부 기록]"
        "\n이전 assistant 메시지 끝의 <tool_log>…</tool_log>는 그 답변에서 사용한 도구와 결과(파일 ID 등)의 내부 기록이에요."
        " 후속 질문에 참고하되, 답변에 <tool_log>를 절대 포함하거나 흉내 내지 마세요."
        "\n\n[중요 규칙]"
        "\n- 도구 실행이 실패하더라도 절대 '할 수 없습니다', '검색이 안 됩니다'라고만 답하지 마세요."
        "\n- 웹 검색이 실패하면, 반드시 당신이 알고 있는 지식으로 직접 답변하세요."
        "\n- '다른 사이트에서 확인하세요', '직접 검색해보세요' 같은 떠넘기기는 절대 하지 마세요."
        "\n- 사용자는 당신에게 답을 원하는 것이지, 다른 곳으로 안내받길 원하는 게 아닙니다."
        "\n- 답변 서식은 Markdown(**굵게**, 불릿, `코드`)만 사용하고 표(table)는 쓰지 마세요 (텔레그램에서 깨짐)."
    )

    return CLAUDE_SYSTEM_PROMPT + extra


async def _queue_slide_file(user_id: int, html_path: str):
    """슬라이드를 PNG로 렌더링해서 전송 대기열에 추가 (실패 시 HTML)"""
    from bot.services.slide_render import render_html_to_png
    png_path = await render_html_to_png(str(html_path))
    _pending_files.setdefault(user_id, []).append(png_path or str(html_path))


# === 위험 작업 확인 ===
def _describe_action(tool_name: str, tool_input: dict) -> str:
    if tool_name == "gmail_send":
        body = tool_input.get("body", "")
        preview = body if len(body) <= 600 else body[:600] + "…"
        return f"📧 메일 발송\n받는 사람: {tool_input.get('to')}\n제목: {tool_input.get('subject')}\n\n{preview}"
    if tool_name == "gmail_reply":
        body = tool_input.get("body", "")
        preview = body if len(body) <= 600 else body[:600] + "…"
        return f"↩️ 메일 답장 (원본 ID: {tool_input.get('message_id')})\n\n{preview}"
    if tool_name == "calendar_delete":
        title = tool_input.get("summary") or tool_input.get("event_id")
        return f"🗑 일정 삭제: {title}"
    return f"{tool_name} {json.dumps(tool_input, ensure_ascii=False)[:300]}"


def _request_confirmation(user_id: int, tool_name: str, tool_input: dict) -> str:
    token = secrets.token_hex(4)
    _pending_actions[user_id] = {
        "token": token, "tool": tool_name, "input": tool_input,
        "summary": _describe_action(tool_name, tool_input), "created": time.time(),
    }
    return (
        "[확인 필요] 아직 실행되지 않았어요. 사용자에게 확인 버튼이 표시됩니다. "
        "내용을 짧게 요약해서 보여주고 버튼으로 확인해달라고만 안내하세요.\n"
        + _pending_actions[user_id]["summary"]
    )


def get_pending_action(user_id: int) -> dict | None:
    """확인 대기 중인 작업 (만료된 것은 버림)"""
    action = _pending_actions.get(user_id)
    if action and time.time() - action["created"] > PENDING_ACTION_TTL:
        _pending_actions.pop(user_id, None)
        return None
    return action


def pop_pending_action(user_id: int, token: str) -> dict | None:
    action = get_pending_action(user_id)
    if action and action["token"] == token:
        return _pending_actions.pop(user_id)
    return None


async def execute_confirmed_action(user_id: int, action: dict) -> str:
    """확인 버튼을 누른 작업 실행"""
    return await _execute_tool(action["tool"], action["input"], user_id, confirmed=True)


# === 도구 실행 함수 ===
async def _execute_tool(tool_name: str, tool_input: dict, user_id: int, confirmed: bool = False) -> str:
    """도구를 실행하고 결과를 반환"""
    if tool_name in CONFIRM_TOOLS and not confirmed:
        return _request_confirmation(user_id, tool_name, tool_input)

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
            memos = await get_memos(user_id, tool_input.get("limit", 10))
            if not memos:
                return "저장된 메모가 없습니다."
            return "\n".join(f"#{m['id']} [{m['created_at'][:16]}] {m['content']}" for m in memos)

        elif tool_name == "search_memos":
            from bot.services.memo_service import search_memos
            memos = await search_memos(user_id, tool_input["keyword"])
            if not memos:
                return f"'{tool_input['keyword']}'에 대한 검색 결과가 없습니다."
            return "\n".join(f"#{m['id']} [{m['created_at'][:16]}] {m['content']}" for m in memos)

        elif tool_name == "create_reminder":
            from bot.services import reminder_service
            remind_at = datetime.fromisoformat(tool_input["remind_at"][:16])
            if tool_input.get("repeat_daily"):
                cron_expr = f"{remind_at.minute} {remind_at.hour} * * *"
                next_run = reminder_service.next_daily_run(remind_at.hour, remind_at.minute)
                rid = await reminder_service.create_recurring_reminder(user_id, tool_input["message"], cron_expr, next_run)
                return f"매일 {remind_at.strftime('%H:%M')} 반복 리마인더 설정 완료 (ID: {rid})"
            if remind_at <= reminder_service.now_kst():
                return "알림 시각이 이미 지났어요. 현재 시각을 확인하고 다시 계산하세요."
            rid = await reminder_service.create_reminder(user_id, tool_input["message"], remind_at)
            return f"리마인더 설정 완료 (ID: {rid}): {remind_at.strftime('%m/%d %H:%M')} — {tool_input['message']}"

        elif tool_name == "list_reminders":
            from bot.services import reminder_service
            reminders = await reminder_service.get_active_reminders(user_id)
            if not reminders:
                return "설정된 리마인더가 없습니다."
            lines = []
            for r in reminders:
                when = f"매일 {r['cron_expr'].split()[1]}:{int(r['cron_expr'].split()[0]):02d}" if r["is_recurring"] else r["remind_at"][:16]
                lines.append(f"#{r['id']} | {when} | {r['message']}")
            return "\n".join(lines)

        elif tool_name == "delete_reminder":
            from bot.services import reminder_service
            ok = await reminder_service.delete_reminder(user_id, int(tool_input["reminder_id"]))
            return "리마인더 삭제 완료" if ok else "해당 ID의 리마인더가 없습니다."

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
            from pathlib import Path
            file_info = await get_file_info(tool_input["file_id"])
            file_name = Path(file_info["name"] or "file").name or "file"
            dest = str(TEMP_DOWNLOAD_PATH / file_name)
            await download_file(tool_input["file_id"], dest)
            _pending_files.setdefault(user_id, []).append(dest)
            return f"파일 '{file_name}' 전송 준비 완료. 텔레그램으로 보내드리겠습니다."

        elif tool_name == "drive_read":
            from bot.services.drive_service import read_file_content
            file_data = await read_file_content(tool_input["file_id"], max_chars=tool_input.get("max_chars", 15000))
            return f"파일: {file_data['name']} ({file_data['mime_type']})\n\n{file_data['content']}"

        elif tool_name == "drive_list":
            from bot.services.drive_service import list_files
            files = await list_files(tool_input.get("folder_id", "root"), tool_input.get("page_size", 20))
            if not files:
                return "폴더가 비어있습니다."
            result = []
            for f in files:
                icon = "📁" if f.get("mimeType") == "application/vnd.google-apps.folder" else "📄"
                result.append(f"{icon} {f['name']} [ID: {f['id']}]")
            return f"Drive 파일 목록 ({len(files)}개):\n" + "\n".join(result)

        elif tool_name == "calendar_list_calendars":
            from bot.services.calendar_service import list_calendars
            calendars = await list_calendars()
            if not calendars:
                return "등록된 캘린더가 없습니다."
            result = [f"• {c['summary']}{' (기본)' if c['primary'] else ''} [ID: {c['id']}]" for c in calendars]
            return f"캘린더 목록 ({len(calendars)}개):\n" + "\n".join(result)

        elif tool_name == "calendar_list":
            from bot.services.calendar_service import list_events
            days = tool_input.get("days", 1)
            events = await list_events(days=days, max_results=tool_input.get("max_results", 10))
            if not events:
                return f"향후 {days}일 내 일정이 없습니다."

            today_str = datetime.now(KST).strftime("%Y-%m-%d")
            tomorrow_str = (datetime.now(KST) + timedelta(days=1)).strftime("%Y-%m-%d")
            result = []
            current_date_label = ""
            for e in events:
                start_str = e["start"]
                event_date = start_str[:10]
                time_str = start_str[11:16] if "T" in start_str else "종일"
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
            from bot.services.calendar_service import create_event, check_conflicts
            conflict_warning = ""
            if not tool_input.get("all_day", False):
                try:
                    conflicts = await check_conflicts(tool_input["start_time"], tool_input["end_time"])
                    if conflicts:
                        items = []
                        for c in conflicts:
                            t = c["start"][11:16] if "T" in c["start"] else "종일"
                            items.append(f"  - {t} {c['summary']}")
                        conflict_warning = f"\n⚠️ 충돌 감지! 같은 시간대에 {len(conflicts)}건의 일정이 있어요:\n" + "\n".join(items) + "\n"
                except Exception:
                    pass
            event = await create_event(
                summary=tool_input["summary"],
                start_time=tool_input["start_time"],
                end_time=tool_input["end_time"],
                description=tool_input.get("description", ""),
                location=tool_input.get("location", ""),
                all_day=tool_input.get("all_day", False),
            )
            return f"일정 생성 완료: {event['summary']} ({event['start']}){conflict_warning}"

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
            query = tool_input.get("query", "is:unread")
            emails = await list_emails(max_results=tool_input.get("max_results", 10), query=query)
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
            sent = await send_email(to=tool_input["to"], subject=tool_input["subject"], body=tool_input["body"])
            return f"이메일 전송 완료 (ID: {sent['id']})\n받는 사람: {tool_input['to']}\n제목: {tool_input['subject']}"

        elif tool_name == "gmail_reply":
            from bot.services.gmail_service import reply_email
            sent = await reply_email(message_id=tool_input["message_id"], body=tool_input["body"])
            return f"답장 전송 완료 (ID: {sent['id']})\n받는 사람: {sent.get('to', '')}\n제목: {sent.get('subject', '')}"

        elif tool_name == "gmail_search":
            from bot.services.gmail_service import search_emails
            query = tool_input["query"]
            emails = await search_emails(query=query, max_results=tool_input.get("max_results", 10))
            if not emails:
                return f"'{query}' 검색 결과가 없습니다."
            result = []
            for e in emails:
                unread = "🔵" if "UNREAD" in e.get("label_ids", []) else "⚪"
                result.append(f"{unread} {e['date']} | {e['from']}\n  제목: {e['subject']}\n  미리보기: {e['snippet'][:80]}...\n  [ID: {e['id']}]")
            return f"검색 결과 ({len(emails)}개, 쿼리: {query}):\n\n" + "\n\n".join(result)

        elif tool_name == "list_portfolio":
            from bot.services import dart_service
            stocks = await dart_service.list_stocks(user_id)
            if not stocks:
                return "등록된 포트폴리오 종목이 없습니다. /portfolio_add <종목명> 으로 추가할 수 있어요."
            return "\n".join(f"#{s['id']} {s['corp_name']} (corp_code: {s['corp_code'] or '미확인'})" for s in stocks)

        elif tool_name == "recent_disclosures":
            from bot.services import dart_service
            corp = await dart_service.find_corp_code(tool_input["corp_name"])
            if not corp:
                return f"'{tool_input['corp_name']}' 기업을 DART에서 찾지 못했습니다."
            items = await dart_service.get_recent_disclosures(corp["corp_code"], days=tool_input.get("days", 7))
            if not items:
                return f"{corp['corp_name']}: 최근 {tool_input.get('days', 7)}일간 공시가 없습니다."
            lines = [f"• {d['rcept_dt']} | {d['report_nm']} (제출: {d['flr_nm']}) {d['url']}" for d in items[:15]]
            return f"{corp['corp_name']} 최근 공시 ({len(items)}건):\n" + "\n".join(lines)

        elif tool_name == "slide_list_types":
            from bot.services.slide_service import list_slide_types
            types = list_slide_types()
            return "CIP 슬라이드 템플릿:\n" + "\n".join(
                f"{'✅' if t['exists'] else '❌'} {t['type']}: {t['label']}" for t in types
            )

        elif tool_name == "slide_generate_cover":
            from bot.services.slide_service import generate_cover_slide, save_generated_slide
            html = generate_cover_slide(
                title=tool_input["title"],
                subtitle=tool_input.get("subtitle", "Investment Memorandum"),
                date=tool_input.get("date", ""),
            )
            path = save_generated_slide(html, "cover.html")
            await _queue_slide_file(user_id, path)
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
            await _queue_slide_file(user_id, path)
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
            await _queue_slide_file(user_id, path)
            return f"섹션 구분 슬라이드 생성 완료: {tool_input['number']} {tool_input['title']}. 파일을 텔레그램으로 전송합니다."

        elif tool_name == "slide_generate_closing":
            from bot.services.slide_service import generate_closing_slide, save_generated_slide
            html = generate_closing_slide()
            path = save_generated_slide(html, "closing.html")
            await _queue_slide_file(user_id, path)
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
        if "invalid_grant" in err_msg or ("token" in err_msg and "expired" in err_msg):
            return "Google OAuth 토큰이 만료되었습니다. 관리자가 토큰을 재발급해야 합니다. 사용자에게 '토큰 만료로 Google 서비스 접근 불가'라고 알려주세요."
        return f"도구 실행 오류: {e}"


# === 대화 히스토리 영속화 (봇 재시작 후에도 대화 맥락 유지) ===
async def _load_history_from_db(user_id: int) -> list[dict]:
    """DB에서 최근 대화 히스토리 로드 (텍스트 턴만 저장됨)"""
    try:
        import aiosqlite
        from bot.config import DATABASE_PATH
        async with aiosqlite.connect(DATABASE_PATH) as db:
            rows = await db.execute_fetchall(
                "SELECT role, content FROM conversations WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, MAX_HISTORY),
            )
        messages = [{"role": r[0], "content": r[1]} for r in reversed(rows)]
        return _normalize_history(messages)
    except Exception as e:
        logger.warning(f"대화 히스토리 로드 실패: {e}")
        return []


async def _save_turn_to_db(user_id: int, role: str, content: str):
    """대화 턴을 DB에 저장하고 오래된 기록 정리"""
    try:
        import aiosqlite
        from bot.config import DATABASE_PATH
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                "INSERT INTO conversations (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content),
            )
            await db.execute(
                "DELETE FROM conversations WHERE user_id = ? AND id NOT IN "
                "(SELECT id FROM conversations WHERE user_id = ? ORDER BY id DESC LIMIT 40)",
                (user_id, user_id),
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"대화 히스토리 저장 실패: {e}")


async def _clear_history_db(user_id: int):
    try:
        import aiosqlite
        from bot.config import DATABASE_PATH
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
            await db.commit()
    except Exception as e:
        logger.warning(f"대화 히스토리 삭제 실패: {e}")


def _normalize_history(messages: list[dict]) -> list[dict]:
    """API 제약 맞추기: 빈 메시지 제거, 첫 메시지는 user, 길이 제한"""
    cleaned = [
        m for m in messages
        if not (isinstance(m.get("content"), str) and not m["content"].strip())
    ]
    if len(cleaned) > MAX_HISTORY:
        cleaned = cleaned[-MAX_HISTORY:]
    while cleaned and (cleaned[0]["role"] != "user" or not isinstance(cleaned[0].get("content"), str)):
        cleaned.pop(0)
    return cleaned


async def _log_api_usage(
    user_id: int, input_tokens: int, output_tokens: int, tool_calls: int, model: str = None,
    cache_read: int = 0, cache_creation: int = 0,
):
    """API 사용량을 DB에 기록"""
    used_model = model or CLAUDE_MODEL
    try:
        from bot.services.usage_service import log_usage, estimate_cost
        await log_usage(user_id, used_model, input_tokens, output_tokens, tool_calls, cache_read, cache_creation)
        cost = estimate_cost(input_tokens, output_tokens, used_model, cache_read, cache_creation)
        label = "Haiku" if "haiku" in used_model else "Sonnet" if "sonnet" in used_model else used_model
        logger.info(
            f"API [{label}]: input={input_tokens}, cache_read={cache_read}, cache_write={cache_creation}, "
            f"output={output_tokens}, tools={tool_calls}, cost=${cost:.4f}"
        )
    except Exception as e:
        logger.error(f"사용량 기록 실패: {e}")


def _with_cache_marker(messages: list[dict]) -> list[dict]:
    """마지막 메시지의 마지막 블록에 cache_control (히스토리 증분 캐싱)"""
    if not messages:
        return messages
    out = list(messages)
    last = dict(out[-1])
    content = last["content"]
    if isinstance(content, str):
        last["content"] = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        blocks = list(content)
        blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        last["content"] = blocks
    out[-1] = last
    return out


def _tool_log_line(name: str, tool_input: dict, result: str) -> str:
    args = json.dumps(tool_input, ensure_ascii=False)
    if len(args) > 200:
        args = args[:200] + "…"
    body = result.replace("\n", " ")
    if len(body) > TOOL_LOG_RESULT_CHARS:
        body = body[:TOOL_LOG_RESULT_CHARS] + "…"
    return f"- {name}({args}) → {body}"


def _strip_tool_log(text: str) -> str:
    """혹시 모델이 답변에 <tool_log>를 흉내 내면 제거"""
    if "<tool_log>" not in text:
        return text
    import re
    return re.sub(r"<tool_log>.*?</tool_log>", "", text, flags=re.DOTALL).strip()


async def chat(
    user_id: int,
    message: str,
    *,
    heavy: bool = False,
    model: str | None = None,
    content_blocks: list[dict] | None = None,
    history_text: str | None = None,
) -> str:
    """Claude와 대화 (도구 항상 사용 가능).

    heavy=True면 Sonnet + 높은 effort, 아니면 Haiku. model로 직접 지정 가능.
    content_blocks: 이미지 등 멀티모달 블록 (이번 턴 API 호출에만 사용).
    history_text: 히스토리/DB에 남길 user 텍스트 (기본 message). 이미지처럼 저장 못 하는 입력은
                  요약 텍스트를 넘긴다.
    """
    if not message or not message.strip():
        return "메시지를 입력해주세요."

    client = _get_client()
    tools = _build_tools()
    model = model or (SONNET_MODEL if heavy else HAIKU_MODEL)

    system = [{"type": "text", "text": _build_system_prompt(), "cache_control": {"type": "ephemeral"}}]
    cached_tools = [dict(t) for t in tools]
    cached_tools[-1]["cache_control"] = {"type": "ephemeral"}

    if user_id not in _conversations:
        _conversations[user_id] = await _load_history_from_db(user_id)
    history = _conversations[user_id]

    # 분 단위 시각은 시스템 프롬프트가 아니라 user 메시지에 (캐시 안정성)
    time_note = f"\n\n(현재 시각 {datetime.now(KST).strftime('%H:%M')} KST)"
    stored_user_text = (history_text or message) + time_note
    history.append({"role": "user", "content": stored_user_text})
    history[:] = _normalize_history(history)
    _conversations[user_id] = history
    await _save_turn_to_db(user_id, "user", stored_user_text)

    if content_blocks:
        api_user_content = list(content_blocks) + [{"type": "text", "text": message + time_note}]
    else:
        api_user_content = stored_user_text
    # 이번 턴 작업용 메시지 목록 (도구 왕복 포함). 히스토리에는 접어서 저장.
    messages = history[:-1] + [{"role": "user", "content": api_user_content}]

    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "tools": 0}
    tool_log: list[str] = []
    final_text = None

    for _ in range(MAX_TOOL_ROUNDS):
        response = await client.messages.create(
            model=model,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=system,
            tools=cached_tools,
            messages=_with_cache_marker(messages),
            **_model_params(model, heavy),
        )

        usage = getattr(response, "usage", None)
        if usage:
            totals["input"] += usage.input_tokens or 0
            totals["output"] += usage.output_tokens or 0
            totals["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
            totals["cache_creation"] += getattr(usage, "cache_creation_input_tokens", 0) or 0

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                totals["tools"] += 1
                logger.info(f"Claude 도구 호출: {block.name}({json.dumps(block.input, ensure_ascii=False)[:300]})")
                result = await _execute_tool(block.name, block.input, user_id)
                logger.info(f"도구 결과 ({len(result)}자): {result[:200]}")
                tool_log.append(_tool_log_line(block.name, block.input, result))
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
            messages.append({"role": "user", "content": tool_results})
            continue

        text_parts = [b.text for b in response.content if getattr(b, "type", "") == "text" and b.text]
        final_text = "\n".join(text_parts).strip()
        if response.stop_reason == "max_tokens":
            final_text = (final_text or "") + "\n\n(답변이 길어 잘렸어요. 이어서 말해달라고 해주세요.)"
        elif response.stop_reason == "refusal":
            final_text = final_text or "이 요청은 안전 정책상 답변할 수 없어요."
        if not final_text:
            final_text = "처리가 완료되었습니다."
        break

    await _log_api_usage(
        user_id, totals["input"], totals["output"], totals["tools"], model,
        totals["cache_read"], totals["cache_creation"],
    )

    if final_text is None:
        final_text = "도구 호출이 너무 많아 중단했어요. 요청을 나눠서 다시 시도해주세요."

    final_text = _strip_tool_log(final_text)

    # 턴 종료: 도구 왕복은 접어서 텍스트 한 덩어리로 저장
    stored_assistant = final_text
    if tool_log:
        log_text = "\n".join(tool_log)
        if len(log_text) > TOOL_LOG_MAX_CHARS:
            log_text = log_text[:TOOL_LOG_MAX_CHARS] + "\n…"
        stored_assistant += f"\n\n<tool_log>\n{log_text}\n</tool_log>"
    history.append({"role": "assistant", "content": stored_assistant})
    await _save_turn_to_db(user_id, "assistant", stored_assistant)
    return final_text


async def simple_completion(prompt: str, *, system: str = "", model: str | None = None,
                            max_tokens: int = 2048, user_id: int = 0) -> str:
    """히스토리·도구 없는 단발 호출 (브리핑 요약 등 내부 작업용). 기본 Haiku."""
    client = _get_client()
    model = model or HAIKU_MODEL
    kwargs = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if system:
        kwargs["system"] = system
    kwargs.update(_model_params(model, heavy=False))
    response = await client.messages.create(**kwargs)
    usage = getattr(response, "usage", None)
    if usage:
        await _log_api_usage(
            user_id, usage.input_tokens or 0, usage.output_tokens or 0, 0, model,
            getattr(usage, "cache_read_input_tokens", 0) or 0,
            getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )
    return "\n".join(b.text for b in response.content if getattr(b, "type", "") == "text" and b.text).strip()


def get_pending_files(user_id: int) -> list[str]:
    """전송 대기 중인 파일 경로 목록 반환 후 대기열 비우기"""
    return _pending_files.pop(user_id, [])


async def clear_history(user_id: int):
    """대화 히스토리 초기화 (메모리 + DB)"""
    _conversations.pop(user_id, None)
    _pending_actions.pop(user_id, None)
    await _clear_history_db(user_id)


def get_history_length(user_id: int) -> int:
    """현재 대화 히스토리 길이"""
    return len(_conversations.get(user_id, []))
