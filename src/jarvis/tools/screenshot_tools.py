"""Clip Curator: screenshot capture, organization, and search."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field

from ..config import DATA_DIR
from ..policy import Tier
from .registry import Args, ToolError, tool

_CLIPS_DIR = DATA_DIR / "clips"
_INDEX_FILE = _CLIPS_DIR / "index.json"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW


def _load_index() -> list[dict]:
    if _INDEX_FILE.exists():
        try:
            return json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_index(entries: list[dict]) -> None:
    _CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    _INDEX_FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Capture screenshot
# ---------------------------------------------------------------------------

class CaptureArgs(Args):
    label: str = Field("", max_length=200, description="Description or label for the screenshot")
    tags: list[str] = Field(default_factory=list, max_length=10, description="Tags for organization")
    region: str = Field("full", pattern="^(full|active)$", description="full screen or active window")


def _capture(region: str) -> Path:
    from PIL import ImageGrab

    _CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = _CLIPS_DIR / f"clip_{ts}.png"

    if region == "active":
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        rect = win32gui.GetWindowRect(hwnd)
        img = ImageGrab.grab(bbox=rect)
    else:
        img = ImageGrab.grab()

    img.save(str(path), "PNG")
    return path


@tool("clip_capture", "Take a screenshot (full screen or active window) and save it with metadata.",
      CaptureArgs, Tier.T1, summary=lambda a: f"Screenshot: {a.label or a.region}")
async def clip_capture(a: CaptureArgs) -> dict:
    path = await asyncio.to_thread(_capture, a.region)
    size_kb = round(path.stat().st_size / 1024, 1)
    entry = {
        "id": path.stem,
        "path": str(path),
        "label": a.label,
        "tags": a.tags,
        "size_kb": size_kb,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    index = _load_index()
    index.append(entry)
    _save_index(index)
    return entry


# ---------------------------------------------------------------------------
# List / search clips
# ---------------------------------------------------------------------------

class ClipListArgs(Args):
    tag: str = Field("", max_length=50)
    search: str = Field("", max_length=200)
    limit: int = Field(20, ge=1, le=100)


@tool("clip_list", "List saved screenshots, optionally filtered by tag or label search.",
      ClipListArgs, Tier.T0, summary=lambda a: "List screenshots")
async def clip_list(a: ClipListArgs) -> dict:
    index = _load_index()
    items = list(reversed(index))
    if a.tag:
        items = [e for e in items if a.tag in e.get("tags", [])]
    if a.search:
        q = a.search.lower()
        items = [e for e in items if q in e.get("label", "").lower() or q in str(e.get("tags", "")).lower()]
    return {"clips": items[: a.limit], "total": len(items)}


# ---------------------------------------------------------------------------
# Delete clip
# ---------------------------------------------------------------------------

class ClipDeleteArgs(Args):
    clip_id: str = Field(..., min_length=1, max_length=100)


@tool("clip_delete", "Delete a screenshot by ID.", ClipDeleteArgs, Tier.T1,
      summary=lambda a: f"Delete clip: {a.clip_id}")
async def clip_delete(a: ClipDeleteArgs) -> dict:
    index = _load_index()
    entry = next((e for e in index if e["id"] == a.clip_id), None)
    if not entry:
        raise ToolError(f"Clip not found: {a.clip_id}")
    path = Path(entry["path"])
    if path.exists():
        path.unlink()
    index = [e for e in index if e["id"] != a.clip_id]
    _save_index(index)
    return {"deleted": a.clip_id}


# ---------------------------------------------------------------------------
# Tag a clip
# ---------------------------------------------------------------------------

class ClipTagArgs(Args):
    clip_id: str = Field(..., min_length=1, max_length=100)
    tags: list[str] = Field(..., min_length=1, max_length=10)


@tool("clip_tag", "Add tags to a screenshot.", ClipTagArgs, Tier.T0,
      summary=lambda a: f"Tag clip: {a.clip_id}")
async def clip_tag(a: ClipTagArgs) -> dict:
    index = _load_index()
    for e in index:
        if e["id"] == a.clip_id:
            existing = set(e.get("tags", []))
            existing.update(a.tags)
            e["tags"] = sorted(existing)
            _save_index(index)
            return {"tagged": True, "tags": e["tags"]}
    raise ToolError(f"Clip not found: {a.clip_id}")
