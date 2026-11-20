"""Timers, reminders, scheduled and recurring tasks (ideas 9-17).

Uses asyncio-native scheduling with persistence in SQLite. Supports:
- One-shot timers and alarms
- Reminders (at a time or after a delay)
- Recurring tasks (cron-like, with an inbox of run results)
- Condition monitors ("tell me when X")
- Event triggers (file watch, process exit)
- Morning briefing / end-of-day recap (scheduled)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('timer','reminder','recurring','monitor','trigger','routine')),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    schedule TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    next_run_at TEXT,
    last_run_at TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','done','failed')),
    run_count INTEGER NOT NULL DEFAULT 0,
    max_runs INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sched_next ON scheduled_tasks(next_run_at) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS task_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result_json TEXT,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed','skipped'))
);
CREATE INDEX IF NOT EXISTS idx_runs_task ON task_runs(task_id, started_at DESC);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _parse_schedule(schedule: str, from_time: datetime | None = None) -> datetime | None:
    """Parse simple schedule strings: 'in 5m', 'in 2h', 'at 09:00', 'every 30m', 'daily 09:00', cron-ish."""
    now = from_time or datetime.now(UTC)
    s = schedule.strip().lower()

    if s.startswith("in "):
        parts = s[3:].strip()
        total = timedelta()
        import re
        for match in re.finditer(r"(\d+)\s*(s|sec|m|min|h|hr|d|day)", parts):
            n = int(match.group(1))
            u = match.group(2)[0]
            if u == "s": total += timedelta(seconds=n)
            elif u == "m": total += timedelta(minutes=n)
            elif u == "h": total += timedelta(hours=n)
            elif u == "d": total += timedelta(days=n)
        return now + total if total else None

    if s.startswith("at "):
        time_str = s[3:].strip()
        try:
            h, m = map(int, time_str.split(":"))
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target
        except (ValueError, TypeError):
            return None

    if s.startswith("every "):
        parts = s[6:].strip()
        import re
        match = re.match(r"(\d+)\s*(s|sec|m|min|h|hr|d|day)", parts)
        if match:
            n = int(match.group(1))
            u = match.group(2)[0]
            delta = {"s": timedelta(seconds=n), "m": timedelta(minutes=n),
                     "h": timedelta(hours=n), "d": timedelta(days=n)}.get(u, timedelta(minutes=n))
            return now + delta
        return None

    if s.startswith("daily "):
        time_str = s[6:].strip()
        try:
            h, m = map(int, time_str.split(":"))
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target
        except (ValueError, TypeError):
            return None

    return None


class Scheduler:
    """Persistent task scheduler running in an asyncio loop."""

    def __init__(self, db_path: Path | str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if str(db_path) != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(_SCHEMA)
        self._handlers: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {}
        self._running = False
        self._watchers: dict[str, asyncio.Task[None]] = {}

    def close(self) -> None:
        self._running = False
        for t in self._watchers.values():
            t.cancel()
        with self._lock:
            self._db.close()

    def on_fire(self, type_: str, handler: Callable[[dict[str, Any]], Awaitable[Any]]) -> None:
        self._handlers[type_] = handler

    def add(self, name: str, type_: str = "timer", schedule: str = "", description: str = "",
            payload: dict[str, Any] | None = None, max_runs: int | None = None) -> str:
        tid = uuid.uuid4().hex[:16]
        ts = _now()
        next_run = _parse_schedule(schedule)
        next_at = next_run.isoformat(timespec="milliseconds").replace("+00:00", "Z") if next_run else None
        if type_ == "timer" and max_runs is None:
            max_runs = 1
        with self._lock:
            self._db.execute(
                "INSERT INTO scheduled_tasks(id, type, name, description, schedule, payload_json, "
                "next_run_at, status, max_runs, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (tid, type_, name, description, schedule, json.dumps(payload or {}),
                 next_at, "active", max_runs, ts, ts),
            )
        log.info("scheduled %s %r next=%s", type_, name, next_at)
        return tid

    def remove(self, task_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0

    def pause(self, task_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE scheduled_tasks SET status = 'paused', updated_at = ? WHERE id = ?",
                                   (_now(), task_id))
        return cur.rowcount > 0

    def resume(self, task_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE scheduled_tasks SET status = 'active', updated_at = ? WHERE id = ?",
                                   (_now(), task_id))
        return cur.rowcount > 0

    def list_tasks(self, type_: str | None = None, status: str = "active", limit: int = 50) -> list[dict[str, Any]]:
        parts, params = ["status = ?"], [status]
        if type_:
            parts.append("type = ?"); params.append(type_)
        sql = f"SELECT * FROM scheduled_tasks WHERE {' AND '.join(parts)} ORDER BY next_run_at LIMIT ?"
        params.append(limit)
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def task_runs(self, task_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM task_runs WHERE task_id = ? ORDER BY started_at DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()]

    async def tick(self) -> int:
        """Check for tasks due and fire them. Returns the number of tasks fired."""
        now = _now()
        with self._lock:
            due = self._db.execute(
                "SELECT * FROM scheduled_tasks WHERE status = 'active' AND next_run_at <= ? "
                "ORDER BY next_run_at LIMIT 20", (now,)
            ).fetchall()
        fired = 0
        for task in due:
            task = dict(task)
            await self._fire(task)
            fired += 1
        return fired

    async def _fire(self, task: dict[str, Any]) -> None:
        tid = task["id"]
        run_id = uuid.uuid4().hex[:16]
        ts = _now()
        with self._lock:
            self._db.execute("INSERT INTO task_runs(id, task_id, started_at, status) VALUES(?,?,?,?)",
                             (run_id, tid, ts, "running"))
        payload = json.loads(task.get("payload_json") or "{}")
        payload["_task"] = task
        handler = self._handlers.get(task["type"])
        result: Any = None
        status = "success"
        try:
            if handler:
                result = await handler(payload)
            else:
                log.warning("no handler for task type %s", task["type"])
                status = "skipped"
        except Exception as exc:
            log.exception("task %s failed", task["name"])
            status = "failed"
            result = {"error": str(exc)}

        with self._lock:
            self._db.execute("UPDATE task_runs SET finished_at = ?, result_json = ?, status = ? WHERE id = ?",
                             (_now(), json.dumps(result, default=str), status, run_id))
            new_count = task.get("run_count", 0) + 1
            max_runs = task.get("max_runs")
            if max_runs and new_count >= max_runs:
                self._db.execute("UPDATE scheduled_tasks SET status = 'done', run_count = ?, last_run_at = ?, "
                                 "updated_at = ? WHERE id = ?", (new_count, ts, _now(), tid))
            else:
                next_run = _parse_schedule(task["schedule"])
                next_at = next_run.isoformat(timespec="milliseconds").replace("+00:00", "Z") if next_run else None
                self._db.execute("UPDATE scheduled_tasks SET run_count = ?, last_run_at = ?, next_run_at = ?, "
                                 "updated_at = ? WHERE id = ?", (new_count, ts, next_at, _now(), tid))

    async def run_loop(self) -> None:
        """Main scheduler loop — runs in the background."""
        self._running = True
        while self._running:
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(1.0)
