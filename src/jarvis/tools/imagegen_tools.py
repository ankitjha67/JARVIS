"""Imagination Engine: local image generation via Stable Diffusion (if available) or online APIs."""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field

from ..config import DATA_DIR
from .. import host
from ..policy import Tier
from .registry import Args, ToolError, tool

_OUTPUT_DIR = DATA_DIR / "generated_images"
_NO_WINDOW = host.NO_WINDOW


# ---------------------------------------------------------------------------
# Generate image via local SD or fallback description
# ---------------------------------------------------------------------------

class ImageGenArgs(Args):
    prompt: str = Field(..., min_length=1, max_length=1000, description="Text prompt for image generation")
    negative_prompt: str = Field("", max_length=500, description="What to avoid in the image")
    width: int = Field(512, ge=256, le=1024)
    height: int = Field(512, ge=256, le=1024)
    steps: int = Field(20, ge=1, le=50)


def _check_sd_available() -> str | None:
    """Check if a local SD-webui API is running."""
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:7860/sdapi/v1/options", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            return "http://127.0.0.1:7860"
    except Exception:
        pass
    try:
        req = urllib.request.Request("http://127.0.0.1:8188/system_stats", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            return "http://127.0.0.1:8188"
    except Exception:
        return None


def _generate_sd(url: str, prompt: str, neg: str, w: int, h: int, steps: int) -> Path:
    import base64
    import json
    import urllib.request

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "prompt": prompt,
        "negative_prompt": neg,
        "width": w, "height": h,
        "steps": steps,
        "sampler_name": "Euler a",
        "batch_size": 1,
    }).encode()
    req = urllib.request.Request(
        f"{url}/sdapi/v1/txt2img",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    images = data.get("images", [])
    if not images:
        raise ToolError("SD returned no images.")
    img_data = base64.b64decode(images[0])
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = _OUTPUT_DIR / f"img_{ts}.png"
    out.write_bytes(img_data)
    return out


@tool("image_generate", "Generate an image from a text prompt using local Stable Diffusion (if running) "
      "or return setup instructions. Requires a local SD WebUI on port 7860.",
      ImageGenArgs, Tier.T2, sensitive=True, summary=lambda a: f"Generate image: {a.prompt[:50]}")
async def image_generate(a: ImageGenArgs) -> dict:
    sd_url = await asyncio.to_thread(_check_sd_available)
    if not sd_url:
        return {
            "available": False,
            "prompt_saved": a.prompt,
            "instructions": "No local Stable Diffusion API detected. Start AUTOMATIC1111 WebUI or ComfyUI "
                           "on port 7860/8188. Or install via: "
                           "git clone https://github.com/AUTOMATIC1111/stable-diffusion-webui && "
                           "cd stable-diffusion-webui && webui-user.bat --api",
        }
    out = await asyncio.to_thread(_generate_sd, sd_url, a.prompt, a.negative_prompt, a.width, a.height, a.steps)
    return {"generated": True, "path": str(out), "sd_api": sd_url}


# ---------------------------------------------------------------------------
# List generated images
# ---------------------------------------------------------------------------

class ImageListArgs(Args):
    limit: int = Field(20, ge=1, le=100)


@tool("image_list", "List recently generated images.",
      ImageListArgs, Tier.T0, summary=lambda a: "List generated images")
async def image_list(a: ImageListArgs) -> dict:
    if not _OUTPUT_DIR.exists():
        return {"images": [], "total": 0}
    files = sorted(_OUTPUT_DIR.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
    items = [{"path": str(f), "size_kb": round(f.stat().st_size / 1024, 1),
              "created": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()}
             for f in files[: a.limit]]
    return {"images": items, "total": len(files)}
