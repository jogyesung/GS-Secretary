"""PC에서 아침 종목 리포트 실행 - Claude 구독으로 API 비용 없이 상세검토

봇 서버의 Anthropic API 대신, PC에 설치된 Claude Code CLI(claude.ai 구독 로그인)를
헤드리스 모드(claude -p)로 호출해서 검토하고, 결과를 텔레그램으로 직접 발송해요.
구독 요금제 안에서 돌기 때문에 API 토큰 비용이 들지 않아요.

준비물:
1. PC에 Claude Code 설치 후 구독 계정으로 로그인
   npm install -g @anthropic-ai/claude-code  →  claude (최초 1회 로그인)
2. 프로젝트 .env에 TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS 설정 (이미 있으면 그대로)
3. 윈도우 작업 스케줄러에 평일 아침 등록:
   schtasks /create /tn "아침종목리포트" /sc weekly /d MON,TUE,WED,THU,FRI /st 07:20 ^
     /tr "python C:\\경로\\GS-Secretary\\scripts\\local_stock_report.py"

서버 자동 발송은 기본으로 꺼져 있어요 (STOCK_REPORT_MODE 기본값 off).

전달 채널 (우선순위 순):
1. 파일 저장: data/stock_reports/날짜.md — 항상 저장
2. Gmail: .env에 STOCK_REPORT_EMAIL_TO=내주소@gmail.com 설정 시
   (카리나의 Google 인증 재사용 - credentials.json + data/token.json 필요,
    없으면 auth_local.py 실행해서 최초 1회 인증)
3. 텔레그램: 이메일 미설정이고 TELEGRAM_BOT_TOKEN이 있을 때

수동 실행: python scripts/local_stock_report.py
발송 없이 파일/화면으로만 보기: python scripts/local_stock_report.py --no-send
"""

import asyncio
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from bot.config import (
    TELEGRAM_BOT_TOKEN,
    ALLOWED_USER_IDS,
    BASE_DIR,
    STOCK_REPORT_EMAIL_TO,
)
from bot.services.stock_research_service import (
    KST,
    ANALYST_SYSTEM_PROMPT,
    collect_watchlist_data,
    format_raw_digest,
    _serialize_for_prompt,
)

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("local_stock_report")

CLAUDE_TIMEOUT_SEC = 600
MAX_CHUNK = 3500  # 텔레그램 4096자 한도 이내 분할

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def run_claude_cli(prompt: str) -> str | None:
    """Claude Code CLI 헤드리스 호출 (구독 로그인 사용, API 과금 없음)"""
    claude = shutil.which("claude")
    if not claude:
        logger.warning(
            "claude CLI를 찾지 못했어요. 설치: npm install -g @anthropic-ai/claude-code"
        )
        return None
    try:
        proc = subprocess.run(
            [claude, "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=CLAUDE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        logger.error("claude CLI 응답 시간 초과")
        return None
    if proc.returncode != 0:
        logger.error(f"claude CLI 실패 (code {proc.returncode}): {(proc.stderr or '')[:300]}")
        return None
    return (proc.stdout or "").strip() or None


def build_report() -> str:
    """수집 → Claude CLI 상세검토. CLI 실패 시 무료 다이제스트 폴백."""
    collected = asyncio.run(collect_watchlist_data())
    if not any(s["reports"] or s["news"] for s in collected):
        raise RuntimeError("수집된 리포트/뉴스가 없습니다.")

    now = datetime.now(KST)
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    prompt = (
        f"{ANALYST_SYSTEM_PROMPT}\n\n"
        f"오늘은 {now.strftime('%Y년 %m월 %d일')} {weekdays[now.weekday()]} 아침이에요.\n"
        f"아래는 밤사이 수집한 관심 종목의 증권사 리포트 목록과 최신 뉴스예요.\n"
        f"이 자료를 상세히 검토해서 아침 브리핑을 작성해주세요.\n"
        f"브리핑 본문만 출력하세요 (앞뒤 설명·인사말 없이).\n\n"
        f"{_serialize_for_prompt(collected)}"
    )

    review = run_claude_cli(prompt)
    if review:
        header = f"📈 *아침 종목 리포트* ({now.strftime('%m/%d')} {weekdays[now.weekday()]})\n\n"
        return header + review

    logger.warning("AI 검토 실패 - 수집 다이제스트로 대체합니다")
    return format_raw_digest(collected)


def _split_chunks(text: str, size: int = MAX_CHUNK) -> list[str]:
    chunks = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut <= 0:
            cut = size
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def save_to_file(text: str) -> Path:
    """리포트를 data/stock_reports/ 아래 날짜별 파일로 저장"""
    out_dir = Path(BASE_DIR) / "data" / "stock_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{datetime.now(KST).strftime('%Y-%m-%d')}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _markdown_to_plain(text: str) -> str:
    """텔레그램 Markdown을 이메일용 평문으로 변환 (*굵게* 제거, [제목](URL) → 제목 - URL)"""
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 - \2", text)
    return text.replace("*", "")


def send_gmail(text: str):
    """카리나의 Gmail 연동으로 리포트 이메일 발송"""
    from bot.services.gmail_service import send_email

    now = datetime.now(KST)
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    subject = f"📈 아침 종목 리포트 {now.strftime('%Y-%m-%d')} ({weekdays[now.weekday()]})"
    asyncio.run(send_email(to=STOCK_REPORT_EMAIL_TO, subject=subject, body=_markdown_to_plain(text)))
    logger.info(f"Gmail 발송 완료: {STOCK_REPORT_EMAIL_TO}")


def send_telegram(text: str):
    """텔레그램 발송 (Markdown 파싱 실패 시 일반 텍스트로 재시도)"""
    url = TELEGRAM_API_URL.format(token=TELEGRAM_BOT_TOKEN)
    with httpx.Client(timeout=30) as client:
        for user_id in ALLOWED_USER_IDS:
            for chunk in _split_chunks(text):
                payload = {
                    "chat_id": user_id,
                    "text": chunk,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                }
                resp = client.post(url, json=payload)
                if resp.status_code != 200:
                    payload.pop("parse_mode")
                    resp = client.post(url, json=payload)
                resp.raise_for_status()
            logger.info(f"발송 완료: {user_id}")


def main():
    no_send = "--no-send" in sys.argv
    logger.info("아침 종목 리포트 수집 시작")
    text = build_report()

    path = save_to_file(text)
    logger.info(f"리포트 파일 저장: {path}")

    if not no_send and STOCK_REPORT_EMAIL_TO:
        send_gmail(text)
    elif not no_send and TELEGRAM_BOT_TOKEN and ALLOWED_USER_IDS:
        send_telegram(text)
    else:
        print("\n" + "=" * 60 + "\n")
        print(text)

    logger.info("아침 종목 리포트 완료")


if __name__ == "__main__":
    main()
