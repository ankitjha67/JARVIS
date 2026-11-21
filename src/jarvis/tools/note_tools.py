"""Quick notes: capture, search, and list ephemeral notes (ideas #3-4)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from pydantic import Field

from ..config import DATA_DIR
from ..policy import Tier
from .registry import Args, ToolError, tool

log = logging.getLogger(__name__)

_NOTES_FILE = DATA_DIR / "notes.json"


def _load() -> list[dict]:
    if _NOTES_FILE.exists():
        try:
            return json.loads(_NOTES_FILE.read_text("utf-8"))
        except Exception:
            return []
    return []


def _save(notes: list[dict]) -> None:
    _NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _NOTES_FILE.write_text(json.dumps(notes, ensure_ascii=False, indent=1), "utf-8")


class NoteAddArgs(Args):
    text: str = Field(..., min_length=1, max_length=2000, description="Note content")
    tags: list[str] = Field(default_factory=list, description="Optional tags for categorization")


@tool("note_add", "Save a quick note with optional tags.",
      NoteAddArgs, Tier.T0, summary=lambda a: f"Note: {a.text[:40]}")
async def note_add(a: NoteAddArgs) -> dict:
    notes = _load()
    entry = {"id": len(notes) + 1, "text": a.text, "tags": a.tags,
             "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    notes.append(entry)
    _save(notes)
    return {"saved": entry}


class NoteListArgs(Args):
    tag: str = Field("", max_length=100, description="Filter by tag (empty for all)")
    limit: int = Field(10, ge=1, le=50, description="Max notes to return")


@tool("note_list", "List recent notes, optionally filtered by tag.",
      NoteListArgs, Tier.T0, summary=lambda a: "List notes")
async def note_list(a: NoteListArgs) -> dict:
    notes = _load()
    if a.tag:
        notes = [n for n in notes if a.tag.lower() in [t.lower() for t in n.get("tags", [])]]
    return {"notes": notes[-a.limit:], "total": len(notes)}


class NoteSearchArgs(Args):
    query: str = Field(..., min_length=1, max_length=200, description="Text to search for")


@tool("note_search", "Search notes by text content.",
      NoteSearchArgs, Tier.T0, taints=True, summary=lambda a: f"Search notes: {a.query[:40]}")
async def note_search(a: NoteSearchArgs) -> dict:
    notes = _load()
    q = a.query.lower()
    matches = [n for n in notes if q in n["text"].lower() or q in " ".join(n.get("tags", [])).lower()]
    return {"results": matches[-20:], "count": len(matches)}


class NoteDeleteArgs(Args):
    note_id: int = Field(..., ge=1, description="Note ID to delete")


@tool("note_delete", "Delete a note by its ID.",
      NoteDeleteArgs, Tier.T1, summary=lambda a: f"Delete note #{a.note_id}")
async def note_delete(a: NoteDeleteArgs) -> dict:
    notes = _load()
    before = len(notes)
    notes = [n for n in notes if n.get("id") != a.note_id]
    if len(notes) == before:
        raise ToolError(f"Note #{a.note_id} not found.")
    _save(notes)
    return {"deleted": a.note_id, "remaining": len(notes)}


# --- Daily notes (idea #61) ---

from datetime import datetime as _dt

_DAILY_DIR = DATA_DIR / "notes" / "daily"


def _daily_path(date: str = "") -> Path:
    if not date:
        date = _dt.now().strftime("%Y-%m-%d")
    return _DAILY_DIR / f"{date}.md"


_DAILY_TEMPLATE = """# Daily Note - {date}

## Tasks
-

## Notes


## Events

"""


class DailyNoteArgs(Args):
    date: str = Field("", description="YYYY-MM-DD (empty for today)")


@tool("note_today", "Get or create today's daily note.",
      DailyNoteArgs, Tier.T0, summary=lambda a: f"Daily note: {a.date or 'today'}")
async def note_today(a: DailyNoteArgs) -> dict:
    _DAILY_DIR.mkdir(parents=True, exist_ok=True)
    date = a.date or _dt.now().strftime("%Y-%m-%d")
    path = _daily_path(date)
    created = False
    if not path.exists():
        path.write_text(_DAILY_TEMPLATE.format(date=date), "utf-8")
        created = True
    text = path.read_text("utf-8")
    return {"date": date, "path": str(path), "created": created, "content": text[:5000]}


class DailyAppendArgs(Args):
    text: str = Field(..., min_length=1, max_length=5000)
    section: str = Field("Notes", description="Section to append under: Tasks, Notes, or Events")


@tool("note_daily_append", "Append text to today's daily note under a section.",
      DailyAppendArgs, Tier.T1, summary=lambda a: f"Append to daily/{a.section}")
async def note_daily_append(a: DailyAppendArgs) -> dict:
    _DAILY_DIR.mkdir(parents=True, exist_ok=True)
    date = _dt.now().strftime("%Y-%m-%d")
    path = _daily_path(date)
    if not path.exists():
        path.write_text(_DAILY_TEMPLATE.format(date=date), "utf-8")
    content = path.read_text("utf-8")
    marker = f"## {a.section}"
    if marker in content:
        idx = content.index(marker) + len(marker)
        nl = content.index("\n", idx)
        content = content[:nl + 1] + a.text + "\n" + content[nl + 1:]
    else:
        content += f"\n{marker}\n{a.text}\n"
    path.write_text(content, "utf-8")
    return {"date": date, "appended": len(a.text)}
