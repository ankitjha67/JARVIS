"""Computer settings: brightness, Wi-Fi, Bluetooth, display settings."""

from __future__ import annotations

import asyncio
import subprocess

from pydantic import Field

from .. import host
from ..policy import Tier
from .registry import Args, ToolError, tool

_CREATE_NO_WINDOW = host.NO_WINDOW


def _ps(script: str, *, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
    )


# -- brightness ----------------------------------------------------------------

class BrightnessArgs(Args):
    percent: int = Field(..., ge=0, le=100, description="Brightness level 0-100")


def _brightness(a: BrightnessArgs) -> dict:
    if host.IS_MAC:
        host.mac.set_brightness(a.percent)
        return {"brightness_percent": a.percent}
    if not host.IS_WINDOWS:
        raise host.unsupported("Brightness control")
    r = _ps(
        f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
        f".WmiSetBrightness(1,{a.percent})"
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()[:200]
        if "not supported" in err.lower() or "not found" in err.lower():
            raise ToolError("Brightness control is not available on this device (desktop monitors typically lack WMI brightness).")
        raise ToolError(f"Could not set brightness: {err}")
    return {"brightness_percent": a.percent}


@tool("brightness_set", "Set the display brightness (0-100). Works on laptops and some monitors.",
      BrightnessArgs, Tier.T1, summary=lambda a: f"Set brightness to {a.percent}%")
async def brightness_set(a: BrightnessArgs) -> dict:
    return await asyncio.to_thread(_brightness, a)


# -- Wi-Fi ---------------------------------------------------------------------

class WifiArgs(Args):
    enable: bool = Field(..., description="True to enable, False to disable Wi-Fi")


def _wifi(a: WifiArgs) -> dict:
    state = "enabled" if a.enable else "disabled"
    if host.IS_MAC:
        host.mac.set_wifi(a.enable)
        return {"wifi": state}
    if not host.IS_WINDOWS:
        raise host.unsupported("Switching Wi-Fi")
    r = subprocess.run(
        ["netsh", "interface", "set", "interface", "Wi-Fi", f"admin={state}"],
        capture_output=True, text=True, timeout=15,
        creationflags=_CREATE_NO_WINDOW,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()[:200]
        raise ToolError(f"Could not {'enable' if a.enable else 'disable'} Wi-Fi: {err}")
    return {"wifi": state}


@tool("wifi_toggle", "Enable or disable the Wi-Fi adapter.",
      WifiArgs, Tier.T2, sensitive=True,
      summary=lambda a: f"{'Enable' if a.enable else 'Disable'} Wi-Fi")
async def wifi_toggle(a: WifiArgs) -> dict:
    return await asyncio.to_thread(_wifi, a)


# -- Bluetooth -----------------------------------------------------------------

class BluetoothArgs(Args):
    enable: bool = Field(..., description="True to enable, False to disable Bluetooth")


def _bluetooth(a: BluetoothArgs) -> dict:
    desired = "On" if a.enable else "Off"
    if host.IS_MAC:
        host.mac.set_bluetooth(a.enable)
        return {"bluetooth": desired.lower()}
    if not host.IS_WINDOWS:
        raise host.unsupported("Switching Bluetooth")
    script = (
        "[Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType=WindowsRuntime] | Out-Null; "
        "$radios = [Windows.Devices.Radios.Radio]::GetRadiosAsync().GetAwaiter().GetResult(); "
        "$bt = $radios | Where-Object { $_.Kind -eq 'Bluetooth' } | Select-Object -First 1; "
        "if ($bt) { "
        f"  $bt.SetStateAsync([Windows.Devices.Radios.RadioState]::{desired}).GetAwaiter().GetResult() | Out-Null; "
        f"  Write-Output 'Bluetooth {desired.lower()}' "
        "} else { Write-Output 'NO_BT_RADIO' }"
    )
    r = _ps(script, timeout=20)
    out = (r.stdout or "").strip()
    if "NO_BT_RADIO" in out:
        raise ToolError("No Bluetooth radio found on this device.")
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()[:200]
        raise ToolError(f"Could not toggle Bluetooth: {err}")
    return {"bluetooth": desired.lower()}


@tool("bluetooth_toggle", "Enable or disable the Bluetooth radio.",
      BluetoothArgs, Tier.T2, sensitive=True,
      summary=lambda a: f"{'Enable' if a.enable else 'Disable'} Bluetooth")
async def bluetooth_toggle(a: BluetoothArgs) -> dict:
    return await asyncio.to_thread(_bluetooth, a)


# -- open settings pages -------------------------------------------------------

class OpenSettingsArgs(Args):
    page: str = Field(
        ..., description="Settings page: display, nightlight, wifi, bluetooth, sound, battery, storage, accounts, privacy, apps, default_apps, notifications, personalization"
    )


_SETTINGS_URIS = {
    "display": "ms-settings:display",
    "nightlight": "ms-settings:nightlight",
    "wifi": "ms-settings:network-wifi",
    "bluetooth": "ms-settings:bluetooth",
    "sound": "ms-settings:sound",
    "battery": "ms-settings:batterysaver",
    "storage": "ms-settings:storagesense",
    "accounts": "ms-settings:yourinfo",
    "privacy": "ms-settings:privacy",
    "apps": "ms-settings:appsfeatures",
    "default_apps": "ms-settings:defaultapps",
    "notifications": "ms-settings:notifications",
    "personalization": "ms-settings:personalization",
}


@tool("open_settings", f"Open a {host.NAME} settings page by name.",
      OpenSettingsArgs, Tier.T1, summary=lambda a: f"Open {a.page} settings")
async def open_settings(a: OpenSettingsArgs) -> dict:
    uri = _SETTINGS_URIS.get(a.page)
    if not uri:
        raise ToolError(f"Unknown settings page '{a.page}'. Available: {', '.join(sorted(_SETTINGS_URIS))}")
    if host.IS_MAC:
        uri = host.mac.settings_uri(a.page)
    try:
        await asyncio.to_thread(host.open_uri, uri)
    except OSError as exc:
        raise ToolError(f"{host.NAME} could not open that settings page.") from exc
    return {"opened": a.page, "uri": uri}
