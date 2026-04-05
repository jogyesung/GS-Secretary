"""Google Drive OAuth 2.0 인증 관리"""

import json
import os
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from bot.config import GOOGLE_CREDENTIALS_FILE, GOOGLE_TOKEN_FILE, GOOGLE_SCOPES


def get_drive_credentials() -> Credentials | None:
    """저장된 토큰에서 Google Drive 자격증명을 가져옵니다."""
    creds = None
    token_path = Path(GOOGLE_TOKEN_FILE)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GOOGLE_SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds)

    return creds


def get_auth_url() -> str | None:
    """OAuth 인증 URL을 생성합니다."""
    if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        return None

    flow = InstalledAppFlow.from_client_secrets_file(
        GOOGLE_CREDENTIALS_FILE,
        scopes=GOOGLE_SCOPES,
        redirect_uri="urn:ietf:wg:oauth:2.0:oob",
    )
    auth_url, _ = flow.authorization_url(prompt="consent")
    return auth_url


def exchange_code(code: str) -> Credentials | None:
    """인증 코드를 토큰으로 교환합니다."""
    if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        return None

    flow = InstalledAppFlow.from_client_secrets_file(
        GOOGLE_CREDENTIALS_FILE,
        scopes=GOOGLE_SCOPES,
        redirect_uri="urn:ietf:wg:oauth:2.0:oob",
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    _save_token(creds)
    return creds


def _save_token(creds: Credentials):
    """토큰을 파일에 저장합니다."""
    token_path = Path(GOOGLE_TOKEN_FILE)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w") as f:
        f.write(creds.to_json())


def is_authenticated() -> bool:
    """Google Drive 인증 여부 확인"""
    creds = get_drive_credentials()
    return creds is not None and creds.valid
