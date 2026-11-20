"""File knowledge tools: index, search, web clips, daily notes (ideas 55-61)."""

from __future__ import annotations


from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool


class FileSearchArgs(Args):
    query: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(10, ge=1, le=50)


@tool("file_search", "Full-text search across indexed files.",
      FileSearchArgs, Tier.T0, taints=True, summary=lambda a: f"Search files: {a.query[:40]}")
async def file_search(a: FileSearchArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    results = hub.file_index.search(a.query, limit=a.limit)
    return {"results": results, "count": len(results)}


class FileNameSearchArgs(Args):
    query: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(10, ge=1, le=50)


@tool("file_name_search", "Search file names in indexed folders.",
      FileNameSearchArgs, Tier.T0, summary=lambda a: f"Name search: {a.query[:40]}")
async def file_name_search(a: FileNameSearchArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    results = hub.file_index.search_names(a.query, limit=a.limit)
    return {"results": results, "count": len(results)}


class IndexFolderArgs(Args):
    folder: str = Field(..., min_length=1, max_length=400)


@tool("file_index_folder", "Index a folder for full-text search.",
      IndexFolderArgs, Tier.T1, summary=lambda a: f"Index {a.folder[:60]}")
async def file_index_folder(a: IndexFolderArgs) -> dict:
    import asyncio
    from pathlib import Path
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    folder = Path(a.folder).resolve()
    if not folder.is_dir():
        raise ToolError(f"Not a directory: {a.folder}")
    _BLOCKED = {"windows", "system32", "syswow64", "program files", "programdata"}
    if any(part.lower() in _BLOCKED for part in folder.parts):
        raise ToolError("Cannot index system directories.")
    count = await asyncio.to_thread(hub.file_index.index_folder, folder)
    return {"indexed": count, "folder": str(folder)}


class WebClipArgs(Args):
    url: str = Field(..., min_length=1, max_length=2000)
    title: str = Field("", max_length=200)
    content: str = Field("", max_length=5000, description="Page content or notes to save")


@tool("web_clip", "Save a web page bookmark with notes.",
      WebClipArgs, Tier.T0, summary=lambda a: f"Clip: {a.url[:60]}")
async def web_clip(a: WebClipArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    cid = hub.file_index.clip_web(a.url, a.title, a.content)
    return {"clipped": True, "id": cid}


class DailyNoteArgs(Args):
    text: str = Field(..., min_length=1, max_length=5000)


@tool("daily_note", "Append to today's daily note.",
      DailyNoteArgs, Tier.T0, summary=lambda a: f"Daily note: {a.text[:40]}")
async def daily_note(a: DailyNoteArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    hub.file_index.daily_note(a.text)
    return {"saved": True}
