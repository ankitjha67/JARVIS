"""Instant file and content search (idea 56)."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from pydantic import Field

from .. import host
from ..policy import Tier
from .registry import Args, ToolError, tool

_SEARCH_PATHS = [
    Path.home(),
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
]


def _run_ps(cmd: str, timeout: int = 15) -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return r.stdout.strip()


def _search_everything(query: str, max_results: int) -> list[dict] | None:
    """Try Everything CLI (es.exe) first -- fastest option."""
    try:
        r = subprocess.run(
            ["es.exe", "-n", str(max_results), query],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            results = []
            for line in r.stdout.strip().splitlines()[:max_results]:
                p = Path(line.strip())
                try:
                    stat = p.stat()
                    results.append({
                        "path": str(p),
                        "name": p.name,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                    })
                except OSError:
                    results.append({"path": str(p), "name": p.name})
            return results
    except FileNotFoundError:
        return None
    except Exception:
        return None
    return None


def _search_windows(query: str, max_results: int, search_path: str) -> list[dict]:
    """Fallback: use PowerShell Get-ChildItem."""
    escaped = query.replace("'", "''")
    path = search_path.replace("'", "''") if search_path else str(Path.home())
    cmd = (
        f"Get-ChildItem -Path '{path}' -Recurse -Filter '*{escaped}*' "
        f"-ErrorAction SilentlyContinue | "
        f"Select-Object -First {max_results} FullName, Length, LastWriteTime | "
        f"ConvertTo-Json -Compress"
    )
    out = _run_ps(cmd)
    if not out:
        return []
    import json
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        return [
            {
                "path": item.get("FullName", ""),
                "name": Path(item.get("FullName", "")).name,
                "size": item.get("Length", 0),
                "modified": item.get("LastWriteTime", ""),
            }
            for item in data
        ]
    except json.JSONDecodeError:
        return []


class FileSearchArgs(Args):
    query: str = Field(..., min_length=1, max_length=200, description="Filename pattern to search for")
    path: str = Field("", max_length=500, description="Directory to search in (defaults to user home)")
    max_results: int = Field(20, ge=1, le=50)


def _search_spotlight(query: str, max_results: int, search_path: str) -> list[dict]:
    results = []
    for line in host.mac.find_files(query, max_results, search_path):
        p = Path(line)
        try:
            stat = p.stat()
            results.append({"path": str(p), "name": p.name, "size": stat.st_size, "modified": stat.st_mtime})
        except OSError:
            results.append({"path": str(p), "name": p.name})
    return results


def _grep(pattern: str, search_path: str, file_filter: str, max_results: int) -> list[dict]:
    """Content search with the system grep (macOS); one hit per file, like Select-String -List."""
    includes = [f"--include={ext.strip()}" for ext in file_filter.split(",") if ext.strip()]
    argv = ["/usr/bin/grep", "-rnIE", "-m", "1", *includes, "-e", pattern, "--", search_path]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=20, check=False).stdout
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
    except OSError as exc:
        raise ToolError("The system grep could not be started.") from exc
    results = []
    for line in out.splitlines():
        path, _, rest = line.partition(":")
        number, _, text = rest.partition(":")
        if number.isdigit():
            results.append({"path": path, "line_number": int(number), "line": text.strip()[:200]})
        if len(results) >= max_results:
            break
    return results


_FIND_HELP = ("Search for files by name across the system (Spotlight)." if host.IS_MAC else
              "Search for files by name across the system. Uses Everything if installed, falls back to Windows Search.")


@tool("file_find", _FIND_HELP,
      FileSearchArgs, Tier.T0, summary=lambda a: f"Search: {a.query[:40]}")
async def file_search(a: FileSearchArgs) -> dict:
    if host.IS_MAC:
        results = await asyncio.to_thread(_search_spotlight, a.query, a.max_results, a.path)
        return {"results": results, "count": len(results)}
    if not host.IS_WINDOWS:
        raise host.unsupported("System-wide file search")
    results = await asyncio.to_thread(_search_everything, a.query, a.max_results)
    if results is None:
        results = await asyncio.to_thread(_search_windows, a.query, a.max_results, a.path)
    return {"results": results, "count": len(results)}


class ContentSearchArgs(Args):
    pattern: str = Field(..., min_length=1, max_length=200, description="Text pattern to search for in file contents")
    path: str = Field("", max_length=500, description="Directory to search in")
    file_filter: str = Field("*.txt,*.py,*.md,*.json,*.csv,*.log", max_length=200,
                             description="Comma-separated file extensions to search")
    max_results: int = Field(20, ge=1, le=50)


@tool("content_search", "Search inside files for text content (like grep).",
      ContentSearchArgs, Tier.T0, summary=lambda a: f"Grep: {a.pattern[:40]}")
async def content_search(a: ContentSearchArgs) -> dict:
    search_path = a.path or str(Path.home())
    if not host.IS_WINDOWS:
        results = await asyncio.to_thread(_grep, a.pattern, os.path.expanduser(search_path), a.file_filter,
                                          a.max_results)
        return {"results": results, "count": len(results)}
    escaped = a.pattern.replace("'", "''")
    includes = " ".join(f"-Include '{ext.strip()}'" for ext in a.file_filter.split(","))
    cmd = (
        f"Get-ChildItem -Path '{search_path}' -Recurse {includes} "
        f"-ErrorAction SilentlyContinue | "
        f"Select-String -Pattern '{escaped}' -List -ErrorAction SilentlyContinue | "
        f"Select-Object -First {a.max_results} Path, LineNumber, Line | "
        f"ConvertTo-Json -Compress"
    )
    out = await asyncio.to_thread(_run_ps, cmd)
    if not out:
        return {"results": [], "count": 0}
    import json
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        results = [
            {
                "path": item.get("Path", ""),
                "line_number": item.get("LineNumber", 0),
                "line": str(item.get("Line", ""))[:200],
            }
            for item in data
        ]
        return {"results": results, "count": len(results)}
    except json.JSONDecodeError:
        return {"results": [], "count": 0}
