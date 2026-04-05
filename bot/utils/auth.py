"""사용자 인증 유틸리티 - Telegram 사용자 whitelist 체크"""

from bot.config import ALLOWED_USER_IDS


def is_authorized(user_id: int) -> bool:
    """사용자 ID가 허용 목록에 있는지 확인"""
    if not ALLOWED_USER_IDS:
        return True  # 허용 목록이 비어있으면 모든 사용자 허용
    return user_id in ALLOWED_USER_IDS
