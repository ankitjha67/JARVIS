"""File management beyond read/write, and power actions (FR-T5, FR-T10)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from pydantic import Field

from .. import host
from ..policy import Tier, canonical, is_sensitive_path, is_system_path
from .registry import Args, ToolError, tool


def _guard(raw: str, *, write: bool = False) -> Path:
    path = canonical(raw)
    if is_sensitive_path(path):
        raise ToolError("That location holds credentials or private keys and is off limits to Jarvis.")
    if write and is_system_path(path):
        raise ToolError("Jarvis does not change files inside the operating system or installed applications.")
    return path


class MkdirArgs(Args):
    path: str = Field(..., min_length=1, max_length=400)


@tool("files_mkdir", "Create a folder (and any missing parent folders).", MkdirArgs, Tier.T1,
      summary=lambda a: f"Create folder {a.path}")
async def files_mkdir(a: MkdirArgs) -> dict:
    path = _guard(a.path, write=True)
    try:
        await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolError(f"Could not create {path}: {exc.strerror}.") from exc
    return {"created": str(path)}


class MoveArgs(Args):
    source: str = Field(..., min_length=1, max_length=400)
    destination: str = Field(..., min_length=1, max_length=400, description="New path, or an existing folder to move into")
    as_copy: bool = Field(False, description="Copy instead of move")


def _classify_move(a: MoveArgs) -> tuple[Tier, str]:
    src, dst = _guard(a.source, write=not a.as_copy), _guard(a.destination, write=True)
    target = dst / src.name if dst.is_dir() else dst
    if target.exists():
        raise ToolError(f"{target} already exists. Choose another name or delete it first.")
    verb = "Copies" if a.as_copy else "Moves"
    return Tier.T2, f"{verb} {src} to {target}."


def _move(a: MoveArgs) -> dict:
    src, dst = _guard(a.source, write=not a.as_copy), _guard(a.destination, write=True)
    if not src.exists():
        raise ToolError(f"{src} does not exist.")
    target = dst / src.name if dst.is_dir() else dst
    if target.exists():
        raise ToolError(f"{target} already exists.")
    if not target.parent.is_dir():
        raise ToolError(f"The folder {target.parent} does not exist.")
    try:
        if a.as_copy:
            if src.is_dir():
                shutil.copytree(src, target)
            else:
                shutil.copy2(src, target)
        else:
            shutil.move(str(src), str(target))
    except (OSError, shutil.Error) as exc:
        raise ToolError(f"Could not {'copy' if a.as_copy else 'move'} {src.name}: {exc}.") from exc
    return {"copied" if a.as_copy else "moved": str(src), "to": str(target)}


@tool("files_move", "Move, rename or copy a file or folder. Never overwrites.", MoveArgs, Tier.T2,
      classify=_classify_move, summary=lambda a: f"{'Copy' if a.as_copy else 'Move'} {a.source} → {a.destination}")
async def files_move(a: MoveArgs) -> dict:
    return await asyncio.to_thread(_move, a)


class DeleteArgs(Args):
    path: str = Field(..., min_length=1, max_length=400)


def _classify_delete(a: DeleteArgs) -> tuple[Tier, str]:
    path = _guard(a.path, write=True)
    mounted_volume = not host.IS_WINDOWS and len(path.parts) == 3 and path.parts[1] == "Volumes"
    if len(path.parts) <= 2 or mounted_volume:
        raise ToolError("Jarvis will not delete a drive or a top-level folder.")
    if path == Path.home():
        raise ToolError("Jarvis will not delete your home folder.")
    kind = "folder and everything in it" if path.is_dir() else "file"
    return Tier.T3, f"Sends the {kind} {path} to the {host.TRASH_NAME}."


def _delete(a: DeleteArgs) -> dict:
    from send2trash import send2trash

    path = _guard(a.path, write=True)
    if not path.exists():
        raise ToolError(f"{path} does not exist.")
    try:
        send2trash(str(path))
    except OSError as exc:
        raise ToolError(f"Could not move {path.name} to the {host.TRASH_NAME}: {exc}.") from exc
    return {"recycled": str(path)}


@tool("files_delete", f"Send a file or folder to the {host.TRASH_NAME} (recoverable from there).", DeleteArgs, Tier.T3,
      sensitive=True, classify=_classify_delete, summary=lambda a: f"Delete {a.path}")
async def files_delete(a: DeleteArgs) -> dict:
    return await asyncio.to_thread(_delete, a)


class PowerArgs(Args):
    action: str = Field(..., pattern="^(sleep|restart|shutdown|sign_out|cancel)$",
                        description="sleep, restart, shutdown, sign_out, or cancel a pending restart/shutdown")


def _classify_power(a: PowerArgs) -> tuple[Tier, str]:
    if a.action == "cancel":
        return Tier.T1, ""
    wait = "after the 60-second system dialog" if host.IS_MAC else "in 30 seconds"
    detail = {"sleep": "Puts the computer to sleep.", "sign_out": "Signs you out; unsaved work is lost.",
              "restart": f"Restarts the computer {wait}; unsaved work is lost.",
              "shutdown": f"Shuts the computer down {wait}; unsaved work is lost."}[a.action]
    return Tier.T3, detail


_POWER_HELP = (
    "Sleep, restart, shut down or sign out. Restart, shutdown and sign-out show the macOS confirmation dialog, "
    "which proceeds after 60 seconds unless the user cancels it there." if host.IS_MAC else
    "Sleep, restart, shut down or sign out. Restart and shutdown wait 30 seconds and can be cancelled.")


@tool("power_action", _POWER_HELP,
      PowerArgs, Tier.T3, sensitive=True, classify=_classify_power,
      summary=lambda a: {"cancel": "Cancel pending shutdown"}.get(a.action, a.action.replace("_", " ").capitalize() + " the computer"))
async def power_action(a: PowerArgs) -> dict:
    if host.IS_MAC:
        return await asyncio.to_thread(host.mac.power, a.action)
    if not host.IS_WINDOWS:
        raise host.unsupported("Power control")
    commands = {
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "restart": ["shutdown.exe", "/r", "/t", "30", "/c", "Jarvis: restarting in 30 seconds"],
        "shutdown": ["shutdown.exe", "/s", "/t", "30", "/c", "Jarvis: shutting down in 30 seconds"],
        "sign_out": ["shutdown.exe", "/l"],
        "cancel": ["shutdown.exe", "/a"],
    }
    proc = await asyncio.to_thread(
        subprocess.run, commands[a.action], capture_output=True, text=True, timeout=15, check=False,
        creationflags=host.NO_WINDOW)
    if proc.returncode != 0 and a.action != "sleep":
        raise ToolError((proc.stderr or proc.stdout or "Windows refused the request.").strip()[:200])
    return {"action": a.action, "delay_s": 30 if a.action in ("restart", "shutdown") else 0}
