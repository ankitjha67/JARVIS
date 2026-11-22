"""Action history tools: query the audit log to see what happened.

Addresses the need to know whether an action completed when the assistant
stops mid-task (e.g. "did that email actually send?").
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool


class ActionHistoryArgs(Args):
    tool_name: str | None = Field(None, description="Filter by tool name (substring match)")
    status: str | None = Field(None, description="Filter by status: ok, error, denied, refused, cancelled")
    limit: int = Field(20, ge=1, le=100, description="Max results to return")


@tool(
    "action_history",
    "Query the execution history of tool actions. Shows what ran, when, whether it "
    "succeeded or failed, and how long it took. Use this to check if an action completed "
    "(e.g. 'did that email send?') or to review recent activity.",
    ActionHistoryArgs, Tier.T0,
    summary=lambda a: f"Query action history{f' for {a.tool_name}' if a.tool_name else ''}",
)
async def action_history(a: ActionHistoryArgs) -> dict[str, Any]:
    from .context import _hub
    if _hub is None:
        raise ToolError("Jarvis core not available.")
    kwargs: dict[str, Any] = {"limit": a.limit}
    if a.tool_name:
        kwargs["tool"] = a.tool_name
    rows, has_more = await asyncio.to_thread(_hub.store.query_audit, **kwargs)
    if a.status:
        rows = [r for r in rows if r.get("status") == a.status]
    for row in rows:
        row.pop("args", None)
    return {
        "actions": rows,
        "count": len(rows),
        "has_more": has_more,
        "tip": "Use tool_name filter to narrow results, e.g. 'email_send' or 'shell_run'.",
    }


class ActionDetailArgs(Args):
    audit_id: int = Field(..., description="The audit entry ID to inspect")


@tool(
    "action_detail",
    "Get full details of a specific action from the audit log, including the arguments "
    "that were passed and the complete error message if it failed.",
    ActionDetailArgs, Tier.T0,
    summary=lambda a: f"Get action detail #{a.audit_id}",
)
async def action_detail(a: ActionDetailArgs) -> dict[str, Any]:
    from .context import _hub
    if _hub is None:
        raise ToolError("Jarvis core not available.")
    rows, _ = await asyncio.to_thread(_hub.store.query_audit, limit=200)
    match = next((r for r in rows if r.get("id") == a.audit_id), None)
    if not match:
        raise ToolError(f"No audit entry with id {a.audit_id} found in recent history.")
    return {"action": match}


class ActionSummaryArgs(Args):
    pass


@tool(
    "action_summary",
    "Get a quick summary of recent action outcomes: how many succeeded, failed, were "
    "denied, or cancelled. Useful for a status check after multi-step operations.",
    ActionSummaryArgs, Tier.T0,
    summary=lambda a: "Summarise recent action outcomes",
)
async def action_summary(a: ActionSummaryArgs) -> dict[str, Any]:
    from .context import _hub
    if _hub is None:
        raise ToolError("Jarvis core not available.")
    rows, _ = await asyncio.to_thread(_hub.store.query_audit, limit=200)
    counts: dict[str, int] = {}
    for r in rows:
        s = r.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    last_failed = next((r for r in rows if r.get("status") in ("error", "cancelled")), None)
    return {
        "total": len(rows),
        "by_status": counts,
        "last_failure": {
            "tool": last_failed["tool"],
            "error": last_failed.get("error"),
            "ts": last_failed.get("ts"),
        } if last_failed else None,
    }
