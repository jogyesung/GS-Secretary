# GS-Secretary

텔레그램 기반 업무 비서 봇 - Google Drive 연동, PC 파일 관리, 메모, 리마인더

## 주요 기능

- **Google Drive**: 파일 목록/검색/업로드/다운로드/공유
- **PC 파일 관리**: 로컬 파일 탐색/전송/저장/삭제
- **메모**: 빠른 메모 저장/조회/검색
- **리마인더**: 일회성/반복 알림 설정
- **파일 감시**: 디렉토리 변경 실시간 알림

## 빠른 시작

### 1. 사전 준비

- Python 3.11+
- Telegram Bot Token ([BotFather](https://t.me/BotFather)에서 발급)
- Google Cloud Console에서 OAuth 2.0 클라이언트 자격증명 (`credentials.json`)

### 2. 설치

```bash
git clone https://github.com/jogyesung/GS-Secretary.git
cd GS-Secretary
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 환경설정

```bash
cp .env.example .env
# .env 파일을 편집하여 설정값 입력
```

### 4. 실행

```bash
python -m bot.main
```

### Docker로 실행

```bash
docker-compose up -d
```

## 환경변수

| 변수 | 설명 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
| `ALLOWED_USER_IDS` | 허용된 사용자 ID (쉼표 구분) |
| `GOOGLE_CREDENTIALS_FILE` | OAuth 자격증명 파일 경로 |
| `ALLOWED_PATHS` | PC 접근 허용 경로 (쉼표 구분) |

## 명령어

| 명령어 | 설명 |
|--------|------|
| `/start` | 봇 시작 |
| `/help` | 명령어 목록 |
| `/drive_auth` | Google Drive 인증 |
| `/drive` | Drive 파일 목록 |
| `/pc` | PC 디렉토리 목록 |
| `/memo <내용>` | 메모 저장 |
| `/remind <시간> <메시지>` | 리마인더 설정 |
