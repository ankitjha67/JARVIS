"""Apps, windows, browser, clipboard, lock (FR-T2, T3, T8, T9, T10)."""

from __future__ import annotations

import asyncio
import ctypes
import difflib
import json
import subprocess
import time
import webbrowser

import psutil
import win32clipboard
import win32con
import win32gui
import win32process
from pydantic import Field

from ..policy import Tier, normalise_web_url
from .registry import Args, ToolError, tool
from .system import NoArgs

# ---------------------------------------------------------------------------------------------
# Installed apps (Start menu, including Store apps)
# ---------------------------------------------------------------------------------------------

_apps_cache: tuple[float, list[dict[str, str]]] | None = None
_APPS_TTL = 600.0
_NO_WINDOW = subprocess.CREATE_NO_WINDOW


def _load_apps() -> list[dict[str, str]]:
    global _apps_cache
    if _apps_cache and time.monotonic() - _apps_cache[0] < _APPS_TTL:
        return _apps_cache[1]
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20, check=False, creationflags=_NO_WINDOW, encoding="utf-8", errors="replace",
        )
        data = json.loads(out.stdout or "[]")
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
        raise ToolError("Could not read the list of installed apps.") from exc
    if isinstance(data, dict):
        data = [data]
    apps = [{"name": str(d["Name"]), "app_id": str(d["AppID"])} for d in data if d.get("Name") and d.get("AppID")]
    _apps_cache = (time.monotonic(), apps)
    return apps


def _match_apps(query: str, limit: int = 8) -> list[dict[str, str]]:
    apps = _load_apps()
    q = query.lower().strip()
    scored: list[tuple[float, dict[str, str]]] = []
    for app in apps:
        name = app["name"].lower()
        if name == q:
            score = 3.0
        elif name.startswith(q) or any(w.startswith(q) for w in name.split()):
            score = 2.0 + len(q) / max(len(name), 1)
        elif q in name:
            score = 1.5 + len(q) / max(len(name), 1)
        else:
            score = difflib.SequenceMatcher(None, q, name).ratio()
            if score < 0.6:
                continue
        scored.append((score, app))
    scored.sort(key=lambda s: (-s[0], len(s[1]["name"])))
    return [a for _, a in scored[:limit]]


class AppQuery(Args):
    name: str = Field(..., min_length=1, max_length=80, description="App name, e.g. 'chrome', 'notepad', 'spotify'")


@tool("apps_search", "Find installed applications by name.", AppQuery, Tier.T0,
      summary=lambda a: f"Search installed apps for “{a.name}”")
async def apps_search(a: AppQuery) -> list[str]:
    return [m["name"] for m in await asyncio.to_thread(_match_apps, a.name)]


def _open_app(a: AppQuery) -> dict:
    matches = _match_apps(a.name, 5)
    if not matches:
        raise ToolError(f"No installed app matches “{a.name}”.")
    best = matches[0]
    # app_id comes from our own index, never from the model; passed as a single argv item (SEC-7).
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{best['app_id']}"], creationflags=_NO_WINDOW)
    return {"opened": best["name"], "other_matches": [m["name"] for m in matches[1:]]}


@tool("apps_open", "Launch an installed application by name.", AppQuery, Tier.T1,
      summary=lambda a: f"Open {a.name}")
async def apps_open(a: AppQuery) -> dict:
    return await asyncio.to_thread(_open_app, a)


# ---------------------------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------------------------

def _windows() -> list[dict]:
    rows: list[dict] = []
    fg = win32gui.GetForegroundWindow()

    def visit(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title or win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid).name()
        except (psutil.Error, OSError):
            proc = ""
        if proc.lower() in ("textinputhost.exe", "applicationframehost.exe") and title in ("Windows Input Experience",):
            return True
        rows.append({"id": hwnd, "title": title, "process": proc, "minimized": bool(win32gui.IsIconic(hwnd)),
                     "focused": hwnd == fg})
        return True

    win32gui.EnumWindows(visit, None)
    return rows


@tool("windows_list", "List open top-level windows with their ids, titles and owning processes.", NoArgs, Tier.T0,
      summary=lambda a: "List open windows")
async def windows_list(_: NoArgs) -> list[dict]:
    return await asyncio.to_thread(_windows)


class WindowArgs(Args):
    id: int | None = Field(None, description="Window id from windows_list")
    title_contains: str | None = Field(None, max_length=120, description="Or: text contained in the window title or process name")


def _resolve_window(a: WindowArgs) -> dict:
    wins = _windows()
    if a.id is not None:
        hit = [w for w in wins if w["id"] == a.id]
    elif a.title_contains:
        q = a.title_contains.lower()
        hit = [w for w in wins if q in w["title"].lower() or q in w["process"].lower()]
    else:
        raise ToolError("Give either id or title_contains.")
    if not hit:
        raise ToolError("No matching window is open.")
    return hit[0]


