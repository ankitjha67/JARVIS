"""Extract labelled document fields with source lines and exception review."""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..policy import Tier
from .registry import Args, ToolError, tool
from .regulatory_tools import _document_pages

_LABELLED_LINE = re.compile(r"^\s*([^:=]{1,100}?)\s*[:=]\s*(.*?)\s*$")


class IntakeField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=10)


class DocumentIntakeArgs(Args):
    paths: list[str] = Field(min_length=1, max_length=10)
    fields: list[IntakeField] = Field(min_length=1, max_length=20)
    unique_field: str | None = Field(None, max_length=80, description="Flag repeated values for this field.")


def _normal(value: str) -> str:
    return " ".join(value.casefold().split())


def review_intake(args: DocumentIntakeArgs) -> dict[str, Any]:
    lookup: dict[str, str] = {}
    field_names: set[str] = set()
    for field in args.fields:
        if not field.name.strip() or _normal(field.name) in field_names:
            raise ValueError("Field names must be distinct and nonempty.")
        field_names.add(_normal(field.name))
        for label in (field.name, *field.aliases):
            key = _normal(label)
            if not key or len(label) > 100 or key in lookup and lookup[key] != field.name:
                raise ValueError("Each field label must be unique across fields and at most 100 characters.")
            lookup[key] = field.name

    unique_field = None
    if args.unique_field is not None:
        unique_field = lookup.get(_normal(args.unique_field))
        if unique_field is None:
            raise ValueError("The unique field must refer to a configured field.")
    documents = []
    identities: dict[str, list[dict]] = defaultdict(list)
    for raw in args.paths:
        if not raw.strip() or len(raw) > 4096:
            raise ValueError("Every document path must be between 1 and 4096 characters.")
        path, pages = _document_pages(raw)
        candidates: dict[str, list[dict]] = defaultdict(list)
        counts: dict[str, int] = defaultdict(int)
        identity = None
        for page, content in pages:
            for line, text in enumerate(content.splitlines(), 1):
                match = _LABELLED_LINE.match(text)
                if not match:
                    continue
                field_name = lookup.get(_normal(match[1]))
                value = match[2]
                if field_name is None or not value:
                    continue
                counts[field_name] += 1
                if counts[field_name] <= 3:
                    candidates[field_name].append({
                        "value": value[:300], "truncated": len(value) > 300, "page": page, "line": line,
                    })
                if field_name == unique_field:
                    identity = _normal(value)
        fields = []
        exceptions = []
        for field in args.fields:
            count = counts[field.name]
            if count != 1:
                exceptions.append({"field": field.name, "issue": "missing" if count == 0 else "ambiguous"})
            fields.append({"name": field.name, "match_count": count, "candidates": candidates[field.name]})
        result = {"source": str(path), "fields": fields, "exceptions": exceptions}
        if unique_field and identity and counts[unique_field] == 1:
            identities[identity].append(result)
        documents.append(result)
    for matches in identities.values():
        if len(matches) > 1:
            for document in matches:
                document["exceptions"].append({"field": unique_field, "issue": "duplicate across documents"})
    return {
        "documents": documents,
        "needs_review": sum(bool(document["exceptions"]) for document in documents),
    }


@tool(
    "document_intake",
    "Extract configured Label: Value fields from local documents with page or line evidence and exceptions.",
    DocumentIntakeArgs, Tier.T0, summary=lambda a: f"Review {len(a.paths)} intake documents", taints=True,
)
async def document_intake(args: DocumentIntakeArgs) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(review_intake, args)
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
