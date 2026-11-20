"""Digest tools: morning briefing, on-demand summary (inspired by OpenJarvis)."""

from __future__ import annotations

from pydantic import Field

from ..policy import Tier
from .registry import Args, tool


class DigestArgs(Args):
    pass


@tool("digest_now", "Build and deliver a morning-style digest right now.",
      DigestArgs, Tier.T0, summary=lambda a: "Build digest")
async def digest_now(a: DigestArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    from ..digest import deliver_digest
    return await deliver_digest(hub)


class DigestPreviewArgs(Args):
    pass


@tool("digest_preview", "Preview the digest without delivering it.",
      DigestPreviewArgs, Tier.T0, summary=lambda a: "Preview digest")
async def digest_preview(a: DigestPreviewArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    from ..digest import build_digest
    text = await build_digest(hub)
    return {"digest": text}
