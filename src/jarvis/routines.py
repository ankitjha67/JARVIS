"""Routine/proactive engine (Mark-LVII, PRD M3–M4).

Builds on the scheduler to run intelligent routines:
morning briefing, end-of-day recap, condition monitors, periodic health checks.
"""

from __future__ import annotations

import asyncio
import logging
import platform
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import psutil

log = logging.getLogger(__name__)

ROUTINE_TYPES = ("morning_briefing", "eod_recap", "health_check", "condition_monitor")

_HEALTH_THRESHOLDS = {
    "disk_percent": 90,
    "memory_percent": 85,
    "cpu_percent": 90,
}


class RoutineEngine:
    """Runs intelligent routines on a schedule, delegating timing to the Scheduler."""

    def __init__(
        self,
        scheduler: Any,
        run_model_fn: Callable[[str, int], Awaitable[str]],
        broadcast_fn: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._run_model = run_model_fn
        self._broadcast = broadcast_fn
        self._dispatchers: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
            "morning_briefing": self._morning_briefing,
            "eod_recap": self._eod_recap,
            "health_check": self._health_check,
            "condition_monitor": self._condition_monitor,
        }

    def register_handlers(self) -> None:
        self._scheduler.on_fire("routine", self._handle_routine)

    async def _handle_routine(self, payload: dict[str, Any]) -> Any:
        task_meta = payload.get("_task", {})
        inner = payload.get("config") or {}
        routine_type = payload.get("routine_type") or inner.get("routine_type") or task_meta.get("name", "")
        handler = self._dispatchers.get(routine_type)
        if not handler:
            log.warning("unknown routine type: %s", routine_type)
            return {"error": f"Unknown routine type: {routine_type}"}
        try:
            result = await handler(payload)
            if self._broadcast:
                from .protocol import msg
                await self._broadcast(msg(
                    "schedule.fire",
                    routine=routine_type,
                    name=task_meta.get("name", routine_type),
                    summary=result.get("summary", str(result)[:300]) if isinstance(result, dict) else str(result)[:300],
                ))
            return result
        except Exception as exc:
            log.exception("routine %s failed", routine_type)
            return {"error": str(exc)}

    # -- built-in routines ---------------------------------------------------------------------

    async def _morning_briefing(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now().astimezone()
        prompt = (
            f"Give me a brief morning briefing for {now.strftime('%A, %B %d %Y')}. Include:\n"
            "1. Current weather conditions (use the weather_get tool for my location if available)\n"
            "2. A motivational thought for the day\n"
            "3. Any system health notes (disk space, memory)\n"
            "Keep it concise — 3-5 sentences total, suitable for being spoken aloud."
        )
        summary = await self._run_model(prompt, 8)
        return {"summary": summary, "type": "morning_briefing", "time": now.isoformat()}

    async def _eod_recap(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now().astimezone()
        prompt = (
            f"Give me a brief end-of-day recap for {now.strftime('%A, %B %d %Y')}. "
            "Summarize what was accomplished today based on conversation history. "
            "Mention any pending tasks or reminders. Keep it to 3-5 sentences."
        )
        summary = await self._run_model(prompt, 6)
        return {"summary": summary, "type": "eod_recap", "time": now.isoformat()}

    async def _health_check(self, _payload: dict[str, Any]) -> dict[str, Any]:
        alerts: list[str] = []
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        if cpu > _HEALTH_THRESHOLDS["cpu_percent"]:
            alerts.append(f"CPU usage is high: {cpu:.0f}%")
        if mem.percent > _HEALTH_THRESHOLDS["memory_percent"]:
            alerts.append(f"Memory usage is high: {mem.percent:.0f}% ({mem.available // (1024**2)} MB free)")

        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                if usage.percent > _HEALTH_THRESHOLDS["disk_percent"]:
                    free_gb = usage.free / (1024**3)
                    alerts.append(f"Disk {part.device} is {usage.percent:.0f}% full ({free_gb:.1f} GB free)")
            except (PermissionError, OSError):
                continue

        return {
            "type": "health_check",
            "healthy": len(alerts) == 0,
            "alerts": alerts,
            "cpu_percent": cpu,
            "memory_percent": mem.percent,
            "summary": "All systems nominal." if not alerts else "; ".join(alerts),
        }

    async def _condition_monitor(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = payload.get("config") or {}
        condition = config.get("condition", "")
        check_prompt = config.get("prompt", "")
        if not condition and not check_prompt:
            return {"met": False, "error": "No condition or prompt specified."}

        if check_prompt:
            prompt = (
                f"Check this condition and respond with ONLY 'YES' or 'NO' followed by a brief explanation:\n"
                f"{check_prompt}"
            )
            result = await self._run_model(prompt, 4)
            met = result.strip().upper().startswith("YES")
            return {"met": met, "explanation": result.strip(), "condition": check_prompt}

        return {"met": False, "condition": condition, "note": "Expression-based conditions not yet supported."}

    # -- public API ---------------------------------------------------------------------------

    def create_routine(
        self,
        name: str,
        routine_type: str,
        schedule: str,
        config: dict[str, Any] | None = None,
    ) -> str:
        if routine_type not in ROUTINE_TYPES:
            raise ValueError(f"Unknown routine type: {routine_type}. Must be one of {ROUTINE_TYPES}")
        payload = {"routine_type": routine_type, "config": config or {}}
        return self._scheduler.add(
            name=name,
            type_="routine",
            schedule=schedule,
            description=f"{routine_type} routine",
            payload=payload,
        )

    def delete_routine(self, task_id: str) -> bool:
        return self._scheduler.remove(task_id)

    def list_routines(self) -> list[dict[str, Any]]:
        return self._scheduler.list_tasks(type_="routine", status="active")
