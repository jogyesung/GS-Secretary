"""기본 명령 핸들러 - /start, /help, /status"""

from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.utils.drive_auth import is_authenticated
from bot.services.watcher_service import get_active_watches


HELP_TEXT = """📋 *GS Secretary 명령어 목록*

*기본*
/start - 봇 시작
/help - 명령어 목록
/status - 봇 상태 확인

*Google Drive*
/drive\\_auth - Google Drive 인증
/drive - Drive 파일 목록
/drive\\_search <키워드> - 파일 검색
/drive\\_mkdir <이름> - 폴더 생성
/drive\\_share <파일ID> - 공유 링크 생성
_(파일 전송 시 Drive에 업로드 가능)_

*PC 파일 관리*
/pc - 허용된 디렉토리 목록
/pc\\_ls <경로> - 디렉토리 탐색
/pc\\_get <경로> - 파일 전송
/pc\\_save <경로> - 파일 저장 (파일 첨부 후)
/pc\\_delete <경로> - 파일 삭제

*메모*
/memo <내용> - 메모 저장
/memos - 메모 목록
/memo\\_search <키워드> - 메모 검색
/memo\\_del <ID> - 메모 삭제

*리마인더*
/remind <시간> <메시지> - 리마인더 설정
  예: /remind 30m 회의 시작
  예: /remind 2h 보고서 제출
/remind\\_daily <HH:MM> <메시지> - 매일 반복
/reminders - 리마인더 목록
/remind\\_del <ID> - 리마인더 삭제

*파일 감시*
/watch <경로> - 디렉토리 변경 감시
/unwatch <경로> - 감시 중지
/watches - 감시 목록
"""


@authorized_only
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """봇 시작 환영 메시지"""
    user = update.effective_user
    await update.message.reply_text(
        f"안녕하세요, {user.first_name}님! 👋\n\n"
        "저는 *GS Secretary* 업무 비서 봇입니다.\n"
        "Google Drive 연동, PC 파일 관리, 메모, 리마인더 등\n"
        "다양한 업무를 도와드립니다.\n\n"
        "명령어 목록은 /help 를 입력하세요.",
        parse_mode="Markdown",
    )


@authorized_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """도움말 표시"""
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


@authorized_only
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """봇 상태 확인"""
    drive_status = "✅ 연결됨" if is_authenticated() else "❌ 미연결"
    watches = get_active_watches()
    watch_status = f"✅ {len(watches)}개 감시 중" if watches else "❌ 없음"

    await update.message.reply_text(
        "📊 *봇 상태*\n\n"
        f"Google Drive: {drive_status}\n"
        f"파일 감시: {watch_status}\n"
        f"봇 상태: ✅ 정상 동작 중",
        parse_mode="Markdown",
    )
