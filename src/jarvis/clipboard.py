"""Clipboard history with search and transforms (idea 47).

Monitors the clipboard in a background thread, stores text entries in SQLite with FTS5 search.
Transforms: rewrite, shorten, translate, explain selected text (idea 48).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clipboard_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    app TEXT NOT NULL DEFAULT '',
    copied_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clip_time ON clipboard_history(copied_at DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS clip_hist_fts USING fts5(text, content='clipboard_history', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS ch_ai AFTER INSERT ON clipboard_history BEGIN
    INSERT INTO clip_hist_fts(rowid, text) VALUES (new.rowid, new.text);
END;
"""

MAX_ENTRIES = 1000
MAX_TEXT_LEN = 10000


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


_SECRET_KEYWORDS = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey", "api-key",
    "sk-", "nvapi-", "ghp_", "gho_", "glpat-", "xoxb-", "xoxp-", "bearer",
    "private_key", "private-key", "aws_secret", "azure_key", "client_secret",
    "-----begin", "auth_token", "access_token", "refresh_token",
})


def _looks_like_secret(text: str) -> bool:
    lower = text.lower().strip()
    if any(kw in lower for kw in _SECRET_KEYWORDS):
        return True
    stripped = text.strip()
    if len(stripped) < 200 and " " not in stripped and any(c.isdigit() for c in stripped):
        if len(stripped) > 16 and sum(1 for c in stripped if not c.isalnum()) > 2:
            return True
    return False


class ClipboardHistory:
    def __init__(self, db_path: Path | str) -> None:
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        if str(db_path) != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(_SCHEMA)
        self._running = False
        self._last_text = ""
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor, name="jarvis-clipboard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _monitor(self) -> None:
        while self._running:
            try:
                import win32clipboard
                win32clipboard.OpenClipboard()
                try:
                    if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                        text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                        if text and text != self._last_text and not _looks_like_secret(text):
                            self._last_text = text
                            self._store(text[:MAX_TEXT_LEN])
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                pass
            time.sleep(1.0)

    def _store(self, text: str) -> None:
        try:
            import ctypes
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            hwnd = user32.GetForegroundWindow()
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            app = buf.value[:100]
        except Exception:
            app = ""
        self._db.execute("INSERT INTO clipboard_history(text, app, copied_at) VALUES(?,?,?)",
                         (text, app, _now()))
        self._db.execute("DELETE FROM clipboard_history WHERE id NOT IN "
                         "(SELECT id FROM clipboard_history ORDER BY id DESC LIMIT ?)", (MAX_ENTRIES,))

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        safe = "".join(c for c in query if c.isalnum() or c in " .-_")[:200].strip()
        if not safe:
            return self.recent(limit)
        try:
            rows = self._db.execute(
                'SELECT h.* FROM clipboard_history h JOIN clip_hist_fts f ON f.rowid = h.rowid '
                'WHERE clip_hist_fts MATCH ? ORDER BY h.id DESC LIMIT ?',
                (f'"{safe}"', limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return self.recent(limit)

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM clipboard_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get(self, entry_id: int) -> str | None:
        row = self._db.execute("SELECT text FROM clipboard_history WHERE id = ?", (entry_id,)).fetchone()
        return row["text"] if row else None

    def close(self) -> None:
        self._running = False
        self._db.close()
