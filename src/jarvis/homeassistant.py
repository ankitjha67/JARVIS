"""Home Assistant bridge: lights, plugs, AC, scenes (idea 75).

Connects to Home Assistant's REST API. The HA URL and long-lived access token
are stored in settings.integrations.homeassistant.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx2 as httpx

log = logging.getLogger(__name__)

TIMEOUT_S = 10.0


class HomeAssistant:
    """Thin wrapper around the Home Assistant REST API."""

    def __init__(self, url: str, token: str) -> None:
        self.base = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _get(self, path: str) -> Any:
        r = httpx.get(f"{self.base}{path}", headers=self._headers, timeout=TIMEOUT_S)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, data: dict[str, Any] | None = None) -> Any:
        r = httpx.post(f"{self.base}{path}", headers=self._headers, json=data or {}, timeout=TIMEOUT_S)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"status": "ok"}

    def check(self) -> dict[str, Any]:
        return dict(self._get("/api/"))

    def states(self) -> list[dict[str, Any]]:
        return list(self._get("/api/states"))

    def state(self, entity_id: str) -> dict[str, Any]:
        return dict(self._get(f"/api/states/{entity_id}"))

    def call_service(self, domain: str, service: str, entity_id: str | None = None,
                     data: dict[str, Any] | None = None) -> Any:
        payload = dict(data or {})
        if entity_id:
            payload["entity_id"] = entity_id
        return self._post(f"/api/services/{domain}/{service}", payload)

    def turn_on(self, entity_id: str, **kwargs: Any) -> Any:
        domain = entity_id.split(".")[0]
        return self.call_service(domain, "turn_on", entity_id, kwargs or None)

    def turn_off(self, entity_id: str) -> Any:
        domain = entity_id.split(".")[0]
        return self.call_service(domain, "turn_off", entity_id)

    def toggle(self, entity_id: str) -> Any:
        domain = entity_id.split(".")[0]
        return self.call_service(domain, "toggle", entity_id)

    def set_temperature(self, entity_id: str, temperature: float) -> Any:
        return self.call_service("climate", "set_temperature", entity_id, {"temperature": temperature})

    def scenes(self) -> list[dict[str, Any]]:
        return [s for s in self.states() if s.get("entity_id", "").startswith("scene.")]

    def activate_scene(self, entity_id: str) -> Any:
        return self.call_service("scene", "turn_on", entity_id)

    def lights(self) -> list[dict[str, Any]]:
        return [s for s in self.states() if s.get("entity_id", "").startswith("light.")]

    def switches(self) -> list[dict[str, Any]]:
        return [s for s in self.states() if s.get("entity_id", "").startswith("switch.")]

    def climate(self) -> list[dict[str, Any]]:
        return [s for s in self.states() if s.get("entity_id", "").startswith("climate.")]

    def media_players(self) -> list[dict[str, Any]]:
        return [s for s in self.states() if s.get("entity_id", "").startswith("media_player.")]

    def fire_event(self, event_type: str, data: dict[str, Any] | None = None) -> Any:
        return self._post(f"/api/events/{event_type}", data)

    def automations(self) -> list[dict[str, Any]]:
        return [s for s in self.states() if s.get("entity_id", "").startswith("automation.")]

    def trigger_automation(self, entity_id: str) -> Any:
        return self.call_service("automation", "trigger", entity_id)
