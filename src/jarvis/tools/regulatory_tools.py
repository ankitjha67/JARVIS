"""Compare document revisions by numbered clause and attach source evidence."""

from __future__ import annotations

import asyncio
import csv
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import Field

from ..policy import Tier
from .file_processor_tools import _extract, _guard
from .registry import Args, ToolError, tool

_CLAUSE = re.compile(r"^\s*(?:#{1,6}\s+)?(?:(?:section|article|clause|rule)\s+)?"
                     r"([0-9]+(?:[.][0-9]+)*[A-Za-z]?)\s*[.)]?\s+(\S.*)$", re.IGNORECASE)


def _document_pages(raw_path: str) -> tuple[Path, list[tuple[int | None, str]]]:
    path = _guard(raw_path)
    if path.suffix.lower() not in {".pdf", ".docx", ".txt", ".md"} or not path.is_file():
        raise ToolError("Select a PDF, DOCX, Markdown, or text document.")
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ToolError("Documents may be at most 25 MiB.")
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ToolError("Install pypdf for text-based PDF comparison.") from exc
        reader = PdfReader(str(path))
        if len(reader.pages) > 500:
            raise ToolError("PDF documents may contain at most 500 pages.")
        pages = [(number, page.extract_text() or "") for number, page in enumerate(reader.pages, 1)]
    else:
        _, content = _extract(path)
        pages = [(None, content)]
    return path, pages


def _clauses(raw_path: str) -> dict[str, dict[str, Any]]:
    path, pages = _document_pages(raw_path)
    clauses: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    body: list[str] = []
    current = "preamble"

    def save() -> None:
        if current in clauses:
            clauses[current]["text"] = " ".join(body).strip()

    for page, content in pages:
        for line, value in enumerate(content.splitlines(), 1):
            value = " ".join(value.split())
            match = _CLAUSE.match(value)
            if match:
                save()
                number, title = match.groups()
                counts[number] += 1
                current = number if counts[number] == 1 else f"{number}#{counts[number]}"
                clauses[current] = {"source": path.name, "page": page, "line": line}
                body = [title]
            elif value:
                clauses.setdefault(current, {"source": path.name, "page": page, "line": line})
                body.append(value)
    save()
    if not counts:
        raise ToolError(f"{path.name} has no recognized numbered clauses. Use selectable text and numbered headings.")
    if len(clauses) > 5000:
        raise ToolError("Documents may contain at most 5,000 clauses.")
    return clauses


def _controls(raw_path: str | None) -> dict[str, list[dict[str, str]]]:
    if raw_path is None:
        return {}
    path = _guard(raw_path)
    if path.suffix.lower() != ".csv" or path.stat().st_size > 1024 * 1024:
        raise ToolError("The control register must be a CSV of at most 1 MiB.")
    controls: dict[str, list[dict[str, str]]] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"clause", "control", "owner"} <= set(reader.fieldnames or []):
            raise ToolError("The control register needs clause, control, and owner columns.")
        for row in reader:
            clause = (row["clause"] or "").strip()
            if clause:
                controls.setdefault(clause, []).append({
                    "control": (row["control"] or "")[:250],
                    "owner": (row["owner"] or "")[:120],
                    "policy": (row.get("policy") or "")[:250],
                })
    return controls


def _preview(clause: dict[str, Any] | None) -> dict[str, Any] | None:
    if clause is None:
        return None
    text = clause["text"]
    return {**clause, "text": text[:750], "text_truncated": len(text) > 750}


def compare_documents(first: str, second: str, control_register: str | None, offset: int, limit: int) -> dict:
    before, after = _clauses(first), _clauses(second)
    controls = _controls(control_register)
    counts = dict.fromkeys(("added", "removed", "changed", "unchanged"), 0)
    changes = []
    for clause in dict.fromkeys((*before, *after)):
        old, new = before.get(clause), after.get(clause)
        if old is None:
            status = "added"
        elif new is None:
            status = "removed"
        elif old["text"] == new["text"]:
            status = "unchanged"
        else:
            status = "changed"
        counts[status] += 1
        if status != "unchanged":
            changes.append((clause, status, old, new))
    page = changes[offset:offset + limit]
    return {
        "documents": [Path(first).name, Path(second).name],
        "counts": counts,
        "changes": [{
            "clause": clause, "status": status, "before": _preview(old), "after": _preview(new),
            "review": "check mapped controls and owner" if clause in controls else "assign owner and review controls",
            "mapped_controls": controls.get(clause, [])[:10],
        } for clause, status, old, new in page],
        "next_offset": offset + len(page) if offset + len(page) < len(changes) else None,
    }


class DocumentCompareArgs(Args):
    first: str = Field(min_length=1, max_length=4096)
    second: str = Field(min_length=1, max_length=4096)
    control_register: str | None = Field(None, max_length=4096, description="Optional clause/control/owner/policy CSV.")
    offset: int = Field(0, ge=0)
    limit: int = Field(10, ge=1, le=25)


@tool(
    "regulatory_compare",
    "Compare two local numbered regulation revisions, with page or line evidence and optional control mappings.",
    DocumentCompareArgs, Tier.T0, summary=lambda _: "Compare numbered document clauses", taints=True,
)
async def regulatory_compare(args: DocumentCompareArgs) -> dict:
    try:
        return await asyncio.to_thread(compare_documents, **args.model_dump())
    except (OSError, ValueError, csv.Error) as exc:
        raise ToolError(str(exc)) from exc
