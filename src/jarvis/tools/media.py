"""Volume and media transport (FR-T7)."""

from __future__ import annotations

import asyncio
import ctypes
from typing import Any

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

_VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}


def _endpoint() -> Any:
    """Default render device's IAudioEndpointVolume. COM must be initialised on the calling thread."""
    import comtypes
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    comtypes.CoInitialize()
    try:
        device = AudioUtilities.GetSpeakers()
        direct = getattr(device, "EndpointVolume", None)  # newer pycaw wraps the device
        if direct is not None:
            return direct
        iface = device.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
        return iface.QueryInterface(IAudioEndpointVolume)
    except Exception as exc:
        raise ToolError("No audio output device is available.") from exc


class VolumeArgs(Args):
    percent: int | None = Field(None, ge=0, le=100, description="Absolute level 0-100")
    change: int | None = Field(None, ge=-100, le=100, description="Or: relative change, e.g. -10")
    mute: bool | None = Field(None, description="Or: mute (true) / unmute (false)")


def _volume(a: VolumeArgs) -> dict:
    if a.percent is None and a.change is None and a.mute is None:
        raise ToolError("Give percent, change or mute.")
    vol = _endpoint()
    current = round(vol.GetMasterVolumeLevelScalar() * 100)
    target = a.percent if a.percent is not None else (current + a.change if a.change is not None else None)
    if target is not None:
        target = max(0, min(100, target))
        vol.SetMasterVolumeLevelScalar(target / 100, None)
    if a.mute is not None:
        vol.SetMute(1 if a.mute else 0, None)
    return {"volume_percent": round(vol.GetMasterVolumeLevelScalar() * 100), "muted": bool(vol.GetMute())}


def _vol_summary(a: VolumeArgs) -> str:
    if a.mute is not None and a.percent is None and a.change is None:
        return "Mute audio" if a.mute else "Unmute audio"
    if a.percent is not None:
        return f"Set volume to {a.percent}%"
    return f"Change volume by {a.change:+d}%" if a.change is not None else "Adjust volume"


@tool("volume_set", "Set, change or mute the system output volume. Returns the resulting level.",
      VolumeArgs, Tier.T1, summary=_vol_summary)
async def volume_set(a: VolumeArgs) -> dict:
    return await asyncio.to_thread(_volume, a)


class MediaArgs(Args):
    action: str = Field(..., pattern="^(play_pause|next|previous|stop)$")


@tool("media_control", "Send a media key: play_pause, next, previous or stop.", MediaArgs, Tier.T1,
      summary=lambda a: f"Media: {a.action.replace('_', ' ')}")
async def media_control(a: MediaArgs) -> dict:
    vk = _VK[a.action]
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
    return {"sent": a.action}
