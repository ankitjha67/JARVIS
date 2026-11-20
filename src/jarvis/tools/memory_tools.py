"""Memory tools: remember, recall, forget, search, review (ideas 1-8)."""

from __future__ import annotations


from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool


class RememberArgs(Args):
    text: str = Field(..., min_length=1, max_length=2000, description="Fact or instruction to remember")
    type: str = Field("fact", description="Memory type: fact, entity, correction, habit")


@tool("memory_remember", "Save a fact, preference, or instruction to long-term memory.",
      RememberArgs, Tier.T0, summary=lambda a: f"Remember: {a.text[:60]}")
async def memory_remember(a: RememberArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    mid = hub.memory.remember(a.text, type_=a.type)
    return {"saved": True, "id": mid}


class RecallArgs(Args):
    query: str = Field(..., min_length=1, max_length=200, description="What to look up in memory")
    limit: int = Field(5, ge=1, le=20)


@tool("memory_recall", "Search long-term memory for relevant facts.",
      RecallArgs, Tier.T0, summary=lambda a: f"Recall: {a.query[:60]}")
async def memory_recall(a: RecallArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    results = hub.memory.search(a.query, limit=a.limit)
    return {"results": results, "count": len(results)}


class ForgetArgs(Args):
    id: str = Field(..., min_length=1, max_length=50, description="Memory ID to forget")


@tool("memory_forget", "Remove a specific memory by ID.",
      ForgetArgs, Tier.T1, summary=lambda a: f"Forget memory #{a.id}")
async def memory_forget(a: ForgetArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    ok = hub.memory.forget(a.id)
    if not ok:
        raise ToolError(f"Memory #{a.id} not found.")
    return {"forgotten": True, "id": a.id}


class ReviewArgs(Args):
    action: str = Field("list", description="list, approve, reject")
    id: str | None = Field(None, min_length=1, max_length=50, description="Review ID for approve/reject")


@tool("memory_review", "Review pending auto-extracted memories.",
      ReviewArgs, Tier.T0, summary=lambda a: f"Review memories: {a.action}")
async def memory_review(a: ReviewArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    if a.action == "list":
        return {"pending": hub.memory.review_pending(limit=10)}
    if a.action in ("approve", "reject") and a.id is not None:
        hub.memory.review_decide(a.id, a.action == "approve")
        return {"decided": a.action, "id": a.id}
    raise ToolError("Provide id for approve/reject.")
