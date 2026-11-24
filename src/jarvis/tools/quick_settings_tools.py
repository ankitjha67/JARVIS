"""Quick system settings toggles (idea 52)."""

from __future__ import annotations

import asyncio
import subprocess

from pydantic import Field

from .. import host
from ..policy import Tier
from .registry import Args, ToolError, tool


def _ps(cmd: str, timeout: int = 10) -> str:
    if not host.IS_WINDOWS:
        raise host.unsupported("This quick setting")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    if r.returncode != 0 and r.stderr.strip():
        raise ToolError(f"PowerShell error: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


class BrightnessGetArgs(Args):
    pass


@tool("brightness_get", "Get current screen brightness level (0-100).",
      BrightnessGetArgs, Tier.T0, summary=lambda a: "Get brightness")
async def brightness_get(a: BrightnessGetArgs) -> dict:
    if host.IS_MAC:
        return {"brightness": await asyncio.to_thread(host.mac.get_brightness)}
    out = await asyncio.to_thread(
        _ps, "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness"
    )
    return {"brightness": int(out) if out.isdigit() else out}


class BrightnessSetArgs(Args):
    level: int = Field(..., ge=0, le=100, description="Brightness 0-100")


@tool("quick_brightness_set", "Set screen brightness (quick settings).", BrightnessSetArgs, Tier.T2,
      summary=lambda a: f"Brightness -> {a.level}%")
async def brightness_set(a: BrightnessSetArgs) -> dict:
    if host.IS_MAC:
        await asyncio.to_thread(host.mac.set_brightness, a.level)
        return {"brightness": a.level}
    await asyncio.to_thread(
        _ps,
        f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, {a.level})",
    )
    return {"brightness": a.level}


class VolumeSetArgs(Args):
    level: int = Field(..., ge=0, le=100, description="Volume 0-100")


@tool("quick_volume_set", "Set system volume level (quick settings).", VolumeSetArgs, Tier.T1,
      summary=lambda a: f"Volume -> {a.level}%")
async def volume_set(a: VolumeSetArgs) -> dict:
    if host.IS_MAC:
        state = await asyncio.to_thread(host.mac.volume, a.level, None, None)
        return {"volume": state["volume_percent"]}
    nircmd = f'nircmd.exe setsysvolume {int(a.level * 655.35)}'
    try:
        await asyncio.to_thread(_ps, nircmd)
    except Exception:
        ps_fallback = (
            "Add-Type -TypeDefinition 'using System.Runtime.InteropServices; "
            "public class Audio { [DllImport(\"winmm.dll\")] "
            "public static extern int waveOutSetVolume(IntPtr hwo, uint dwVolume); }'; "
            f"$v = [int]({a.level} * 655.35); "
            "[Audio]::waveOutSetVolume([IntPtr]::Zero, ($v -bor ($v -shl 16)))"
        )
        await asyncio.to_thread(_ps, ps_fallback)
    return {"volume": a.level}


class WifiToggleArgs(Args):
    enable: bool = Field(..., description="True to enable, False to disable")


@tool("quick_wifi_toggle", "Enable or disable Wi-Fi (quick settings).", WifiToggleArgs, Tier.T2,
      summary=lambda a: f"Wi-Fi {'on' if a.enable else 'off'}")
async def wifi_toggle(a: WifiToggleArgs) -> dict:
    state = "enabled" if a.enable else "disabled"
    if host.IS_MAC:
        await asyncio.to_thread(host.mac.set_wifi, a.enable)
        return {"wifi": state}
    await asyncio.to_thread(
        _ps, f'netsh interface set interface "Wi-Fi" admin={state}'
    )
    return {"wifi": state}


class BluetoothToggleArgs(Args):
    enable: bool = Field(..., description="True to enable, False to disable")


@tool("quick_bluetooth_toggle", "Enable or disable Bluetooth (quick settings).", BluetoothToggleArgs, Tier.T2,
      summary=lambda a: f"Bluetooth {'on' if a.enable else 'off'}")
async def bluetooth_toggle(a: BluetoothToggleArgs) -> dict:
    if host.IS_MAC:
        await asyncio.to_thread(host.mac.set_bluetooth, a.enable)
        return {"bluetooth": "enabled" if a.enable else "disabled"}
    if a.enable:
        cmd = (
            "$radio = Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Status -eq 'Error' -or $_.Status -eq 'Disabled' } | "
            "Select-Object -First 1; "
            "if ($radio) { Enable-PnpDevice -InstanceId $radio.InstanceId -Confirm:$false }"
        )
    else:
        cmd = (
            "$radio = Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Status -eq 'OK' } | Select-Object -First 1; "
            "if ($radio) { Disable-PnpDevice -InstanceId $radio.InstanceId -Confirm:$false }"
        )
    await asyncio.to_thread(_ps, cmd)
    return {"bluetooth": "enabled" if a.enable else "disabled"}


class NightLightToggleArgs(Args):
    enable: bool = Field(..., description="True to enable, False to disable")


@tool("night_light_toggle", "Toggle Windows Night Light.", NightLightToggleArgs, Tier.T2,
      summary=lambda a: f"Night light {'on' if a.enable else 'off'}", platforms=("win32",))
async def night_light_toggle(a: NightLightToggleArgs) -> dict:
    key = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current\default$windows.data.bluelightreduction.bluelightreductionstate\windows.data.bluelightreduction.bluelightreductionstate"
    if a.enable:
        cmd = f'$d = (Get-ItemProperty -Path "{key}" -Name "Data" -ErrorAction SilentlyContinue).Data; if ($d) {{ $d[18] = 0x15; Set-ItemProperty -Path "{key}" -Name "Data" -Value $d }}'
    else:
        cmd = f'$d = (Get-ItemProperty -Path "{key}" -Name "Data" -ErrorAction SilentlyContinue).Data; if ($d) {{ $d[18] = 0x13; Set-ItemProperty -Path "{key}" -Name "Data" -Value $d }}'
    try:
        await asyncio.to_thread(_ps, cmd)
    except Exception:
        pass
    return {"night_light": "enabled" if a.enable else "disabled"}
