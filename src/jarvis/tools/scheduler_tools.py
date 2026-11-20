"""Scheduler tools: timers, reminders, recurring tasks (ideas 9-17)."""

from __future__ import annotations


from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool


class ScheduleArgs(Args):
    description: str = Field(..., min_length=1, max_length=500)
    schedule: str = Field(..., min_length=1, max_length=100,
                          description="e.g. 'in 5m', 'at 09:00', 'every 30m', 'daily 09:00'")
    type: str = Field("reminder", description="timer, reminder, recurring, monitor")


@tool("schedule_create", "Create a timer, reminder, or recurring task.",
      ScheduleArgs, Tier.T0, summary=lambda a: f"Schedule: {a.description[:60]}")
async def schedule_create(a: ScheduleArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    tid = hub.scheduler.add(name=a.description, type_=a.type, schedule=a.schedule)
    return {"created": True, "task_id": tid}


class ScheduleListArgs(Args):
    status: str = Field("active", description="Filter: active, paused, done, failed")


@tool("schedule_list", "List scheduled tasks and reminders.",
      ScheduleListArgs, Tier.T0, summary=lambda a: "List scheduled tasks")
async def schedule_list(a: ScheduleListArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    tasks = hub.scheduler.list_tasks(status=a.status)
    return {"tasks": tasks, "count": len(tasks)}


class ScheduleCancelArgs(Args):
    task_id: str = Field(..., min_length=1, max_length=50)


@tool("schedule_cancel", "Cancel a scheduled task.",
      ScheduleCancelArgs, Tier.T1, summary=lambda a: f"Cancel task {a.task_id}")
async def schedule_cancel(a: ScheduleCancelArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    ok = hub.scheduler.remove(a.task_id)
    if not ok:
        raise ToolError(f"Task {a.task_id} not found.")
    return {"cancelled": True, "task_id": a.task_id}
