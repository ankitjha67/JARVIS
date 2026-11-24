"""Sentinel Watchers: create condition monitors that trigger on system events."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import urllib.request
from typing import Any

import psutil
from pydantic import Field

from .. import host
from ..policy import Tier
from .registry import Args, ToolError, tool

log = logging.getLogger(__name__)

_VALID_CONDITIONS = (
    "cpu_above", "memory_above", "disk_above",
    "process_running", "process_stopped",
    "file_changed", "url_reachable",
)

_watchers: dict[str, dict[str, Any]] = {}


def _file_hash(path: str) -> str | None:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _check_one(w: dict[str, Any]) -> dict[str, Any]:
    cond = w["condition"]
    target = w["target"]
    threshold = w.get("threshold", 0)
    triggered = False
    value: Any = None

    if cond == "cpu_above":
        value = psutil.cpu_percent(interval=0.5)
        triggered = value > threshold
    elif cond == "memory_above":
        value = psutil.virtual_memory().percent
        triggered = value > threshold
    elif cond == "disk_above":
        disk_path = target or host.SYSTEM_DISK
        value = psutil.disk_usage(disk_path).percent
        triggered = value > threshold
    elif cond == "process_running":
        value = any(p.info["name"] and target.lower() in p.info["name"].lower()
                     for p in psutil.process_iter(["name"]))
        triggered = value
    elif cond == "process_stopped":
        value = not any(p.info["name"] and target.lower() in p.info["name"].lower()
                        for p in psutil.process_iter(["name"]))
        triggered = value
    elif cond == "file_changed":
        current = _file_hash(target)
        previous = w.get("_last_hash")
        if previous is None:
            w["_last_hash"] = current
            value = "baseline recorded"
            triggered = False
        else:
            triggered = current != previous
            value = f"changed (was {previous[:8]}, now {current[:8] if current else 'gone'})" if triggered else "unchanged"
            if triggered:
                w["_last_hash"] = current
    elif cond == "url_reachable":
        try:
            req = urllib.request.Request(target, method="HEAD", headers={"User-Agent": "Jarvis/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                value = resp.status
                triggered = 200 <= resp.status < 400
        except Exception as exc:
            value = str(exc)[:100]
            triggered = False

    return {"triggered": triggered, "value": value, "checked_at": time.time()}


# ---------------------------------------------------------------------------
# sentinel_create
# ---------------------------------------------------------------------------

class SentinelCreateArgs(Args):
    name: str = Field(..., min_length=1, max_length=60, description="Unique watcher name.")
    condition: str = Field(
        ..., description="One of: cpu_above, memory_above, disk_above, process_running, process_stopped, file_changed, url_reachable",
    )
    threshold: float = Field(0, description="Numeric threshold (for cpu/memory/disk conditions).")
    target: str = Field("", max_length=500, description="Target: process name, file path, URL, or disk path.")


@tool(
    "sentinel_create",
    "Create a named condition watcher. Supported: cpu_above, memory_above, disk_above, "
    "process_running, process_stopped, file_changed, url_reachable.",
    SentinelCreateArgs, Tier.T1, sensitive=True,
    summary=lambda a: f"Create watcher: {a.name}",
)
async def sentinel_create(a: SentinelCreateArgs) -> dict:
    if a.condition not in _VALID_CONDITIONS:
        raise ToolError(f"Invalid condition '{a.condition}'. Must be one of: {', '.join(_VALID_CONDITIONS)}")
    if a.name in _watchers:
        raise ToolError(f"Watcher '{a.name}' already exists. Remove it first.")
    _watchers[a.name] = {
        "condition": a.condition,
        "threshold": a.threshold,
        "target": a.target,
        "created_at": time.time(),
        "last_check": None,
    }
    return {"created": a.name, "condition": a.condition, "total_watchers": len(_watchers)}


# ---------------------------------------------------------------------------
# sentinel_list
# ---------------------------------------------------------------------------

class SentinelListArgs(Args):
    pass


@tool(
    "sentinel_list",
    "List all active sentinel watchers and their last-checked status.",
    SentinelListArgs, Tier.T0,
    summary=lambda a: "List watchers",
)
async def sentinel_list(a: SentinelListArgs) -> dict:
    out = []
    for name, w in _watchers.items():
        entry = {
            "name": name,
            "condition": w["condition"],
            "threshold": w.get("threshold"),
            "target": w.get("target"),
            "last_check": w.get("last_check"),
        }
        out.append(entry)
    return {"watchers": out, "count": len(out)}


# ---------------------------------------------------------------------------
# sentinel_check
# ---------------------------------------------------------------------------

class SentinelCheckArgs(Args):
    name: str = Field("", max_length=60, description="Check a specific watcher, or leave empty to check all.")


@tool(
    "sentinel_check",
    "Run all (or one) sentinel watchers now and return which ones triggered.",
    SentinelCheckArgs, Tier.T1,
    summary=lambda a: f"Check watcher{': ' + a.name if a.name else 's'}",
)
async def sentinel_check(a: SentinelCheckArgs) -> dict:
    if not _watchers:
        raise ToolError("No watchers defined. Use sentinel_create first.")
    targets = {a.name: _watchers[a.name]} if a.name else dict(_watchers)
    if a.name and a.name not in _watchers:
        raise ToolError(f"Watcher '{a.name}' not found.")

    results = []
    for name, w in targets.items():
        result = await asyncio.to_thread(_check_one, w)
        w["last_check"] = result
        results.append({"name": name, **result})

    triggered = [r for r in results if r["triggered"]]
    return {"results": results, "triggered": triggered, "triggered_count": len(triggered)}


# ---------------------------------------------------------------------------
# sentinel_remove
# ---------------------------------------------------------------------------

class SentinelRemoveArgs(Args):
    name: str = Field(..., min_length=1, max_length=60)


@tool(
    "sentinel_remove",
    "Remove a sentinel watcher by name.",
    SentinelRemoveArgs, Tier.T0,
    summary=lambda a: f"Remove watcher: {a.name}",
)
async def sentinel_remove(a: SentinelRemoveArgs) -> dict:
    if a.name not in _watchers:
        raise ToolError(f"Watcher '{a.name}' not found.")
    del _watchers[a.name]
    return {"removed": a.name, "remaining": len(_watchers)}
