"""Buffer social media tools: post, schedule, analytics (MCP integration)."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

_BUFFER_API = "https://api.bufferapp.com/1"


def _get_token() -> str:
    from .. import secrets
    token = secrets.get_key("buffer")
    if not token:
        raise ToolError("Buffer API token not configured. Add it in Settings → Keys as 'buffer'.")
    return token


async def _api(method: str, path: str, **kwargs: Any) -> Any:
    import httpx2 as httpx
    token = _get_token()
    url = f"{_BUFFER_API}{path}"
    params = kwargs.pop("params", {})
    params["access_token"] = token
    async with httpx.AsyncClient(timeout=15.0) as client:
        if method == "GET":
            r = await client.get(url, params=params, **kwargs)
        else:
            r = await client.post(url, params=params, **kwargs)
        r.raise_for_status()
        return r.json()


class BufferProfilesArgs(Args):
    pass


@tool("buffer_profiles", "List connected Buffer social media profiles.",
      BufferProfilesArgs, Tier.T0, summary=lambda a: "List Buffer profiles")
async def buffer_profiles(a: BufferProfilesArgs) -> dict:
    profiles = await _api("GET", "/profiles.json")
    return {"profiles": [{"id": p["id"], "service": p.get("service"), "name": p.get("formatted_username", "")}
                         for p in profiles]}


class BufferPostArgs(Args):
    text: str = Field(..., min_length=1, max_length=2000)
    profile_ids: list[str] = Field(..., min_length=1, max_length=10)
    now: bool = Field(False, description="Post immediately instead of adding to queue")
    scheduled_at: str = Field("", max_length=30, description="ISO datetime for scheduled post")
    media_link: str = Field("", max_length=2000, description="URL to image/video to attach")


@tool("buffer_post", "Create or schedule a social media post via Buffer.",
      BufferPostArgs, Tier.T2, sensitive=True,
      summary=lambda a: f"Buffer post: {a.text[:40]}")
async def buffer_post(a: BufferPostArgs) -> dict:
    data: dict[str, Any] = {"text": a.text, "profile_ids[]": a.profile_ids}
    if a.now:
        data["now"] = "true"
    if a.scheduled_at:
        data["scheduled_at"] = a.scheduled_at
    if a.media_link:
        data["media[link]"] = a.media_link
    result = await _api("POST", "/updates/create.json", data=data)
    return {"success": result.get("success", False), "id": result.get("update", {}).get("id", ""),
            "message": result.get("message", "")}


class BufferQueueArgs(Args):
    profile_id: str = Field(..., min_length=1, max_length=50)


@tool("buffer_queue", "View pending posts in a Buffer profile's queue.",
      BufferQueueArgs, Tier.T0, summary=lambda a: f"Buffer queue: {a.profile_id[:20]}")
async def buffer_queue(a: BufferQueueArgs) -> dict:
    updates = await _api("GET", f"/profiles/{a.profile_id}/updates/pending.json")
    items = updates.get("updates", [])
    return {"pending": [{"id": u["id"], "text": u.get("text", "")[:200], "due_at": u.get("due_at", "")}
                        for u in items[:20]], "total": updates.get("total", len(items))}


class BufferAnalyticsArgs(Args):
    profile_id: str = Field(..., min_length=1, max_length=50)


@tool("buffer_analytics", "Get analytics for a Buffer profile's recent posts.",
      BufferAnalyticsArgs, Tier.T0, summary=lambda a: f"Buffer analytics: {a.profile_id[:20]}")
async def buffer_analytics(a: BufferAnalyticsArgs) -> dict:
    updates = await _api("GET", f"/profiles/{a.profile_id}/updates/sent.json")
    items = updates.get("updates", [])
    return {"sent": [{"id": u["id"], "text": u.get("text", "")[:100],
                      "stats": u.get("statistics", {})} for u in items[:10]],
            "total": updates.get("total", len(items))}
