"""The operating-system layer.

Everything that differs between Windows and macOS goes through here, so the rest of Jarvis asks for an
outcome ("copy this text", "lock the screen") rather than for a Win32 or AppleScript call. Functions block;
callers on the event loop wrap them in asyncio.to_thread. Failures the user can act on raise ToolError.

Simple primitives are implemented for every OS in `_windows` / `_macos`. Larger Windows features (UI
Automation, window management, audio endpoints) keep their code in the tool modules and branch to
`host.mac` on macOS.
"""

from __future__ import annotations

import io
import math
import os
import signal
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

from ..errors import ToolError

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
NAME = "Windows" if IS_WINDOWS else "macOS" if IS_MAC else "Linux"
SHELL_NAME = "PowerShell" if IS_WINDOWS else "zsh" if IS_MAC else "sh"
KEYSTORE_NAME = ("Windows Credential Manager" if IS_WINDOWS else "the macOS Keychain" if IS_MAC
                 else "the system keyring")
TRASH_NAME = "Recycle Bin" if IS_WINDOWS else "Trash"
SYSTEM_DISK = (os.environ.get("SystemDrive") or "C:") + "\\" if IS_WINDOWS else "/"
HOTKEYS = ({"summon": "Ctrl+Option+J", "kill": "Ctrl+Option+Delete"} if IS_MAC
           else {"summon": "Ctrl+Alt+J", "kill": "Ctrl+Alt+Backspace"})

# Keeps console windows from flashing up on Windows; subprocess rejects any other value elsewhere.
NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0

if IS_WINDOWS:
    from . import _windows as _impl
elif IS_MAC:
    from . import _macos as _impl
    from . import _macos as mac  # noqa: F401 - the macOS-only features, used by tools behind `if host.IS_MAC`
else:
    _impl = None  # type: ignore[assignment]


def unsupported(feature: str) -> ToolError:
    return ToolError(f"{feature} is not available on {NAME} yet.")


def _need(name: str, feature: str) -> Any:
    fn = getattr(_impl, name, None)
    if fn is None:
        raise unsupported(feature)
    return fn


# -- processes --------------------------------------------------------------------------------------

def child_kwargs() -> dict[str, Any]:
    """Popen keyword arguments for a child whose whole process tree may have to be killed later."""
    return {"creationflags": NO_WINDOW} if IS_WINDOWS else {"start_new_session": True}


def kill_tree(pid: int) -> None:
    """Stop a process and everything it started. The child must have been spawned with child_kwargs()."""
    if IS_WINDOWS:
        subprocess.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False, creationflags=NO_WINDOW)
        return
    try:
        group = os.getpgid(pid)
    except ProcessLookupError:
        return
    if group == os.getpgid(0):  # never signal Jarvis's own group
        _signal(os.kill, pid, signal.SIGKILL)
        return
    _signal(os.killpg, group, signal.SIGTERM)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.killpg(group, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.05)
    _signal(os.killpg, group, signal.SIGKILL)


def _signal(send: Any, target: int, sig: int) -> None:
    try:
        send(target, sig)
    except (ProcessLookupError, PermissionError):
        pass


def shell_argv(command: str) -> list[str]:
    """The non-interactive, profile-free command line that runs `command` in this OS's shell."""
    if IS_WINDOWS:
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
    if IS_MAC:
        return ["/bin/zsh", "-f", "-c", command]
    return ["/bin/sh", "-c", command]


def npm() -> str:
    return "npm.cmd" if IS_WINDOWS else "npm"


# -- opening things ---------------------------------------------------------------------------------

def open_path(path: str | Path) -> None:
    """Open a file or folder with its default application."""
    _need("open_target", "Opening files")(str(path))


def open_uri(uri: str) -> None:
    """Open a URI (tel:, a settings page, …) with whatever app is registered for it. Raises OSError."""
    _need("open_target", "Opening links")(uri)


# -- clipboard --------------------------------------------------------------------------------------

def clipboard_get() -> str | None:
    """The clipboard's text, or None when it holds something else."""
    return _need("clipboard_get", "The clipboard")()


def clipboard_set(text: str) -> None:
    _need("clipboard_set", "The clipboard")(text)


def clipboard_change_count() -> int | None:
    """A number that changes whenever the clipboard does, where the OS offers one cheaply."""
    fn = getattr(_impl, "clipboard_change_count", None)
    return fn() if fn else None


# -- sound ------------------------------------------------------------------------------------------

def tone_wav(freq: float, ms: int, volume: float = 0.35) -> bytes:
    rate = 22050
    n = max(1, int(rate * ms / 1000))
    fade = max(1, int(rate * 0.008))
    samples = [int(32767 * volume * min(1.0, i / fade, (n - i) / fade) * math.sin(2 * math.pi * freq * i / rate))
               for i in range(n)]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


def play_wav(wav: bytes) -> None:
    """Play a WAV through the default output and wait for it to finish."""
    _need("play_wav", "Audio playback")(wav)


def beep(freq: int = 1000, ms: int = 400) -> None:
    """A short alert tone. Never raises: a missing speaker must not break a timer."""
    try:
        fn = getattr(_impl, "beep", None)
        if fn:
            fn(freq, ms)
        else:
            play_wav(tone_wav(freq, ms))
    except Exception:  # noqa: BLE001
        pass


# -- session ----------------------------------------------------------------------------------------

def lock_screen() -> None:
    _need("lock_screen", "Locking the screen")()


def foreground_app() -> dict[str, Any] | None:
    """{"title", "process", "pid"} of the window the user is working in. Never raises."""
    fn = getattr(_impl, "foreground_app", None)
    try:
        return fn() if fn else None
    except Exception:  # noqa: BLE001
        return None


def foreground_title() -> str:
    fg = foreground_app()
    return str(fg["title"]) if fg else ""
