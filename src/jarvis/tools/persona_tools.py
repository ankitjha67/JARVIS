"""Editable persona and standing instructions (idea 5)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import Field

from ..config import DATA_DIR
from ..policy import Tier
from .registry import Args, ToolError, tool

_PERSONA_FILE = DATA_DIR / "persona.md"

_DEFAULT_PERSONA = """You are JARVIS, a personal AI assistant. You are helpful, concise, and proactive.
Speak naturally. When unsure, ask. Prioritize safety and privacy.
"""


def _load_persona() -> str:
    if _PERSONA_FILE.exists():
        return _PERSONA_FILE.read_text("utf-8")
    return _DEFAULT_PERSONA


def _save_persona(text: str) -> None:
    _PERSONA_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PERSONA_FILE.write_text(text, "utf-8")


class PersonaGetArgs(Args):
    pass


@tool("persona_get", "Get the current persona and standing instructions.",
      PersonaGetArgs, Tier.T0, summary=lambda a: "Get persona")
async def persona_get(a: PersonaGetArgs) -> dict:
    text = await asyncio.to_thread(_load_persona)
    return {"persona": text, "path": str(_PERSONA_FILE)}


class PersonaSetArgs(Args):
    text: str = Field(..., min_length=1, max_length=5000, description="New persona/instructions text")


@tool("persona_set", "Update the persona and standing instructions.",
      PersonaSetArgs, Tier.T2, summary=lambda a: f"Set persona ({len(a.text)} chars)")
async def persona_set(a: PersonaSetArgs) -> dict:
    await asyncio.to_thread(_save_persona, a.text)
    return {"saved": True, "length": len(a.text)}


class PersonaAppendArgs(Args):
    instruction: str = Field(..., min_length=1, max_length=2000, description="Instruction to add")


@tool("persona_append", "Add a standing instruction to the persona.",
      PersonaAppendArgs, Tier.T1, summary=lambda a: f"Add instruction: {a.instruction[:40]}")
async def persona_append(a: PersonaAppendArgs) -> dict:
    current = await asyncio.to_thread(_load_persona)
    updated = current.rstrip() + "\n" + a.instruction + "\n"
    await asyncio.to_thread(_save_persona, updated)
    return {"added": a.instruction, "total_length": len(updated)}
