"""Running in the background (Windows): a notification-area icon, one running copy, and starting at sign-in.

When the HUD window is closed Jarvis keeps running so the wake word and the summon hotkey still work. The icon
is how the user sees that it is running, whether the microphone is in use, and how they quit for real.

Built on pywin32 only. On other systems `AVAILABLE` is False and closing the window quits, as before.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from .config import PORT, ROOT

log = logging.getLogger("jarvis.tray")

AVAILABLE = sys.platform == "win32"
_CLASS = "JarvisBackground"
_TITLE = f"JarvisBackground-{PORT}"  # per port, so a development copy does not answer for the installed one
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "Jarvis"

_WM_APP = 0x8000
_WM_TRAY = _WM_APP + 1
_WM_SUMMON = _WM_APP + 2
_WM_NOTIFY = _WM_APP + 3
_OPEN, _WAKE, _QUIT = 1001, 1002, 1003


def summon_running_copy() -> bool:
    """Ask a copy of Jarvis that is already running to show its window. True if one was found."""
    if not AVAILABLE:
        return False
    import win32gui

    try:
        hwnd = win32gui.FindWindow(_CLASS, _TITLE)
    except win32gui.error:
        return False
    if not hwnd:
        return False
    try:
        win32gui.PostMessage(hwnd, _WM_SUMMON, 0, 0)
    except win32gui.error:
        return False
    return True


def login_command() -> str:
    """The command Windows runs at sign-in: no console window, and the HUD stays hidden until summoned."""
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return f'"{windowless if windowless.exists() else exe}" -m jarvis --background'


def starts_at_login() -> bool:
    if not AVAILABLE:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _RUN_NAME)
    except OSError:
        return False
    return str(value) == login_command()


def set_start_at_login(enabled: bool) -> None:
    """Add or remove Jarvis's own entry in the current user's sign-in list. Nothing system-wide is touched."""
    if not AVAILABLE:
        return
    import winreg

    # A brand-new Windows account may not have this key yet, so it is created when missing.
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, login_command())
            return
        try:
            winreg.DeleteValue(key, _RUN_NAME)
        except FileNotFoundError:
            pass


class Tray:
    """The notification-area icon. Callbacks run on the icon's own thread and must not block for long.

    `status` returns (line shown in the tooltip and menu, whether the wake word is on).
    """

    def __init__(self, name: str, on_open: Callable[[], None], on_quit: Callable[[], None],
                 on_toggle_wake: Callable[[bool], None], status: Callable[[], tuple[str, bool]]) -> None:
        self.name = name
        self._on_open, self._on_quit, self._on_toggle_wake, self._status = on_open, on_quit, on_toggle_wake, status
        self._hwnd = 0
        self._icon = 0
        self._tip = ""
        self._pending: tuple[str, str] | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="jarvis-tray", daemon=True)
        self.active = False

    def start(self) -> bool:
        if not AVAILABLE:
            return False
        self._thread.start()
        self._ready.wait(5)
        return self.active

    def stop(self) -> None:
        if self._hwnd:
            import win32con
            import win32gui

            try:
                win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
            except win32gui.error:
                pass
            self._thread.join(timeout=3)

    def notify(self, title: str, text: str) -> None:
        """Show a Windows notification from the icon."""
        if not self._hwnd:
            return
        import win32gui

        self._pending = (title[:63], text[:255])
        try:
            win32gui.PostMessage(self._hwnd, _WM_NOTIFY, 0, 0)
        except win32gui.error:
            pass

    # -- everything below runs on the icon's thread ---------------------------------------------

    def _tooltip(self) -> str:
        line, _ = self._status()
        return f"{self.name} · {line}"[:127] if line else self.name[:127]

    def _icon_data(self, flags: int, tip: str) -> tuple:
        return (self._hwnd, 0, flags, _WM_TRAY, self._icon, tip)

    def _add_icon(self) -> None:
        import win32gui

        self._tip = self._tooltip()
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, self._icon_data(flags, self._tip))
            self.active = True
        except win32gui.error:
            # No taskbar yet (very early in sign-in). Explorer announces itself later and the icon is added then.
            log.info("the notification area is not ready yet; the icon will be added when it is")

    def _run(self) -> None:
        import win32api
        import win32con
        import win32gui

        try:
            instance = win32api.GetModuleHandle(None)
            taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")
            window_class = win32gui.WNDCLASS()
            window_class.hInstance = instance
            window_class.lpszClassName = _CLASS
            window_class.lpfnWndProc = {
                _WM_TRAY: self._on_icon, _WM_SUMMON: self._on_summon, _WM_NOTIFY: self._on_notify,
                win32con.WM_TIMER: self._on_timer, win32con.WM_DESTROY: self._on_destroy,
                taskbar_created: self._on_taskbar_created,
            }
            win32gui.RegisterClass(window_class)
            self._hwnd = win32gui.CreateWindow(_CLASS, _TITLE, 0, 0, 0, 0, 0, 0, 0, instance, None)
            try:
                self._icon = win32gui.LoadImage(
                    0, str(ROOT / "assets" / "logo.ico"), win32con.IMAGE_ICON, 0, 0,
                    win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
            except win32gui.error:
                self._icon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
            self._add_icon()
            import ctypes

            ctypes.windll.user32.SetTimer(self._hwnd, 1, 4000, None)  # keeps the tooltip's microphone line current
        except Exception:
            log.exception("the notification-area icon could not be created")
            self.active = False
            self._ready.set()
            return
        self._ready.set()
        win32gui.PumpMessages()

    def _on_taskbar_created(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        self._add_icon()  # Explorer restarted and forgot every icon
        return 0

    def _on_timer(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        import win32gui

        tip = self._tooltip()
        if tip != self._tip and self.active:
            self._tip = tip
            try:
                win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, self._icon_data(win32gui.NIF_TIP, tip))
            except win32gui.error:
                pass
        return 0

    def _on_notify(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        import win32gui

        pending, self._pending = self._pending, None
        if pending and self.active:
            title, text = pending
            try:
                win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, (
                    self._hwnd, 0, win32gui.NIF_INFO, _WM_TRAY, self._icon, self._tip, text, 10, title,
                    win32gui.NIIF_INFO))
            except win32gui.error:
                log.debug("notification failed", exc_info=True)
        return 0

    def _on_summon(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        self._call(self._on_open)
        return 0

    def _on_icon(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        import win32con

        if lparam in (win32con.WM_LBUTTONUP, win32con.WM_LBUTTONDBLCLK):
            self._call(self._on_open)
        elif lparam == win32con.WM_RBUTTONUP:
            self._menu()
        return 0

    def _menu(self) -> None:
        import win32con
        import win32gui

        line, wake_on = self._status()
        menu = win32gui.CreatePopupMenu()
        try:
            if line:
                win32gui.AppendMenu(menu, win32con.MF_STRING | win32con.MF_GRAYED, 0, line)
                win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(menu, win32con.MF_STRING, _OPEN, f"Open {self.name}")
            win32gui.AppendMenu(menu, win32con.MF_STRING | (win32con.MF_CHECKED if wake_on else 0), _WAKE,
                                f"Listen for “{self.name}”")
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(menu, win32con.MF_STRING, _QUIT, f"Quit {self.name}")
            win32gui.SetMenuDefaultItem(menu, 0 if not line else 2, 1)
            x, y = win32gui.GetCursorPos()
            try:
                win32gui.SetForegroundWindow(self._hwnd)  # otherwise the menu will not close on a click elsewhere
            except win32gui.error:
                pass
            chosen = win32gui.TrackPopupMenu(
                menu, win32con.TPM_RIGHTBUTTON | win32con.TPM_RETURNCMD | win32con.TPM_NONOTIFY, x, y, 0,
                self._hwnd, None)
            win32gui.PostMessage(self._hwnd, win32con.WM_NULL, 0, 0)
        finally:
            win32gui.DestroyMenu(menu)
        if chosen == _OPEN:
            self._call(self._on_open)
        elif chosen == _WAKE:
            self._call(lambda: self._on_toggle_wake(not wake_on))
        elif chosen == _QUIT:
            self._call(self._on_quit)

    def _on_destroy(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        import win32gui

        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self._hwnd, 0))
        except win32gui.error:
            pass
        self.active = False
        win32gui.PostQuitMessage(0)
        return 0

    @staticmethod
    def _call(action: Callable[[], None]) -> None:
        try:
            action()
        except Exception:
            log.exception("a notification-area action failed")
