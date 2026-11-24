"""Incident Commander: system diagnostics, process inspection, and network monitoring."""

from __future__ import annotations

import asyncio
import subprocess

from pydantic import Field

from .. import host
from ..policy import Tier
from .registry import Args, ToolError, tool

_NO_WINDOW = host.NO_WINDOW


# ---------------------------------------------------------------------------
# System snapshot
# ---------------------------------------------------------------------------

class CaptureArgs(Args):
    event_log_count: int = Field(10, ge=1, le=50, description="Recent system log errors to include")


def _recent_errors(event_count: int) -> list[str]:
    if host.IS_MAC:
        return host.mac.recent_errors(event_count)
    try:
        result = subprocess.run(
            ["wevtutil", "qe", "System", "/c:" + str(event_count),
             "/rd:true", "/f:text", "/q:*[System[Level<=2]]"],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ["(the system event log is unavailable)"]
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()][:200]


def _capture(event_count: int) -> dict:
    import psutil

    cpu_percent = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(host.SYSTEM_DISK)

    top_procs = []
    for p in sorted(psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]),
                    key=lambda p: p.info.get("cpu_percent") or 0, reverse=True)[:10]:
        top_procs.append({
            "pid": p.info["pid"], "name": p.info["name"],
            "cpu_percent": p.info.get("cpu_percent"),
            "memory_percent": round(p.info.get("memory_percent") or 0, 1),
        })

    events = _recent_errors(event_count)

    return {
        "cpu_percent": cpu_percent,
        "memory": {"total_gb": round(mem.total / 1e9, 1), "used_percent": mem.percent,
                    "available_gb": round(mem.available / 1e9, 1)},
        "disk_c": {"total_gb": round(disk.total / 1e9, 1), "used_percent": disk.percent,
                   "free_gb": round(disk.free / 1e9, 1)},
        "top_processes": top_procs,
        "recent_errors": events[:100],
    }


@tool("incident_capture", "Capture a system state snapshot: CPU, memory, disk, top processes, recent event log errors.",
      CaptureArgs, Tier.T1, summary=lambda a: "System state snapshot")
async def incident_capture(a: CaptureArgs) -> dict:
    return await asyncio.to_thread(_capture, a.event_log_count)


# ---------------------------------------------------------------------------
# Process detail
# ---------------------------------------------------------------------------

class ProcessDetailArgs(Args):
    name: str = Field("", max_length=200, description="Process name (e.g. 'python.exe')")
    pid: int = Field(0, ge=0, description="Or: process ID")


def _process_detail(name: str, pid: int) -> dict:
    import psutil

    targets: list[psutil.Process] = []
    if pid:
        try:
            targets = [psutil.Process(pid)]
        except psutil.NoSuchProcess:
            raise ToolError(f"No process with PID {pid}.")
    elif name:
        targets = [p for p in psutil.process_iter(["name"]) if name.lower() in (p.info["name"] or "").lower()]
    else:
        raise ToolError("Provide a process name or PID.")

    if not targets:
        raise ToolError(f"No matching process found for '{name or pid}'.")

    results = []
    for p in targets[:5]:
        try:
            with p.oneshot():
                info = {
                    "pid": p.pid, "name": p.name(),
                    "cpu_percent": p.cpu_percent(interval=0.5),
                    "memory_mb": round(p.memory_info().rss / 1e6, 1),
                    "threads": p.num_threads(),
                    "status": p.status(),
                    "create_time": p.create_time(),
                    "cmdline": " ".join(p.cmdline()[:5]) if p.cmdline() else "",
                }
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            info = {"pid": p.pid, "name": getattr(p, "name", lambda: "?")(), "error": "access denied"}
        results.append(info)
    return {"processes": results, "count": len(results)}


@tool("incident_process_detail", "Get detailed info about a process by name or PID.",
      ProcessDetailArgs, Tier.T0, summary=lambda a: f"Process: {a.name or a.pid}")
async def incident_process_detail(a: ProcessDetailArgs) -> dict:
    return await asyncio.to_thread(_process_detail, a.name, a.pid)


# ---------------------------------------------------------------------------
# Network connections
# ---------------------------------------------------------------------------

class NetworkArgs(Args):
    filter_port: int = Field(0, ge=0, le=65535, description="Filter by port (0 = all)")
    filter_status: str = Field("", max_length=20, description="Filter by status (ESTABLISHED, LISTEN, etc.)")


def _network(port: int, status_filter: str) -> dict:
    import psutil

    conns = psutil.net_connections(kind="inet")
    results = []
    for c in conns:
        if port and not (c.laddr and c.laddr.port == port) and not (c.raddr and c.raddr.port == port):
            continue
        if status_filter and c.status.lower() != status_filter.lower():
            continue
        entry = {
            "local": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
            "remote": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "",
            "status": c.status, "pid": c.pid,
        }
        if c.pid:
            try:
                entry["process"] = psutil.Process(c.pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                entry["process"] = "?"
        results.append(entry)
        if len(results) >= 50:
            break
    return {"connections": results, "count": len(results)}


@tool("incident_network", "Show active network connections with process info.",
      NetworkArgs, Tier.T0, summary=lambda a: "Network connections")
async def incident_network(a: NetworkArgs) -> dict:
    return await asyncio.to_thread(_network, a.filter_port, a.filter_status)


# ---------------------------------------------------------------------------
# Windows service status
# ---------------------------------------------------------------------------

class ServiceStatusArgs(Args):
    name: str = Field(..., min_length=1, max_length=200, description=(
        "launchd service label, e.g. com.apple.Finder" if host.IS_MAC else "Windows service name"))


async def _service_status(name: str) -> dict:
    if host.IS_MAC:
        return await asyncio.to_thread(host.mac.service_status, name)
    if not host.IS_WINDOWS:
        raise host.unsupported("Service status")
    proc = await asyncio.create_subprocess_exec(
        "sc", "query", name,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=_NO_WINDOW,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=10)
    text = out.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise ToolError(f"Service '{name}' not found or access denied.")

    state = "UNKNOWN"
    for line in text.splitlines():
        if "STATE" in line:
            parts = line.split()
            state = parts[-1] if parts else state
            break
    return {"service": name, "state": state, "raw": text.strip()[:1000]}


@tool("incident_service_status",
      "Check the status of a launchd service." if host.IS_MAC else "Check the status of a Windows service.",
      ServiceStatusArgs, Tier.T0, summary=lambda a: f"Service: {a.name}")
async def incident_service_status(a: ServiceStatusArgs) -> dict:
    return await _service_status(a.name)
