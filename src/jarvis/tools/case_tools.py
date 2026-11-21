"""Local case files for correspondence, evidence references, and next actions."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Literal

from pydantic import Field

from ..policy import Tier
from ..store import Store
from . import context
from .registry import Args, ToolError, tool

CaseState = Literal["open", "waiting", "resolved"]


def _store() -> Store:
    hub = getattr(context, "_hub", None)
    if hub is None:
        raise ToolError("Case storage is unavailable.")
    return hub.store


class CaseSaveArgs(Args):
    case_id: str | None = Field(None, pattern=r"^[a-f0-9]{16}$")
    revision: int | None = Field(None, ge=1, description="Required with case_id. Read the current case revision first.")
    title: str = Field(min_length=1, max_length=180)
    status: CaseState = "open"
    deadline: date | None = None
    next_action: str = Field("", max_length=600)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    draft: str = Field("", max_length=6000)


@tool(
    "case_save",
    "Create a local case file or replace its fields using the current revision. Stores evidence references and a correspondence draft.",
    CaseSaveArgs, Tier.T2, summary=lambda a: f"Save case file: {a.title}", sensitive=True,
)
async def case_save(args: CaseSaveArgs) -> dict:
    if bool(args.case_id) != bool(args.revision):
        raise ToolError("Supply both case ID and revision when updating a case.")
    if not args.title.strip() or any(not item.strip() or len(item) > 1000 for item in args.evidence):
        raise ToolError("Enter a title and nonempty evidence references of at most 1,000 characters.")
    data = args.model_dump(mode="json", exclude={"case_id", "revision"})
    try:
        return await asyncio.to_thread(_store().save_case, data, args.case_id, args.revision)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


class CaseGetArgs(Args):
    case_id: str = Field(pattern=r"^[a-f0-9]{16}$")


@tool("case_get", "Read a case file including its current revision, evidence and draft.",
      CaseGetArgs, Tier.T0, summary=lambda _: "Read case file", taints=True)
async def case_get(args: CaseGetArgs) -> dict:
    result = await asyncio.to_thread(_store().get_case, args.case_id)
    if result is None:
        raise ToolError("Case file not found.")
    return result


class CaseListArgs(Args):
    status: CaseState | None = None
    limit: int = Field(20, ge=1, le=50)


@tool("case_list", "List local case files ordered by deadline, with next actions and status.",
      CaseListArgs, Tier.T0, summary=lambda _: "List case files", taints=True)
async def case_list(args: CaseListArgs) -> dict:
    return {"cases": await asyncio.to_thread(_store().list_cases, args.status, args.limit)}
