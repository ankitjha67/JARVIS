"""Global hotkeys via Win32 RegisterHotKey on a dedicated message-loop thread (FR-U8)."""

from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable
from ctypes import wintypes

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
VK_J, VK_BACK = 0x4A, 0x08


class HotkeyThread(threading.Thread):
    def __init__(self, on_summon: Callable[[], None], on_kill: Callable[[], None]) -> None:
        super().__init__(name="jarvis-hotkeys", daemon=True)
        self._handlers = {1: on_summon, 2: on_kill}
        self._thread_id: int | None = None
        self.registered: dict[str, bool] = {}

    def run(self) -> None:
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        mods = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
        self.registered = {
            "summon": bool(user32.RegisterHotKey(None, 1, mods, VK_J)),
            "kill": bool(user32.RegisterHotKey(None, 2, mods, VK_BACK)),
        }
        for name, ok in self.registered.items():
            if not ok:
                log.warning("hotkey %s is already taken by another application", name)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                handler = self._handlers.get(int(msg.wParam))
                if handler:
                    try:
                        handler()
                    except Exception:
                        log.exception("hotkey handler failed")
        user32.UnregisterHotKey(None, 1)
        user32.UnregisterHotKey(None, 2)

    def stop(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
