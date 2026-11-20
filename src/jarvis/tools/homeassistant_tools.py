"""Home Assistant tools: smart home control (idea 75)."""

from __future__ import annotations

import asyncio

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool


import re as _re

_ENTITY_RE = _re.compile(r'^[a-z_]+\.[a-z0-9_]+$')


def _ha():
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    if not hasattr(hub, 'ha') or hub.ha is None:
        raise ToolError("Home Assistant is not configured. Add URL and token in Settings → Integrations.")
    return hub.ha


def _validate_entity(entity_id: str) -> str:
    if not _ENTITY_RE.match(entity_id):
        raise ToolError(f"Invalid entity ID format: {entity_id}")
    return entity_id


class HAListArgs(Args):
    type: str = Field("all", description="all, lights, switches, climate, scenes, media")


@tool("ha_list", "List Home Assistant devices by type.",
      HAListArgs, Tier.T0, summary=lambda a: f"HA devices: {a.type}")
async def ha_list(a: HAListArgs) -> dict:
    ha = _ha()
    getter = {"lights": ha.lights, "switches": ha.switches, "climate": ha.climate,
              "scenes": ha.scenes, "media": ha.media_players, "all": ha.states}
    fn = getter.get(a.type, ha.states)
    items = await asyncio.to_thread(fn)
    return {"devices": [{"entity_id": d.get("entity_id"), "state": d.get("state"),
                         "name": d.get("attributes", {}).get("friendly_name", "")} for d in items[:50]],
            "count": len(items)}


class HAControlArgs(Args):
    entity_id: str = Field(..., min_length=1, max_length=200)
    action: str = Field(..., description="turn_on, turn_off, toggle")


@tool("ha_control", "Turn on, off, or toggle a Home Assistant device.",
      HAControlArgs, Tier.T1, sensitive=True, summary=lambda a: f"HA {a.action}: {a.entity_id}")
async def ha_control(a: HAControlArgs) -> dict:
    ha = _ha()
    eid = _validate_entity(a.entity_id)
    fn = {"turn_on": ha.turn_on, "turn_off": ha.turn_off, "toggle": ha.toggle}.get(a.action)
    if not fn:
        raise ToolError("Action must be turn_on, turn_off, or toggle.")
    await asyncio.to_thread(fn, eid)
    return {"done": True, "entity_id": eid, "action": a.action}


class HASceneArgs(Args):
    entity_id: str = Field(..., min_length=1, max_length=200)


@tool("ha_scene", "Activate a Home Assistant scene.",
      HASceneArgs, Tier.T1, summary=lambda a: f"HA scene: {a.entity_id}")
async def ha_scene(a: HASceneArgs) -> dict:
    ha = _ha()
    eid = _validate_entity(a.entity_id)
    await asyncio.to_thread(ha.activate_scene, eid)
    return {"activated": True, "scene": eid}


class HATempArgs(Args):
    entity_id: str = Field(..., min_length=1, max_length=200)
    temperature: float = Field(..., ge=10, le=40)


@tool("ha_temperature", "Set thermostat temperature.",
      HATempArgs, Tier.T1, sensitive=True, summary=lambda a: f"HA temp {a.entity_id}: {a.temperature}°")
async def ha_temperature(a: HATempArgs) -> dict:
    ha = _ha()
    eid = _validate_entity(a.entity_id)
    await asyncio.to_thread(ha.set_temperature, eid, a.temperature)
    return {"set": True, "entity_id": eid, "temperature": a.temperature}
