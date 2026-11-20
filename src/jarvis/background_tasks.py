"""Background task engine with checkpointing (Mark-LVII).

Persistent background tasks that survive restarts. Each step checkpoints
intermediate state to SQLite so a crashed task can resume from the last
successful checkpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bg_tasks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('research','automation','monitoring','analysis')),
    name TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','paused','done','failed','cancelled')),
    priority INTEGER NOT NULL DEFAULT 5 CHECK (priority BETWEEN 0 AND 9),
    progress REAL NOT NULL DEFAULT 0.0,
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bg_tasks_status ON bg_tasks(status, priority DESC);

CREATE TABLE IF NOT EXISTS bg_task_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES bg_tasks(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','running','done','failed','skipped')),
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(task_id, step_index)
);
CREATE INDEX IF NOT EXISTS idx_bg_steps_task ON bg_task_steps(task_id, step_index);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class BackgroundTaskEngine:
    """Persistent background task storage with checkpointing."""

    def __init__(self, db_path: Path | str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if str(db_path) != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(_SCHEMA)
        self._mark_interrupted()

    def _mark_interrupted(self) -> None:
        """On startup, any task still marked 'running' was interrupted — reset to 'queued' so it can resume."""
        with self._lock:
            self._db.execute(
                "UPDATE bg_tasks SET status = 'queued', updated_at = ? WHERE status = 'running'",
                (_now(),),
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def create(
        self, name: str, type_: str = "research", goal: str = "",
        priority: int = 5, steps: list[str] | None = None,
    ) -> str:
        tid = uuid.uuid4().hex[:16]
        ts = _now()
        with self._lock:
            self._db.execute(
                "INSERT INTO bg_tasks(id, type, name, goal, priority, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (tid, type_, name, goal, max(0, min(9, priority)), ts, ts),
            )
            if steps:
                for i, desc in enumerate(steps):
                    self._db.execute(
                        "INSERT INTO bg_task_steps(task_id, step_index, description) VALUES(?,?,?)",
                        (tid, i, desc),
                    )
        log.info("bg_task created %s %r (%s)", tid, name, type_)
        return tid

    def checkpoint(self, task_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE bg_tasks SET checkpoint_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(data, default=str), _now(), task_id),
            )

    def resume(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT checkpoint_json FROM bg_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if not row:
            return None
        data = json.loads(row["checkpoint_json"])
        return data if data else None

    def set_status(self, task_id: str, status: str, *, error: str | None = None,
                   result: Any = None, progress: float | None = None) -> None:
        ts = _now()
        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, ts]
        if status == "running":
            sets.append("started_at = COALESCE(started_at, ?)")
            params.append(ts)
        if status in ("done", "failed", "cancelled"):
            sets.append("finished_at = ?")
            params.append(ts)
        if error is not None:
            sets.append("error = ?")
            params.append(error[:2000])
        if result is not None:
            sets.append("result_json = ?")
            params.append(json.dumps(result, default=str)[:50_000])
        if progress is not None:
            sets.append("progress = ?")
            params.append(max(0.0, min(1.0, progress)))
        params.append(task_id)
        with self._lock:
            self._db.execute(f"UPDATE bg_tasks SET {', '.join(sets)} WHERE id = ?", params)

    def set_progress(self, task_id: str, progress: float) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE bg_tasks SET progress = ?, updated_at = ? WHERE id = ?",
                (max(0.0, min(1.0, progress)), _now(), task_id),
            )

    def update_step(self, task_id: str, step_index: int, status: str,
                    output: Any = None) -> None:
        ts = _now()
        sets = ["status = ?"]
        params: list[Any] = [status]
        if status == "running":
            sets.append("started_at = ?")
            params.append(ts)
        if status in ("done", "failed", "skipped"):
            sets.append("finished_at = ?")
            params.append(ts)
        if output is not None:
            sets.append("output_json = ?")
            params.append(json.dumps(output, default=str)[:20_000])
        params.extend([task_id, step_index])
        with self._lock:
            self._db.execute(
                f"UPDATE bg_task_steps SET {', '.join(sets)} WHERE task_id = ? AND step_index = ?",
                params,
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM bg_tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                return None
            task = dict(row)
            steps = self._db.execute(
                "SELECT * FROM bg_task_steps WHERE task_id = ? ORDER BY step_index",
                (task_id,),
            ).fetchall()
        task["steps"] = [dict(s) for s in steps]
        task["checkpoint"] = json.loads(task.pop("checkpoint_json", "{}"))
        task["result"] = json.loads(task.pop("result_json", "null") or "null")
        for s in task["steps"]:
            s["output"] = json.loads(s.pop("output_json", "null") or "null")
            s.pop("input_json", None)
        return task

    def list_tasks(self, status: str | None = None, type_: str | None = None,
                   limit: int = 50) -> list[dict[str, Any]]:
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if type_:
            where.append("type = ?")
            params.append(type_)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        params.append(max(1, min(200, limit)))
        with self._lock:
            rows = self._db.execute(
                f"SELECT id, type, name, goal, status, priority, progress, error, "
                f"created_at, started_at, finished_at FROM bg_tasks{clause} "
                f"ORDER BY priority DESC, created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE bg_tasks SET status = 'cancelled', finished_at = ?, updated_at = ? "
                "WHERE id = ? AND status IN ('queued', 'running', 'paused')",
                (_now(), _now(), task_id),
            )
        return cur.rowcount > 0

    def queued_ids(self) -> list[str]:
        """Return task IDs that are queued, ordered by priority then creation time."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM bg_tasks WHERE status = 'queued' ORDER BY priority DESC, created_at LIMIT 20"
            ).fetchall()
        return [r["id"] for r in rows]

    def cleanup(self, max_done: int = 100) -> int:
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM bg_tasks WHERE status IN ('done','failed','cancelled') "
                "ORDER BY finished_at DESC"
            ).fetchall()
        ids_to_remove = [r["id"] for r in rows[max_done:]]
        if ids_to_remove:
            with self._lock:
                self._db.executemany("DELETE FROM bg_tasks WHERE id = ?", [(i,) for i in ids_to_remove])
        return len(ids_to_remove)


