"""Real machine sensors: psutil for CPU/RAM/disk/net/battery, NVML for the NVIDIA GPU."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import psutil

log = logging.getLogger(__name__)

_lock = threading.Lock()
_nvml: Any = None
_nvml_handle: Any = None
_nvml_failed = False
_last_net: tuple[float, int, int] | None = None
_top_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
_TOP_EVERY_S = 3.0
_cpu_count = psutil.cpu_count() or 1


def _top_processes() -> list[dict[str, Any]]:
    """Heaviest applications, grouped by executable name. Refreshed every few seconds (it walks all processes)."""
    global _top_cache
    now = time.monotonic()
    if now - _top_cache[0] < _TOP_EVERY_S:
        return _top_cache[1]
    groups: dict[str, list[float]] = {}
    try:
        procs = list(psutil.process_iter(["name", "memory_info", "cpu_percent"]))
    except (MemoryError, OSError, psutil.Error):
        # Seen live when Windows ran out of commit: keep the previous reading rather than fail telemetry.
        _top_cache = (now, _top_cache[1])
        return _top_cache[1]
    for proc in procs:
        info = proc.info
        name = (info.get("name") or "").removesuffix(".exe")
        mem = info.get("memory_info")
        if not name or mem is None:
            continue
        slot = groups.setdefault(name, [0.0, 0.0, 0.0])
        slot[0] += mem.rss / 1048576
        slot[1] += (info.get("cpu_percent") or 0.0) / _cpu_count
        slot[2] += 1
    top = sorted(groups.items(), key=lambda kv: kv[1][0], reverse=True)[:8]
    result = [{"name": n, "mem_mb": round(v[0]), "cpu_percent": round(min(v[1], 100.0), 1), "count": int(v[2])}
              for n, v in top]
    _top_cache = (now, result)
    return result


def _gpu() -> dict[str, Any] | None:
    global _nvml, _nvml_handle, _nvml_failed
    if _nvml_failed:
        return None
    try:
        if _nvml is None:
            import pynvml

            pynvml.nvmlInit()
            _nvml = pynvml
            _nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = _nvml.nvmlDeviceGetUtilizationRates(_nvml_handle)
        mem = _nvml.nvmlDeviceGetMemoryInfo(_nvml_handle)
        name = _nvml.nvmlDeviceGetName(_nvml_handle)
        try:
            temp: int | None = int(_nvml.nvmlDeviceGetTemperature(_nvml_handle, _nvml.NVML_TEMPERATURE_GPU))
        except Exception:  # noqa: BLE001 - temperature is optional on some drivers
            temp = None
        return {
            "name": name.decode() if isinstance(name, bytes) else str(name),
            "percent": float(util.gpu),
            "vram_used_gb": round(mem.used / 1073741824, 2),
            "vram_total_gb": round(mem.total / 1073741824, 2),
            "temp_c": temp,
        }
    except Exception as exc:  # noqa: BLE001 - no NVIDIA GPU / driver is a normal condition
        log.info("GPU telemetry unavailable: %s", type(exc).__name__)
        _nvml_failed = True
        return None


def sample() -> dict[str, Any]:
    """One snapshot. Blocking but fast (<5 ms); call from a worker thread."""
    global _last_net
    with _lock:
        vm = psutil.virtual_memory()
        disks = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            disks.append({"mount": part.mountpoint.rstrip("\\"), "free_gb": round(u.free / 1073741824, 1),
                          "total_gb": round(u.total / 1073741824, 1), "percent": u.percent})
        now = time.monotonic()
        io = psutil.net_io_counters()
        down = up = 0.0
        if _last_net:
            dt = max(now - _last_net[0], 1e-3)
            down = max(io.bytes_recv - _last_net[1], 0) / dt
            up = max(io.bytes_sent - _last_net[2], 0) / dt
        _last_net = (now, io.bytes_recv, io.bytes_sent)
        batt = psutil.sensors_battery()
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "ram_percent": vm.percent,
            "ram_used_gb": round((vm.total - vm.available) / 1073741824, 1),
            "ram_total_gb": round(vm.total / 1073741824, 1),
            "gpu": _gpu(),
            "disks": disks,
            "net_down_kbps": round(down * 8 / 1000, 1),
            "net_up_kbps": round(up * 8 / 1000, 1),
            "battery": {"percent": batt.percent, "plugged": bool(batt.power_plugged)} if batt else None,
            "top": _top_processes(),
        }
