"""Global hotkeys (FR-U8): summon and kill switch.

Windows: Win32 RegisterHotKey on a dedicated message-loop thread.
macOS: Carbon RegisterEventHotKey, which needs no Accessibility permission. Its events arrive on the
application's main run loop, so the hotkeys work while the HUD window is open (not with --no-window).
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
VK_J, VK_BACK = 0x4A, 0x08


class _WindowsHotkeys(threading.Thread):
    def __init__(self, on_summon: Callable[[], None], on_kill: Callable[[], None]) -> None:
        super().__init__(name="jarvis-hotkeys", daemon=True)
        self._handlers = {1: on_summon, 2: on_kill}
        self._thread_id: int | None = None
        self.registered: dict[str, bool] = {}

    def run(self) -> None:
        from ctypes import wintypes

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


# -- macOS (Carbon) ---------------------------------------------------------------------------------

_CARBON = "/System/Library/Frameworks/Carbon.framework/Carbon"
_CONTROL_KEY, _OPTION_KEY = 0x1000, 0x0800
_KVK_J, _KVK_DELETE = 0x26, 0x33
_EVENT_CLASS_KEYBOARD = 0x6B657962  # 'keyb'
_EVENT_HOTKEY_PRESSED = 5
_PARAM_DIRECT_OBJECT = 0x2D2D2D2D  # '----'
_TYPE_HOTKEY_ID = 0x686B6964  # 'hkid'
_SIGNATURE = 0x4A525653  # 'JRVS'


class _HotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


class _MacHotkeys:
    """Same surface as the Windows thread: start(), stop(), registered. start() must run on the main thread."""

    def __init__(self, on_summon: Callable[[], None], on_kill: Callable[[], None]) -> None:
        self._handlers = {1: on_summon, 2: on_kill}
        self.registered: dict[str, bool] = {"summon": False, "kill": False}
        self._carbon: Any = None
        self._callback: Any = None  # keeps the C callback alive
        self._refs: list[ctypes.c_void_p] = []
        self._handler_ref = ctypes.c_void_p()

    def start(self) -> None:
        try:
            self._install()
        except Exception:
            log.exception("global hotkeys could not be registered")
        for name, ok in self.registered.items():
            if not ok:
                log.warning("hotkey %s could not be registered (already taken by another application?)", name)

    def _install(self) -> None:
        carbon = ctypes.CDLL(_CARBON)
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, _HANDLER, ctypes.c_ulong,
                                               ctypes.POINTER(_EventTypeSpec), ctypes.c_void_p,
                                               ctypes.POINTER(ctypes.c_void_p)]
        carbon.InstallEventHandler.restype = ctypes.c_int32
        carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _HotKeyID, ctypes.c_void_p,
                                               ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        carbon.RegisterEventHotKey.restype = ctypes.c_int32
        carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]
        carbon.GetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                             ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
        carbon.GetEventParameter.restype = ctypes.c_int32
        self._carbon = carbon
        self._callback = _HANDLER(self._on_event)
        target = carbon.GetApplicationEventTarget()
        spec = _EventTypeSpec(_EVENT_CLASS_KEYBOARD, _EVENT_HOTKEY_PRESSED)
        status = carbon.InstallEventHandler(target, self._callback, 1, ctypes.byref(spec), None,
                                            ctypes.byref(self._handler_ref))
        if status != 0:
            raise OSError(f"InstallEventHandler failed ({status})")
        mods = _CONTROL_KEY | _OPTION_KEY
        for name, hotkey_id, key in (("summon", 1, _KVK_J), ("kill", 2, _KVK_DELETE)):
            ref = ctypes.c_void_p()
            ok = carbon.RegisterEventHotKey(key, mods, _HotKeyID(_SIGNATURE, hotkey_id), target, 0,
                                            ctypes.byref(ref)) == 0
            self.registered[name] = ok
            if ok:
                self._refs.append(ref)

    def _on_event(self, _call: Any, event: Any, _data: Any) -> int:
        try:
            hotkey = _HotKeyID()
            status = self._carbon.GetEventParameter(event, _PARAM_DIRECT_OBJECT, _TYPE_HOTKEY_ID, None,
                                                    ctypes.sizeof(hotkey), None, ctypes.byref(hotkey))
            handler = self._handlers.get(int(hotkey.id)) if status == 0 and hotkey.signature == _SIGNATURE else None
            if handler:
                # Off the main thread: the handlers talk to the window, which must not wait on its own loop.
                threading.Thread(target=self._guarded, args=(handler,), name="jarvis-hotkey", daemon=True).start()
        except Exception:
            log.exception("hotkey event failed")
        return 0

    @staticmethod
    def _guarded(handler: Callable[[], None]) -> None:
        try:
            handler()
        except Exception:
            log.exception("hotkey handler failed")

    def stop(self) -> None:
        if not self._carbon:
            return
        for ref in self._refs:
            self._carbon.UnregisterEventHotKey(ref)
        self._refs.clear()
        if self._handler_ref:
            self._carbon.RemoveEventHandler(self._handler_ref)
            self._handler_ref = ctypes.c_void_p()


class _NoHotkeys:
    def __init__(self, *_: Any) -> None:
        self.registered: dict[str, bool] = {"summon": False, "kill": False}

    def start(self) -> None:
        log.info("global hotkeys are not available on this platform")

    def stop(self) -> None:
        pass


HotkeyThread: Any = (_WindowsHotkeys if sys.platform == "win32" else _MacHotkeys if sys.platform == "darwin"
                     else _NoHotkeys)
