"""설정 로딩 모듈 - .env 파일에서 환경변수를 읽어옵니다."""

import os
import base64
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
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
]


def _restore_google_files():
    """환경변수에서 Google 인증 파일 복원 (클라우드 배포용)"""
    creds_b64 = os.getenv("GOOGLE_CREDENTIALS_B64", "")
    token_b64 = os.getenv("GOOGLE_TOKEN_B64", "")

    if creds_b64 and not Path(GOOGLE_CREDENTIALS_FILE).exists():
        Path(GOOGLE_CREDENTIALS_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(GOOGLE_CREDENTIALS_FILE).write_bytes(base64.b64decode(creds_b64))

    if token_b64:
        Path(GOOGLE_TOKEN_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(GOOGLE_TOKEN_FILE).write_bytes(base64.b64decode(token_b64))


_restore_google_files()

# 로컬 PC 파일 접근 허용 경로
ALLOWED_PATHS = [
    Path(p.strip())
    for p in os.getenv("ALLOWED_PATHS", "").split(",")
    if p.strip()
]

# 데이터베이스 (Railway는 Volume을 /data에 마운트하고 DATABASE_PATH=/data/gs_secretary.db 권장)
DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "gs_secretary.db"))
# Railway가 볼륨을 마운트하면 이 환경변수를 넣어줌. 없으면 재배포 시 DB가 사라짐.
RAILWAY_VOLUME_MOUNT_PATH = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "")
IS_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))

# DB 야간 백업을 올릴 Google Drive 폴더명 (빈 값이면 백업 안 함)
DB_BACKUP_DRIVE_FOLDER = os.getenv("DB_BACKUP_DRIVE_FOLDER", "GS-Secretary 백업")

# Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
CLAUDE_LIGHT_MODEL = os.getenv("CLAUDE_LIGHT_MODEL", "claude-haiku-4-5")
# Sonnet 5는 adaptive thinking이 기본이라 채팅은 낮은 effort로 (low/medium/high)
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "low")
CLAUDE_HEAVY_EFFORT = os.getenv("CLAUDE_HEAVY_EFFORT", "medium")
CLAUDE_SYSTEM_PROMPT = os.getenv(
    "CLAUDE_SYSTEM_PROMPT",
    "당신의 이름은 '카리나'이고, GS Secretary 텔레그램 업무 비서예요.\n\n"
    "[카리나 성격 & 말투]\n"
    "- 밝고 친근한 '~요' 체를 사용해요. 반말은 쓰지 않아요.\n"
    "- 이모지를 적절히 섞어 써요 (📂🔍✅❌💡 등). 하지만 도배하지는 않아요.\n"
    "- 가끔 귀여운 리액션을 해요: '앗!', '오!', '음...', '찾았어요!' 같은 표현.\n"
    "- 업무적인 내용도 딱딱하지 않게, 사람 냄새 나게 전달해요.\n"
    "- 길게 늘어놓지 말고 핵심만 간결하게 답변해요.\n"
    "- 검색 결과나 분석을 전달할 때는 정리된 형태(볼드, 줄바꿈)로 보기 좋게.\n"
    "- 사용자를 이름 없이 자연스럽게 대화해요.\n"
    "- 절대 '~습니다', '~합니다' 같은 딱딱한 존댓말을 쓰지 마세요. 항상 '~요' 체로!\n\n"
    "[역할]\n"
    "업무 관련 질문, 문서 작성, 요약, 번역, 아이디어 정리, "
    "Google Drive/Calendar/Gmail 연동, 슬라이드 생성 등을 도와줘요."
)
# thinking 토큰도 max_tokens에 포함되므로 여유 있게
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "8192"))

# Gemini API (실시간 웹 검색용)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# DART 전자공시 API (opendart.fss.or.kr에서 발급)
DART_API_KEY = os.getenv("DART_API_KEY", "")

# 아침 종목 리포트 워치리스트 ('이름:코드' 쉼표 구분, 6자리 숫자=국내 종목코드, 그 외=해외 티커)
STOCK_WATCHLIST = os.getenv(
    "STOCK_WATCHLIST",
    "삼성전자:005930,SK하이닉스:000660,현대차:005380,구글:GOOGL",
)

# 아침 종목 리포트 발송 시각 (KST, HH:MM)
STOCK_REPORT_TIME = os.getenv("STOCK_REPORT_TIME", "07:20")

# 아침 종목 리포트 이메일 수신자 (설정 시 로컬 스크립트가 텔레그램 대신 Gmail로 발송)
STOCK_REPORT_EMAIL_TO = os.getenv("STOCK_REPORT_EMAIL_TO", "")

# 증권사 리포트 원문 PDF 저장 여부 (로컬 스크립트, data/stock_reports/날짜/에 다운로드)
STOCK_REPORT_SAVE_PDF = os.getenv("STOCK_REPORT_SAVE_PDF", "true").lower() in ("1", "true", "yes")

# PDF를 업로드할 Google Drive 폴더명 (빈 값이면 Drive 업로드 안 함)
STOCK_REPORT_DRIVE_FOLDER = os.getenv("STOCK_REPORT_DRIVE_FOLDER", "증권사리포트")

# 아침 종목 리포트 모드:
#   off    - 서버 자동 발송 끔 (기본. PC의 scripts/local_stock_report.py로 실행)
#   api    - 서버에서 Claude API로 상세검토 후 자동 발송 (API 비용 발생)
#   digest - 수집한 리포트/뉴스 목록만 자동 발송 (무료)
STOCK_REPORT_MODE = os.getenv("STOCK_REPORT_MODE", "off").lower()

# 일일 API 비용 한도 (USD) - 초과 시 텔레그램 알림
DAILY_COST_LIMIT_USD = float(os.getenv("DAILY_COST_LIMIT_USD", "1.0"))

# 조용시간 (KST, 이 시간대에는 긴급 등급이 아닌 알림을 보류했다가 아침에 묶어서 발송)
QUIET_HOURS_START = int(os.getenv("QUIET_HOURS_START", "22"))
QUIET_HOURS_END = int(os.getenv("QUIET_HOURS_END", "7"))

# DART 다이제스트(정기보고서 등 낮은 등급 공시 묶음) 발송 시각 (KST, HH:MM)
DART_DIGEST_TIME = os.getenv("DART_DIGEST_TIME", "08:00")

# 포트폴리오 가격 변동 알림 임계값 (%, 국내 종목, 장중 15분 주기 체크). 0이면 끔
PRICE_ALERT_THRESHOLD_PCT = float(os.getenv("PRICE_ALERT_THRESHOLD_PCT", "3.0"))

# 미팅 준비 브리핑: 참석자/설명이 있는 일정 N분 전에 관련 메일·문서·메모 요약 발송. 0이면 끔
MEETING_PREP_MINUTES = int(os.getenv("MEETING_PREP_MINUTES", "30"))

# 실행 모드: cloud (Drive+메모+리마인더), local (PC파일만), all (전부)
RUN_MODE = os.getenv("RUN_MODE", "all").lower()

# 로깅
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# 임시 다운로드 경로
TEMP_DOWNLOAD_PATH = Path(os.getenv("TEMP_DOWNLOAD_PATH", str(BASE_DIR / "data" / "temp")))
