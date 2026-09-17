"""Google OAuth 2.0 인증 관리

봇 안에서 하는 OOB(urn:ietf:wg:oauth:2.0:oob) 인증 흐름은 Google이 막아서 동작하지 않는다.
인증은 PC에서 `python auth_local.py`로 하고, 서버에는 GOOGLE_TOKEN_B64로 올린다.
"""

import asyncio
import logging
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from bot.config import GOOGLE_TOKEN_FILE, GOOGLE_SCOPES

logger = logging.getLogger(__name__)


def get_drive_credentials() -> Credentials | None:
    """저장된 토큰에서 Google 자격증명을 가져옵니다 (동기 — 핸들러에서는 async 버전 사용)."""
    creds = None
    token_path = Path(GOOGLE_TOKEN_FILE)

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), GOOGLE_SCOPES)
        except Exception as e:
            logger.error(f"토큰 파일 로드 실패: {e}")
            return None

    if not creds:
        return None

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            logger.info("Google OAuth 토큰 자동 갱신 성공")
        except Exception as e:
            logger.error(f"토큰 갱신 실패 (재인증 필요): {e}")
            return None

    return creds


def _save_token(creds: Credentials):
    """토큰을 파일에 저장합니다."""
    token_path = Path(GOOGLE_TOKEN_FILE)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w") as f:
        f.write(creds.to_json())


def is_authenticated() -> bool:
    """Google 인증 여부 확인 (동기 — 토큰 갱신 시 네트워크 호출이 있으니 핸들러에서는 async 버전)"""
    creds = get_drive_credentials()
    return creds is not None and creds.valid


async def is_authenticated_async() -> bool:
    return await asyncio.to_thread(is_authenticated)


AUTH_HELP_TEXT = (
    "🔐 Google 인증은 PC에서 진행해요.\n\n"
    "1. 프로젝트 폴더에서 `python auth_local.py` 실행 → 브라우저 로그인\n"
    "2. 생성된 data/token.json을 base64로 인코딩해 Railway 환경변수 GOOGLE_TOKEN_B64에 설정\n"
    "   (PowerShell: [Convert]::ToBase64String([IO.File]::ReadAllBytes('data/token.json')))\n"
    "3. 재배포하면 연결돼요."
)
