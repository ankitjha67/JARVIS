"""Credential health: password strength, breach lookup (k-anonymity), generation."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import secrets
import string
import urllib.request

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

_COMMON_PASSWORDS = frozenset([
    "password", "123456", "12345678", "qwerty", "abc123", "monkey", "1234567",
    "letmein", "trustno1", "dragon", "baseball", "iloveyou", "master", "sunshine",
    "ashley", "bailey", "shadow", "123123", "654321", "superman", "qazwsx",
    "michael", "football", "password1", "password123", "welcome", "hello",
    "charlie", "donald", "admin", "login", "princess", "starwars",
])


def _password_entropy(pw: str) -> float:
    pool = 0
    if re.search(r"[a-z]", pw):
        pool += 26
    if re.search(r"[A-Z]", pw):
        pool += 26
    if re.search(r"\d", pw):
        pool += 10
    if re.search(r"[^a-zA-Z0-9]", pw):
        pool += 32
    if pool == 0:
        return 0.0
    return len(pw) * math.log2(pool)


def _strength_score(pw: str) -> dict:
    entropy = _password_entropy(pw)
    length = len(pw)
    has_upper = bool(re.search(r"[A-Z]", pw))
    has_lower = bool(re.search(r"[a-z]", pw))
    has_digit = bool(re.search(r"\d", pw))
    has_symbol = bool(re.search(r"[^a-zA-Z0-9]", pw))
    char_classes = sum([has_upper, has_lower, has_digit, has_symbol])
    is_common = pw.lower() in _COMMON_PASSWORDS
    has_repeat = bool(re.search(r"(.)\1{2,}", pw))
    has_sequence = bool(re.search(r"(012|123|234|345|456|567|678|789|abc|bcd|cde|def|xyz)", pw.lower()))

    suggestions: list[str] = []
    if length < 12:
        suggestions.append("Use at least 12 characters.")
    if char_classes < 3:
        suggestions.append("Mix uppercase, lowercase, digits, and symbols.")
    if is_common:
        suggestions.append("This is a commonly used password — choose something unique.")
    if has_repeat:
        suggestions.append("Avoid repeated characters (e.g. 'aaa').")
    if has_sequence:
        suggestions.append("Avoid sequential patterns (e.g. '123', 'abc').")

    if is_common or entropy < 25:
        rating = "very_weak"
    elif entropy < 40:
        rating = "weak"
    elif entropy < 60:
        rating = "moderate"
    elif entropy < 80:
        rating = "strong"
    else:
        rating = "very_strong"

    return {
        "rating": rating,
        "entropy_bits": round(entropy, 1),
        "length": length,
        "character_classes": char_classes,
        "is_common": is_common,
        "suggestions": suggestions,
    }


class PasswordCheckArgs(Args):
    password: str = Field(..., min_length=1, max_length=200,
                          description="Password to evaluate locally (never transmitted).")


@tool("credential_check_password",
      "Check password strength locally: entropy, common-pattern detection, suggestions. "
      "The password is NOT stored or transmitted.",
      PasswordCheckArgs, Tier.T0,
      summary=lambda a: "Check password strength")
async def credential_check_password(a: PasswordCheckArgs) -> dict:
    return _strength_score(a.password)


# ---------------------------------------------------------------------------
# HIBP Pwned Passwords k-anonymity (free, no API key)
# ---------------------------------------------------------------------------

def _check_pwned(password: str) -> int:
    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]
    url = f"https://api.pwnedpasswords.com/range/{prefix}"
    req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-CredentialCheck"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8")
    for line in body.splitlines():
        parts = line.strip().split(":")
        if len(parts) == 2 and parts[0] == suffix:
            return int(parts[1])
    return 0


class PwnedCheckArgs(Args):
    password: str = Field(..., min_length=1, max_length=200,
                          description="Password to check (only the first 5 chars of its SHA-1 hash are sent).")


@tool("credential_check_reuse",
      "Check if a password appears in known data breaches via HIBP Pwned Passwords "
      "(k-anonymity: only the first 5 hex chars of the SHA-1 hash leave the machine).",
      PwnedCheckArgs, Tier.T0,
      summary=lambda a: "Check password breach exposure")
async def credential_check_reuse(a: PwnedCheckArgs) -> dict:
    try:
        count = await asyncio.to_thread(_check_pwned, a.password)
    except Exception as exc:
        raise ToolError(f"HIBP lookup failed: {exc}") from exc
    return {
        "seen_count": count,
        "compromised": count > 0,
        "recommendation": "This password has appeared in data breaches — do not use it."
        if count > 0 else "Not found in known breaches.",
    }


# ---------------------------------------------------------------------------
# Password generator
# ---------------------------------------------------------------------------

class GenerateArgs(Args):
    length: int = Field(20, ge=8, le=128, description="Password length")
    uppercase: bool = Field(True)
    lowercase: bool = Field(True)
    digits: bool = Field(True)
    symbols: bool = Field(True)
    exclude_ambiguous: bool = Field(False, description="Exclude visually ambiguous chars (0O1lI)")


@tool("credential_generate",
      "Generate a cryptographically secure random password.",
      GenerateArgs, Tier.T0,
      summary=lambda a: f"Generate {a.length}-char password")
async def credential_generate(a: GenerateArgs) -> dict:
    alphabet = ""
    if a.uppercase:
        alphabet += string.ascii_uppercase
    if a.lowercase:
        alphabet += string.ascii_lowercase
    if a.digits:
        alphabet += string.digits
    if a.symbols:
        alphabet += "!@#$%^&*()-_=+[]{}|;:,.<>?"
    if not alphabet:
        raise ToolError("Enable at least one character class.")
    if a.exclude_ambiguous:
        alphabet = alphabet.translate(str.maketrans("", "", "0O1lI|"))
    if not alphabet:
        raise ToolError("No characters left after excluding ambiguous set.")
    pw = "".join(secrets.choice(alphabet) for _ in range(a.length))
    score = _strength_score(pw)
    return {"password": pw, "length": len(pw), "entropy_bits": score["entropy_bits"]}
