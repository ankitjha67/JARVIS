"""Trace requirements to UAT tests and review evidence from local CSV or XLSX registers."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from typing import Any

from pydantic import Field

from ..policy import Tier
from ..reconciliation import _read
from .registry import Args, ToolError, tool

_RESULTS = {"pass", "fail", "blocked", "not run"}


def trace_uat(requirements: str, tests: str, previous_requirements: str | None,
              offset: int, limit: int) -> dict[str, Any]:
    requirement_columns, entries = _read(requirements, ["requirement_id"], None)
    test_columns, cases = _read(tests, ["test_id", "requirement_id"], None)
    if "requirement" not in requirement_columns or not {"result", "evidence"} <= set(test_columns):
        raise ValueError("Requirements need requirement_id and requirement. Tests need test_id, requirement_id, result and evidence.")
    previous: dict = {}
    if previous_requirements:
        columns, previous = _read(previous_requirements, ["requirement_id"], None)
        if "requirement" not in columns:
            raise ValueError("The previous requirements register needs a requirement column.")

    linked = defaultdict(list)
    orphaned = []
    orphan_count = 0
    for (test_id, requirement_id), (row, record) in cases.items():
        result = str(record["result"] or "").strip().lower()
        if result not in _RESULTS:
            raise ValueError(f"Test row {row} needs a result of pass, fail, blocked, or not run.")
        evidence = str(record["evidence"] or "").strip()
        test = {"test_id": test_id[:150], "row": row, "result": result, "evidence": evidence[:350]}
        if (requirement_id,) in entries:
            linked[requirement_id].append(test)
        else:
            orphan_count += 1
            if len(orphaned) < limit:
                orphaned.append({"requirement_id": requirement_id[:150], **test})

    totals: Counter[str] = Counter()
    results = []
    for index, ((requirement_id,), (row, record)) in enumerate(entries.items()):
        cases_for_requirement = linked[requirement_id]
        prior = previous.get((requirement_id,))
        description = str(record["requirement"] or "")
        if not description.strip():
            raise ValueError(f"Requirement row {row} needs a description.")
        if not previous_requirements:
            revision = "not compared"
        elif prior is None:
            revision = "added"
        elif prior[1]["requirement"] != record["requirement"]:
            revision = "changed"
        else:
            revision = "unchanged"
        coverage = "verified"
        if not cases_for_requirement:
            coverage = "uncovered"
        elif any(case["result"] != "pass" or not case["evidence"] for case in cases_for_requirement):
            coverage = "needs evidence or retest"
        totals[coverage] += 1
        if offset <= index < offset + limit:
            results.append({
                "requirement_id": requirement_id[:150], "requirement": description[:500], "source_row": row,
                "coverage": coverage, "revision": revision,
                "retest_due": bool(cases_for_requirement and revision in ("added", "changed")),
                "test_count": len(cases_for_requirement), "tests": cases_for_requirement[:8],
            })
    removed = [key[0][:150] for key in previous.keys() - entries.keys()]
    return {
        "coverage_totals": dict(totals), "requirement_count": len(entries), "requirements": results,
        "orphan_test_count": orphan_count, "orphan_test_sample": orphaned,
        "removed_requirement_count": len(removed), "removed_requirement_sample": sorted(removed)[:limit],
        "next_offset": offset + len(results) if offset + len(results) < len(entries) else None,
    }


class UatTraceArgs(Args):
    requirements: str = Field(min_length=1, max_length=4096)
    tests: str = Field(min_length=1, max_length=4096)
    previous_requirements: str | None = Field(None, max_length=4096)
    offset: int = Field(0, ge=0)
    limit: int = Field(10, ge=1, le=25)


@tool(
    "uat_trace",
    "Build a local UAT coverage and regression review from requirement and test registers with source-row evidence.",
    UatTraceArgs, Tier.T0, summary=lambda _: "Trace requirements to UAT evidence", taints=True,
)
async def uat_trace(args: UatTraceArgs) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(trace_uat, **args.model_dump())
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
