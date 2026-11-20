"""Vision tools: screen capture, OCR, screen search (ideas 18-25)."""

from __future__ import annotations

import asyncio
import base64

from pydantic import Field

from ..policy import Tier
from .registry import Args, tool


class ScreenshotArgs(Args):
    monitor: int = Field(0, ge=0, le=4, description="Monitor number (0 = primary)")
    region: list[int] | None = Field(None, description="[x, y, w, h] crop region")


@tool("screen_capture", "Take a screenshot of the screen or a region.",
      ScreenshotArgs, Tier.T0, summary=lambda a: "Capture screen")
async def screen_capture(a: ScreenshotArgs) -> dict:
    from ..vision import capture_screen
    region_tuple = tuple(a.region) if a.region and len(a.region) == 4 else None
    img = await asyncio.to_thread(capture_screen, a.monitor, region_tuple)
    b64 = base64.b64encode(img).decode()
    return {"image_base64": b64, "size_bytes": len(img), "format": "png"}


class OcrArgs(Args):
    text: str = Field("", description="Unused — runs on latest screenshot")


@tool("screen_ocr", "Run OCR on the latest screenshot and return text.",
      OcrArgs, Tier.T0, summary=lambda a: "OCR screen")
async def screen_ocr(a: OcrArgs) -> dict:
    from ..vision import capture_screen, ocr_local
    img = await asyncio.to_thread(capture_screen)
    text = await asyncio.to_thread(ocr_local, img)
    return {"text": text[:5000], "truncated": len(text) > 5000}


class ScreenSearchArgs(Args):
    query: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(5, ge=1, le=20)


@tool("screen_search", "Search through screen capture history (OCR text).",
      ScreenSearchArgs, Tier.T0, taints=True, summary=lambda a: f"Search screens: {a.query[:40]}")
async def screen_search(a: ScreenSearchArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    if not hasattr(hub, 'screen_history') or hub.screen_history is None:
        return {"results": [], "note": "Screen history not running"}
    results = hub.screen_history.search(a.query, limit=a.limit)
    return {"results": results, "count": len(results)}
