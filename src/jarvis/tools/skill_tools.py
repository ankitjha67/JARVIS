"""Skill tools: list, create, get (ideas 62-63)."""

from __future__ import annotations

from pydantic import Field

from ..policy import Tier
from .registry import Args, tool


class SkillListArgs(Args):
    pass


@tool("skill_list", "List available skills.",
      SkillListArgs, Tier.T0, summary=lambda a: "List skills")
async def skill_list(a: SkillListArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    return {"skills": hub.skills.list_skills()}


class SkillCreateArgs(Args):
    name: str = Field(..., min_length=1, max_length=50)
    description: str = Field(..., min_length=1, max_length=200)
    triggers: list[str] = Field(..., min_length=1, max_length=10)
    instructions: str = Field(..., min_length=1, max_length=5000)


@tool("skill_create", "Create a new skill from instructions or recorded macro.",
      SkillCreateArgs, Tier.T1, summary=lambda a: f"Create skill: {a.name}")
async def skill_create(a: SkillCreateArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    path = hub.skills.create(a.name, a.description, a.triggers, a.instructions)
    return {"created": True, "path": str(path)}
