"""System observation and process control (FR-T1, FR-T2)."""

from __future__ import annotations

import asyncio
import os

import psutil
from pydantic import Field

from .. import telemetry
from .. import host
from ..policy import Tier
from .registry import Args, ToolError, tool


class NoArgs(Args):
    pass


@tool("system_status", "Current CPU, memory, GPU, disk, battery and network usage of this computer.",
      NoArgs, Tier.T0, summary=lambda a: "Read system status")
async def system_status(_: NoArgs) -> dict:
    return await asyncio.to_thread(telemetry.sample)


class ProcessListArgs(Args):
    sort_by: str = Field("memory", pattern="^(memory|cpu)$", description="memory or cpu")
    limit: int = Field(15, ge=1, le=50)
    name_contains: str | None = Field(None, max_length=80, description="Only processes whose name contains this text")


def _process_list(a: ProcessListArgs) -> list[dict]:
    rows = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
        info = p.info
        name = info.get("name") or ""
        if a.name_contains and a.name_contains.lower() not in name.lower():
            continue
        mem = info.get("memory_info")
        rows.append({"pid": info["pid"], "name": name, "memory_mb": round(mem.rss / 1048576, 1) if mem else 0.0,
                     "cpu_percent": info.get("cpu_percent") or 0.0})
    key = "memory_mb" if a.sort_by == "memory" else "cpu_percent"
    return sorted(rows, key=lambda r: r[key], reverse=True)[: a.limit]


@tool("process_list", "List running processes, sorted by memory or CPU.", ProcessListArgs, Tier.T0,
      summary=lambda a: f"List processes by {a.sort_by}")
async def process_list(a: ProcessListArgs) -> list[dict]:
    return await asyncio.to_thread(_process_list, a)


class ProcessKillArgs(Args):
    pid: int = Field(..., ge=5, description="Process id from process_list")


_PROTECTED = {"system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
              "lsass.exe", "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe", "memory compression",
              # macOS: ending these logs the user out or takes the desktop down
              "launchd", "kernel_task", "windowserver", "loginwindow", "finder", "dock", "systemuiserver",
              "securityd", "opendirectoryd", "coreservicesd", "cfprefsd", "tccd"}


def _kill(a: ProcessKillArgs) -> dict:
    if a.pid == os.getpid():
        raise ToolError("That is Jarvis itself. Close the window to exit.")
    try:
        proc = psutil.Process(a.pid)
        name = proc.name()
        if name.lower() in _PROTECTED:
            raise ToolError(f"{name} is a protected {host.NAME} process and will not be terminated.")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            proc.kill()
        return {"terminated": name, "pid": a.pid}
    except psutil.NoSuchProcess as exc:
        raise ToolError(f"No process with pid {a.pid}.") from exc
    except psutil.AccessDenied as exc:
        raise ToolError("Access denied — that process belongs to the system or another user.") from exc


@tool("process_kill", "Terminate a process by pid. Unsaved work in it is lost.", ProcessKillArgs, Tier.T2,
      summary=lambda a: f"Terminate process {a.pid}", sensitive=True)
async def process_kill(a: ProcessKillArgs) -> dict:
    return await asyncio.to_thread(_kill, a)
