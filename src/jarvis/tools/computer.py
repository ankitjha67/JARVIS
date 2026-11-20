"""Desktop control through Windows UI Automation (FR-T12).

The model sees the foreground window as a list of controls with short refs (`ui_snapshot`) and acts on a
control by ref (`ui_click`, `ui_type`). This works with any text model — no screenshots or vision needed —
and is more reliable than clicking pixels. All UIA calls run on one dedicated thread (COM apartment rule).
"""

from __future__ import annotations

import asyncio
import ctypes
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from typing import Any

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool
from .system import NoArgs

_uia: Any = None
_holder: Any = None


def _init_thread() -> None:
    global _uia, _holder
    import uiautomation

    _uia = uiautomation
    _holder = uiautomation.UIAutomationInitializerInThread()  # keeps COM initialised for the thread's lifetime
    uiautomation.SetGlobalSearchTimeout(2)


_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-uia", initializer=_init_thread)


async def _run(fn: Any, *args: Any) -> Any:
    return await asyncio.get_running_loop().run_in_executor(_pool, fn, *args)


# Controls worth showing to the model, with the short type name used in the snapshot.
_INTERACTIVE = {
    "ButtonControl": "button", "EditControl": "edit", "MenuItemControl": "menuitem", "ListItemControl": "item",
    "TabItemControl": "tab", "CheckBoxControl": "checkbox", "ComboBoxControl": "combo", "HyperlinkControl": "link",
    "TreeItemControl": "treeitem", "RadioButtonControl": "radio", "SplitButtonControl": "button",
    "DocumentControl": "document", "DataItemControl": "row", "SliderControl": "slider", "SpinnerControl": "spinner",
}
_MAX_ELEMENTS = 140
_refs: dict[str, Any] = {}


def _is_password(ctl: Any) -> bool:
    try:
        return bool(ctl.IsPassword)
    except Exception:  # noqa: BLE001 - COM property access can fail on dying controls
        return False


def _snapshot() -> dict[str, Any]:
    window = _uia.GetForegroundControl()
    if window is None:
        raise ToolError("No window is in the foreground.")
    top = window.GetTopLevelControl() or window
    _refs.clear()
    elements: list[dict[str, Any]] = []
    texts: list[str] = []
    deadline = time.monotonic() + 5.0
    visited = 0
    for ctl, _depth in _uia.WalkControl(top, includeTop=False, maxDepth=14):
        visited += 1
        if visited > 4000 or time.monotonic() > deadline or len(elements) >= _MAX_ELEMENTS:
            break
        try:
            kind = ctl.ControlTypeName
            name = (ctl.Name or "").strip()
            if kind == "TextControl":
                if name and len(texts) < 60:
                    texts.append(name[:200])
                continue
            short = _INTERACTIVE.get(kind)
            if not short or ctl.IsOffscreen:
                continue
            rect = ctl.BoundingRectangle
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            item: dict[str, Any] = {"ref": f"e{len(elements) + 1}", "type": short, "name": name[:120]}
            if not ctl.IsEnabled:
                item["disabled"] = True
            if short in ("edit", "combo", "document"):
                if _is_password(ctl):
                    item["password_field"] = True
                else:
                    try:
                        value = ctl.GetValuePattern().Value
                        if value:
                            item["value"] = value[:200]
                    except Exception:  # noqa: BLE001 - not every control exposes a value
                        pass
            if not name and "value" not in item and short not in ("edit", "document", "combo"):
                continue  # unnamed, valueless controls are noise
            _refs[item["ref"]] = ctl
            elements.append(item)
        except Exception:  # noqa: BLE001 - controls disappear mid-walk; skip them
            continue
    return {
        "window": (top.Name or "")[:160], "class": top.ClassName, "elements": elements, "visible_text": texts,
        "truncated": len(elements) >= _MAX_ELEMENTS,
    }


@tool("ui_snapshot",
      "List the controls (buttons, fields, menus, links…) of the window in the foreground, each with a ref. "
      "Call this before ui_click or ui_type, and again whenever the window changes.",
      NoArgs, Tier.T0, taints=True, summary=lambda a: "Read the foreground window's controls")
async def ui_snapshot(_: NoArgs) -> dict:
    return await _run(_snapshot)


def _resolve(ref: str) -> Any:
    ctl = _refs.get(ref)
    if ctl is None:
        raise ToolError(f"Unknown ref {ref}. Call ui_snapshot first and use a ref from its result.")
    try:
        if not ctl.Exists(0.5):
            raise ToolError("That control no longer exists. Take a new ui_snapshot.")
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError("That control no longer exists. Take a new ui_snapshot.") from exc
    return ctl


class ClickArgs(Args):
    ref: str = Field(..., pattern=r"^e\d{1,4}$", description="Ref from the latest ui_snapshot")
    double: bool = False
    right: bool = False


def _click(a: ClickArgs) -> dict:
    ctl = _resolve(a.ref)
    name = (ctl.Name or ctl.ControlTypeName)[:80]
    try:
        if not a.double and not a.right:
            try:
                ctl.GetInvokePattern().Invoke()  # semantic click: no mouse movement, works when covered
                return {"clicked": name, "method": "invoke"}
            except Exception:  # noqa: BLE001 - fall back to a real mouse click
                pass
        if a.right:
            ctl.RightClick(simulateMove=False)
        elif a.double:
            ctl.DoubleClick(simulateMove=False)
        else:
            ctl.Click(simulateMove=False)
    except Exception as exc:
        raise ToolError(f"Could not click “{name}” ({type(exc).__name__}).") from exc
    return {"clicked": name, "method": "mouse"}


