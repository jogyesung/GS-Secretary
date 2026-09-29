# GS-Secretary (카리나)

텔레그램으로 쓰는 업무 비서 봇. Claude가 Google Drive·Calendar·Gmail, 메모, 리마인더, PC 파일, 웹 검색, DART 공시를 도구로 쓰고, 아침마다 브리핑을 보내요.

## 주요 기능

**AI 대화 (카리나)**
- 메시지를 보내면 답해요. 도구는 항상 켜져 있어서 "24년 2월 계약서 찾아줘" → Drive+Gmail 동시 검색, "내일 3시 회의 잡아줘" → 캘린더 생성(충돌 감지), "그럼 화요일로 바꿔줘" 같은 후속 요청도 처리해요.
- 🎤 음성, 🖼 사진, 📄 PDF/워드/엑셀을 보내면 분석하고, 대화에 남아서 "여기서 EBITDA 얼마야?"처럼 이어 물을 수 있어요.
- "내일 9시에 세무사 통화 알려줘" → 리마인더, "삼성전자 최근 공시 뭐야?" → DART 조회, "오늘 코스피 어때?" → 실시간 검색.
- 📧 메일 발송(새 메일·답장)과 🗑 일정 삭제는 실행 전에 확인 버튼이 떠요.

**브리핑·알림**
- 매일 07:00: 오늘 일정 + 먼저 볼 메일 5건(긴급도 정리) + 리마인더, 이어서 이번 주 증시 이벤트(실적발표·물가지수·중앙은행).
- 매주 월 07:30: 주간 일정 + 지난주 API 비용.
- 미팅 30분 전: 참석자와 주고받은 최근 메일, 관련 Drive 문서, 메모를 모아 준비 브리핑.
- DART 공시: 긴급(주요사항·유상증자·잠정실적 등)은 즉시, 정기보고서는 08:00 다이제스트, 임원 소유상황보고서 같은 잡음은 기본 무음. 알림의 🔕 버튼으로 유형별 무음 조정. 장중 ±3% 가격 변동 알림.
- 아침 종목 리포트(평일 07:20, 옵션): 증권사 리포트 + 뉴스를 AI가 검토.
- 운영: 봇 오류·스케줄 잡 실패·재시작 시 관리자 DM, 일일 API 비용 한도 알림, 매일 03:00 DB를 Drive에 백업, 22~07시는 긴급 아닌 알림 보류.

**모델 라우팅**
기본은 Haiku 4.5 + 도구, 문서 검색·작성처럼 무거운 작업은 Sonnet 5. Gemini는 웹 검색·음성 인식·증시 브리핑 검색에 써요. 시스템 프롬프트·도구·대화 히스토리에 프롬프트 캐싱을 걸어 비용을 줄이고, `/usage`에서 모델별 비용과 캐시 히트율을 볼 수 있어요.

## 빠른 시작 (로컬)

```bash
git clone https://github.com/jogyesung/GS-Secretary.git
cd GS-Secretary
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # 값 채우기
python auth_local.py               # Google 인증 (브라우저) → data/token.json
python -m bot.main
```

Python 3.11+. 필요한 키: Telegram 봇 토큰, Anthropic, Gemini(검색·음성), DART(공시, 선택).

## Railway 배포

Dockerfile로 빌드돼요. 반드시 할 것:

