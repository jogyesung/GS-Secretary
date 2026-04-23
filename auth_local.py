"""Google OAuth 로컬 인증 스크립트 - PC에서 실행하여 토큰 발급"""

import base64
import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
]

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = Path("data") / "token.json"


def main():
    if not Path(CREDENTIALS_FILE).exists():
        print("❌ credentials.json 파일이 없습니다.")
        print("   Google Cloud Console에서 다운로드하세요.")
        return

    print("🔐 Google OAuth 인증을 시작합니다...")
    print("   브라우저가 자동으로 열립니다. Google 계정으로 로그인하세요.\n")

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, scopes=SCOPES)
    creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"\n✅ 인증 완료! 토큰 저장됨: {TOKEN_FILE}")

    # Railway용 Base64 출력
    token_b64 = base64.b64encode(TOKEN_FILE.read_bytes()).decode()
    print(f"\n{'='*60}")
    print("📋 Railway GOOGLE_TOKEN_B64 값 (아래 전체를 복사하세요):")
    print(f"{'='*60}")
    print(token_b64)
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
