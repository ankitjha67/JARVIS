"""`jarvis --doctor`: check what works on this computer and print a report to paste into a bug report.

Every check is read-only or puts things back as it found them (the clipboard test restores the clipboard,
the key-store test deletes its probe entry). Nothing is sent anywhere.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from . import __version__, host
from .config import DATA_DIR, UI_DIST
from .errors import ToolError

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
Result = tuple[str, str]


def _platform() -> Result:
    mac = platform.mac_ver()[0]
    system = f"macOS {mac}" if mac else f"{platform.system()} {platform.release()} ({platform.version()})"
    detail = f"{system}, {platform.machine()}, Python {platform.python_version()}"
    if host.IS_MAC and platform.machine() != "arm64":
        return WARN, detail + " - Intel Macs are not supported yet (no PyTorch wheels)"
    if not (host.IS_WINDOWS or host.IS_MAC):
        return WARN, detail + " - only Windows and macOS are supported"
    return OK, detail


def _tools() -> Result:
    from . import tools

    return OK, f"{len(tools.REGISTRY)} tools registered"


def _data_dir() -> Result:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    probe = DATA_DIR / ".doctor"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    return OK, str(DATA_DIR)


def _hud() -> Result:
    if (UI_DIST / "index.html").exists():
        return OK, "built"
    return FAIL, "ui/dist is missing - run: npm --prefix ui install && npm --prefix ui run build"


def _webview() -> Result:
    from importlib.metadata import version

    import webview  # noqa: F401

    detail = f"pywebview {version('pywebview')}"
    if host.IS_MAC:
        import AppKit  # noqa: F401
        import WebKit  # noqa: F401

        detail += ", WKWebView available"
    return OK, detail


def _keystore() -> Result:
    import keyring

    backend = type(keyring.get_keyring()).__name__
    keyring.set_password("jarvis-doctor", "probe", "probe-value")
    try:
        if keyring.get_password("jarvis-doctor", "probe") != "probe-value":
            return FAIL, f"{backend}: a saved value did not read back"
    finally:
        keyring.delete_password("jarvis-doctor", "probe")
    return OK, f"{backend} stores and returns keys"


def _clipboard() -> Result:
    before = host.clipboard_get()
    if before is None and os.environ.get("JARVIS_DOCTOR_WRITE") != "1":
        # Empty, or an image or file that could not be put back: leave it alone.
        return OK, "readable (write test skipped: the clipboard holds no text to restore)"
    probe = f"jarvis-doctor-{time.time_ns()} é✓"
    host.clipboard_set(probe)
    try:
        if host.clipboard_get() != probe:
            return FAIL, "text written to the clipboard did not read back"
    finally:
        host.clipboard_set(before or "")
    return OK, "read, write and non-ASCII text work"


def _shell() -> Result:
    done = subprocess.run(host.shell_argv("echo jarvis-ok"), capture_output=True, text=True, timeout=30, check=False,
                          creationflags=host.NO_WINDOW)
    if "jarvis-ok" not in done.stdout:
        return FAIL, f"{host.SHELL_NAME} returned {done.returncode}: {(done.stderr or done.stdout).strip()[:120]}"
    return OK, f"{host.SHELL_NAME} runs commands"


def _foreground() -> Result:
    fg = host.foreground_app()
    if not fg:
        return WARN, "no foreground window could be identified"
    return OK, f"{fg['process']} - \"{fg['title'][:50]}\""


def _apps() -> Result:
    from .tools import desktop

    apps = desktop._load_apps()
    return (OK if apps else WARN), f"{len(apps)} installed apps found"


def _windows() -> Result:
    from .tools import desktop

    rows = desktop._windows()
    if host.IS_MAC and desktop._window_note:
        return WARN, f"{len(rows)} windows (limited): Accessibility permission is off"
    return OK, f"{len(rows)} windows listed"


def _volume() -> Result:
    if host.IS_MAC:
        level, muted = host.mac._volume_state()
        if level is None:
            return WARN, "the current output device has no volume control"
        return OK, f"{level}%{' (muted)' if muted else ''}"
    from .tools import media

    vol = media._endpoint()
    return OK, f"{round(vol.GetMasterVolumeLevelScalar() * 100)}%"


def _voices() -> Result:
    from . import ack

    voices = ack.list_voices()
    if not voices:
        return WARN, "no English system voices found; spoken replies need a cloud voice"
    return OK, f"{len(voices)} English voices, default \"{ack.default_voice()}\""


def _screen() -> Result:
    from . import vision

    png = vision.capture_screen()
    detail = f"captured {len(png) // 1024} KB"
    if host.IS_MAC and host.mac.screen_recording_granted() is False:
        return WARN, detail + " - Screen Recording permission is off, so only the desktop picture is visible"
    return OK, detail


def _microphone() -> Result:
    import sounddevice

    inputs = [d for d in sounddevice.query_devices() if d.get("max_input_channels", 0) > 0]
    if not inputs:
        return WARN, "no input device; the wake word and voice input will not work"
    return OK, f"{len(inputs)} input device(s), e.g. {inputs[0]['name']}"


def _accessibility() -> Result:
    if not host.IS_MAC:
        return SKIP, "not needed on this OS"
    granted = host.mac.accessibility_granted()
    if granted:
        return OK, "granted"
    if granted is None:
        return WARN, "could not be determined"
    return WARN, ("off - window control, typing, shortcuts and media keys are unavailable until you enable the app "
                  "you start Jarvis from under System Settings > Privacy & Security > Accessibility")


def _hotkeys() -> Result:
    if not host.IS_MAC:
        return SKIP, "registered when the app starts"
    from .hotkeys import HotkeyThread

    keys = HotkeyThread(lambda: None, lambda: None)
    keys.start()
    registered = dict(keys.registered)
    keys.stop()
    if all(registered.values()):
        return OK, f"{host.HOTKEYS['summon']} and {host.HOTKEYS['kill']} can be registered"
    taken = ", ".join(host.HOTKEYS[name] for name, ok in registered.items() if not ok)
    return WARN, f"could not register {taken} (another app may be using it)"


def _trash() -> Result:
    import send2trash  # noqa: F401

    return OK, f"deletions go to the {host.TRASH_NAME}"


CHECKS: list[tuple[str, Callable[[], Result]]] = [
    ("System", _platform),
    ("Tools", _tools),
    ("Data folder", _data_dir),
    ("HUD build", _hud),
    ("Window shell", _webview),
    ("Key store", _keystore),
    ("Shell", _shell),
    ("Clipboard", _clipboard),
    ("Foreground app", _foreground),
    ("Installed apps", _apps),
    ("Window list", _windows),
    ("Volume", _volume),
    ("System voices", _voices),
    ("Screen capture", _screen),
    ("Microphone", _microphone),
    ("Accessibility", _accessibility),
    ("Global hotkeys", _hotkeys),
    ("Trash", _trash),
]

_MARK = {OK: "PASS", WARN: "WARN", FAIL: "FAIL", SKIP: "SKIP"}


def run() -> list[dict[str, Any]]:
    results = []
    for name, check in CHECKS:
        started = time.perf_counter()
        try:
            status, detail = check()
        except ToolError as exc:
            status, detail = WARN, str(exc)
        except Exception as exc:  # noqa: BLE001 - the report is the point; nothing may stop it
            status, detail = FAIL, f"{type(exc).__name__}: {exc}"[:300]
        results.append({"check": name, "status": status, "detail": detail,
                        "ms": int((time.perf_counter() - started) * 1000)})
    return results


def main(as_json: bool = False) -> int:
    """Print the report. Exit code 1 when any check failed (warnings do not fail)."""
    results = run()
    if as_json:
        print(json.dumps({"jarvis": __version__, "os": host.NAME, "checks": results}, indent=2, ensure_ascii=False))
    else:
        out = [f"Jarvis {__version__} doctor - {host.NAME}", ""]
        out += [f"[{_MARK[r['status']]}] {r['check']:<15} {r['detail']}" for r in results]
        counts = {s: sum(r["status"] == s for r in results) for s in (OK, WARN, FAIL)}
        out += ["", f"{counts[OK]} passed, {counts[WARN]} warnings, {counts[FAIL]} failed."]
        text = "\n".join(out)
        try:
            print(text)
        except UnicodeEncodeError:  # legacy Windows consoles
            sys.stdout.buffer.write(text.encode("utf-8", "replace") + b"\n")
    return 1 if any(r["status"] == FAIL for r in results) else 0
