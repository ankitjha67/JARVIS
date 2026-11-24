"""Security tools: lock screen, process watch, network check (ideas 79-82)."""

from __future__ import annotations

import asyncio

from pydantic import Field

from .. import host
from ..policy import Tier
from .registry import Args, ToolError, tool


class LockScreenArgs(Args):
    pass


@tool("security_lock", "Lock the screen immediately.",
      LockScreenArgs, Tier.T1, sensitive=True, summary=lambda a: "Lock screen")
async def security_lock(a: LockScreenArgs) -> dict:
    await asyncio.to_thread(host.lock_screen)
    return {"locked": True}


class ProcessListArgs(Args):
    filter: str = Field("", max_length=100, description="Filter by process name")


@tool("security_processes", "List running processes (filtered by name).",
      ProcessListArgs, Tier.T0, summary=lambda a: f"Processes: {a.filter or 'all'}")
async def security_processes(a: ProcessListArgs) -> dict:
    import psutil

    def _list() -> list[dict]:
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'memory_info']):
            try:
                info = p.info
                if a.filter and a.filter.lower() not in info['name'].lower():
                    continue
                procs.append({"pid": info['pid'], "name": info['name'],
                             "memory_mb": round(info['memory_info'].rss / 1048576, 1) if info['memory_info'] else 0})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        procs.sort(key=lambda x: x.get('memory_mb', 0), reverse=True)
        return procs

    procs = await asyncio.to_thread(_list)
    return {"processes": procs[:50], "total": len(procs)}


class NetworkCheckArgs(Args):
    pass


@tool("security_network", "Show active network connections.",
      NetworkCheckArgs, Tier.T0, summary=lambda a: "Network connections")
async def security_network(a: NetworkCheckArgs) -> dict:
    import psutil

    def _list() -> list[dict]:
        conns = []
        for c in psutil.net_connections(kind='inet'):
            if c.status == 'ESTABLISHED':
                try:
                    proc = psutil.Process(c.pid)
                    name = proc.name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    name = "unknown"
                conns.append({
                    "pid": c.pid, "process": name,
                    "local": f"{c.laddr.ip}:{c.laddr.port}",
                    "remote": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "",
                })
        return conns

    conns = await asyncio.to_thread(_list)
    return {"connections": conns[:50], "total": len(conns)}


class KillProcessArgs(Args):
    pid: int = Field(..., ge=1)
    force: bool = Field(False, description="Force kill (SIGKILL)")


@tool("security_kill", "Kill a process by PID.",
      KillProcessArgs, Tier.T2, sensitive=True, summary=lambda a: f"Kill PID {a.pid}")
async def security_kill(a: KillProcessArgs) -> dict:
    import psutil

    PROTECTED_PIDS = {0, 4} if host.IS_WINDOWS else {0, 1}

    if a.pid in PROTECTED_PIDS:
        raise ToolError(f"Cannot kill system process PID {a.pid}.")

    def _kill() -> dict:
        proc = psutil.Process(a.pid)
        name = proc.name()
        protected_names = {"system", "smss.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe",
                           "launchd", "kernel_task", "windowserver", "loginwindow", "securityd", "opendirectoryd"}
        if name.lower() in protected_names:
            raise ToolError(f"Cannot kill protected system process: {name}")
        if a.force:
            proc.kill()
        else:
            proc.terminate()
        return {"killed": True, "pid": a.pid, "name": name, "method": "kill" if a.force else "terminate"}

    try:
        return await asyncio.to_thread(_kill)
    except psutil.NoSuchProcess:
        raise ToolError(f"Process {a.pid} not found.")
    except psutil.AccessDenied:
        raise ToolError(f"Access denied to kill process {a.pid}. May need admin rights.")
