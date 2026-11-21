"""Timer and reminder manager (idea 9)."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    remind_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    fired INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_remind_at ON reminders(remind_at);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class _ActiveTimer:
    __slots__ = ("id", "label", "end_ts", "timer")

    def __init__(self, tid: str, label: str, end_ts: float, timer: threading.Timer):
        self.id = tid
        self.label = label
        self.end_ts = end_ts
        self.timer = timer


class TimerManager:
    def __init__(self, db_path: Path | str, on_fire: Callable[[str, str], Any] | None = None) -> None:
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        if str(db_path) != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(_SCHEMA)
        self._on_fire = on_fire
        self._timers: dict[str, _ActiveTimer] = {}
        self._lock = threading.Lock()
        self._running = False
        self._checker: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._checker = threading.Thread(target=self._check_reminders, name="jarvis-reminders", daemon=True)
        self._checker.start()

    def stop(self) -> None:
        self._running = False
        with self._lock:
            for t in self._timers.values():
                t.timer.cancel()
            self._timers.clear()

    def set_timer(self, seconds: float, label: str = "") -> dict[str, Any]:
        tid = uuid.uuid4().hex[:8]
        end_ts = time.time() + seconds

        def _fire():
            with self._lock:
                self._timers.pop(tid, None)
            try:
                import winsound
                winsound.Beep(1000, 500)
            except Exception:
                pass
            if self._on_fire:
                self._on_fire("timer", f"Timer done: {label or tid}")

        t = threading.Timer(seconds, _fire)
        t.daemon = True
        t.start()
        with self._lock:
            self._timers[tid] = _ActiveTimer(tid, label or f"Timer {tid}", end_ts, t)
        return {"id": tid, "label": label, "seconds": seconds}

    def cancel_timer(self, tid: str) -> bool:
        with self._lock:
            at = self._timers.pop(tid, None)
        if at:
            at.timer.cancel()
            return True
        return False

    def list_timers(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            return [
                {"id": t.id, "label": t.label, "remaining_seconds": max(0, int(t.end_ts - now))}
                for t in self._timers.values()
            ]

    def set_reminder(self, message: str, remind_at: str) -> dict[str, str]:
        rid = uuid.uuid4().hex[:8]
        self._db.execute(
            "INSERT INTO reminders(id, message, remind_at, created_at) VALUES(?,?,?,?)",
            (rid, message, remind_at, _now_iso()),
        )
        return {"id": rid, "message": message, "remind_at": remind_at}

    def cancel_reminder(self, rid: str) -> bool:
        cur = self._db.execute("DELETE FROM reminders WHERE id = ? AND fired = 0", (rid,))
        return cur.rowcount > 0

    def list_reminders(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, message, remind_at, created_at FROM reminders WHERE fired = 0 ORDER BY remind_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def _check_reminders(self) -> None:
        while self._running:
            try:
                now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
                rows = self._db.execute(
                    "SELECT id, message FROM reminders WHERE fired = 0 AND remind_at <= ?", (now,)
                ).fetchall()
                for row in rows:
                    self._db.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (row["id"],))
                    try:
                        import winsound
                        winsound.Beep(800, 300)
                    except Exception:
                        pass
                    if self._on_fire:
                        self._on_fire("reminder", row["message"])
            except Exception:
                log.debug("reminder check failed", exc_info=True)
            time.sleep(30)

    def close(self) -> None:
        self.stop()
        self._db.close()
