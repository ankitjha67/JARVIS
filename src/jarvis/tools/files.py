"""File tools (FR-T4, FR-T5). Sensitive paths are refused for every operation (SEC-6)."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from .. import host
from ..policy import Tier, canonical, is_executable_file, is_sensitive_path, is_system_path
from .registry import Args, ToolError, tool

READ_CAP = 64 * 1024
WRITE_CAP = 1024 * 1024
_SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "$recycle.bin", "system volume information",
              "appdata", "windows", ".cache"} | ({"library", ".trash"} if host.IS_MAC else set())


def _guard(raw: str) -> Path:
    path = canonical(raw)
    if is_sensitive_path(path):
        raise ToolError("That location holds credentials or private keys and is off limits to Jarvis.")
    return path


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class ListArgs(Args):
    path: str = Field("~", max_length=400, description="Folder path. ~ is the user's home folder.")
    limit: int = Field(100, ge=1, le=500)


def _list(a: ListArgs) -> dict:
    path = _guard(a.path)
    if not path.is_dir():
        raise ToolError(f"{path} is not a folder.")
    entries = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    st = e.stat()
                except OSError:
                    continue
                entries.append({"name": e.name, "type": "dir" if e.is_dir() else "file",
                                "size": st.st_size if e.is_file() else None, "modified": _iso(st.st_mtime)})
    except PermissionError as exc:
        raise ToolError(f"Access denied to {path}.") from exc
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return {"path": str(path), "entries": entries[: a.limit], "total": len(entries)}


@tool("files_list", "List the contents of a folder.", ListArgs, Tier.T0, summary=lambda a: f"List {a.path}")
async def files_list(a: ListArgs) -> dict:
    return await asyncio.to_thread(_list, a)


class SearchArgs(Args):
    query: str = Field(..., min_length=1, max_length=120,
                       description="File name text or glob, e.g. 'resume', '*.pdf', 'budget*.xlsx'")
    root: str = Field("~", max_length=400, description="Folder to search under")
    limit: int = Field(20, ge=1, le=100)


def _search(a: SearchArgs) -> dict:
    root = _guard(a.root)
    if not root.is_dir():
        raise ToolError(f"{root} is not a folder.")
    pattern = a.query.lower()
    is_glob = any(c in pattern for c in "*?[")
    deadline = time.monotonic() + 8.0
    hits: list[tuple[float, str, int]] = []
    scanned = 0
    timed_out = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            scanned += 1
            low = name.lower()
            if (fnmatch.fnmatch(low, pattern) if is_glob else pattern in low):
                full = Path(dirpath) / name
                if is_sensitive_path(full):
                    continue
                try:
                    st = full.stat()
                except OSError:
                    continue
                hits.append((st.st_mtime, str(full), st.st_size))
        if time.monotonic() > deadline or scanned > 300_000:
            timed_out = True
            break
    hits.sort(reverse=True)
    return {
        "matches": [{"path": p, "modified": _iso(m), "size": s} for m, p, s in hits[: a.limit]],
        "total_found": len(hits), "files_scanned": scanned, "incomplete": timed_out,
    }


@tool("files_search", "Find files by name under a folder, newest first.", SearchArgs, Tier.T0,
      summary=lambda a: f"Search {a.root} for “{a.query}”")
async def files_search(a: SearchArgs) -> dict:
    return await asyncio.to_thread(_search, a)


class ReadArgs(Args):
    path: str = Field(..., min_length=1, max_length=400)


def _read(a: ReadArgs) -> dict:
    path = _guard(a.path)
    if not path.is_file():
        raise ToolError(f"{path} is not a file.")
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            data = fh.read(READ_CAP)
    except PermissionError as exc:
        raise ToolError(f"Access denied to {path}.") from exc
    if b"\x00" in data[:4096]:
        raise ToolError("That is a binary file; only text files can be read.")
    return {"path": str(path), "size": size, "truncated": size > READ_CAP,
            "text": data.decode("utf-8", errors="replace")}


@tool("files_read", "Read a text file (first 64 KB).", ReadArgs, Tier.T0, taints=True,
      summary=lambda a: f"Read {a.path}")
async def files_read(a: ReadArgs) -> dict:
    return await asyncio.to_thread(_read, a)


class WriteArgs(Args):
    path: str = Field(..., min_length=1, max_length=400)
    content: str = Field(..., max_length=WRITE_CAP)
    overwrite: bool = Field(False, description="Must be true to replace an existing file")


def _classify_write(a: WriteArgs) -> tuple[Tier, str]:
    path = _guard(a.path)
    if is_system_path(path):
        raise ToolError("Jarvis does not write inside Windows or Program Files.")
    if path.exists():
        if not a.overwrite:
            raise ToolError(f"{path} already exists. Set overwrite to true to replace it.")
        return Tier.T3, f"Overwrites the existing file {path}. The previous contents cannot be recovered."
    if is_executable_file(path):
        return Tier.T3, f"Creates an executable or script file ({path.suffix})."
    return Tier.T2, f"Creates the file {path}."


def _write(a: WriteArgs) -> dict:
    path = _guard(a.path)
    if not path.parent.is_dir():
        raise ToolError(f"The folder {path.parent} does not exist.")
    try:
        path.write_text(a.content, encoding="utf-8", newline="")
    except PermissionError as exc:
        raise ToolError(f"Access denied writing {path}.") from exc
    return {"written": str(path), "bytes": len(a.content.encode("utf-8"))}


@tool("files_write", "Create a text file, or replace one when overwrite is true.", WriteArgs, Tier.T2,
      summary=lambda a: f"Write {a.path}", classify=_classify_write, sensitive=True)
async def files_write(a: WriteArgs) -> dict:
    return await asyncio.to_thread(_write, a)


class OpenArgs(Args):
    path: str = Field(..., min_length=1, max_length=400, description="File or folder to open with its default app")


def _classify_open(a: OpenArgs) -> tuple[Tier, str]:
    path = _guard(a.path)
    if path.is_file() and is_executable_file(path):
        return Tier.T2, f"Opening {path.name} runs code on your machine."
    return Tier.T1, ""


def _open(a: OpenArgs) -> dict:
    path = _guard(a.path)
    if not path.exists():
        raise ToolError(f"{path} does not exist.")
    try:
        host.open_path(path)
    except OSError as exc:
        raise ToolError(f"{host.NAME} could not open {path.name}: {exc.strerror or 'no associated app'}.") from exc
    return {"opened": str(path)}


@tool("files_open", "Open a file or folder with its default application.", OpenArgs, Tier.T1,
      summary=lambda a: f"Open {a.path}", classify=_classify_open)
async def files_open(a: OpenArgs) -> dict:
    return await asyncio.to_thread(_open, a)
