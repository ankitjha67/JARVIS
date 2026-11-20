"""Agent tools: background tasks and plan mode (ideas 64-65)."""

from __future__ import annotations

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool


class AgentCreateArgs(Args):
    name: str = Field(..., min_length=1, max_length=100)
    goal: str = Field(..., min_length=1, max_length=2000)


@tool("agent_create", "Create a background sub-agent task.",
      AgentCreateArgs, Tier.T1, summary=lambda a: f"Agent: {a.name[:40]}")
async def agent_create(a: AgentCreateArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    tid = hub.agents.create(a.name, a.goal)
    await hub.agents.start(tid)
    return {"created": True, "task_id": tid, "status": "running"}


class AgentListArgs(Args):
    status: str | None = Field(None, description="Filter: pending, running, done, failed")


@tool("agent_list", "List background agent tasks.",
      AgentListArgs, Tier.T0, summary=lambda a: "List agents")
async def agent_list(a: AgentListArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    tasks = hub.agents.list_tasks(status=a.status)
    return {"tasks": tasks, "count": len(tasks)}


class AgentCancelArgs(Args):
    task_id: str = Field(..., min_length=1, max_length=50)


@tool("agent_cancel", "Cancel a running agent task.",
      AgentCancelArgs, Tier.T1, summary=lambda a: f"Cancel agent {a.task_id}")
async def agent_cancel(a: AgentCancelArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    ok = await hub.agents.cancel(a.task_id)
    if not ok:
        raise ToolError("Task not running or not found.")
    return {"cancelled": True, "task_id": a.task_id}


class PlanCreateArgs(Args):
    title: str = Field(..., min_length=1, max_length=200)
    steps: list[str] = Field(..., min_length=1, max_length=20)


@tool("plan_create", "Create a multi-step plan for user review.",
      PlanCreateArgs, Tier.T0, summary=lambda a: f"Plan: {a.title[:40]}")
async def plan_create(a: PlanCreateArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    pid = hub.plans.create(a.title, a.steps)
    plan = hub.plans.get(pid)
    return {"plan": plan}


class AgentStatusArgs(Args):
    task_id: str = Field(..., min_length=1, max_length=50)


@tool("agent_status", "Get detailed status of a background agent task.",
      AgentStatusArgs, Tier.T0, summary=lambda a: f"Agent status: {a.task_id}")
async def agent_status(a: AgentStatusArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    task = hub.agents.get(a.task_id)
    if not task:
        raise ToolError(f"Agent task not found: {a.task_id}")
    return task


class AgentHandoffArgs(Args):
    goal: str = Field(..., min_length=1, max_length=2000, description="What the sub-agent should accomplish")
    name: str = Field("sub-agent", max_length=100, description="Name for the background task")
    wait: bool = Field(False, description="Wait for the sub-agent to finish and return its result")
    max_steps: int = Field(10, ge=1, le=50, description="Maximum steps the sub-agent can take")


@tool("agent_handoff", "Delegate work to a background sub-agent. Can optionally wait for completion.",
      AgentHandoffArgs, Tier.T1, summary=lambda a: f"Handoff: {a.name[:40]}")
async def agent_handoff(a: AgentHandoffArgs) -> dict:
    import asyncio
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    tid = hub.agents.create(a.name, a.goal)

    if a.wait:
        result = await hub.orchestrator.run_headless(a.goal, max_steps=a.max_steps)
        task = hub.agents.get(tid)
        if task:
            task["result"] = result
            task["status"] = "done"
        return {"task_id": tid, "status": "done", "result": result}

    await hub.agents.start(tid)
    return {"task_id": tid, "status": "running", "note": "Use agent_status to check progress."}


class PlanActionArgs(Args):
    plan_id: str = Field(..., min_length=1, max_length=50)
    action: str = Field(..., description="approve, advance, cancel")


@tool("plan_action", "Approve, advance, or cancel a plan.",
      PlanActionArgs, Tier.T1, summary=lambda a: f"Plan {a.action}: {a.plan_id}")
async def plan_action(a: PlanActionArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    if a.action == "approve":
        ok = hub.plans.approve(a.plan_id)
    elif a.action == "advance":
        result = hub.plans.advance(a.plan_id)
        return {"plan": result} if result else {"error": "Cannot advance"}
    else:
        raise ToolError("Unknown action. Use: approve, advance")
    return {"success": ok, "action": a.action}
