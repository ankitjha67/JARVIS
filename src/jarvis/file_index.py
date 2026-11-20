"""Index chosen folders and answer with citations (idea 55). Full-text file search using SQLite FTS5.

Also covers: instant filename search (56), drop-a-file summarise (58), web clipper (59),
YouTube/podcast summariser (60), automatic daily note (61).
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexed_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    ext TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    hash TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    indexed_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS file_fts USING fts5(
    name, content, path,
    content='indexed_files', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS fi_ai AFTER INSERT ON indexed_files BEGIN
    INSERT INTO file_fts(rowid, name, content, path) VALUES (new.rowid, new.name, new.content, new.path);
END;
CREATE TRIGGER IF NOT EXISTS fi_ad AFTER DELETE ON indexed_files BEGIN
    INSERT INTO file_fts(file_fts, rowid, name, content, path) VALUES ('delete', old.rowid, old.name, old.content, old.path);
END;
CREATE TRIGGER IF NOT EXISTS fi_au AFTER UPDATE ON indexed_files BEGIN
    INSERT INTO file_fts(file_fts, rowid, name, content, path) VALUES ('delete', old.rowid, old.name, old.content, old.path);
    INSERT INTO file_fts(rowid, name, content, path) VALUES (new.rowid, new.name, new.content, new.path);
END;

CREATE TABLE IF NOT EXISTS web_clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    clipped_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS clip_fts USING fts5(title, content, url, content='web_clips', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS wc_ai AFTER INSERT ON web_clips BEGIN
    INSERT INTO clip_fts(rowid, title, content, url) VALUES (new.rowid, new.title, new.content, new.url);
END;

CREATE TABLE IF NOT EXISTS daily_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".csv", ".xml", ".html", ".css", ".sql", ".sh", ".ps1",
    ".bat", ".cmd", ".log", ".rst", ".tex", ".r", ".go", ".rs", ".java", ".c", ".cpp",
    ".h", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".scala", ".lua", ".vim",
    ".conf", ".env", ".gitignore", ".dockerfile", ".makefile",
}

MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB text limit per file


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FileIndex:
    def __init__(self, db_path: Path | str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if str(db_path) != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(_SCHEMA)
        self._indexing = False

    def close(self) -> None:
        self._indexing = False
        with self._lock:
            self._db.close()

    def index_folder(self, folder: Path, extensions: set[str] | None = None) -> int:
        """Index all text files in a folder. Returns count of files indexed."""
        exts = extensions or TEXT_EXTENSIONS
        count = 0
        self._indexing = True
        for root, dirs, files in os.walk(folder):
            if not self._indexing:
                break
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git", "venv")]
            for name in files:
                if not self._indexing:
                    break
                ext = Path(name).suffix.lower()
                if ext not in exts:
                    continue
                filepath = Path(root) / name
                try:
                    stat = filepath.stat()
                    if stat.st_size > MAX_FILE_SIZE:
                        continue
                    content = filepath.read_text(encoding="utf-8", errors="replace")
                    file_hash = hashlib.md5(content.encode()[:4096]).hexdigest()
                    with self._lock:
                        existing = self._db.execute("SELECT hash FROM indexed_files WHERE path = ?",
                                                     (str(filepath),)).fetchone()
                        if existing and existing["hash"] == file_hash:
                            continue
                        self._db.execute(
                            "INSERT INTO indexed_files(path, name, ext, size, hash, content, indexed_at) "
                            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                            "name=excluded.name, size=excluded.size, hash=excluded.hash, "
                            "content=excluded.content, indexed_at=excluded.indexed_at",
                            (str(filepath), name, ext, stat.st_size, file_hash, content[:50000], _now()),
                        )
                    count += 1
                except (OSError, UnicodeDecodeError):
                    continue
        self._indexing = False
        return count

    @staticmethod
    def _safe_fts(query: str) -> str:
        return "".join(c for c in query if c.isalnum() or c in " .-_")[:200].strip()

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        safe = self._safe_fts(query)
        if not safe:
            return []
        with self._lock:
            try:
                rows = self._db.execute(
                    'SELECT f.path, f.name, f.ext, f.size, f.indexed_at, '
                    'snippet(file_fts, 1, ">>>", "<<<", "...", 40) as snippet '
                    'FROM indexed_files f JOIN file_fts ON file_fts.rowid = f.rowid '
                    'WHERE file_fts MATCH ? ORDER BY rank LIMIT ?',
                    (f'"{safe}"', limit),
                ).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                return []

    def search_names(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT path, name, ext, size FROM indexed_files WHERE name LIKE ? ORDER BY name LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_content(self, path: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT content FROM indexed_files WHERE path = ?", (path,)).fetchone()
        return row["content"] if row else None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._db.execute("SELECT COUNT(*) as n FROM indexed_files").fetchone()["n"]
            by_ext = {r["ext"]: r["n"] for r in
                      self._db.execute("SELECT ext, COUNT(*) as n FROM indexed_files GROUP BY ext ORDER BY n DESC LIMIT 20").fetchall()}
        return {"total_files": total, "by_extension": by_ext}

    # -- web clips (idea 59) ----------------------------------------------------------------

    def clip_web(self, url: str, title: str, content: str, tags: list[str] | None = None) -> int:
        with self._lock:
            cur = self._db.execute("INSERT INTO web_clips(url, title, content, tags, clipped_at) VALUES(?,?,?,?,?)",
                                   (url, title, content[:50000], str(tags or []), _now()))
            return int(cur.lastrowid or 0)

    def search_clips(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        safe = self._safe_fts(query)
        if not safe:
            return []
        with self._lock:
            try:
                rows = self._db.execute(
                    'SELECT w.*, snippet(clip_fts, 1, ">>>", "<<<", "...", 40) as snippet '
                    'FROM web_clips w JOIN clip_fts ON clip_fts.rowid = w.rowid '
                    'WHERE clip_fts MATCH ? ORDER BY rank LIMIT ?',
                    (f'"{safe}"', limit),
                ).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                return []

    # -- daily notes (idea 61) ---------------------------------------------------------------

    def daily_note(self, content: str, date: str | None = None) -> None:
        d = date or datetime.now(UTC).strftime("%Y-%m-%d")
        with self._lock:
            existing = self._db.execute("SELECT content FROM daily_notes WHERE date = ?", (d,)).fetchone()
            if existing:
                self._db.execute("UPDATE daily_notes SET content = ? WHERE date = ?",
                                 (existing["content"] + "\n" + content, d))
            else:
                self._db.execute("INSERT INTO daily_notes(date, content, created_at) VALUES(?,?,?)",
                                 (d, content, _now()))

    def get_daily_notes(self, limit: int = 7) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM daily_notes ORDER BY date DESC LIMIT ?", (limit,)).fetchall()]
