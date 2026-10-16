"""
Configuration loader. Secrets come from environment variables (.env in your
own environment) — never hardcoded (Master Prompt Section 48).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")):
    """Minimal .env loader (no python-dotenv dependency required)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class Settings:
    telegram_bot_token: str = ""
    owner_id: str = ""

    telegram_api_id: str = ""       # empty => Userbot scanner disabled (Section 7.3)
    telegram_api_hash: str = ""

    email_host: str = ""
    email_port: str = ""
    email_username: str = ""
    email_password: str = ""

    anthropic_api_key: str = ""     # empty => AI classifier layer stays off, rule engine only

    telegram_tos_acknowledged: bool = False  # explicit opt-in gate — see Section 7.4

    database_path: str = "job_hunter.db"

    @property
    def scanner_enabled(self) -> bool:
        """
        True only if BOTH credentials are set AND the owner has explicitly
        acknowledged reviewing Master Prompt Section 7.4 (Telegram API ToS
        compliance for the Userbot->AI pipeline) via TELEGRAM_TOS_ACKNOWLEDGED
        in .env. Credentials alone are not enough — this is a deliberate
        extra gate so the scanner never starts silently without that review
        having happened (Governing Resolution Rule 7).
        """
        return bool(self.telegram_api_id and self.telegram_api_hash and self.telegram_tos_acknowledged)

    @property
    def email_enabled(self) -> bool:
        return bool(self.email_host and self.email_username and self.email_password)

    @classmethod
    def load(cls) -> "Settings":
        _load_dotenv()
        return cls(
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            owner_id=os.environ.get("OWNER_ID", ""),
            telegram_api_id=os.environ.get("TELEGRAM_API_ID", ""),
            telegram_api_hash=os.environ.get("TELEGRAM_API_HASH", ""),
            email_host=os.environ.get("EMAIL_HOST", ""),
            email_port=os.environ.get("EMAIL_PORT", ""),
            email_username=os.environ.get("EMAIL_USERNAME", ""),
            email_password=os.environ.get("EMAIL_PASSWORD", ""),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            telegram_tos_acknowledged=os.environ.get("TELEGRAM_TOS_ACKNOWLEDGED", "").strip().lower() in ("1", "true", "yes"),
            database_path=os.environ.get("DATABASE_PATH", "job_hunter.db"),
        )
