"""Ambient Context Radar: monitor active windows and track focus history."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pydantic import Field

from .. import host
from ..policy import Tier
from .registry import Args, tool

log = logging.getLogger(__name__)

# Module-level state for tracking
_watch_active: bool = False
_focus_history: list[dict[str, Any]] = []
_last_snapshot: list[dict[str, Any]] = []
_watch_task: asyncio.Task[None] | None = None
_MAX_HISTORY = 200


def _enum_windows() -> list[dict[str, Any]]:
    """Enumerate visible windows with title, process, and position."""
    if host.IS_MAC:
        # The window server's own list: cheap enough to poll and needs no Accessibility permission.
        return [{"hwnd": n, "title": w["title"] or w["process"], "process": w["process"], "pid": w["pid"],
                 "rect": {"left": w["rect"][0], "top": w["rect"][1], "right": w["rect"][0] + w["rect"][2],
                          "bottom": w["rect"][1] + w["rect"][3]}}
                for n, w in enumerate(host.mac.visible_windows(), start=1)]
    if not host.IS_WINDOWS:
        raise host.unsupported("Window monitoring")
    import psutil
    import win32gui
    import win32process

    results: list[dict[str, Any]] = []

    def _visitor(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        try:
            rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            rect = (0, 0, 0, 0)
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            proc_name = proc.name()
        except Exception:
            pid = 0
            proc_name = ""
        results.append({
            "hwnd": hwnd,
            "title": title,
            "process": proc_name,
            "pid": pid,
            "rect": {"left": rect[0], "top": rect[1], "right": rect[2], "bottom": rect[3]},
        })
        return True

    try:
        win32gui.EnumWindows(_visitor, None)
    except Exception:
        pass
    return results


def _get_foreground() -> dict[str, Any] | None:
    """Get the currently focused window."""
    fg = host.foreground_app()
    return {**fg, "time": time.time()} if fg else None


# ---------------------------------------------------------------------------
# ambient_scan
# ---------------------------------------------------------------------------

class AmbientScanArgs(Args):
    pass


@tool(
    "ambient_scan",
    "List all visible windows with their title, process name, PID and screen position.",
    AmbientScanArgs, Tier.T0,
    summary=lambda a: "Scan active windows",
)
async def ambient_scan(a: AmbientScanArgs) -> dict:
    windows = await asyncio.to_thread(_enum_windows)
    return {"windows": windows, "count": len(windows)}


# ---------------------------------------------------------------------------
# ambient_watch
# ---------------------------------------------------------------------------

class AmbientWatchArgs(Args):
    action: str = Field(
        "start", pattern="^(start|stop)$",
        description="'start' to begin monitoring, 'stop' to end.",
    )
    interval_seconds: int = Field(5, ge=1, le=60, description="How often to sample the foreground window.")


async def _watch_loop(interval: int) -> None:
    global _focus_history, _last_snapshot
    last_title = ""
    while _watch_active:
        try:
            fg = await asyncio.to_thread(_get_foreground)
            if fg and fg.get("title") != last_title:
                last_title = fg["title"]
                _focus_history.append(fg)
                if len(_focus_history) > _MAX_HISTORY:
                    _focus_history = _focus_history[-_MAX_HISTORY:]
            snapshot = await asyncio.to_thread(_enum_windows)
            _last_snapshot = snapshot
        except Exception:
            pass
        await asyncio.sleep(interval)


@tool(
    "ambient_watch",
    "Start or stop background monitoring of window focus changes.",
    AmbientWatchArgs, Tier.T1,
    summary=lambda a: f"{'Start' if a.action == 'start' else 'Stop'} ambient watch",
)
async def ambient_watch(a: AmbientWatchArgs) -> dict:
    global _watch_active, _watch_task

    if a.action == "start":
        if _watch_active:
            return {"already_running": True}
        _watch_active = True
        loop = asyncio.get_running_loop()
        _watch_task = loop.create_task(_watch_loop(a.interval_seconds))
        return {"started": True, "interval": a.interval_seconds}
    else:
        if not _watch_active:
            return {"already_stopped": True}
        _watch_active = False
        if _watch_task and not _watch_task.done():
            _watch_task.cancel()
            try:
                await _watch_task
            except asyncio.CancelledError:
                pass
        _watch_task = None
        return {"stopped": True, "history_entries": len(_focus_history)}


# ---------------------------------------------------------------------------
# ambient_focus_history
# ---------------------------------------------------------------------------

class AmbientFocusHistoryArgs(Args):
    limit: int = Field(20, ge=1, le=200, description="Max entries to return.")


@tool(
    "ambient_focus_history",
    "Return recent window focus changes recorded by ambient_watch.",
    AmbientFocusHistoryArgs, Tier.T0,
    summary=lambda a: "Focus history",
)
async def ambient_focus_history(a: AmbientFocusHistoryArgs) -> dict:
    history = _focus_history[-a.limit:]
    return {
        "history": history,
        "count": len(history),
        "watching": _watch_active,
    }
