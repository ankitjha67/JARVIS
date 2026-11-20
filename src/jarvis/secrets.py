"""API keys live in Windows Credential Manager via keyring (SEC-5, FR-P10)."""

from __future__ import annotations

import logging
import os
import re

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from .config import KEYRING_SERVICE, NAME_RE

log = logging.getLogger(__name__)

# Fallback for development only: JARVIS_KEY_<PROVIDER> environment variables.
_ENV_PREFIX = "JARVIS_KEY_"


def get_key(provider_id: str) -> str | None:
    env = os.environ.get(_ENV_PREFIX + provider_id.upper())
    if env:
        return _remember(env)
    try:
        return _remember(keyring.get_password(KEYRING_SERVICE, provider_id) or None)
    except KeyringError as exc:
        log.warning("keyring read failed for %s: %s", provider_id, type(exc).__name__)
        return None


_known: set[str] = set()


def _remember(value: str | None) -> str | None:
    """Keep every secret Jarvis has handled so `redact` can mask it wherever it turns up."""
    if value and len(value) >= 8:
        _known.add(value)
    return value


def has_key(provider_id: str) -> bool:
    return get_key(provider_id) is not None


def set_key(provider_id: str, value: str | None) -> None:
    """Store or (when value is empty) delete a provider key."""
    if not NAME_RE.match(provider_id):
        raise ValueError("names use lowercase letters, digits and underscore, starting with a letter")
    value = (value or "").strip()
    if not value:
        try:
            keyring.delete_password(KEYRING_SERVICE, provider_id)
        except PasswordDeleteError:
            pass
        return
    if provider_id == "email":
        value = value.replace(" ", "")  # app passwords are often shown in spaced groups
    if len(value) > 512 or any(ch.isspace() for ch in value):
        raise ValueError("that does not look like a key or app password")
    keyring.set_password(KEYRING_SERVICE, provider_id, value)
    _remember(value)


_SECRET_PATTERNS = [
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,})"),
    re.compile(r"\b(nvapi-[A-Za-z0-9_\-]{16,})"),
    re.compile(r"\b(gsk_[A-Za-z0-9]{16,})"),
    re.compile(r"\b(AIza[0-9A-Za-z_\-]{30,})"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,})"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password|passwd|pwd)\s*[=:]\s*)[^\s'\";,]{6,}"),
]


def redact(text: str) -> str:
    """Mask anything that looks like a credential before it reaches logs or the audit table."""
    for value in _known:
        if value in text:
            text = text.replace(value, "[REDACTED]")
    for pat in _SECRET_PATTERNS:
        if pat.groups >= 1 and pat.pattern.startswith("(?i)"):
            text = pat.sub(lambda m: m.group(1) + "[REDACTED]", text)
        else:
            text = pat.sub("[REDACTED]", text)
    return text
