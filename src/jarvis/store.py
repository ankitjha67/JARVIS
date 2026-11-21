"""SQLite persistence: conversations, messages, audit (append-only), settings."""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import DEFAULT_SETTINGS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user','assistant','tool')),
    content_json TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    run_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    tier INTEGER NOT NULL,
    decision TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(id DESC);
CREATE TABLE IF NOT EXISTS case_files (
    id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content_text, role,
    content='messages', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS msg_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content_text, role)
    VALUES (new.rowid, json_extract(new.content_json, '$.content'), new.role);
END;
CREATE TRIGGER IF NOT EXISTS msg_fts_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content_text, role)
    VALUES ('delete', old.rowid, json_extract(old.content_json, '$.content'), old.role);
END;
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Store:
    """Thread-safe wrapper around one SQLite connection. Call from worker threads."""

    def __init__(self, path: Path | str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def save_case(self, data: dict[str, Any], case_id: str | None = None,
                  revision: int | None = None) -> dict[str, Any]:
        timestamp = now_iso()
        serialized = json.dumps(data)
        with self._lock:
            if case_id is None:
                case_id = uuid.uuid4().hex[:16]
                self._db.execute("INSERT INTO case_files VALUES (?, 1, ?, ?)", (case_id, serialized, timestamp))
                new_revision = 1
            else:
                result = self._db.execute(
                    "UPDATE case_files SET revision = revision + 1, data_json = ?, updated_at = ? "
                    "WHERE id = ? AND revision = ?", (serialized, timestamp, case_id, revision),
                )
                if result.rowcount == 0:
                    raise ValueError("The case changed or no longer exists. Read the current case before editing.")
                new_revision = revision + 1
        return {"id": case_id, "revision": new_revision, "updated_at": timestamp}

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM case_files WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            return None
        return json.loads(row["data_json"]) | {key: row[key] for key in ("id", "revision", "updated_at")}

    def list_cases(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                """SELECT id, revision, updated_at,
                   json_extract(data_json, '$.title') AS title,
                   json_extract(data_json, '$.status') AS status,
                   json_extract(data_json, '$.deadline') AS deadline,
                   json_extract(data_json, '$.next_action') AS next_action
                   FROM case_files
                   WHERE ? IS NULL OR json_extract(data_json, '$.status') = ?
                   ORDER BY deadline NULLS LAST, updated_at DESC LIMIT ?""", (status, status, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- settings ---------------------------------------------------------------------------

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            rows = self._db.execute("SELECT key, value_json FROM settings").fetchall()
        stored = {r["key"]: json.loads(r["value_json"]) for r in rows if r["key"] in DEFAULT_SETTINGS}
        return _deep_merge(DEFAULT_SETTINGS, stored)

    def replace_setting(self, key: str, value: Any) -> None:
        """Overwrite one setting wholesale (update_settings merges, so it cannot remove nested keys)."""
        with self._lock:
            self._db.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (key, json.dumps(value), now_iso()),
            )

    def kv_set_item(self, key: str, item: str, present: bool) -> None:
        """Add or remove one name in an internal index."""
        value = self.kv(key)
        if present:
            value[item] = now_iso()
        else:
            value.pop(item, None)
        self.replace_setting(key, value)

    def kv(self, key: str, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        """Internal state that is not a user setting (e.g. model test results). A patch is shallow-merged."""
        with self._lock:
            row = self._db.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
            value: dict[str, Any] = json.loads(row["value_json"]) if row else {}
            if patch is not None:
                value.update(patch)
                self._db.execute(
                    "INSERT INTO settings(key, value_json, updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (key, json.dumps(value), now_iso()),
                )
        return value

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        merged = _deep_merge(self.get_settings(), patch)
        ts = now_iso()
        with self._lock:
            self._db.execute("BEGIN")
            try:
                for key in patch:
                    self._db.execute(
                        "INSERT INTO settings(key, value_json, updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                        (key, json.dumps(merged[key]), ts),
                    )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return merged

    # -- conversations ------------------------------------------------------------------------

    def current_conversation(self) -> str:
        with self._lock:
            row = self._db.execute("SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1").fetchone()
        return row["id"] if row else self.new_conversation()

    def new_conversation(self, title: str = "New conversation") -> str:
        cid, ts = uuid.uuid4().hex, now_iso()
        with self._lock:
            self._db.execute(
                "INSERT INTO conversations(id, title, created_at, updated_at) VALUES(?,?,?,?)", (cid, title, ts, ts)
            )
        return cid

    def add_message(
        self, conversation_id: str, message: dict[str, Any], provider: str | None = None, model: str | None = None
    ) -> None:
        ts = now_iso()
        with self._lock:
            self._db.execute("BEGIN")
            try:
                self._db.execute(
                    "INSERT INTO messages(conversation_id, role, content_json, provider, model, created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (conversation_id, message["role"], json.dumps(message), provider, model, ts),
                )
                if message["role"] == "user":
                    self._db.execute(
                        "UPDATE conversations SET title = CASE WHEN title = 'New conversation' THEN ? ELSE title END, "
                        "updated_at = ? WHERE id = ?",
                        (str(message.get("content", ""))[:80] or "New conversation", ts, conversation_id),
                    )
                else:
                    self._db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (ts, conversation_id))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def get_messages(self, conversation_id: str, limit: int) -> list[dict[str, Any]]:
        """Most recent `limit` messages in chronological order, with provider/model/created_at attached."""
        with self._lock:
            rows = self._db.execute(
                "SELECT content_json, provider, model, created_at FROM messages WHERE conversation_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        out = []
        for r in reversed(rows):
            msg = json.loads(r["content_json"])
            msg["_meta"] = {"provider": r["provider"], "model": r["model"], "ts": r["created_at"]}
            out.append(msg)
        return out

    # -- conversation search (FTS5, idea #4) -------------------------------------------------------

    def search_conversations(self, query: str, *, role: str | None = None,
                             limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        safe = "".join(c for c in query if c.isalnum() or c in " .-_")[:200].strip()
        if not safe:
            return []
        fts_query = f'"{safe}"'
        params: list[Any] = [fts_query]
        role_clause = ""
        if role:
            role_clause = "AND m.role = ?"
            params.append(role)
        params.append(limit)
        sql = (
            "SELECT m.id, m.conversation_id, m.role, m.content_json, m.created_at, "
            "c.title AS conversation_title "
            "FROM messages m "
            "JOIN messages_fts f ON f.rowid = m.rowid "
            "JOIN conversations c ON c.id = m.conversation_id "
            f"WHERE messages_fts MATCH ? {role_clause} "
            "ORDER BY m.created_at DESC LIMIT ?"
        )
        with self._lock:
            try:
                rows = self._db.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return []
        results = []
        for r in rows:
            content = json.loads(r["content_json"]).get("content", "")
            results.append({
                "message_id": r["id"],
                "conversation_id": r["conversation_id"],
                "conversation_title": r["conversation_title"],
                "role": r["role"],
                "content": str(content)[:500],
                "created_at": r["created_at"],
            })
        return results

    def rebuild_messages_fts(self) -> None:
        with self._lock:
            self._db.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")

    # -- audit (append-only: there is deliberately no update or delete path, SEC-12) ------------

    def add_audit(
        self, *, run_id: str, tool: str, args: dict[str, Any], tier: int, decision: str, decided_by: str,
        status: str, duration_ms: int = 0, error: str | None = None,
    ) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO audit(ts, run_id, tool, args_json, tier, decision, decided_by, status, duration_ms, error) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (now_iso(), run_id, tool, json.dumps(args, default=str), tier, decision, decided_by, status,
                 duration_ms, error),
            )
            return int(cur.lastrowid or 0)

    def query_audit(self, *, limit: int = 50, before_id: int | None = None, tool: str | None = None,
                    min_tier: int | None = None) -> tuple[list[dict[str, Any]], bool]:
        limit = max(1, min(limit, 200))
        where, params = [], []
        if before_id is not None:
            where.append("id < ?"); params.append(before_id)
        if tool:
            where.append("tool LIKE ?"); params.append(f"%{tool}%")
        if min_tier is not None:
            where.append("tier >= ?"); params.append(min_tier)
        sql = "SELECT * FROM audit" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._db.execute(sql, (*params, limit + 1)).fetchall()
        items = [dict(r) | {"args": json.loads(r["args_json"])} for r in rows[:limit]]
        for it in items:
            it.pop("args_json", None)
        return items, len(rows) > limit
