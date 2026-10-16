"""Credential encryption, webhook signature verification and log redaction."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


def _fernet() -> Fernet:
    secret = get_settings().app_secret_key.encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(b"career-coach-credential-v1" + secret).digest())
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "••••"


def compute_github_signature(secret: str, raw_body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def verify_github_signature(secret: str, raw_body: bytes, header: str | None) -> bool:
    """HMAC-SHA256 over the raw body, constant-time compare (GitHub X-Hub-Signature-256)."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = compute_github_signature(secret, raw_body)
    return hmac.compare_digest(expected.encode(), header.strip().encode())


_REDACT_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(authorization:\s*)(bearer|token)\s+\S+"),
]


def redact(text: str | None) -> str:
    if not text:
        return ""
    out = text
    for p in _REDACT_PATTERNS:
        out = p.sub(lambda m: (m.group(1) + "[REDACTED]") if m.lastindex else "[REDACTED]", out)
    return out


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
