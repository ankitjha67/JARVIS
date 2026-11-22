"""Privacy Guardian: network activity monitor and privacy audit tools."""

from __future__ import annotations

import asyncio
import socket
from datetime import datetime, timezone

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

_DNS_LOG: list[dict] = []
_MAX_LOG = 500


# ---------------------------------------------------------------------------
# Network connections audit
# ---------------------------------------------------------------------------

class NetAuditArgs(Args):
    filter_established: bool = Field(False, description="Only show ESTABLISHED connections")
    filter_process: str = Field("", max_length=200, description="Filter by process name substring")


def _net_audit(a: NetAuditArgs) -> list[dict]:
    import psutil

    conns = psutil.net_connections(kind="inet")
    pid_name: dict[int, str] = {}
    results = []
    for c in conns:
        if a.filter_established and c.status != "ESTABLISHED":
            continue
        name = ""
        if c.pid:
            if c.pid not in pid_name:
                try:
                    pid_name[c.pid] = psutil.Process(c.pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pid_name[c.pid] = "?"
            name = pid_name[c.pid]
        if a.filter_process and a.filter_process.lower() not in name.lower():
            continue
        raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
        results.append({
            "pid": c.pid,
            "process": name,
            "local": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
            "remote": raddr,
            "status": c.status,
        })
    results.sort(key=lambda r: r["process"])
    return results[:200]


@tool("privacy_net_audit", "Audit active network connections: see which processes are talking to the internet.",
      NetAuditArgs, Tier.T0, summary=lambda a: "Network connection audit")
async def privacy_net_audit(a: NetAuditArgs) -> dict:
    conns = await asyncio.to_thread(_net_audit, a)
    unique_procs = len({c["process"] for c in conns if c["process"] != "?"})
    return {"connections": conns, "total": len(conns), "unique_processes": unique_procs}


# ---------------------------------------------------------------------------
# DNS lookup logger (resolve and log)
# ---------------------------------------------------------------------------

class DnsLookupArgs(Args):
    hostname: str = Field(..., min_length=1, max_length=253)


@tool("privacy_dns_lookup", "Resolve a hostname and log the result for the privacy audit trail.",
      DnsLookupArgs, Tier.T0, summary=lambda a: f"DNS lookup: {a.hostname}")
async def privacy_dns_lookup(a: DnsLookupArgs) -> dict:
    try:
        ips = await asyncio.to_thread(socket.getaddrinfo, a.hostname, None, socket.AF_UNSPEC)
    except socket.gaierror as exc:
        raise ToolError(f"DNS resolution failed for {a.hostname}: {exc}") from exc
    addresses = sorted({addr[4][0] for addr in ips})
    entry = {"hostname": a.hostname, "addresses": addresses, "ts": datetime.now(timezone.utc).isoformat()}
    _DNS_LOG.append(entry)
    if len(_DNS_LOG) > _MAX_LOG:
        _DNS_LOG.pop(0)
    return entry


# ---------------------------------------------------------------------------
# Listening ports
# ---------------------------------------------------------------------------

class ListeningArgs(Args):
    pass


def _listening_ports() -> list[dict]:
    import psutil

    conns = psutil.net_connections(kind="inet")
    listeners = []
    seen = set()
    for c in conns:
        if c.status != "LISTEN":
            continue
        key = (c.laddr.ip, c.laddr.port)
        if key in seen:
            continue
        seen.add(key)
        name = ""
        if c.pid:
            try:
                name = psutil.Process(c.pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        listeners.append({
            "address": c.laddr.ip,
            "port": c.laddr.port,
            "pid": c.pid,
            "process": name,
        })
    listeners.sort(key=lambda r: r["port"])
    return listeners


@tool("privacy_listening_ports", "List all listening (open) ports and which processes own them.",
      ListeningArgs, Tier.T0, summary=lambda a: "Listening ports scan")
async def privacy_listening_ports(a: ListeningArgs) -> dict:
    ports = await asyncio.to_thread(_listening_ports)
    return {"listeners": ports, "total": len(ports)}


# ---------------------------------------------------------------------------
# Startup programs audit
# ---------------------------------------------------------------------------

class StartupArgs(Args):
    pass


def _startup_programs() -> list[dict]:
    import winreg

    locations = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ]
    items = []
    for hive, path in locations:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
                items.append({
                    "name": name,
                    "command": value[:300] if isinstance(value, str) else str(value)[:300],
                    "scope": "user" if hive == winreg.HKEY_CURRENT_USER else "machine",
                })
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
    return items


@tool("privacy_startup_audit", "List programs that run at Windows startup (registry Run keys).",
      StartupArgs, Tier.T0, summary=lambda a: "Startup programs audit")
async def privacy_startup_audit(a: StartupArgs) -> dict:
    programs = await asyncio.to_thread(_startup_programs)
    return {"startup_programs": programs, "total": len(programs)}
