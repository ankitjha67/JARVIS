"""Developer tools: git status, error explain, project launcher (ideas 70-74)."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool


import re as _re

_SAFE_PATH = _re.compile(r'^[a-zA-Z]:[/\\]|^[./]')

def _validate_repo_path(path: str) -> str:
    p = Path(path).resolve()
    if not p.is_dir():
        raise ToolError(f"Not a directory: {path}")
    return str(p)


class GitStatusArgs(Args):
    path: str = Field(".", max_length=400, description="Repository path")


@tool("git_status", "Show git status of a repository.",
      GitStatusArgs, Tier.T0, summary=lambda a: f"Git status: {a.path[:40]}")
async def git_status(a: GitStatusArgs) -> dict:
    try:
        cwd = _validate_repo_path(a.path)
        result = await asyncio.to_thread(
            subprocess.run, ["git", "status", "--porcelain", "-b"],
            capture_output=True, text=True, timeout=10, cwd=cwd)
        return {"output": result.stdout[:3000], "returncode": result.returncode}
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"Git not available: {exc}")


class GitLogArgs(Args):
    path: str = Field(".", max_length=400)
    limit: int = Field(10, ge=1, le=50)


@tool("git_log", "Show recent git commits.",
      GitLogArgs, Tier.T0, summary=lambda a: f"Git log: {a.path[:40]}")
async def git_log(a: GitLogArgs) -> dict:
    try:
        cwd = _validate_repo_path(a.path)
        result = await asyncio.to_thread(
            subprocess.run, ["git", "log", f"--max-count={a.limit}", "--oneline", "--no-decorate"],
            capture_output=True, text=True, timeout=10, cwd=cwd)
        return {"commits": result.stdout[:3000], "returncode": result.returncode}
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"Git not available: {exc}")


class GitDiffArgs(Args):
    path: str = Field(".", max_length=400)
    staged: bool = Field(False, description="Show staged changes")


@tool("git_diff", "Show git diff (working tree or staged).",
      GitDiffArgs, Tier.T0, taints=True, summary=lambda a: f"Git diff: {a.path[:40]}")
async def git_diff(a: GitDiffArgs) -> dict:
    cmd = ["git", "diff"] + (["--staged"] if a.staged else [])
    try:
        cwd = _validate_repo_path(a.path)
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=10, cwd=cwd)
        return {"diff": result.stdout[:5000], "truncated": len(result.stdout) > 5000}
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"Git not available: {exc}")


class ErrorExplainArgs(Args):
    error: str = Field(..., min_length=1, max_length=3000)
    language: str = Field("python", max_length=30)


@tool("error_explain", "Explain an error message and suggest fixes.",
      ErrorExplainArgs, Tier.T0, summary=lambda a: f"Explain {a.language} error")
async def error_explain(a: ErrorExplainArgs) -> dict:
    return {"error": a.error, "language": a.language,
            "note": "Pass this to the model for a detailed explanation."}


class ProjectLaunchArgs(Args):
    path: str = Field(..., min_length=1, max_length=400)
    command: str = Field("", max_length=200, description="Command to run, or auto-detect")


@tool("project_launch", "Launch a project (auto-detects package.json, Cargo.toml, etc).",
      ProjectLaunchArgs, Tier.T1, summary=lambda a: f"Launch: {a.path[:40]}")
async def project_launch(a: ProjectLaunchArgs) -> dict:
    import re as _re
    p = Path(a.path).resolve()
    if not p.exists():
        raise ToolError(f"Path not found: {a.path}")
    if a.command:
        import shlex
        cmd_parts = shlex.split(a.command)
        _BLOCKED_CMDS = {"rm", "del", "format", "mkfs", "dd", "powershell", "cmd", "bash", "sh", "curl", "wget", "nc", "ncat"}
        if cmd_parts and cmd_parts[0].lower().split("\\")[-1].split("/")[-1].split(".")[0] in _BLOCKED_CMDS:
            raise ToolError(f"Blocked command: {cmd_parts[0]}")
    else:
        if (p / "package.json").exists():
            cmd_parts = ["npm", "start"]
        elif (p / "Cargo.toml").exists():
            cmd_parts = ["cargo", "run"]
        elif (p / "pyproject.toml").exists():
            safe_name = _re.sub(r"[^a-zA-Z0-9_-]", "", p.name)
            if not safe_name:
                raise ToolError("Cannot derive module name from project path.")
            cmd_parts = ["uv", "run", "python", "-m", safe_name]
        elif (p / "Makefile").exists():
            cmd_parts = ["make"]
        else:
            raise ToolError("Could not auto-detect project type. Provide a command.")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_parts, cwd=str(p), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        return {"launched": True, "pid": proc.pid, "command": " ".join(cmd_parts)}
    except Exception as exc:
        raise ToolError(f"Launch failed: {exc}")
