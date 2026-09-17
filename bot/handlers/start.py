"""기본 명령 핸들러 - /start, /help, /status"""

from datetime import datetime, timezone, timedelta
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import (
    RUN_MODE, ANTHROPIC_API_KEY, GEMINI_API_KEY, DART_API_KEY,
    CLAUDE_MODEL, CLAUDE_LIGHT_MODEL, STOCK_REPORT_MODE, STOCK_REPORT_TIME,
    IS_RAILWAY, RAILWAY_VOLUME_MOUNT_PATH, DATABASE_PATH,
    PRICE_ALERT_THRESHOLD_PCT, MEETING_PREP_MINUTES, DART_DIGEST_TIME,
)
from bot.middleware.auth_middleware import authorized_only
from bot.utils.telegram_fmt import reply_html

KST = timezone(timedelta(hours=9))


def _build_help_text() -> str:
    """모드에 따라 도움말 텍스트 생성"""
    sections = [
        "📋 **카리나 명령어 목록**\n",
        "**기본**\n"
        "/start - 봇 시작\n"
        "/help - 명령어 목록\n"
        "/status - 봇 상태·스케줄 확인\n",
    ]

    if RUN_MODE in ("cloud", "all"):
        sections.append(
            "**Google Drive**\n"
            "/drive_auth - 인증 상태·안내\n"
            "/drive [폴더ID] - 파일 목록\n"
            "/drive_search <키워드> - 파일 검색\n"
            "/drive_mkdir <이름> - 폴더 생성\n"
            "/drive_share <파일ID> - 공유 링크\n"
            "/drive_upload [폴더ID] - 다음에 보내는 파일을 업로드\n"
        )
        sections.append(
            "**메모**\n"
            "/memo <내용> · /memos · /memo_search <키워드> · /memo_del <ID>\n"
            "  대화 중 '메모해줘'라고 해도 돼요.\n"
        )
        sections.append(
            "**리마인더**\n"
            "/remind <30m|2h|1d> <메시지> - 일회성\n"
            "/remind_daily <HH:MM> <메시지> - 매일 반복\n"
            "/reminders · /remind_del <ID>\n"
            "  대화로도 돼요: '내일 9시에 세무사 통화 알려줘'\n"
        )
        prep = f"  미팅 {MEETING_PREP_MINUTES}분 전에는 관련 메일·문서·메모를 모아 준비 브리핑을 보내요.\n" if MEETING_PREP_MINUTES > 0 and ANTHROPIC_API_KEY else ""
        sections.append(
            "**브리핑**\n"
            "/briefing - 오늘 브리핑 (일정 + 먼저 볼 메일 + 리마인더)\n"
            "  매일 07:00 자동 발송, 이번 주 증시 이벤트(실적·물가·중앙은행)도 함께 와요.\n"
            "  매주 월 07:30에는 주간 일정 + 지난주 API 비용 요약.\n"
            + prep
        )

    price = f"  장중 ±{PRICE_ALERT_THRESHOLD_PCT:g}% 움직이면 가격 알림도 보내요.\n" if PRICE_ALERT_THRESHOLD_PCT > 0 else ""
    sections.append(
        "**포트폴리오 (DART 공시)**\n"
        "/portfolio · /portfolio_add <종목명> · /portfolio_del <ID>\n"
        f"  긴급 공시(주요사항·유상증자·잠정실적 등)는 즉시, 정기보고서는 {DART_DIGEST_TIME} 다이제스트로,\n"
        "  임원 소유상황보고서 같은 잡음은 기본 무음이에요. 알림의 🔕 버튼으로 유형별 무음 조정.\n"
        + price
    )

    if RUN_MODE in ("local", "all"):
        sections.append(
            "**PC 파일**\n"
            "/pc - 허용 폴더 · /pc_ls <경로> · /pc_get <경로>\n"
            "/pc_save <경로> (그 다음 파일 전송) · /pc_delete <경로>\n"
        )

    if GEMINI_API_KEY:
        sections.append(
            "**실시간 검색**\n"
            "/search <질문> - Gemini 웹 검색\n"
            "  대화 중에도 '오늘 코스피 어때?'처럼 물으면 알아서 검색해요.\n"
        )

    sections.append(
        "**CIP 슬라이드**\n"
        "'프로젝트 알바트로스 표지 만들어줘', '섹션 구분 슬라이드' 같이 말하면 PNG로 보내드려요.\n"
    )

    try:
        from bot.services.stock_research_service import get_watchlist
        names = ", ".join(s["name"] for s in get_watchlist()) or "(STOCK_WATCHLIST 미설정)"
    except Exception:
        names = "(설정 확인 필요)"
    auto = f"평일 {STOCK_REPORT_TIME} 자동 발송" if STOCK_REPORT_MODE != "off" else "수동 요청만 (자동 발송 꺼짐)"
    sections.append(
        "**아침 종목 리포트**\n"
        f"/stock_report - 증권사 리포트 + 뉴스 AI 검토\n"
        f"  대상: {names}\n"
        f"  {auto}\n"
    )

    if ANTHROPIC_API_KEY:
        sections.append(
            "**AI 대화**\n"
            "그냥 메시지를 보내면 카리나가 답해요. 도구(Drive·캘린더·Gmail·메모·리마인더·검색)는 항상 켜져 있어요.\n"
            "🎤 음성, 🖼 사진, 📄 PDF/워드/엑셀도 보내면 분석하고, 이어서 질문할 수 있어요.\n"
            "📧 메일 발송과 🗑 일정 삭제는 실행 전에 확인 버튼이 떠요.\n"
            "/clear - 대화 초기화 · /usage - API 사용량/비용\n"
        )

    return "\n".join(sections)


