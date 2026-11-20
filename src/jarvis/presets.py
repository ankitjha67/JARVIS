"""Preset configurations (inspired by OpenJarvis).

Quick-switch between pre-defined profiles: privacy modes, performance tuning,
voice settings, and routing preferences.
"""

from __future__ import annotations

from typing import Any

BUILT_IN: dict[str, dict[str, Any]] = {
    "private": {
        "label": "Maximum Privacy",
        "description": "Local models only, no cloud calls, no telemetry sent externally.",
        "settings": {
            "privacy_mode": "local",
        },
    },
    "balanced": {
        "label": "Balanced",
        "description": "Local for simple tasks, cloud for demanding ones. Default.",
        "settings": {
            "privacy_mode": "hybrid",
        },
    },
    "performance": {
        "label": "Maximum Performance",
        "description": "Always use the best available model regardless of privacy.",
        "settings": {
            "privacy_mode": "cloud",
        },
    },
    "silent": {
        "label": "Silent Mode",
        "description": "No voice input or output, wake word disabled.",
        "settings": {
            "voice": {"speak": False, "auto_send": False, "wake_word": False},
        },
    },
    "hands-free": {
        "label": "Hands-Free",
        "description": "Wake word on, auto-send spoken input, speak all replies.",
        "settings": {
            "voice": {"speak": True, "auto_send": True, "wake_word": True, "wake_listen": True},
        },
    },
}


def list_presets() -> list[dict[str, str]]:
    return [{"id": k, "label": v["label"], "description": v["description"]}
            for k, v in BUILT_IN.items()]


def get_preset(preset_id: str) -> dict[str, Any] | None:
    return BUILT_IN.get(preset_id)


def apply_preset(settings_store: Any, preset_id: str) -> dict[str, Any]:
    """Apply a preset's settings to the settings store. Returns the applied patch."""
    preset = BUILT_IN.get(preset_id)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_id}")
    patch = preset["settings"]
    settings_store.patch(patch)
    return patch
