"""God's Eye View: 3D geospatial intelligence globe with live data feeds.

Wraps https://github.com/bilawalsidhu/gods-eye-view — a CesiumJS-based globe that
provides real-time tracking of flights, ships, satellites, earthquakes, fires, weather,
CCTV cameras, military installations, and more.  The heavy lifting is done by the
project's own MCP server (~30 tools), which Jarvis connects to via the MCP catalogue.
This module handles the lifecycle: clone, install, start/stop the dev server.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from pydantic import Field

from .. import host
from ..config import DATA_DIR
from ..policy import Tier
from .registry import Args, ToolError, tool

log = logging.getLogger(__name__)

_REPO_URL = "https://github.com/bilawalsidhu/gods-eye-view.git"
_DEFAULT_DIR_NAME = "gods-eye-view"
_DEV_PORT = 4173
_NO_WINDOW = host.NO_WINDOW

# Module-level handle so we can stop it later.
_server_proc: asyncio.subprocess.Process | None = None


def _get_install_dir(settings: dict[str, Any] | None = None) -> Path:
    """Return the God's Eye View installation directory from settings or the default."""
    if settings and settings.get("gods_eye_dir"):
        return Path(settings["gods_eye_dir"])
    return DATA_DIR / _DEFAULT_DIR_NAME


# ---------------------------------------------------------------------------
# Setup / install
# ---------------------------------------------------------------------------

class GodsEyeSetupArgs(Args):
    install_dir: str = Field(
        "",
        max_length=400,
        description="Where to clone the repo. Leave empty for the default (data/gods-eye-view).",
    )


@tool(
    "gods_eye_setup",
    "Clone and install God's Eye View (3D geospatial intelligence globe). "
    "Downloads the repo and runs npm ci. Run once before using gods_eye_start.",
    GodsEyeSetupArgs, Tier.T2, sensitive=True,
    summary=lambda a: "Install God's Eye View",
)
async def gods_eye_setup(a: GodsEyeSetupArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]

    settings = await asyncio.to_thread(hub.store.get_settings)
    target = Path(a.install_dir) if a.install_dir else _get_install_dir(settings)
    target = target.resolve()

    if (target / "package.json").exists():
        needs_install = not (target / "node_modules").exists()
        if not needs_install:
            return {"already_installed": True, "path": str(target)}
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", _REPO_URL, str(target),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            raise ToolError(f"git clone failed: {err.decode('utf-8', errors='replace')[:400]}")

    npm = host.npm()
    proc = await asyncio.create_subprocess_exec(
        npm, "ci",
        cwd=str(target),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=_NO_WINDOW,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=300)
    if proc.returncode != 0:
        raise ToolError(f"npm ci failed: {err.decode('utf-8', errors='replace')[:400]}")

    await asyncio.to_thread(hub.store.update_settings, {"gods_eye_dir": str(target)})

    return {"installed": True, "path": str(target), "next": "Run gods_eye_start to launch the dev server, then mcp_install gods-eye-view to connect the tools."}


# ---------------------------------------------------------------------------
# Start / stop the dev server
# ---------------------------------------------------------------------------

class GodsEyeStartArgs(Args):
    pass


@tool(
    "gods_eye_start",
    "Start the God's Eye View dev server on port 4173. "
    "Required before using any God's Eye View MCP tools.",
    GodsEyeStartArgs, Tier.T2, sensitive=True,
    summary=lambda a: "Start God's Eye View server",
)
async def gods_eye_start(a: GodsEyeStartArgs) -> dict:
    global _server_proc
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    settings = await asyncio.to_thread(hub.store.get_settings)
    target = _get_install_dir(settings)

    if not (target / "package.json").exists():
        raise ToolError("God's Eye View is not installed. Run gods_eye_setup first.")

    if _server_proc is not None and _server_proc.returncode is None:
        return {"already_running": True, "port": _DEV_PORT}

    npm = host.npm()
    _server_proc = await asyncio.create_subprocess_exec(
        npm, "run", "dev",
        cwd=str(target),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **host.child_kwargs(),
    )

    # Wait briefly for the server to start listening.
    for _ in range(20):
        await asyncio.sleep(0.5)
        if _server_proc.returncode is not None:
            err = (await _server_proc.stderr.read(2000)).decode("utf-8", errors="replace") if _server_proc.stderr else ""
            raise ToolError(f"Dev server exited immediately: {err[:400]}")
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", _DEV_PORT), timeout=1.0,
            )
            writer.close()
            await writer.wait_closed()
            return {"started": True, "port": _DEV_PORT, "url": f"http://localhost:{_DEV_PORT}",
                    "next": "Run mcp_install gods-eye-view to connect the ~30 geospatial tools."}
        except (ConnectionRefusedError, TimeoutError, OSError):
            continue

    return {"started": True, "port": _DEV_PORT, "warning": "Server process is running but port not yet responding. It may need a moment."}


class GodsEyeStopArgs(Args):
    pass


@tool(
    "gods_eye_stop",
    "Stop the God's Eye View dev server.",
    GodsEyeStopArgs, Tier.T1,
    summary=lambda a: "Stop God's Eye View server",
)
async def gods_eye_stop(a: GodsEyeStopArgs) -> dict:
    global _server_proc
    if _server_proc is None or _server_proc.returncode is not None:
        _server_proc = None
        return {"already_stopped": True}

    pid = _server_proc.pid
    await asyncio.to_thread(host.kill_tree, pid)
    _server_proc = None
    return {"stopped": True}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class GodsEyeStatusArgs(Args):
    pass


@tool(
    "gods_eye_status",
    "Check whether God's Eye View is installed and its dev server is running.",
    GodsEyeStatusArgs, Tier.T0,
    summary=lambda a: "God's Eye View status",
)
async def gods_eye_status(a: GodsEyeStatusArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    settings = await asyncio.to_thread(hub.store.get_settings)
    target = _get_install_dir(settings)
    installed = (target / "package.json").exists()

    server_running = False
    if _server_proc is not None and _server_proc.returncode is None:
        server_running = True
    elif installed:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", _DEV_PORT), timeout=1.0,
            )
            writer.close()
            await writer.wait_closed()
            server_running = True
        except (ConnectionRefusedError, TimeoutError, OSError):
            pass

    mcp_connected = False
    mcp_tools = 0
    for s in hub.mcp.status():
        if s["name"] == "gods-eye-view" and s["state"] == "ready":
            mcp_connected = True
            mcp_tools = s["tools"]

    return {
        "installed": installed,
        "install_path": str(target) if installed else None,
        "server_running": server_running,
        "server_port": _DEV_PORT if server_running else None,
        "mcp_connected": mcp_connected,
        "mcp_tools": mcp_tools,
    }
