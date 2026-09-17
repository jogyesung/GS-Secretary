"""테스트 공통 설정 - 환경변수를 config 로딩 전에 고정하고 임시 DB를 쓴다"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="gs_test_")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("ALLOWED_USER_IDS", "1,2")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")
os.environ.setdefault("GEMINI_API_KEY", "gm-test")
os.environ.setdefault("DART_API_KEY", "dart-test")
os.environ.setdefault("RUN_MODE", "all")
os.environ.setdefault("ALLOWED_PATHS", _tmp)
os.environ.setdefault("DATABASE_PATH", str(Path(_tmp) / "test.db"))
os.environ.setdefault("TEMP_DOWNLOAD_PATH", str(Path(_tmp) / "temp"))
os.environ.setdefault("GOOGLE_TOKEN_FILE", str(Path(_tmp) / "token.json"))
os.environ.setdefault("GOOGLE_TOKEN_B64", "")
os.environ.setdefault("GOOGLE_CREDENTIALS_B64", "")

import asyncio
import pytest


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    from bot.database import init_db
    asyncio.run(init_db())
