"""Routine management and undo tools (Mark-LVII)."""

from __future__ import annotations

from typing import Any, Literal

from ..policy import Tier
from .registry import Args, ToolError, tool


class RoutineCreateArgs(Args):
    name: str
    routine_type: Literal["morning_briefing", "eod_recap", "health_check", "condition_monitor"]
    schedule: str
    config: dict[str, Any] | None = None


@tool(
    "routine_create",
    "Create a scheduled routine: morning_briefing (daily summary), eod_recap (end-of-day wrap-up), "
    "health_check (system monitoring), or condition_monitor (watch for a condition). "
    "Schedule examples: 'daily 08:00', 'every 30m', 'daily 18:00'.",
    RoutineCreateArgs, Tier.T1,
    summary=lambda a: f"Create {a.routine_type} routine '{a.name}' on schedule '{a.schedule}'",
)
async def routine_create(args: RoutineCreateArgs) -> dict[str, Any]:
    from ..tools.context import _hub
    if _hub is None:
        raise ToolError("Jarvis core not available.")
    engine = getattr(_hub, "routines", None)
    if engine is None:
        raise ToolError("Routine engine not initialized.")
    try:
        task_id = engine.create_routine(args.name, args.routine_type, args.schedule, args.config)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {"task_id": task_id, "name": args.name, "type": args.routine_type, "schedule": args.schedule}


class RoutineListArgs(Args):
    pass


@tool(
    "routine_list",
    "List all active scheduled routines.",
    RoutineListArgs, Tier.T0,
    summary=lambda a: "List active routines",
)
async def routine_list(args: RoutineListArgs) -> dict[str, Any]:
    from ..tools.context import _hub
    if _hub is None:
        raise ToolError("Jarvis core not available.")
    engine = getattr(_hub, "routines", None)
    if engine is None:
        raise ToolError("Routine engine not initialized.")
    routines = engine.list_routines()
    return {"routines": routines, "count": len(routines)}


class RoutineDeleteArgs(Args):
    task_id: str


@tool(
    "routine_delete",
    "Delete a scheduled routine by its task ID.",
    RoutineDeleteArgs, Tier.T1,
    summary=lambda a: f"Delete routine {a.task_id}",
)
async def routine_delete(args: RoutineDeleteArgs) -> dict[str, Any]:
    from ..tools.context import _hub
    if _hub is None:
        raise ToolError("Jarvis core not available.")
    engine = getattr(_hub, "routines", None)
    if engine is None:
        raise ToolError("Routine engine not initialized.")
    removed = engine.delete_routine(args.task_id)
    if not removed:
        raise ToolError(f"Routine {args.task_id} not found.")
    return {"deleted": args.task_id}


class UndoLastArgs(Args):
    pass


@tool(
    "undo_last",
    "Undo the last reversible action (file write, move, copy, clipboard change).",
    UndoLastArgs, Tier.T1,
    summary=lambda a: "Undo last action",
)
async def undo_last(args: UndoLastArgs) -> dict[str, Any]:
    from ..tools.context import _hub
    if _hub is None:
        raise ToolError("Jarvis core not available.")
    undo = getattr(_hub, "undo_stack", None)
    if undo is None:
        raise ToolError("Undo stack not initialized.")
    entry = undo.undo_last()
    if entry is None:
        raise ToolError("Nothing to undo.")
    reverse = entry.reverse_action
    if not reverse:
        raise ToolError("The last action cannot be undone automatically.")

    # Execute the reverse action
    from .registry import REGISTRY
    rev_tool = REGISTRY.get(reverse.get("tool", ""))
    if rev_tool is None:
        return {"undone": entry.tool_name, "reverse_action": reverse,
                "note": f"Reverse tool '{reverse.get('tool')}' not found. Manual undo needed.",
                "manual": True}
    try:
        rev_args = rev_tool.args_model.model_validate(reverse.get("args", {}))
        await rev_tool.handler(rev_args)
        return {"undone": entry.tool_name, "original_args": entry.args,
                "reverse_tool": reverse.get("tool"), "description": reverse.get("description", ""),
                "success": True}
    except Exception as exc:
        return {"undone": entry.tool_name, "error": f"Undo failed: {exc}",
                "reverse_action": reverse, "manual": True}


class UndoListArgs(Args):
    limit: int = 10


@tool(
    "undo_list",
    "List recent actions that can be undone.",
    UndoListArgs, Tier.T0,
    summary=lambda a: "List undoable actions",
)
async def undo_list(args: UndoListArgs) -> dict[str, Any]:
    from ..tools.context import _hub
    if _hub is None:
        raise ToolError("Jarvis core not available.")
    undo = getattr(_hub, "undo_stack", None)
    if undo is None:
        raise ToolError("Undo stack not initialized.")
    entries = undo.list_undoable(args.limit)
    return {"undoable": entries, "count": len(entries), "can_undo": undo.can_undo()}