RunnerFn = Callable[
    [str, "BackgroundTaskEngine", Callable[[str, float], Awaitable[None]]],
    Awaitable[Any],
]


class TaskRunner:
    """Manages asyncio execution of background tasks with a concurrency limit."""

    def __init__(
        self, engine: BackgroundTaskEngine, max_concurrent: int = 3,
        on_update: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.engine = engine
        self._sem = asyncio.Semaphore(max_concurrent)
        self._on_update = on_update
        self._running: dict[str, asyncio.Task[None]] = {}
        self._default_runner: RunnerFn | None = None
        self._active = False

    def set_runner(self, runner: RunnerFn) -> None:
        self._default_runner = runner

    async def submit(self, task_id: str, runner: RunnerFn | None = None) -> bool:
        if task_id in self._running:
            return False
        fn = runner or self._default_runner
        if not fn:
            log.warning("no runner for bg task %s", task_id)
            return False
        self._running[task_id] = asyncio.create_task(self._execute(task_id, fn))
        return True

    async def cancel(self, task_id: str) -> bool:
        at = self._running.get(task_id)
        if at and not at.done():
            at.cancel()
            self.engine.cancel(task_id)
            return True
        return self.engine.cancel(task_id)

    async def _execute(self, task_id: str, runner: RunnerFn) -> None:
        async with self._sem:
            self.engine.set_status(task_id, "running")
            await self._notify(task_id)

            async def report(step_desc: str, progress: float) -> None:
                self.engine.set_progress(task_id, progress)
                await self._notify(task_id, step=step_desc, progress=progress)

            try:
                result = await runner(task_id, self.engine, report)
                self.engine.set_status(task_id, "done", result=result, progress=1.0)
            except asyncio.CancelledError:
                self.engine.set_status(task_id, "cancelled")
            except Exception as exc:
                log.exception("bg task %s failed", task_id)
                self.engine.set_status(task_id, "failed", error=str(exc)[:1000])
            finally:
                self._running.pop(task_id, None)
                await self._notify(task_id)

    async def _notify(self, task_id: str, step: str = "", progress: float = -1) -> None:
        if self._on_update:
            task = self.engine.get_task(task_id)
            if task:
                await self._on_update(task_id, task)

    async def run_loop(self) -> None:
        """Periodically check for queued tasks and start them."""
        self._active = True
        while self._active:
            try:
                queued = await asyncio.to_thread(self.engine.queued_ids)
                for tid in queued:
                    if tid not in self._running:
                        await self.submit(tid)
                await asyncio.to_thread(self.engine.cleanup)
            except Exception:
                log.exception("bg task runner loop error")
            await asyncio.sleep(2.0)

    def stop(self) -> None:
        self._active = False
        for task in self._running.values():
            task.cancel()
