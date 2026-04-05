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

# 실행 모드: cloud (Drive+메모+리마인더), local (PC파일만), all (전부)
RUN_MODE = os.getenv("RUN_MODE", "all").lower()

# 로깅
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# 임시 다운로드 경로
TEMP_DOWNLOAD_PATH = Path(os.getenv("TEMP_DOWNLOAD_PATH", str(BASE_DIR / "data" / "temp")))