@tool("ui_click", "Click a control from the latest ui_snapshot.", ClickArgs, Tier.T2,
      summary=lambda a: f"{'Right-click' if a.right else 'Double-click' if a.double else 'Click'} control {a.ref}")
async def ui_click(a: ClickArgs) -> dict:
    return await _run(_click, a)


# -- keyboard ---------------------------------------------------------------------------------------

INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 1, 0x0002, 0x0004


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MOUSEINPUT(ctypes.Structure):  # sizes the union correctly
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _send(vk: int = 0, scan: int = 0, flags: int = 0) -> None:
    inp = _INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=_KEYBDINPUT(vk, scan, flags, 0, None)))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _type_unicode(text: str) -> None:
    for ch in text:
        if ch == "\n":
            _send(vk=0x0D)
            _send(vk=0x0D, flags=KEYEVENTF_KEYUP)
        else:
            units = ch.encode("utf-16-le")  # characters outside the BMP are sent as a surrogate pair
            for i in range(0, len(units), 2):
                code = int.from_bytes(units[i:i + 2], "little")
                _send(scan=code, flags=KEYEVENTF_UNICODE)
                _send(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
        time.sleep(0.004)


class TypeArgs(Args):
    text: str = Field(..., min_length=1, max_length=5000)
    ref: str | None = Field(None, pattern=r"^e\d{1,4}$", description="Field to type into; omit to type at the cursor")
    clear: bool = Field(False, description="Replace the field's current contents")
    enter: bool = Field(False, description="Press Enter afterwards")


def _type(a: TypeArgs) -> dict:
    target = _resolve(a.ref) if a.ref else _uia.GetFocusedControl()
    if target is not None and _is_password(target):
        # SEC-11: Jarvis never enters credentials.
        raise ToolError("That is a password field. Jarvis does not type passwords — enter it yourself.")
    if a.ref:
        try:
            target.SetFocus()
        except Exception:  # noqa: BLE001 - some controls only take focus through a click
            target.Click(simulateMove=False)
        time.sleep(0.1)
    if a.clear:
        for vk in (0x11, 0x41):  # Ctrl+A
            _send(vk=vk)
        for vk in (0x41, 0x11):
            _send(vk=vk, flags=KEYEVENTF_KEYUP)
        _send(vk=0x2E)
        _send(vk=0x2E, flags=KEYEVENTF_KEYUP)
    _type_unicode(a.text)
    if a.enter:
        _send(vk=0x0D)
        _send(vk=0x0D, flags=KEYEVENTF_KEYUP)
    return {"typed_chars": len(a.text), "into": (target.Name or target.ControlTypeName)[:80] if target else "focus"}


def _type_summary(a: TypeArgs) -> str:
    preview = " ".join(a.text.split())
    return f"Type “{preview[:60]}{'…' if len(preview) > 60 else ''}”" + (f" into {a.ref}" if a.ref else "")


@tool("ui_type", "Type text into a control from ui_snapshot, or at the current cursor. Never use for passwords.",
      TypeArgs, Tier.T2, summary=_type_summary)
async def ui_type(a: TypeArgs) -> dict:
    return await _run(_type, a)


_KEYS = {
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "enter": 0x0D, "return": 0x0D,
    "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25,
    "right": 0x27, "insert": 0x2D, "printscreen": 0x2C,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}


class HotkeyArgs(Args):
    keys: str = Field(..., min_length=1, max_length=40, description="Combination such as 'ctrl+s', 'alt+f4', 'win+d', 'enter'")


def _parse_keys(keys: str) -> list[int]:
    codes = []
    for part in keys.lower().replace(" ", "").split("+"):
        if part in _KEYS:
            codes.append(_KEYS[part])
        elif len(part) == 1 and part.isalnum():
            codes.append(ord(part.upper()))
        else:
            raise ToolError(f"Unknown key “{part}”. Use names like ctrl, alt, shift, win, enter, tab, f5 or a letter.")
    return codes


def _classify_hotkey(a: HotkeyArgs) -> tuple[Tier, str]:
    codes = _parse_keys(a.keys)
    if 0x5B in codes and ord("L") in codes:
        return Tier.T1, ""  # lock: harmless
    if set(codes) >= {0x11, 0x12, 0x2E}:
        raise ToolError("Ctrl+Alt+Delete cannot be sent by software.")
    return Tier.T2, "Sends a keyboard shortcut to the active window."


def _hotkey(a: HotkeyArgs) -> dict:
    codes = _parse_keys(a.keys)
    for vk in codes:
        _send(vk=vk)
    for vk in reversed(codes):
        _send(vk=vk, flags=KEYEVENTF_KEYUP)
    return {"sent": a.keys}


@tool("ui_hotkey", "Press a key or keyboard shortcut in the active window.", HotkeyArgs, Tier.T2,
      summary=lambda a: f"Press {a.keys}", classify=_classify_hotkey)
async def ui_hotkey(a: HotkeyArgs) -> dict:
    return await _run(_hotkey, a)
