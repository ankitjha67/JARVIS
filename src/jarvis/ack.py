"""Audible acknowledgement when Jarvis wakes: a short two-note chime, and a spoken reply in a chosen voice."""

from __future__ import annotations

import io
import logging
import math
import struct
import threading
import wave
from typing import Any

log = logging.getLogger(__name__)

# Windows keeps its newer voices (George, Ravi, …) in a second registry category that classic SAPI
# does not list by default; both are searched.
_CATEGORIES = (
    r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices",
    r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices",
)
PREFERRED_MALE = ("George", "Ravi", "David", "Mark", "James", "Guy", "Ryan")

_speak_lock = threading.Lock()
_chime_lock = threading.Lock()
_chime: bytes | None = None
_voices_cache: list[dict[str, str]] | None = None


def _build_chime() -> bytes:
    """Two rising sine notes with a soft envelope, rendered once as an in-memory WAV."""
    rate = 22050
    samples: list[int] = []
    for freq, seconds in ((880.0, 0.07), (1318.5, 0.13)):
        n = int(rate * seconds)
        for i in range(n):
            envelope = min(1.0, i / (rate * 0.008)) * (1.0 - i / n) ** 1.5
            samples.append(int(32767 * 0.35 * envelope * math.sin(2 * math.pi * freq * i / rate)))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


def _tokens() -> list[Any]:
    import win32com.client

    found = []
    for category_id in _CATEGORIES:
        try:
            category = win32com.client.Dispatch("SAPI.SpObjectTokenCategory")
            category.SetId(category_id, False)
            tokens = category.EnumerateTokens()
            found.extend(tokens.Item(i) for i in range(tokens.Count))
        except Exception:
            log.debug("voice category unavailable: %s", category_id, exc_info=True)
    return found


def _describe(token: Any) -> dict[str, str]:
    def attr(name: str) -> str:
        try:
            return str(token.GetAttribute(name))
        except Exception:  # noqa: BLE001 - voices omit attributes freely; a missing one is not an error
            return ""

    return {"name": str(token.GetDescription()), "gender": attr("Gender").lower(), "language": attr("Language")}


def list_voices() -> list[dict[str, str]]:
    """Installed English voices, de-duplicated by name. Blocking (COM); cached after the first call."""
    global _voices_cache
    if _voices_cache is not None:
        return _voices_cache
    import pythoncom

    pythoncom.CoInitialize()
    try:
        seen: dict[str, dict[str, str]] = {}
        for token in _tokens():
            info = _describe(token)
            # Language is a hex LCID; the low byte 0x09 is English.
            codes = [c for c in info["language"].split(";") if c]
            if codes and not any(int(c, 16) & 0xFF == 0x09 for c in codes):
                continue
            seen.setdefault(info["name"], info)
        _voices_cache = sorted(seen.values(), key=lambda v: (v["gender"] != "male", v["name"]))
    except Exception:
        log.debug("could not list voices", exc_info=True)
        _voices_cache = []
    finally:
        pythoncom.CoUninitialize()
    return _voices_cache


def default_voice() -> str:
    """The first installed male English voice, by preference order; otherwise whatever exists."""
    voices = list_voices()
    males = [v["name"] for v in voices if v["gender"] == "male"]
    for wanted in PREFERRED_MALE:
        for name in males:
            if wanted.lower() in name.lower():
                return name
    return males[0] if males else (voices[0]["name"] if voices else "")


def _say(text: str, voice_name: str) -> None:
    if not _speak_lock.acquire(blocking=False):
        return  # already speaking; do not stack replies
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            wanted = voice_name or default_voice()
            if wanted:
                for token in _tokens():
                    if str(token.GetDescription()) == wanted:
                        voice.Voice = token
                        break
            voice.Speak(text)
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        log.debug("spoken reply failed", exc_info=True)
    finally:
        _speak_lock.release()


def _ring() -> None:
    global _chime
    if not _chime_lock.acquire(blocking=False):
        return
    try:
        import winsound

        if _chime is None:
            _chime = _build_chime()
        winsound.PlaySound(_chime, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
    except Exception:
        log.debug("chime failed", exc_info=True)
    finally:
        _chime_lock.release()


def chime() -> None:
    """Play the wake chime without blocking the caller."""
    threading.Thread(target=_ring, name="jarvis-chime", daemon=True).start()


def speak(text: str, voice_name: str = "") -> None:
    """Say a short phrase in the chosen (or default male) voice without blocking the caller."""
    if text.strip():
        threading.Thread(target=_say, args=(text[:300], voice_name), name="jarvis-speak", daemon=True).start()
