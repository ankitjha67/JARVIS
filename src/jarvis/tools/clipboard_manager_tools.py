"""Clipboard Manager: history, pin, and search across clipboard entries."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from pydantic import Field

from .. import host
from ..policy import Tier
from .registry import Args, ToolError, tool

_HISTORY: list[dict] = []
_PINNED: list[dict] = []
_MAX_HISTORY = 50


def _read_clipboard() -> str:
    return host.clipboard_get() or ""


def _write_clipboard(text: str) -> None:
    host.clipboard_set(text)


# ---------------------------------------------------------------------------
# Capture current clipboard to history
# ---------------------------------------------------------------------------

class ClipCaptureArgs(Args):
    pass


@tool("clipboard_capture", "Snapshot the current clipboard text into the clipboard history.",
      ClipCaptureArgs, Tier.T0, summary=lambda a: "Capture clipboard")
async def clipboard_capture(a: ClipCaptureArgs) -> dict:
    text = await asyncio.to_thread(_read_clipboard)
    if not text:
        raise ToolError("Clipboard is empty or has no text.")
    entry = {"text": text[:2000], "ts": datetime.now(timezone.utc).isoformat()}
    if _HISTORY and _HISTORY[-1]["text"] == entry["text"]:
        return {"captured": False, "reason": "duplicate"}
    _HISTORY.append(entry)
    if len(_HISTORY) > _MAX_HISTORY:
        _HISTORY.pop(0)
    return {"captured": True, "length": len(text), "history_size": len(_HISTORY)}


# ---------------------------------------------------------------------------
# History / search
# ---------------------------------------------------------------------------

class ClipHistoryArgs(Args):
    search: str = Field("", max_length=200, description="Filter history by substring")
    limit: int = Field(10, ge=1, le=50)


@tool("clipboard_history", "Browse or search the clipboard history.",
      ClipHistoryArgs, Tier.T0, summary=lambda a: "Clipboard history")
async def clipboard_history(a: ClipHistoryArgs) -> dict:
    items = list(reversed(_HISTORY))
    if a.search:
        q = a.search.lower()
        items = [e for e in items if q in e["text"].lower()]
    return {"entries": items[: a.limit], "total": len(items)}


# ---------------------------------------------------------------------------
# Pin / unpin
# ---------------------------------------------------------------------------

class ClipPinArgs(Args):
    text: str = Field("", max_length=2000, description="Text to pin (empty = pin current clipboard)")
    label: str = Field("", max_length=100)


@tool("clipboard_pin", "Pin a text snippet for quick recall later.",
      ClipPinArgs, Tier.T0, summary=lambda a: f"Pin: {a.label or 'snippet'}")
async def clipboard_pin(a: ClipPinArgs) -> dict:
    text = a.text
    if not text:
        text = await asyncio.to_thread(_read_clipboard)
    if not text:
        raise ToolError("Nothing to pin.")
    entry = {"text": text[:2000], "label": a.label, "ts": datetime.now(timezone.utc).isoformat()}
    _PINNED.append(entry)
    return {"pinned": True, "label": a.label, "total_pinned": len(_PINNED)}


class ClipPinnedArgs(Args):
    pass


@tool("clipboard_pinned", "List all pinned clipboard snippets.",
      ClipPinnedArgs, Tier.T0, summary=lambda a: "Pinned snippets")
async def clipboard_pinned(a: ClipPinnedArgs) -> dict:
    return {"pinned": list(reversed(_PINNED)), "total": len(_PINNED)}


# ---------------------------------------------------------------------------
# Paste from history
# ---------------------------------------------------------------------------

class ClipPasteArgs(Args):
    index: int = Field(0, ge=0, description="Index into history (0 = most recent)")


@tool("clipboard_paste", "Copy a history entry back to the clipboard.",
      ClipPasteArgs, Tier.T1, summary=lambda a: f"Restore clipboard #{a.index}")
async def clipboard_paste(a: ClipPasteArgs) -> dict:
    items = list(reversed(_HISTORY))
    if a.index >= len(items):
        raise ToolError(f"History only has {len(items)} entries.")
    text = items[a.index]["text"]
    await asyncio.to_thread(_write_clipboard, text)
    return {"restored": True, "preview": text[:100]}
