"""Windows implementations of the simple host primitives (pywin32, winsound, user32)."""

from __future__ import annotations

import ctypes
import os
import time
from typing import Any

from ..errors import ToolError


def open_target(target: str) -> None:
    os.startfile(target)  # the caller validated the target


def _clip_open() -> Any:
    import win32clipboard

    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            return win32clipboard
        except win32clipboard.error:
            time.sleep(0.05)
    raise ToolError("The clipboard is busy. Try again.")


def clipboard_get() -> str | None:
    clip = _clip_open()
    try:
        if not clip.IsClipboardFormatAvailable(clip.CF_UNICODETEXT):
            return None
        return str(clip.GetClipboardData(clip.CF_UNICODETEXT))
    finally:
        clip.CloseClipboard()


def clipboard_set(text: str) -> None:
    clip = _clip_open()
    try:
        clip.EmptyClipboard()
        clip.SetClipboardData(clip.CF_UNICODETEXT, text)
    finally:
        clip.CloseClipboard()


def play_wav(wav: bytes) -> None:
    import winsound

    winsound.PlaySound(wav, winsound.SND_MEMORY | winsound.SND_NODEFAULT)


def beep(freq: int, ms: int) -> None:
    import winsound

    winsound.Beep(freq, ms)


def lock_screen() -> None:
    if not ctypes.windll.user32.LockWorkStation():
        raise ToolError("Windows refused to lock the workstation.")


def foreground_app() -> dict[str, Any] | None:
    import psutil

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        process = psutil.Process(pid.value).name()
    except (psutil.Error, OSError):
        process = ""
    return {"title": buf.value, "process": process, "pid": int(pid.value)}
