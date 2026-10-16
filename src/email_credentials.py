"""
Per-user email credentials (owner requirement: NO shared email account —
every user sends applications from their OWN email, via their own App
Password after enabling 2FA on their side).

Passwords are encrypted at rest using a key from .env (ENCRYPTION_KEY).
Never logged, never displayed back to the user after entry.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

try:
    from cryptography.fernet import Fernet, InvalidToken
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False

# Known providers -> (smtp_host, smtp_port). Extend as needed.
KNOWN_PROVIDERS = {
    "gmail.com": ("smtp.gmail.com", 587),
    "googlemail.com": ("smtp.gmail.com", 587),
    "outlook.com": ("smtp-mail.outlook.com", 587),
    "hotmail.com": ("smtp-mail.outlook.com", 587),
    "live.com": ("smtp-mail.outlook.com", 587),
    "yahoo.com": ("smtp.mail.yahoo.com", 587),
    "icloud.com": ("smtp.mail.me.com", 587),
    "me.com": ("smtp.mail.me.com", 587),
}

_APP_PASSWORD_SETUP_LINKS = {
    "smtp.gmail.com": "https://myaccount.google.com/apppasswords",
    "smtp-mail.outlook.com": "https://account.live.com/proofs/AppPassword",
    "smtp.mail.yahoo.com": "https://login.yahoo.com/account/security",
}


def detect_smtp_provider(email: str) -> Optional[tuple]:
    """Returns (host, port) if the email domain is a known provider, else None
    (caller must then ask the user to enter host/port manually)."""
    domain = email.strip().lower().split("@")[-1]
    return KNOWN_PROVIDERS.get(domain)


def app_password_setup_link(smtp_host: str) -> Optional[str]:
    return _APP_PASSWORD_SETUP_LINKS.get(smtp_host)


def get_encryption_key() -> bytes:
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set in .env — required to store per-user "
            "App Passwords encrypted at rest. Generate one with: "
            "python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return key.encode()


def encrypt_password(plain_password: str) -> str:
    if not CRYPTOGRAPHY_AVAILABLE:
        raise RuntimeError("The 'cryptography' package is not installed.")
    f = Fernet(get_encryption_key())
    return f.encrypt(plain_password.encode()).decode()


def decrypt_password(encrypted_password: str) -> str:
    if not CRYPTOGRAPHY_AVAILABLE:
        raise RuntimeError("The 'cryptography' package is not installed.")
    f = Fernet(get_encryption_key())
    try:
        return f.decrypt(encrypted_password.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError(
            "Could not decrypt stored App Password — ENCRYPTION_KEY may have "
            "changed since it was stored. The user needs to re-enter it."
        ) from e


# Loose App Password shape check (Gmail-style: 16 chars, often shown with
# spaces like "abcd efgh ijkl mnop"). Not exhaustive — just catches obvious
# mistakes like pasting a normal password by accident.
_APP_PASSWORD_LOOKS_VALID_RE = re.compile(r"^[A-Za-z0-9 ]{8,32}$")


def looks_like_app_password(value: str) -> bool:
    return bool(_APP_PASSWORD_LOOKS_VALID_RE.match(value.strip()))


@dataclass
class SmtpCredentials:
    email: str
    smtp_host: str
    smtp_port: int
    encrypted_password: str

    def decrypted_password(self) -> str:
        return decrypt_password(self.encrypted_password)
