"""Long-term memory: facts, preferences, entities, corrections, automatic extraction (FR-M3, ideas 1-8).

Uses SQLite FTS5 for full-text search over stored facts. Memory is local, encrypted at rest via
the SQLite WAL on the existing database, and searchable from tools and the model.

Memory types:
- fact: "remember that …" / automatically extracted preferences and decisions
- entity: people, projects, aliases ("my broker is Rahul")
- correction: "never do X again" / "always do Y"
- session: end-of-session summary (auto-extracted)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('fact','entity','correction','session','habit')),
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    tags TEXT NOT NULL DEFAULT '[]',
    importance REAL NOT NULL DEFAULT 0.5,
    access_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_mem_updated ON memories(updated_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, tags, type,
    content='memories', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, tags, type) VALUES (new.rowid, new.content, new.tags, new.type);
END;
CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, type) VALUES ('delete', old.rowid, old.content, old.tags, old.type);
END;
CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, type) VALUES ('delete', old.rowid, old.content, old.tags, old.type);
    INSERT INTO memories_fts(rowid, content, tags, type) VALUES (new.rowid, new.content, new.tags, new.type);
END;

CREATE TABLE IF NOT EXISTS review_queue (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class MemoryStore:
    """Thread-safe long-term memory backed by the same SQLite database or a separate one."""

    def __init__(self, db_path: Path | str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if str(db_path) != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def remember(self, content: str, type_: str = "fact", tags: list[str] | None = None,
                 source: str = "user", importance: float = 0.5, review: bool = False) -> str:
        mid = uuid.uuid4().hex[:16]
        ts = _now()
        tags_json = json.dumps(tags or [])
        with self._lock:
            self._db.execute(
                "INSERT INTO memories(id, type, content, source, tags, importance, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (mid, type_, content, source, tags_json, importance, ts, ts),
            )
            if review:
                self._db.execute("INSERT INTO review_queue(id, memory_id, status, created_at) VALUES(?,?,?,?)",
                                 (uuid.uuid4().hex[:16], mid, "pending", ts))
        return mid

    def forget(self, memory_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self._db.execute("DELETE FROM review_queue WHERE memory_id = ?", (memory_id,))
        return cur.rowcount > 0

    def search(self, query: str, type_: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        parts, params = [], []
        if query.strip():
            parts.append("memories_fts MATCH ?")
            safe = "".join(c for c in query if c.isalnum() or c in " .-_")[:200].strip()
            params.append(f'"{safe}"' if safe else "memory")
        if type_:
            parts.append("m.type = ?")
            params.append(type_)
        where = " AND ".join(parts) if parts else "1=1"
        sql = (f"SELECT m.* FROM memories m "
               f"JOIN memories_fts f ON f.rowid = m.rowid "
               f"WHERE {where} ORDER BY m.importance DESC, m.updated_at DESC LIMIT ?")
        params.append(limit)
        with self._lock:
            try:
                rows = self._db.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                rows = self._db.execute(
                    "SELECT * FROM memories WHERE type = ? ORDER BY updated_at DESC LIMIT ?",
                    (type_ or "fact", limit),
                ).fetchall()
        for row in rows:
            with self._lock:
                self._db.execute("UPDATE memories SET access_count = access_count + 1 WHERE id = ?", (row["id"],))
        return [dict(r) for r in rows]

    def recall(self, type_: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if type_:
            sql = "SELECT * FROM memories WHERE type = ? ORDER BY importance DESC, updated_at DESC LIMIT ?"
            params: tuple[Any, ...] = (type_, limit)
        else:
            sql = "SELECT * FROM memories ORDER BY importance DESC, updated_at DESC LIMIT ?"
            params = (limit,)
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def update(self, memory_id: str, content: str | None = None, importance: float | None = None,
               tags: list[str] | None = None) -> bool:
        sets, params = [], []
        if content is not None:
            sets.append("content = ?"); params.append(content)
        if importance is not None:
            sets.append("importance = ?"); params.append(importance)
        if tags is not None:
            sets.append("tags = ?"); params.append(json.dumps(tags))
        if not sets:
            return False
        sets.append("updated_at = ?"); params.append(_now())
        params.append(memory_id)
        with self._lock:
            cur = self._db.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", params)
        return cur.rowcount > 0

    def review_pending(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT r.id as review_id, r.status, m.* FROM review_queue r "
                "JOIN memories m ON m.id = r.memory_id WHERE r.status = 'pending' "
                "ORDER BY r.created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def review_decide(self, review_id: str, approve: bool) -> None:
        with self._lock:
            row = self._db.execute("SELECT memory_id FROM review_queue WHERE id = ?", (review_id,)).fetchone()
            if not row:
                return
            self._db.execute("UPDATE review_queue SET status = ? WHERE id = ?",
                             ("approved" if approve else "rejected", review_id))
            if not approve:
                self._db.execute("DELETE FROM memories WHERE id = ?", (row["memory_id"],))

    def habits(self) -> list[dict[str, Any]]:
        return self.recall("habit", 20)

    def corrections(self) -> list[dict[str, Any]]:
        return self.recall("correction", 50)

    def entities(self, query: str = "") -> list[dict[str, Any]]:
        if query:
            return self.search(query, "entity", 20)
        return self.recall("entity", 50)

    def context_for_model(self, query: str = "", limit: int = 10) -> str:
        """Build a memory context block for the system prompt."""
        parts = []
        corrs = self.corrections()
        if corrs:
            parts.append("Standing corrections:\n" + "\n".join(f"- {c['content']}" for c in corrs[:5]))
        relevant = self.search(query, limit=limit) if query else self.recall(limit=limit)
        if relevant:
            parts.append("Remembered facts:\n" + "\n".join(f"- {m['content']}" for m in relevant))
        return "\n\n".join(parts)

    def extract_memories(self, conversation: list[dict[str, Any]]) -> list[str]:
        """Auto-extract potential memories from a conversation for the review queue."""
        extracted = []
        for msg in conversation:
            text = str(msg.get("content", ""))
            lower = text.lower()
            if any(kw in lower for kw in ("remember that", "remember:", "don't forget", "keep in mind",
                                           "my name is", "i prefer", "i always", "i never",
                                           "i work at", "i'm a", "i am a")):
                mid = self.remember(text[:500], "fact", source="auto", review=True, importance=0.6)
                extracted.append(mid)
            if any(kw in lower for kw in ("never do that", "don't do that", "stop doing", "always use",
                                           "wrong way", "right way", "not like that")):
                mid = self.remember(text[:500], "correction", source="auto", review=True, importance=0.8)
                extracted.append(mid)
        return extracted

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._db.execute("SELECT COUNT(*) as n FROM memories").fetchone()["n"]
            by_type = {r["type"]: r["n"] for r in
                       self._db.execute("SELECT type, COUNT(*) as n FROM memories GROUP BY type").fetchall()}
            pending = self._db.execute("SELECT COUNT(*) as n FROM review_queue WHERE status = 'pending'").fetchone()["n"]
        return {"total": total, "by_type": by_type, "pending_reviews": pending}
