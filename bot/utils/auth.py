"""사용자 인증 유틸리티 - Telegram 사용자 whitelist 체크"""

import logging

from bot.config import ALLOWED_USER_IDS

logger = logging.getLogger(__name__)

if not ALLOWED_USER_IDS:
    logger.error("ALLOWED_USER_IDS가 비어 있어요. 모든 요청을 거부합니다 (fail-closed).")


def is_authorized(user_id: int) -> bool:
    """허용 목록에 있는 사용자만. 목록이 비어 있으면 아무도 허용하지 않는다."""
    return bool(ALLOWED_USER_IDS) and user_id in ALLOWED_USER_IDS
