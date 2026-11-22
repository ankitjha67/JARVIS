"""Universal Scratchpad: quick persistent notes stored as local JSON."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field

from ..config import DATA_DIR
from ..policy import Tier
from .registry import Args, ToolError, tool

_FILE = DATA_DIR / "scratchpad.json"


def _load() -> list[dict]:
    try:
        return json.loads(_FILE.read_text("utf-8")) if _FILE.exists() else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(notes: list[dict]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(notes, indent=2, ensure_ascii=False), "utf-8")


# ---------------------------------------------------------------------------
# Add
# ---------------------------------------------------------------------------

class ScratchpadAddArgs(Args):
    text: str = Field(..., min_length=1, max_length=5000, description="Note content")
    tags: list[str] = Field(default_factory=list, max_length=10, description="Optional tags for categorisation")


@tool("scratchpad_add", "Add a quick note to the scratchpad.",
      ScratchpadAddArgs, Tier.T1, summary=lambda a: f"Note: {a.text[:50]}")
async def scratchpad_add(a: ScratchpadAddArgs) -> dict:
    def _add() -> dict:
        notes = _load()
        entry = {
            "id": uuid.uuid4().hex[:8],
            "text": a.text,
            "tags": [t.strip().lower() for t in a.tags if t.strip()],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        notes.insert(0, entry)
        _save(notes)
        return entry
    return await asyncio.to_thread(_add)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

class ScratchpadListArgs(Args):
    tag: str = Field("", max_length=50, description="Filter by tag (empty = all)")


@tool("scratchpad_list", "List scratchpad notes, newest first. Optionally filter by tag.",
      ScratchpadListArgs, Tier.T0, summary=lambda a: f"List notes{' tag=' + a.tag if a.tag else ''}")
async def scratchpad_list(a: ScratchpadListArgs) -> dict:
    notes = await asyncio.to_thread(_load)
    if a.tag:
        t = a.tag.strip().lower()
        notes = [n for n in notes if t in n.get("tags", [])]
    return {"notes": notes[:50], "total": len(notes)}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class ScratchpadSearchArgs(Args):
    query: str = Field(..., min_length=1, max_length=200, description="Text to search for")


@tool("scratchpad_search", "Full-text search across scratchpad notes.",
      ScratchpadSearchArgs, Tier.T0, summary=lambda a: f"Search notes: {a.query[:40]}")
async def scratchpad_search(a: ScratchpadSearchArgs) -> dict:
    notes = await asyncio.to_thread(_load)
    q = a.query.lower()
    hits = [n for n in notes if q in n.get("text", "").lower() or q in " ".join(n.get("tags", [])).lower()]
    return {"results": hits[:30], "count": len(hits)}


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class ScratchpadDeleteArgs(Args):
    note_id: str = Field(..., min_length=1, max_length=16, description="Note ID to delete")


@tool("scratchpad_delete", "Delete a scratchpad note by ID.",
      ScratchpadDeleteArgs, Tier.T1, summary=lambda a: f"Delete note {a.note_id}")
async def scratchpad_delete(a: ScratchpadDeleteArgs) -> dict:
    def _del() -> dict:
        notes = _load()
        before = len(notes)
        notes = [n for n in notes if n.get("id") != a.note_id]
        if len(notes) == before:
            raise ToolError(f"Note '{a.note_id}' not found.")
        _save(notes)
        return {"deleted": a.note_id, "remaining": len(notes)}
    return await asyncio.to_thread(_del)


# ---------------------------------------------------------------------------
# Clear all
# ---------------------------------------------------------------------------

class ScratchpadClearArgs(Args):
    pass


@tool("scratchpad_clear", "Delete ALL scratchpad notes. This cannot be undone.",
      ScratchpadClearArgs, Tier.T1, sensitive=True, summary=lambda a: "Clear all notes")
async def scratchpad_clear(a: ScratchpadClearArgs) -> dict:
    def _clear() -> dict:
        count = len(_load())
        _save([])
        return {"cleared": count}
    return await asyncio.to_thread(_clear)
