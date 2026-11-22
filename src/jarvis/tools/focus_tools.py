"""Focus Forge: deep work sessions with distraction tracking."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

log = logging.getLogger(__name__)

_session: dict[str, Any] | None = None
_history: list[dict[str, Any]] = []
_MAX_HISTORY = 20


# ---------------------------------------------------------------------------
# focus_start
# ---------------------------------------------------------------------------

class FocusStartArgs(Args):
    duration_minutes: int = Field(25, ge=1, le=480, description="Session length in minutes (default 25, Pomodoro).")
    label: str = Field("Deep Work", max_length=100, description="Label for this focus session.")
    allowed_apps: list[str] = Field(
        default_factory=list, max_length=20,
        description="Process names that are allowed during the session (others count as distractions).",
    )
    blocked_sites: list[str] = Field(
        default_factory=list, max_length=50,
        description="Domain names to consider distracting (tracked, not system-blocked).",
    )


@tool(
    "focus_start",
    "Start a timed deep-work session. Tracks distractions via the ambient context radar. "
    "Does NOT modify the system hosts file.",
    FocusStartArgs, Tier.T1, sensitive=True,
    summary=lambda a: f"Focus: {a.duration_minutes}min — {a.label}",
)
async def focus_start(a: FocusStartArgs) -> dict:
    global _session
    if _session and _session.get("active"):
        remaining = _session["end_time"] - time.time()
        if remaining > 0:
            raise ToolError(f"Focus session already active ({remaining / 60:.0f} min left). End it first.")

    _session = {
        "active": True,
        "label": a.label,
        "start_time": time.time(),
        "end_time": time.time() + a.duration_minutes * 60,
        "duration_minutes": a.duration_minutes,
        "allowed_apps": [app.lower() for app in a.allowed_apps],
        "blocked_sites": [s.lower() for s in a.blocked_sites],
        "distractions": [],
    }
    return {
        "started": True,
        "label": a.label,
        "duration_minutes": a.duration_minutes,
        "ends_at_epoch": _session["end_time"],
        "tip": "Run ambient_watch to auto-track window switches during focus.",
    }


# ---------------------------------------------------------------------------
# focus_status
# ---------------------------------------------------------------------------

class FocusStatusArgs(Args):
    pass


@tool(
    "focus_status",
    "Check the current focus session: active, time remaining, distractions detected.",
    FocusStatusArgs, Tier.T0,
    summary=lambda a: "Focus status",
)
async def focus_status(a: FocusStatusArgs) -> dict:
    if not _session or not _session.get("active"):
        return {"active": False, "message": "No focus session running."}

    now = time.time()
    remaining = max(0, _session["end_time"] - now)
    elapsed = now - _session["start_time"]

    if remaining <= 0:
        _finish_session("completed")
        return {"active": False, "completed": True, "label": _session["label"]}

    # Check ambient history for distractions
    distraction_count = len(_session.get("distractions", []))

    return {
        "active": True,
        "label": _session["label"],
        "elapsed_minutes": round(elapsed / 60, 1),
        "remaining_minutes": round(remaining / 60, 1),
        "distraction_count": distraction_count,
    }


# ---------------------------------------------------------------------------
# focus_end
# ---------------------------------------------------------------------------

class FocusEndArgs(Args):
    pass


def _finish_session(reason: str) -> dict[str, Any]:
    global _session
    if not _session:
        return {}
    elapsed = time.time() - _session["start_time"]
    summary = {
        "label": _session["label"],
        "planned_minutes": _session["duration_minutes"],
        "actual_minutes": round(elapsed / 60, 1),
        "reason": reason,
        "distraction_count": len(_session.get("distractions", [])),
        "distractions": _session.get("distractions", [])[:10],
        "started_at": _session["start_time"],
        "ended_at": time.time(),
    }
    _history.append(summary)
    if len(_history) > _MAX_HISTORY:
        _history.pop(0)
    _session["active"] = False
    return summary


@tool(
    "focus_end",
    "End the current focus session early and return a summary.",
    FocusEndArgs, Tier.T1,
    summary=lambda a: "End focus session",
)
async def focus_end(a: FocusEndArgs) -> dict:
    if not _session or not _session.get("active"):
        raise ToolError("No active focus session to end.")
    summary = _finish_session("ended_early")
    return summary


# ---------------------------------------------------------------------------
# focus_stats
# ---------------------------------------------------------------------------

class FocusStatsArgs(Args):
    limit: int = Field(10, ge=1, le=20, description="Number of past sessions to return.")


@tool(
    "focus_stats",
    "Return history of recent focus sessions with duration and distraction counts.",
    FocusStatsArgs, Tier.T0,
    summary=lambda a: "Focus history",
)
async def focus_stats(a: FocusStatsArgs) -> dict:
    sessions = _history[-a.limit:]
    total_minutes = sum(s.get("actual_minutes", 0) for s in sessions)
    return {
        "sessions": sessions,
        "count": len(sessions),
        "total_focus_minutes": round(total_minutes, 1),
        "currently_active": bool(_session and _session.get("active")),
    }