def _window_label(a: WindowArgs) -> str:
    return f"“{a.title_contains}”" if a.title_contains else f"#{a.id}"


def _focus(a: WindowArgs) -> dict:
    w = _resolve_window(a)
    hwnd = w["id"]
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    # Windows only lets the foreground process hand over focus; a synthetic Alt tap satisfies that rule.
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except win32gui.error as exc:
        raise ToolError("Windows refused to bring that window forward.") from exc
    return {"focused": w["title"]}


@tool("window_focus", "Bring a window to the front.", WindowArgs, Tier.T1,
      summary=lambda a: f"Focus window {_window_label(a)}")
async def window_focus(a: WindowArgs) -> dict:
    return await asyncio.to_thread(_focus, a)


class WindowStateArgs(WindowArgs):
    state: str = Field(..., pattern="^(minimize|maximize|restore)$")


def _set_state(a: WindowStateArgs) -> dict:
    w = _resolve_window(a)
    cmd = {"minimize": win32con.SW_MINIMIZE, "maximize": win32con.SW_MAXIMIZE, "restore": win32con.SW_RESTORE}[a.state]
    win32gui.ShowWindow(w["id"], cmd)
    return {"window": w["title"], "state": a.state}


@tool("window_set_state", "Minimize, maximize or restore a window.", WindowStateArgs, Tier.T1,
      summary=lambda a: f"{a.state.capitalize()} window {_window_label(a)}")
async def window_set_state(a: WindowStateArgs) -> dict:
    return await asyncio.to_thread(_set_state, a)


def _close(a: WindowArgs) -> dict:
    w = _resolve_window(a)
    win32gui.PostMessage(w["id"], win32con.WM_CLOSE, 0, 0)
    return {"close_requested": w["title"]}


@tool("window_close", "Ask a window to close. The app may prompt to save; unsaved work can be lost.", WindowArgs,
      Tier.T2, summary=lambda a: f"Close window {_window_label(a)}")
async def window_close(a: WindowArgs) -> dict:
    return await asyncio.to_thread(_close, a)


# ---------------------------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------------------------

class UrlArgs(Args):
    url: str = Field(..., min_length=4, max_length=2000, description="http(s) URL. A bare domain is treated as https.")


def _normalise_url(raw: str) -> str:
    url = normalise_web_url(raw)
    if url is None:
        raise ToolError("Only plain http and https URLs can be opened.")
    return url


def _classify_url(a: UrlArgs) -> tuple[Tier, str]:
    _normalise_url(a.url)
    return Tier.T1, ""


@tool("web_open", "Open a web page in the default browser.", UrlArgs, Tier.T1,
      summary=lambda a: f"Open {a.url}", classify=_classify_url)
async def web_open(a: UrlArgs) -> dict:
    url = _normalise_url(a.url)
    if not await asyncio.to_thread(webbrowser.open, url):
        raise ToolError("No browser could be opened.")
    return {"opened": url}


# ---------------------------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------------------------

_CLIP_MAX = 20_000


def _clip_open() -> None:
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            return
        except win32clipboard.error:
            time.sleep(0.05)
    raise ToolError("The clipboard is busy. Try again.")


def _clip_read() -> dict:
    _clip_open()
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return {"text": "", "note": "The clipboard does not contain text."}
        text = str(win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT))
    finally:
        win32clipboard.CloseClipboard()
    return {"text": text[:_CLIP_MAX], "truncated": len(text) > _CLIP_MAX}


@tool("clipboard_read", "Read the text currently on the clipboard.", NoArgs, Tier.T0, taints=True,
      summary=lambda a: "Read clipboard")
async def clipboard_read(_: NoArgs) -> dict:
    return await asyncio.to_thread(_clip_read)


class ClipWriteArgs(Args):
    text: str = Field(..., max_length=_CLIP_MAX)


def _clip_write(a: ClipWriteArgs) -> dict:
    _clip_open()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, a.text)
    finally:
        win32clipboard.CloseClipboard()
    return {"copied_chars": len(a.text)}


@tool("clipboard_write", "Replace the clipboard contents with text.", ClipWriteArgs, Tier.T1,
      summary=lambda a: f"Copy {len(a.text)} characters to clipboard")
async def clipboard_write(a: ClipWriteArgs) -> dict:
    return await asyncio.to_thread(_clip_write, a)


# ---------------------------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------------------------

@tool("power_lock", "Lock the computer (shows the Windows lock screen).", NoArgs, Tier.T1,
      summary=lambda a: "Lock the computer")
async def power_lock(_: NoArgs) -> dict:
    if not ctypes.windll.user32.LockWorkStation():
        raise ToolError("Windows refused to lock the workstation.")
    return {"locked": True}
