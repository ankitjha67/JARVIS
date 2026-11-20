"""Deep research tools: multi-step research with source verification (Mark-LVII)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

log = logging.getLogger(__name__)

_researcher = None
_running_tasks: dict[str, asyncio.Task[Any]] = {}


def _get_researcher() -> Any:
    global _researcher
    if _researcher is not None:
        return _researcher
    try:
        from ..tools.context import _hub
        if _hub is None:
            raise ToolError("Jarvis core not initialised yet.")
        from ..deep_research import DeepResearcher
        _researcher = DeepResearcher(_hub.router, _hub.store)
        return _researcher
    except ImportError:
        raise ToolError("Deep research module not available.")


class ResearchArgs(Args):
    question: str = Field(..., min_length=5, max_length=2000, description="The research question to investigate.")
    max_sources: int = Field(10, ge=2, le=30, description="Maximum number of sources to gather.")


@tool("deep_research",
      "Run a multi-step deep research pipeline: decomposes the question into sub-questions, searches multiple "
      "sources, extracts and cross-references claims, then synthesizes a verified report with citations and "
      "confidence scores. Returns the full report when complete.",
      ResearchArgs, Tier.T1,
      summary=lambda a: f"Deep research: {a.question[:60]}",
      sensitive=True, taints=True)
async def deep_research(a: ResearchArgs) -> dict[str, Any]:
    researcher = _get_researcher()
    report = await researcher.research(a.question, max_sources=a.max_sources)
    if report.status == "failed":
        raise ToolError(f"Research failed: {report.error}")
    return report.to_json()


class ResearchStatusArgs(Args):
    report_id: str = Field("", max_length=20, description="ID of a specific report, or empty for all active.")


@tool("research_status",
      "Check the status of deep research tasks. Returns active/completed reports.",
      ResearchStatusArgs, Tier.T0,
      summary=lambda a: "Check research status")
async def research_status(a: ResearchStatusArgs) -> dict[str, Any]:
    researcher = _get_researcher()
    if a.report_id:
        report = researcher.get_report(a.report_id)
        if report is None:
            return {"error": "Report not found", "active_count": len(researcher.active_reports())}
        return report.to_json()
    reports = researcher.active_reports()
    return {"active_count": len(reports), "reports": reports}