@authorized_only
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """봇 시작 환영 메시지"""
    user = update.effective_user
    mode_label = {"cloud": "☁️ 클라우드", "local": "💻 로컬", "all": "☁️+💻 전체"}.get(RUN_MODE, RUN_MODE)

    features = []
    if ANTHROPIC_API_KEY:
        features.append("Claude AI 대화")
    if RUN_MODE in ("cloud", "all"):
        features.extend(["Google Drive/Calendar/Gmail", "메모", "리마인더", "브리핑"])
    if RUN_MODE in ("local", "all"):
        features.append("PC 파일 관리")
    if DART_API_KEY:
        features.append("DART 공시 알림")

    await reply_html(
        update,
        f"안녕하세요, {user.first_name}님! 👋\n\n"
        f"저는 **카리나**, GS Secretary 업무 비서예요.\n"
        f"현재 모드: **{mode_label}**\n"
        f"활성 기능: {', '.join(features)}\n\n"
        f"명령어 목록은 /help 를 입력하세요.",
    )


@authorized_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """도움말 표시"""
    await reply_html(update, _build_help_text())


def _storage_status() -> str:
    if not IS_RAILWAY:
        return "로컬 디스크"
    db_path = Path(DATABASE_PATH).resolve()
    if RAILWAY_VOLUME_MOUNT_PATH and str(db_path).startswith(str(Path(RAILWAY_VOLUME_MOUNT_PATH).resolve())):
        return "✅ Railway 볼륨"
    return "⚠️ 휘발성 (재배포 시 DB 소실 — 볼륨 필요)"


@authorized_only
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """봇 상태 확인"""
    mode_label = {"cloud": "☁️ 클라우드", "local": "💻 로컬", "all": "☁️+💻 전체"}.get(RUN_MODE, RUN_MODE)
    lines = ["📊 **봇 상태**", "", f"실행 모드: {mode_label}", f"저장소: {_storage_status()}"]

    if RUN_MODE in ("cloud", "all"):
        from bot.utils.drive_auth import is_authenticated_async
        google = "✅ 연결됨" if await is_authenticated_async() else "❌ 미연결 (/drive_auth 참고)"
        lines.append(f"Google: {google}")

    keys = [
        f"Anthropic {'✅' if ANTHROPIC_API_KEY else '❌'}",
        f"Gemini {'✅' if GEMINI_API_KEY else '❌'}",
        f"DART {'✅' if DART_API_KEY else '❌'}",
    ]
    lines.append("API 키: " + " · ".join(keys))

    if ANTHROPIC_API_KEY:
        from bot.services.claude_service import get_history_length
        turns = get_history_length(update.effective_user.id) // 2
        lines.append(f"모델: {CLAUDE_LIGHT_MODEL} (기본) / {CLAUDE_MODEL} (무거운 작업)")
        lines.append(f"대화 히스토리: {turns}턴")
        try:
            from bot.services.usage_service import get_usage_today
            usage = await get_usage_today()
            hit = f", 캐시 히트 {usage['cache_hit_rate'] * 100:.0f}%" if usage["calls"] else ""
            lines.append(f"오늘 API 비용: ${usage['cost']:.3f} ({usage['calls']}회{hit})")
        except Exception:
            pass

    from bot.utils.jobs import get_job_status
    job_rows = await get_job_status()
    next_runs = {}
    try:
        for job in context.job_queue.jobs():
            if job.next_t:
                next_runs[job.name] = job.next_t.astimezone(KST).strftime("%m/%d %H:%M")
    except Exception:
        pass

    names = sorted(set([r["job"] for r in job_rows] + list(next_runs.keys())))
    if names:
        lines.append("")
        lines.append("**스케줄 잡**")
        last = {r["job"]: r for r in job_rows}
        for name in names:
            r = last.get(name)
            state = "—"
            if r:
                state = f"{'✅' if r['status'] == 'ok' else '❌'} {r['last_run'] or ''}"
                if r["status"] != "ok" and r.get("error"):
                    state += f" ({r['error'][:60]})"
            nxt = f" → 다음 {next_runs[name]}" if name in next_runs else ""
            lines.append(f"• {name}: {state}{nxt}")

    await reply_html(update, "\n".join(lines))
