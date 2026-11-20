"""Background task management tools (Mark-LVII)."""

from __future__ import annotations

from typing import Any, Literal

from ..policy import Tier
from .registry import Args, ToolError, tool


class _BgCreateArgs(Args):
    name: str
    goal: str
    type: Literal["research", "automation", "monitoring", "analysis"] = "research"
    priority: int = 5
    steps: list[str] | None = None


@tool(
    "bg_task_create",
    "Create a persistent background task that runs asynchronously with checkpointing.",
    _BgCreateArgs, Tier.T1,
    summary=lambda a: f"Create background task: {a.name}",
)
async def bg_task_create(args: _BgCreateArgs) -> dict[str, Any]:
    from ..tools.context import _hub  # type: ignore[attr-defined]
    if _hub is None:
        raise ToolError("Background task engine not available.")
    engine = _hub.bg_engine
    task_id = await _run(engine.create, args.name, args.type, args.goal, args.priority, args.steps)
    return {"task_id": task_id, "status": "queued"}


class _BgStatusArgs(Args):
    task_id: str


@tool(
    "bg_task_status",
    "Check the status and progress of a background task.",
    _BgStatusArgs, Tier.T0,
    summary=lambda a: f"Check background task {a.task_id}",
)
async def bg_task_status(args: _BgStatusArgs) -> dict[str, Any]:
    from ..tools.context import _hub  # type: ignore[attr-defined]
    if _hub is None:
        raise ToolError("Background task engine not available.")
    task = await _run(_hub.bg_engine.get_task, args.task_id)
    if not task:
        raise ToolError(f"Task {args.task_id} not found.")
    task.pop("checkpoint", None)
    return task


class _BgListArgs(Args):
    status: Literal["queued", "running", "paused", "done", "failed", "cancelled"] | None = None
    type: Literal["research", "automation", "monitoring", "analysis"] | None = None
    limit: int = 20


@tool(
    "bg_task_list",
    "List background tasks, optionally filtered by status or type.",
    _BgListArgs, Tier.T0,
    summary=lambda a: "List background tasks",
)
async def bg_task_list(args: _BgListArgs) -> dict[str, Any]:
    from ..tools.context import _hub  # type: ignore[attr-defined]
    if _hub is None:
        raise ToolError("Background task engine not available.")
    tasks = await _run(_hub.bg_engine.list_tasks, args.status, args.type, args.limit)
    return {"tasks": tasks, "count": len(tasks)}


class _BgCancelArgs(Args):
    task_id: str


@tool(
    "bg_task_cancel",
    "Cancel a queued or running background task.",
    _BgCancelArgs, Tier.T1,
    summary=lambda a: f"Cancel background task {a.task_id}",
)
async def bg_task_cancel(args: _BgCancelArgs) -> dict[str, Any]:
    from ..tools.context import _hub  # type: ignore[attr-defined]
    if _hub is None:
        raise ToolError("Background task engine not available.")
    ok = await _hub.bg_runner.cancel(args.task_id)
    if not ok:
        raise ToolError(f"Task {args.task_id} could not be cancelled (not found or already finished).")
    return {"cancelled": True}


async def _run(fn, *args):
    import asyncio
    return await asyncio.to_thread(fn, *args)
