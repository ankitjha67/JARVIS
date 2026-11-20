"""Outcome verification and model ladder tools (Mark-LVII)."""

from __future__ import annotations

from typing import Any

from ..policy import Tier
from .registry import Args, ToolError, tool


class VerifyLastArgs(Args):
    """No arguments needed — verifies the most recent tool action."""
    pass


@tool(
    "verify_last_action",
    "Verify whether the most recent tool action succeeded by checking its outcome "
    "(file exists, process running, exit code, etc).",
    VerifyLastArgs, Tier.T0,
    summary=lambda a: "Verify last action outcome",
)
async def verify_last_action(args: VerifyLastArgs) -> dict[str, Any]:
    from ..tools.context import _hub
    if _hub is None:
        raise ToolError("Jarvis core not available.")
    verifier = getattr(_hub, "verifier", None)
    if verifier is None:
        raise ToolError("Outcome verifier not initialised.")
    last = verifier.last_result
    if last is None:
        return {"status": "no_action", "message": "No tool action has been executed yet to verify."}
    return {
        "verified": last.verified,
        "confidence": round(last.confidence, 2),
        "evidence": last.evidence,
        "suggestion": last.suggestion or None,
        "tool": verifier._last_tool,
    }


class ModelStatsArgs(Args):
    """No arguments needed."""
    pass


@tool(
    "model_stats",
    "Show model ladder usage statistics: calls, latency, success rate and escalations per tier.",
    ModelStatsArgs, Tier.T0,
    summary=lambda a: "Show model ladder stats",
)
async def model_stats(args: ModelStatsArgs) -> dict[str, Any]:
    from ..tools.context import _hub
    if _hub is None:
        raise ToolError("Jarvis core not available.")
    ladder = getattr(_hub, "ladder", None)
    if ladder is None:
        raise ToolError("Model ladder not initialised.")
    stats = ladder.stats()
    if not stats:
        return {"message": "No model usage recorded yet.", "tiers": {}}
    return {"tiers": stats}
