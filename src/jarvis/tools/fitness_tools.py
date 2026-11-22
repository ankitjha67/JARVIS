"""Fitness companion: health reminders, activity logging, daily summaries."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

_REMINDER_TYPES = ("hydration", "posture", "eye_break", "stretch", "walk", "breathe", "custom")

_reminders: dict[str, dict[str, Any]] = {}
_activity_log: deque[dict[str, Any]] = deque(maxlen=200)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

class ReminderArgs(Args):
    name: str = Field(..., min_length=1, max_length=60,
                      description="Reminder name, e.g. 'hydration' or 'posture check'.")
    reminder_type: str = Field("custom", max_length=30,
                               description=f"One of: {', '.join(_REMINDER_TYPES)}")
    interval_minutes: int = Field(30, ge=5, le=480,
                                  description="How often to remind (minutes).")
    message: str = Field("", max_length=200, description="Custom reminder text.")
    enabled: bool = Field(True)


@tool("fitness_reminder",
      "Set a recurring health reminder (hydration, posture, eye break, stretch, etc.).",
      ReminderArgs, Tier.T1,
      summary=lambda a: f"Set reminder: {a.name}")
async def fitness_reminder(a: ReminderArgs) -> dict:
    now = _now()
    _reminders[a.name] = {
        "name": a.name,
        "type": a.reminder_type,
        "interval_minutes": a.interval_minutes,
        "message": a.message or f"Time for: {a.name}",
        "enabled": a.enabled,
        "created": now.isoformat(),
        "next_trigger": (now + timedelta(minutes=a.interval_minutes)).isoformat(),
    }
    return {"set": a.name, "interval_minutes": a.interval_minutes,
            "next_trigger": _reminders[a.name]["next_trigger"],
            "total_active": sum(1 for r in _reminders.values() if r["enabled"])}


class CheckArgs(Args):
    pass


@tool("fitness_check",
      "Check all active health reminders and see which are currently due.",
      CheckArgs, Tier.T0,
      summary=lambda a: "Check health reminders")
async def fitness_check(a: CheckArgs) -> dict:
    now = _now()
    due: list[dict] = []
    upcoming: list[dict] = []
    for r in _reminders.values():
        if not r["enabled"]:
            continue
        trigger = datetime.fromisoformat(r["next_trigger"])
        entry = {"name": r["name"], "type": r["type"], "message": r["message"],
                 "next_trigger": r["next_trigger"]}
        if trigger <= now:
            due.append(entry)
            r["next_trigger"] = (now + timedelta(minutes=r["interval_minutes"])).isoformat()
        else:
            upcoming.append(entry)
    return {"due_now": due, "upcoming": upcoming,
            "total_active": len(due) + len(upcoming)}


# ---------------------------------------------------------------------------
# Activity logging
# ---------------------------------------------------------------------------

class LogArgs(Args):
    activity: str = Field(..., min_length=1, max_length=100,
                          description="Activity type: steps, water, exercise, sleep, etc.")
    value: float = Field(..., description="Numeric value (e.g. 2000 steps, 500 ml water).")
    unit: str = Field("", max_length=20, description="Unit: steps, ml, minutes, hours, etc.")
    note: str = Field("", max_length=200)


@tool("fitness_log",
      "Log a health/fitness activity (steps, water intake, exercise, etc.).",
      LogArgs, Tier.T1,
      summary=lambda a: f"Log: {a.value} {a.unit} {a.activity}")
async def fitness_log(a: LogArgs) -> dict:
    entry = {
        "activity": a.activity,
        "value": a.value,
        "unit": a.unit or a.activity,
        "note": a.note,
        "timestamp": _now().isoformat(),
    }
    _activity_log.append(entry)
    return {"logged": entry, "total_entries_today": _count_today()}


def _count_today() -> int:
    today = _now().date()
    return sum(1 for e in _activity_log
               if datetime.fromisoformat(e["timestamp"]).date() == today)


class SummaryArgs(Args):
    pass


@tool("fitness_summary",
      "Summarise today's logged fitness/health activities with totals per type.",
      SummaryArgs, Tier.T0,
      summary=lambda a: "Today's fitness summary")
async def fitness_summary(a: SummaryArgs) -> dict:
    today = _now().date()
    today_entries = [e for e in _activity_log
                     if datetime.fromisoformat(e["timestamp"]).date() == today]
    totals: dict[str, float] = {}
    units: dict[str, str] = {}
    for e in today_entries:
        key = e["activity"]
        totals[key] = totals.get(key, 0) + e["value"]
        units[key] = e["unit"]
    summary = [{"activity": k, "total": v, "unit": units[k]} for k, v in totals.items()]
    return {"date": today.isoformat(), "activities": summary,
            "entry_count": len(today_entries),
            "active_reminders": sum(1 for r in _reminders.values() if r["enabled"])}
