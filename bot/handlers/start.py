"""기본 명령 핸들러 - /start, /help, /status"""

from telegram import Update
from telegram.ext import ContextTypes
from bot.config import RUN_MODE, ANTHROPIC_API_KEY, GEMINI_API_KEY
from bot.middleware.auth_middleware import authorized_only


def _build_help_text() -> str:
    """모드에 따라 도움말 텍스트 생성"""
    sections = [
        "📋 *GS Secretary 명령어 목록*\n",
        "*기본*\n"
        "/start - 봇 시작\n"
        "/help - 명령어 목록\n"
        "/status - 봇 상태 확인\n",
    ]

    if RUN_MODE in ("cloud", "all"):
        sections.append(
            "*Google Drive*\n"
            "/drive\\_auth - Google Drive 인증\n"
            "/drive - Drive 파일 목록\n"
            "/drive\\_search <키워드> - 파일 검색\n"
            "/drive\\_mkdir <이름> - 폴더 생성\n"
            "/drive\\_share <파일ID> - 공유 링크 생성\n"
            "_(파일 전송 시 Drive에 업로드 가능)_\n"
        )
        sections.append(
            "*메모*\n"
            "/memo <내용> - 메모 저장\n"
            "/memos - 메모 목록\n"
            "/memo\\_search <키워드> - 메모 검색\n"
            "/memo\\_del <ID> - 메모 삭제\n"
        )
        sections.append(
            "*리마인더*\n"
            "/remind <시간> <메시지> - 리마인더 설정\n"
            "  예: /remind 30m 회의 시작\n"
            "/remind\\_daily <HH:MM> <메시지> - 매일 반복\n"
            "/reminders - 리마인더 목록\n"
            "/remind\\_del <ID> - 리마인더 삭제\n"
        )
        sections.append(
            "*브리핑*\n"
            "/briefing - 오늘의 브리핑 (일정+메일+리마인더)\n"
            "  매일 오전 7시 자동 발송됩니다.\n"
            "  📈 이번 주 증시 이벤트(실적발표·물가지수·중앙은행)도 함께 정리해드려요.\n"
            "  매주 월요일 7시 30분에는 주간 일정 브리핑도 보내드려요.\n"
        )

    sections.append(
        "*포트폴리오 (DART 공시 알림)*\n"
        "/portfolio - 내 종목 목록\n"
        "/portfolio\\_add <종목명> - 종목 추가\n"
        "/portfolio\\_del <ID> - 종목 삭제\n"
        "  등록 종목에 새 공시가 뜨면 바로 알려드려요.\n"
    )

    if RUN_MODE in ("local", "all"):
        sections.append(
            "*PC 파일 관리*\n"
            "/pc - 허용된 디렉토리 목록\n"
            "/pc\\_ls <경로> - 디렉토리 탐색\n"
            "/pc\\_get <경로> - 파일 전송\n"
            "/pc\\_save <경로> - 파일 저장 (파일 첨부 후)\n"
            "/pc\\_delete <경로> - 파일 삭제\n"
        )

    if GEMINI_API_KEY:
        sections.append(
            "*실시간 웹 검색*\n"
            "/search <질문> - 실시간 웹 검색 (Gemini)\n"
            "  예: /search 오늘 코스피 지수\n"
        )

    sections.append(
        "*CIP 슬라이드*\n"
        "카리나에게 슬라이드/PPT 생성을 요청하세요.\n"
        "  예: '프로젝트 알바트로스 표지 만들어줘'\n"
        "  예: '섹션 구분 슬라이드 만들어줘'\n"
    )

    if ANTHROPIC_API_KEY:
        sections.append(
            "*AI 대화*\n"
            "그냥 메시지를 보내면 카리나가 답변합니다.\n"
            "🎤 음성 메시지도 인식해서 답변합니다.\n"
            "🖼 사진을 보내면 이미지를 분석합니다.\n"
            "📄 PDF/워드/엑셀 파일을 보내면 내용을 분석·요약합니다.\n"
            "  캡션을 달면 그에 맞게 분석해요.\n"
            "대화 중 자동으로 PC 파일 접근, 웹 검색도 합니다.\n"
            "/clear - 대화 히스토리 초기화\n"
            "/usage - API 사용량/비용 조회\n"
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
        features.extend(["Google Drive 연동", "메모", "리마인더"])
    if RUN_MODE in ("local", "all"):
        features.append("PC 파일 관리")

    await update.message.reply_text(
        f"안녕하세요, {user.first_name}님! 👋\n\n"
        f"저는 *GS Secretary* 업무 비서 봇입니다.\n"
        f"현재 모드: *{mode_label}*\n"
        f"활성 기능: {', '.join(features)}\n\n"
        f"명령어 목록은 /help 를 입력하세요.",
        parse_mode="Markdown",
    )


@authorized_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """도움말 표시"""
    await update.message.reply_text(_build_help_text(), parse_mode="Markdown")


@authorized_only
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """봇 상태 확인"""
    mode_label = {"cloud": "☁️ 클라우드", "local": "💻 로컬", "all": "☁️+💻 전체"}.get(RUN_MODE, RUN_MODE)

    lines = [f"📊 *봇 상태*\n", f"실행 모드: {mode_label}"]

    if RUN_MODE in ("cloud", "all"):
        from bot.utils.drive_auth import is_authenticated
        drive_status = "✅ 연결됨" if is_authenticated() else "❌ 미연결"
        lines.append(f"Google Drive: {drive_status}")

    if RUN_MODE in ("local", "all"):
        from bot.services.watcher_service import get_active_watches
        watches = get_active_watches()
        watch_status = f"✅ {len(watches)}개 감시 중" if watches else "없음"
        lines.append(f"파일 감시: {watch_status}")

    if ANTHROPIC_API_KEY:
        from bot.services.claude_service import get_history_length
        user_id = update.effective_user.id
        history_len = get_history_length(user_id)
        lines.append(f"Claude AI: ✅ 활성 (대화 {history_len // 2}턴)")
    else:
        lines.append("Claude AI: ❌ 비활성")

    lines.append("봇 상태: ✅ 정상 동작 중")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
