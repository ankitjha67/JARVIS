"""Timers, stopwatch, and alarms (ideas #9-10)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

log = logging.getLogger(__name__)

_timers: dict[str, dict[str, Any]] = {}
_next_id = 1


def _gen_id() -> str:
    global _next_id
    tid = f"timer_{_next_id}"
    _next_id += 1
    return tid


def _parse_duration(text: str) -> int:
    """Parse '5m', '1h30m', '90s', '2 minutes', etc. into seconds."""
    import re
    text = text.strip().lower()
    total = 0
    for val, unit in re.findall(r"(\d+)\s*(h(?:ours?)?|m(?:in(?:utes?)?)?|s(?:ec(?:onds?)?)?)?", text):
        v = int(val)
        u = unit[:1] if unit else "s"
        if u == "h":
            total += v * 3600
        elif u == "m":
            total += v * 60
        else:
            total += v
    if total == 0:
        try:
            total = int(text)
        except ValueError:
            raise ToolError(f"Could not parse duration: {text}")
    return total


class TimerSetArgs(Args):
    duration: str = Field(..., min_length=1, max_length=50, description="Duration like '5m', '1h30m', '90s'")
    label: str = Field("Timer", max_length=100, description="Optional label for the timer")


@tool("timer_set", "Set a countdown timer. Plays a chime when done.",
      TimerSetArgs, Tier.T0, summary=lambda a: f"Timer: {a.duration} ({a.label})")
async def timer_set(a: TimerSetArgs) -> dict:
    secs = _parse_duration(a.duration)
    tid = _gen_id()
    _timers[tid] = {"label": a.label, "duration_s": secs, "started": time.time(), "done": False}

    async def _countdown() -> None:
        await asyncio.sleep(secs)
        _timers[tid]["done"] = True
        try:
            from . import context
            hub = context._hub  # type: ignore[attr-defined]
            await hub.broadcast({"type": "notice", "level": "info",
                                 "message": f"Timer '{a.label}' is done! ({a.duration})"})
            import winsound
            await asyncio.to_thread(winsound.Beep, 1000, 500)
        except Exception:
            log.debug("timer notification failed", exc_info=True)

    asyncio.create_task(_countdown())
    return {"timer_id": tid, "label": a.label, "duration_s": secs,
            "message": f"Timer set for {a.duration}. I'll notify you when it's done."}


class TimerListArgs(Args):
    pass


@tool("timer_list", "List all active timers and their remaining time.",
      TimerListArgs, Tier.T0, summary=lambda a: "List timers")
async def timer_list(a: TimerListArgs) -> dict:
    now = time.time()
    active = []
    for tid, t in _timers.items():
        elapsed = now - t["started"]
        remaining = max(0, t["duration_s"] - elapsed)
        active.append({
            "id": tid, "label": t["label"], "duration_s": t["duration_s"],
            "remaining_s": round(remaining), "done": t["done"],
        })
    return {"timers": active, "count": len(active)}


class TimerCancelArgs(Args):
    timer_id: str = Field(..., min_length=1, max_length=20, description="Timer ID to cancel")


@tool("timer_cancel", "Cancel an active timer.",
      TimerCancelArgs, Tier.T0, summary=lambda a: f"Cancel timer {a.timer_id}")
async def timer_cancel(a: TimerCancelArgs) -> dict:
    t = _timers.pop(a.timer_id, None)
    if t is None:
        raise ToolError(f"No timer with id {a.timer_id}")
    return {"cancelled": a.timer_id, "label": t["label"]}


class StopwatchArgs(Args):
    action: str = Field(..., pattern="^(start|stop|lap|reset)$", description="start, stop, lap, or reset")


_stopwatch: dict[str, Any] = {"running": False, "start_time": 0.0, "elapsed": 0.0, "laps": []}


@tool("stopwatch", "Start, stop, lap, or reset a stopwatch.",
      StopwatchArgs, Tier.T0, summary=lambda a: f"Stopwatch: {a.action}")
async def stopwatch(a: StopwatchArgs) -> dict:
    now = time.time()
    if a.action == "start":
        if _stopwatch["running"]:
            raise ToolError("Stopwatch already running.")
        _stopwatch["running"] = True
        _stopwatch["start_time"] = now
        return {"status": "running", "elapsed_s": _stopwatch["elapsed"]}
    elif a.action == "stop":
        if _stopwatch["running"]:
            _stopwatch["elapsed"] += now - _stopwatch["start_time"]
            _stopwatch["running"] = False
        return {"status": "stopped", "elapsed_s": round(_stopwatch["elapsed"], 2)}
    elif a.action == "lap":
        if not _stopwatch["running"]:
            raise ToolError("Stopwatch is not running.")
        elapsed = _stopwatch["elapsed"] + (now - _stopwatch["start_time"])
        _stopwatch["laps"].append(round(elapsed, 2))
        return {"lap": len(_stopwatch["laps"]), "elapsed_s": round(elapsed, 2), "laps": _stopwatch["laps"]}
    else:
        _stopwatch.update({"running": False, "start_time": 0.0, "elapsed": 0.0, "laps": []})
        return {"status": "reset"}


# --- Reminders (idea #9 extension) ---

from datetime import UTC, datetime, timedelta


class ReminderSetArgs(Args):
    message: str = Field(..., min_length=1, max_length=500, description="What to remind about")
    minutes: int = Field(0, ge=0, description="Minutes from now (use this OR time)")
    time: str = Field("", description="HH:MM for today, or ISO datetime e.g. '2026-11-22T09:00:00Z'")


@tool("reminder_set", "Set a reminder. Specify minutes from now or an absolute time.",
      ReminderSetArgs, Tier.T1, summary=lambda a: f"Remind: {a.message[:40]}")
async def reminder_set(a: ReminderSetArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    if a.minutes > 0:
        remind_at = (datetime.now(UTC) + timedelta(minutes=a.minutes)).isoformat(
            timespec="seconds").replace("+00:00", "Z")
    elif a.time:
        if len(a.time) <= 5 and ":" in a.time:
            today = datetime.now(UTC).date()
            remind_at = f"{today}T{a.time}:00Z"
        else:
            remind_at = a.time if a.time.endswith("Z") else a.time + "Z"
    else:
        raise ToolError("Specify either 'minutes' or 'time'")
    return hub.timers.set_reminder(a.message, remind_at)


class ReminderListArgs(Args):
    pass


@tool("reminder_list", "List upcoming reminders.", ReminderListArgs, Tier.T0,
      summary=lambda a: "List reminders")
async def reminder_list(a: ReminderListArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    reminders = hub.timers.list_reminders()
    return {"reminders": reminders, "count": len(reminders)}


class ReminderCancelArgs(Args):
    id: str = Field(..., description="Reminder ID to cancel")


@tool("reminder_cancel", "Cancel a pending reminder.", ReminderCancelArgs, Tier.T1,
      summary=lambda a: f"Cancel reminder {a.id}")
async def reminder_cancel(a: ReminderCancelArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    if hub.timers.cancel_reminder(a.id):
        return {"cancelled": True}
    raise ToolError(f"Reminder {a.id} not found or already fired")
