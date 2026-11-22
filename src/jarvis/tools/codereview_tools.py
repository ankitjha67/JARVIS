"""Code Review Copilot: git diff analysis, blame, log, and change stats."""

from __future__ import annotations

import asyncio
import os
import subprocess

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

_NO_WINDOW = subprocess.CREATE_NO_WINDOW


async def _git(args: list[str], cwd: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=_NO_WINDOW,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        raise ToolError(f"git {args[0]} failed: {err.decode('utf-8', errors='replace')[:400]}")
    return out.decode("utf-8", errors="replace")


def _resolve_cwd(directory: str) -> str:
    d = directory or os.getcwd()
    if not os.path.isdir(d):
        raise ToolError(f"Directory does not exist: {d}")
    return d


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

class DiffArgs(Args):
    directory: str = Field("", max_length=500, description="Repo path (default: cwd)")
    staged: bool = Field(False, description="Show staged changes only")
    path: str = Field("", max_length=500, description="Limit diff to a specific file or directory")


@tool("codereview_diff", "Show git diff (working tree or staged) for a repository.",
      DiffArgs, Tier.T0, summary=lambda a: f"Git diff{'(staged)' if a.staged else ''}")
async def codereview_diff(a: DiffArgs) -> dict:
    cwd = _resolve_cwd(a.directory)
    cmd = ["diff"]
    if a.staged:
        cmd.append("--staged")
    if a.path:
        cmd += ["--", a.path]
    text = await _git(cmd, cwd)
    if not text.strip():
        return {"diff": "", "message": "No changes."}
    capped = text[:15000]
    return {"diff": capped, "truncated": len(text) > 15000}


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------

class LogArgs(Args):
    directory: str = Field("", max_length=500, description="Repo path (default: cwd)")
    count: int = Field(15, ge=1, le=100, description="Number of commits")
    author: str = Field("", max_length=100, description="Filter by author")


@tool("codereview_log", "Show recent git commit log.",
      LogArgs, Tier.T0, summary=lambda a: f"Git log (last {a.count})")
async def codereview_log(a: LogArgs) -> dict:
    cwd = _resolve_cwd(a.directory)
    cmd = ["log", f"--oneline", f"-n{a.count}", "--format=%h %as %an | %s"]
    if a.author:
        cmd.append(f"--author={a.author}")
    text = await _git(cmd, cwd)
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return {"commits": lines, "count": len(lines)}


# ---------------------------------------------------------------------------
# Blame
# ---------------------------------------------------------------------------

class BlameArgs(Args):
    file: str = Field(..., min_length=1, max_length=500, description="File path relative to repo root")
    directory: str = Field("", max_length=500, description="Repo path (default: cwd)")
    start_line: int = Field(0, ge=0, description="Start line (0 = from beginning)")
    end_line: int = Field(0, ge=0, description="End line (0 = to end)")


@tool("codereview_blame", "Run git blame on a file, optionally for a line range.",
      BlameArgs, Tier.T0, summary=lambda a: f"Blame: {a.file}")
async def codereview_blame(a: BlameArgs) -> dict:
    cwd = _resolve_cwd(a.directory)
    cmd = ["blame", "--porcelain"]
    if a.start_line and a.end_line:
        cmd += [f"-L{a.start_line},{a.end_line}"]
    elif a.start_line:
        cmd += [f"-L{a.start_line},+50"]
    cmd.append(a.file)
    text = await _git(cmd, cwd)
    return {"blame": text[:12000], "truncated": len(text) > 12000}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class StatsArgs(Args):
    directory: str = Field("", max_length=500, description="Repo path (default: cwd)")
    staged: bool = Field(False, description="Stats for staged changes only")
    ref: str = Field("", max_length=100, description="Compare against a ref (branch, tag, commit)")


@tool("codereview_stats", "Show file-level change statistics (git diff --stat).",
      StatsArgs, Tier.T0, summary=lambda a: "Git diff stats")
async def codereview_stats(a: StatsArgs) -> dict:
    cwd = _resolve_cwd(a.directory)
    cmd = ["diff", "--stat"]
    if a.staged:
        cmd.append("--staged")
    if a.ref:
        cmd.append(a.ref)
    text = await _git(cmd, cwd)
    return {"stats": text.strip() or "No changes.", "truncated": False}
