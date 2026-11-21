"""Search past conversations (idea #4): FTS5-backed full-text search over message history."""

from __future__ import annotations

import asyncio

from pydantic import Field

from ..policy import Tier
from .registry import Args, tool


class SearchConversationsArgs(Args):
    query: str = Field(..., min_length=1, max_length=200, description="Text to search for in past conversations")
    role: str | None = Field(None, description="Filter by role: user, assistant, or None for all")
    limit: int = Field(10, ge=1, le=50, description="Max results to return")


@tool("conversation_search", "Search past conversations for a topic, decision, or phrase.",
      SearchConversationsArgs, Tier.T0, summary=lambda a: f"Search conversations: {a.query[:60]}")
async def conversation_search(a: SearchConversationsArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    results = await asyncio.to_thread(hub.store.search_conversations, a.query, role=a.role, limit=a.limit)
    return {"results": results, "count": len(results), "query": a.query}


class ListConversationsArgs(Args):
    limit: int = Field(20, ge=1, le=100, description="Max conversations to return")


@tool("conversation_list", "List recent conversations by title and date.",
      ListConversationsArgs, Tier.T0, summary=lambda a: "List conversations")
async def conversation_list(a: ListConversationsArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]

    def _list() -> list[dict]:
        with hub.store._lock:
            rows = hub.store._db.execute(
                "SELECT id, title, created_at, updated_at FROM conversations "
                "ORDER BY updated_at DESC LIMIT ?", (a.limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    results = await asyncio.to_thread(_list)
    return {"conversations": results, "count": len(results)}
