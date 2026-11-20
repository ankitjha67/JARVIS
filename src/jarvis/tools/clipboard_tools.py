"""Clipboard tools: history search, get, paste (idea 47)."""

from __future__ import annotations

from pydantic import Field

from ..policy import Tier
from .registry import Args, tool


class ClipSearchArgs(Args):
    query: str = Field("", max_length=200)
    limit: int = Field(10, ge=1, le=50)


@tool("clipboard_search", "Search clipboard history or list recent entries.",
      ClipSearchArgs, Tier.T0, taints=True, summary=lambda a: f"Clipboard: {a.query[:40] or 'recent'}")
async def clipboard_search(a: ClipSearchArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    if a.query:
        results = hub.clipboard.search(a.query, limit=a.limit)
    else:
        results = hub.clipboard.recent(limit=a.limit)
    return {"entries": results, "count": len(results)}


class ClipGetArgs(Args):
    id: int = Field(..., description="Clipboard entry ID")


@tool("clipboard_get", "Get a specific clipboard entry by ID.",
      ClipGetArgs, Tier.T0, taints=True, summary=lambda a: f"Clipboard #{a.id}")
async def clipboard_get(a: ClipGetArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    text = hub.clipboard.get(a.id)
    if text is None:
        return {"error": "Entry not found"}
    return {"text": text[:5000], "truncated": len(text) > 5000}
