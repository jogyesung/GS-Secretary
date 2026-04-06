"""Claude AI 대화 핸들러 - 텍스트 메시지를 Claude에게 전달"""

import os
from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware.auth_middleware import authorized_only
from bot.services import claude_service


@authorized_only
async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """일반 텍스트 메시지를 Claude에게 전달"""
    message = update.message.text
    user_id = update.effective_user.id

    # "입력 중..." 표시
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        response = await claude_service.chat(user_id, message)

        # 텔레그램 메시지 길이 제한 (4096자)
        if len(response) > 4000:
            for i in range(0, len(response), 4000):
                await update.message.reply_text(response[i:i + 4000])
        else:
            await update.message.reply_text(response)

        # 대기 중인 파일 전송
        pending_files = claude_service.get_pending_files(user_id)
        for file_path in pending_files:
            try:
                with open(file_path, "rb") as f:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=f,
                        filename=os.path.basename(file_path),
                    )
            except Exception as e:
                await update.message.reply_text(f"❌ 파일 전송 실패: {e}")
            finally:
                # 임시 파일 삭제
                try:
                    os.remove(file_path)
                except OSError:
                    pass

    except RuntimeError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ Claude 응답 오류: {e}")


@authorized_only
async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """대화 히스토리 초기화"""
    user_id = update.effective_user.id
    claude_service.clear_history(user_id)
    await update.message.reply_text("🗑️ 대화 히스토리가 초기화되었습니다.")
