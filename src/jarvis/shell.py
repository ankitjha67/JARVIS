"""HUD window on the system web view (WebView2 on Windows, WKWebView on macOS).

Isolated here so the shell can be swapped later (ADR 2).
"""

from __future__ import annotations

import sys
from typing import Any

import webview

from .config import DATA_DIR, ROOT


def force_foreground() -> bool:
    """Raise and focus the HUD window, even though another app has focus."""
    if sys.platform == "darwin":
        return _activate_macos()
    if sys.platform != "win32":
        return False
    return _force_foreground_windows()


def _activate_macos() -> bool:
    try:
        from AppKit import NSApplication
        from PyObjCTools import AppHelper
    except ImportError:
        return False
    # AppKit may only be touched from the main thread; the wake word and hotkeys call from others.
    AppHelper.callAfter(lambda: NSApplication.sharedApplication().activateIgnoringOtherApps_(True))
    return True


def _force_foreground_windows() -> bool:
    """Windows refuses SetForegroundWindow from a background process unless that process just received input,
    so a synthetic Alt tap is sent first; a brief always-on-top flip guarantees the window is at least
    visible above everything else if focus is still refused.
    """
    import ctypes
    import os

    import win32con
    import win32gui
    import win32process

    target: list[int] = []
    my_pid = os.getpid()

    def visit(hwnd: int, _: object) -> bool:
        if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == my_pid and win32gui.GetWindowText(hwnd):
                target.append(hwnd)
                return False
        return True

    try:
        win32gui.EnumWindows(visit, None)
    except win32gui.error:
        pass  # EnumWindows reports an error when the callback stops it early
    if not target:
        return False
    hwnd = target[0]
    # Never restore or show the window from here: doing that with raw Win32 calls leaves the embedded WebView2
    # un-painted (a black window). The caller restores through pywebview first; this only raises and focuses.
    flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
    win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except win32gui.error:
        return False
    return True


def run_window(url: str, window_ref: dict[str, Any], title: str = "Jarvis") -> None:
    """Blocks on the main thread until the window is closed."""
    # The web view opens a native Save As dialog for report downloads.
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(
        title, url, width=1280, height=800, min_size=(1100, 680),
        frameless=True, easy_drag=False, background_color="#05080D", text_select=True,
    )
    window_ref["win"] = window
    # Web view user data would default to a folder in the user profile; keep it with the project data.
    options: dict[str, Any] = {"private_mode": False, "storage_path": str(DATA_DIR / "webview")}
    if sys.platform == "win32":
        options["icon"] = str(ROOT / "assets" / "logo.ico")
    webview.start(**options)