1. **Volume을 `/data`에 마운트**하고 환경변수에 `DATABASE_PATH=/data/gs_secretary.db`, `GOOGLE_TOKEN_FILE=/data/token.json` 설정. 없으면 재배포할 때마다 메모·리마인더·포트폴리오·사용량이 사라져요 (`/status`에서 "저장소" 항목으로 확인).
2. Google 인증은 PC에서 `python auth_local.py`로 하고, `credentials.json`과 `data/token.json`을 base64로 인코딩해 `GOOGLE_CREDENTIALS_B64`, `GOOGLE_TOKEN_B64`에 넣어요.
   - PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes('data/token.json'))`
   - macOS/Linux: `base64 -w0 data/token.json`
3. OAuth 동의 화면이 "테스트" 모드면 refresh token이 7일마다 만료돼요. "프로덕션"으로 게시하세요.

## 환경변수 (핵심)

| 변수 | 설명 |
|------|------|
| `TELEGRAM_BOT_TOKEN`, `ALLOWED_USER_IDS` | 봇 토큰, 허용 사용자 (비어 있으면 아무도 사용 불가) |
| `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `CLAUDE_LIGHT_MODEL` | Claude. 기본 `claude-sonnet-5` / `claude-haiku-4-5` |
| `GEMINI_API_KEY` | 웹 검색·음성 인식·증시 브리핑 |
| `DART_API_KEY` | 공시 알림·조회 (opendart.fss.or.kr 무료) |
| `DATABASE_PATH`, `GOOGLE_TOKEN_FILE` | Railway는 `/data/...` |
| `GOOGLE_CREDENTIALS_B64`, `GOOGLE_TOKEN_B64` | 서버용 Google 인증 파일 |
| `ALLOWED_PATHS`, `RUN_MODE` | PC 파일 접근 경로, 실행 모드(all/cloud/local) |
| `DAILY_COST_LIMIT_USD`, `QUIET_HOURS_START/END` | 비용 한도 알림, 조용시간 |
| `PRICE_ALERT_THRESHOLD_PCT`, `MEETING_PREP_MINUTES`, `DART_DIGEST_TIME` | 가격 알림 임계값, 미팅 준비 시점, 공시 다이제스트 시각 |
| `STOCK_WATCHLIST`, `STOCK_REPORT_MODE` | 종목 리포트 대상, `off`/`api`/`digest` |

전체 목록과 기본값은 `.env.example` 참조.

## 명령어

| 명령어 | 설명 |
|--------|------|
| `/start` `/help` `/status` | 시작, 도움말, 상태(연결·키·비용·스케줄 잡) |
| `/drive_auth` `/drive` `/drive_search` `/drive_mkdir` `/drive_share` `/drive_upload` | Google Drive |
| `/memo` `/memos` `/memo_search` `/memo_del` | 메모 |
| `/remind` `/remind_daily` `/reminders` `/remind_del` | 리마인더 (대화로도 가능) |
| `/briefing` | 오늘 브리핑 + 증시 이벤트 |
| `/portfolio` `/portfolio_add` `/portfolio_del` | DART 공시 알림 종목 |
| `/stock_report` | 아침 종목 리포트 즉시 요청 |
| `/pc` `/pc_ls` `/pc_get` `/pc_save` `/pc_delete` | PC 파일 (local/all) |
| `/search` | Gemini 실시간 검색 |
| `/usage` `/clear` | API 비용, 대화 초기화 |

## 로컬 스크립트

**아침 종목 리포트를 PC + Claude 구독으로 (API 비용 없이)**
서버 자동 발송은 기본으로 꺼져 있어요(`STOCK_REPORT_MODE=off`). PC에 Claude Code(`npm install -g @anthropic-ai/claude-code`)를 설치하고 구독 계정으로 로그인한 뒤:

```
python scripts/local_stock_report.py            # 텔레그램(또는 STOCK_REPORT_EMAIL_TO 설정 시 Gmail)로 발송
python scripts/local_stock_report.py --no-send  # 파일/화면만
```

Windows 작업 스케줄러 예: `schtasks /create /tn "아침종목리포트" /sc weekly /d MON,TUE,WED,THU,FRI /st 07:20 /tr "python C:\경로\GS-Secretary\scripts\local_stock_report.py"`
결과는 `data/stock_reports/날짜.md`에 저장되고, 리포트 원문 PDF는 Drive `증권사리포트/날짜`에 아카이빙돼요. CLI는 빈 임시 폴더에서 도구 없이 실행돼 프로젝트의 비밀 파일에 접근하지 못해요.

**저축은행·캐피탈 상장사 사업보고서 수집**
```
python scripts/collect_sector_filings.py              # 기본: 저축은행, 캐피탈
python scripts/collect_sector_filings.py 저축은행 캐피탈 카드
python scripts/collect_sector_filings.py --no-drive   # PC 저장만
```

## 테스트

```bash
pip install -r requirements-dev.txt
pytest
```

라우팅 키워드, 도구 스키마↔핸들러 일치, chat 루프(도구 왕복·캐시 마커·히스토리 접기), 텔레그램 HTML 변환, 리마인더 KST, 파일명 검증을 검사해요.
