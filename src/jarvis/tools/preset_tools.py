"""Preset configuration tools: quick-switch profiles."""

from __future__ import annotations

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool


class PresetListArgs(Args):
    pass


@tool("preset_list", "List available configuration presets.",
      PresetListArgs, Tier.T0, summary=lambda a: "List presets")
async def preset_list(a: PresetListArgs) -> dict:
    from ..presets import list_presets
    return {"presets": list_presets()}


class PresetApplyArgs(Args):
    preset: str = Field(..., min_length=1, max_length=50, description="Preset id to apply")


@tool("preset_apply", "Apply a configuration preset.",
      PresetApplyArgs, Tier.T1, summary=lambda a: f"Apply preset: {a.preset}")
async def preset_apply(a: PresetApplyArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    from ..presets import apply_preset
    try:
        patch = apply_preset(hub.settings_store, a.preset)
        return {"applied": a.preset, "patch": patch}
    except ValueError as e:
        raise ToolError(str(e))
