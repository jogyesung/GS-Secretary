# GS-Secretary

텔레그램 기반 업무 비서 봇 - Google Drive 연동, PC 파일 관리, 메모, 리마인더

## 주요 기능

- **Google Drive**: 파일 목록/검색/업로드/다운로드/공유
- **PC 파일 관리**: 로컬 파일 탐색/전송/저장/삭제
- **메모**: 빠른 메모 저장/조회/검색
- **리마인더**: 일회성/반복 알림 설정
- **파일 감시**: 디렉토리 변경 실시간 알림
- **아침 종목 리포트**: 증권사 리포트+종목 뉴스 취합 → AI 상세검토 → 평일 아침 브리핑

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
| `/stock_report` | 아침 종목 리포트 즉시 요청 (평일 07:20 KST 자동 발송) |

## 아침 종목 리포트를 무료로 돌리기 (PC + Claude 구독)

서버에서 Claude API로 검토하면 토큰 비용이 들어요. PC에 Claude Code를 설치하고
구독 계정으로 로그인하면, 같은 검토를 **API 비용 없이** 구독 요금제 안에서 돌릴 수 있어요.

서버 자동 발송은 기본으로 꺼져 있어요 (`STOCK_REPORT_MODE=off`가 기본값).
서버에서 자동 발송을 원할 때만 Railway 환경변수에 `api` 또는 `digest`를 설정하세요.

1. PC에 Claude Code 설치 + 로그인: `npm install -g @anthropic-ai/claude-code` → `claude`
2. 윈도우 작업 스케줄러에 평일 아침 등록 (선택):

```
schtasks /create /tn "아침종목리포트" /sc weekly /d MON,TUE,WED,THU,FRI /st 07:20 ^
  /tr "python C:\경로\GS-Secretary\scripts\local_stock_report.py"
```

- 수동 실행: `python scripts/local_stock_report.py`
- **Gmail로 받기**: `.env`에 `STOCK_REPORT_EMAIL_TO=내주소@gmail.com` 설정 (카리나의 Google 인증 재사용,
  `credentials.json` + `data/token.json` 필요 — 없으면 `python auth_local.py`로 최초 1회 인증)
- 이메일 미설정 시 텔레그램으로, 그것도 없으면 화면 출력. `--no-send`는 발송 없이 파일/화면만
- 결과는 항상 `data/stock_reports/날짜.md`에 저장돼요.
- **리포트 원문 PDF 아카이빙**: 기본으로 `data/stock_reports/날짜/` 폴더에 PDF를 내려받고,
  Google Drive의 `증권사리포트/날짜` 폴더에도 자동 업로드해요.
  (`STOCK_REPORT_SAVE_PDF=false`로 끄기, `STOCK_REPORT_DRIVE_FOLDER=`(빈 값)으로 Drive만 끄기)
- Claude CLI가 없거나 실패하면 수집한 리포트/뉴스 목록(무료 다이제스트)으로 자동 대체돼요.
