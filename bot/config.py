"""설정 로딩 모듈 - .env 파일에서 환경변수를 읽어옵니다."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# 프로젝트 루트 디렉토리
BASE_DIR = Path(__file__).resolve().parent.parent

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_IDS = [
    int(uid.strip())
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
]

# Google Drive OAuth 2.0
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", str(BASE_DIR / "data" / "token.json"))
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/drive"]

# 로컬 PC 파일 접근 허용 경로
ALLOWED_PATHS = [
    Path(p.strip())
    for p in os.getenv("ALLOWED_PATHS", "").split(",")
    if p.strip()
]

# 데이터베이스
DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "gs_secretary.db"))

# Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
CLAUDE_SYSTEM_PROMPT = os.getenv(
    "CLAUDE_SYSTEM_PROMPT",
    "당신은 GS Secretary라는 텔레그램 업무 비서입니다. "
    "한국어로 친절하고 간결하게 답변하세요. "
    "업무 관련 질문, 문서 작성, 요약, 번역, 아이디어 정리 등을 도와줍니다."
)
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "4096"))

# Gemini API (실시간 웹 검색용)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# 실행 모드: cloud (Drive+메모+리마인더), local (PC파일만), all (전부)
RUN_MODE = os.getenv("RUN_MODE", "all").lower()

# 로깅
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# 임시 다운로드 경로
TEMP_DOWNLOAD_PATH = Path(os.getenv("TEMP_DOWNLOAD_PATH", str(BASE_DIR / "data" / "temp")))
