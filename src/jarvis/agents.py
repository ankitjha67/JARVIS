"""Background sub-agents with a task board (idea 64, 65).

An agent is a long-running background task that uses the model and tools to accomplish a goal.
Agents run as asyncio tasks, report progress, and can be paused/cancelled.

Also covers plan mode (idea 65): show a plan, let the user edit, then execute.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class AgentTask:
    id: str
    name: str
    goal: str
    status: str = "pending"  # pending | running | paused | done | failed | cancelled
    progress: float = 0.0
    steps: list[dict[str, Any]] = field(default_factory=list)
    result: Any = None
    error: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "goal": self.goal, "status": self.status,
            "progress": self.progress, "steps": self.steps, "result": self.result,
            "error": self.error, "created_at": self.created_at,
            "started_at": self.started_at, "finished_at": self.finished_at,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


AgentRunner = Callable[[AgentTask, Callable[[str, float], Awaitable[None]]], Awaitable[Any]]


class AgentManager:
    """Manages background sub-agents."""

    def __init__(self, on_update: Callable[[], Awaitable[None]] | None = None) -> None:
        self._tasks: dict[str, AgentTask] = {}
        self._asyncio_tasks: dict[str, asyncio.Task[None]] = {}
        self._runners: dict[str, AgentRunner] = {}
        self._on_update = on_update

    def register_runner(self, name: str, runner: AgentRunner) -> None:
        self._runners[name] = runner

    def create(self, name: str, goal: str, runner_name: str = "default") -> str:
        tid = uuid.uuid4().hex[:12]
        task = AgentTask(id=tid, name=name, goal=goal, created_at=_now())
        self._tasks[tid] = task
        return tid

    async def start(self, task_id: str, runner: AgentRunner | None = None) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status == "running":
            return False
        task.status = "running"
        task.started_at = _now()
        run_fn = runner or self._runners.get("default")
        if not run_fn:
            task.status = "failed"
            task.error = "no runner registered"
            return False
        self._asyncio_tasks[task_id] = asyncio.create_task(self._run(task, run_fn))
        return True

    async def _run(self, task: AgentTask, runner: AgentRunner) -> None:
        async def report(step: str, progress: float) -> None:
            task.steps.append({"step": step, "at": _now()})
            task.progress = min(progress, 1.0)
            if self._on_update:
                await self._on_update()

        try:
            task.result = await runner(task, report)
            task.status = "done"
            task.progress = 1.0
        except asyncio.CancelledError:
            task.status = "cancelled"
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)[:500]
            log.exception("agent %s failed", task.name)
        task.finished_at = _now()
        if self._on_update:
            await self._on_update()

    async def cancel(self, task_id: str) -> bool:
        at = self._asyncio_tasks.get(task_id)
        if at and not at.done():
            at.cancel()
            return True
        return False

    def list_tasks(self, status: str | None = None) -> list[dict[str, Any]]:
        tasks = self._tasks.values()
        if status:
            tasks = [t for t in tasks if t.status == status]
        return [t.to_json() for t in sorted(tasks, key=lambda t: t.created_at, reverse=True)]

    def get(self, task_id: str) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        return task.to_json() if task else None

    async def cleanup(self, max_done: int = 50) -> None:
        done = [t for t in self._tasks.values() if t.status in ("done", "failed", "cancelled")]
        done.sort(key=lambda t: t.finished_at)
        for task in done[:-max_done]:
            self._tasks.pop(task.id, None)
            self._asyncio_tasks.pop(task.id, None)


@dataclass
class Plan:
    """A plan the user can review and edit before execution (idea 65)."""
    id: str
    title: str
    steps: list[dict[str, Any]]
    status: str = "draft"  # draft | approved | executing | done | cancelled
    current_step: int = 0

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "steps": self.steps,
                "status": self.status, "current_step": self.current_step}


class PlanManager:
    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}

    def create(self, title: str, steps: list[str]) -> str:
        pid = uuid.uuid4().hex[:12]
        plan_steps = [{"index": i, "description": s, "status": "pending"} for i, s in enumerate(steps)]
        self._plans[pid] = Plan(id=pid, title=title, steps=plan_steps)
        return pid

    def approve(self, plan_id: str) -> bool:
        plan = self._plans.get(plan_id)
        if plan and plan.status == "draft":
            plan.status = "approved"
            return True
        return False

    def advance(self, plan_id: str) -> dict[str, Any] | None:
        plan = self._plans.get(plan_id)
        if not plan or plan.status not in ("approved", "executing"):
            return None
        plan.status = "executing"
        if plan.current_step < len(plan.steps):
            step = plan.steps[plan.current_step]
            step["status"] = "done"
            plan.current_step += 1
        if plan.current_step >= len(plan.steps):
            plan.status = "done"
        return plan.to_json()

    def get(self, plan_id: str) -> dict[str, Any] | None:
        plan = self._plans.get(plan_id)
        return plan.to_json() if plan else None

    def list_plans(self) -> list[dict[str, Any]]:
        return [p.to_json() for p in self._plans.values()]
