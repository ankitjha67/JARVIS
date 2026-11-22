"""Workflow recorder: record, save and replay sequences of tool calls as macros."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

_recording: dict[str, Any] | None = None
_workflows: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

class RecordStartArgs(Args):
    name: str = Field(..., min_length=1, max_length=80, description="Workflow name")
    description: str = Field("", max_length=300)


@tool("workflow_record_start",
      "Start recording a named workflow (macro). Subsequent workflow_record_step calls add steps.",
      RecordStartArgs, Tier.T1, sensitive=True,
      summary=lambda a: f"Start recording: {a.name}")
async def workflow_record_start(a: RecordStartArgs) -> dict:
    global _recording
    if _recording is not None:
        raise ToolError(f"Already recording workflow '{_recording['name']}'. Stop it first.")
    _recording = {
        "name": a.name,
        "description": a.description,
        "steps": [],
        "started": _now_iso(),
    }
    return {"recording": a.name, "status": "started"}


class RecordStepArgs(Args):
    tool_name: str = Field(..., min_length=1, max_length=80, description="Tool to invoke")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments as JSON")
    description: str = Field("", max_length=200, description="Human description of this step")


@tool("workflow_record_step",
      "Add a step to the workflow currently being recorded.",
      RecordStepArgs, Tier.T1,
      summary=lambda a: f"Record step: {a.tool_name}")
async def workflow_record_step(a: RecordStepArgs) -> dict:
    if _recording is None:
        raise ToolError("No workflow is being recorded. Call workflow_record_start first.")
    step = {
        "index": len(_recording["steps"]),
        "tool_name": a.tool_name,
        "arguments": a.arguments,
        "description": a.description,
    }
    _recording["steps"].append(step)
    return {"added": step, "total_steps": len(_recording["steps"])}


class RecordStopArgs(Args):
    pass


@tool("workflow_record_stop",
      "Stop recording and save the current workflow.",
      RecordStopArgs, Tier.T0,
      summary=lambda a: "Stop recording workflow")
async def workflow_record_stop(a: RecordStopArgs) -> dict:
    global _recording
    if _recording is None:
        raise ToolError("No workflow is being recorded.")
    name = _recording["name"]
    _recording["finished"] = _now_iso()
    _workflows[name] = _recording
    step_count = len(_recording["steps"])
    _recording = None
    return {"saved": name, "steps": step_count, "total_workflows": len(_workflows)}


# ---------------------------------------------------------------------------
# Management
# ---------------------------------------------------------------------------

class ListArgs(Args):
    pass


@tool("workflow_list",
      "List all saved workflows with their step counts.",
      ListArgs, Tier.T0,
      summary=lambda a: "List workflows")
async def workflow_list(a: ListArgs) -> dict:
    items = []
    for w in _workflows.values():
        items.append({
            "name": w["name"],
            "description": w["description"],
            "steps": len(w["steps"]),
            "created": w["started"],
        })
    recording = None
    if _recording is not None:
        recording = {"name": _recording["name"], "steps_so_far": len(_recording["steps"])}
    return {"workflows": items, "count": len(items), "currently_recording": recording}


class ReplayArgs(Args):
    name: str = Field(..., min_length=1, max_length=80)


@tool("workflow_replay",
      "Return the steps of a saved workflow for the orchestrator to replay.",
      ReplayArgs, Tier.T2, sensitive=True,
      summary=lambda a: f"Replay workflow: {a.name}")
async def workflow_replay(a: ReplayArgs) -> dict:
    wf = _workflows.get(a.name)
    if not wf:
        raise ToolError(f"No workflow named '{a.name}'. Use workflow_list to see available ones.")
    return {
        "name": wf["name"],
        "description": wf["description"],
        "steps": wf["steps"],
        "step_count": len(wf["steps"]),
        "note": "Execute each step in order using the specified tool and arguments.",
    }


class DeleteArgs(Args):
    name: str = Field(..., min_length=1, max_length=80)


@tool("workflow_delete",
      "Delete a saved workflow.",
      DeleteArgs, Tier.T1,
      summary=lambda a: f"Delete workflow: {a.name}")
async def workflow_delete(a: DeleteArgs) -> dict:
    if a.name not in _workflows:
        raise ToolError(f"No workflow named '{a.name}'.")
    del _workflows[a.name]
    return {"deleted": a.name, "remaining": len(_workflows)}
