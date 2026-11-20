"""PowerShell execution (FR-T6). Never elevated, always approved, bounded in time and output."""

from __future__ import annotations

import asyncio
import subprocess

from pydantic import Field

from ..policy import Tier, canonical, classify_shell, shell_refusal
from .registry import Args, ToolError, tool

OUTPUT_CAP = 16_000


class ShellArgs(Args):
    command: str = Field(..., min_length=1, max_length=4000, description="PowerShell command line")
    cwd: str | None = Field(None, max_length=400, description="Working directory (defaults to the user's home)")
    timeout_s: int = Field(60, ge=1, le=600)


def _classify(a: ShellArgs) -> tuple[Tier, str]:
    refusal = shell_refusal(a.command)
    if refusal:
        raise ToolError(refusal)
    return classify_shell(a.command)


def _short(a: ShellArgs) -> str:
    cmd = " ".join(a.command.split())
    return f"Run: {cmd[:120]}{'…' if len(cmd) > 120 else ''}"


async def _kill_tree(pid: int) -> None:
    killer = await asyncio.create_subprocess_exec(
        "taskkill.exe", "/PID", str(pid), "/T", "/F",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    await killer.wait()


@tool("shell_run",
      "Run a PowerShell command as the current (non-administrator) user and return its output. "
      "Use only when no dedicated tool fits.",
      ShellArgs, Tier.T2, taints=True, summary=_short, classify=_classify, sensitive=True)
async def shell_run(a: ShellArgs) -> dict:
    cwd = canonical(a.cwd or "~")
    if not cwd.is_dir():
        raise ToolError(f"Working directory {cwd} does not exist.")
    proc = await asyncio.create_subprocess_exec(
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", a.command,
        cwd=str(cwd), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=a.timeout_s)
    except TimeoutError as exc:
        await _kill_tree(proc.pid)
        raise ToolError(f"The command was stopped after {a.timeout_s} s.") from exc
    except asyncio.CancelledError:
        await asyncio.shield(_kill_tree(proc.pid))  # kill switch must not leave children behind (SEC-8)
        raise
    stdout = out.decode("utf-8", errors="replace")
    stderr = err.decode("utf-8", errors="replace")
    return {
        "exit_code": proc.returncode,
        "stdout": stdout[:OUTPUT_CAP], "stderr": stderr[:OUTPUT_CAP // 4],
        "truncated": len(stdout) > OUTPUT_CAP or len(stderr) > OUTPUT_CAP // 4,
    }
