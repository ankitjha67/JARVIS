"""Run deterministic, read-only workflows through the background queue."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .background_tasks import BackgroundTaskEngine, ProgressFn
from .protocol import RunCenter
from .reconciliation import check_extract, reconcile, test_fixture
from .scheduler import Scheduler, _parse_schedule
from .store import Store

WORKFLOW_GOAL = "workflow/reconcile"
ROUTINE_TYPE = "extract_reconciliation"


class ReconcileInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: str = Field(min_length=1, max_length=4096)
    right: str = Field(min_length=1, max_length=4096)
    keys: list[str] = Field(min_length=1, max_length=10)
    left_sheet: str | None = Field(None, max_length=255)
    right_sheet: str | None = Field(None, max_length=255)
    metric: str | None = Field(None, max_length=255)
    export_workbook: bool = False


def enqueue(engine: BackgroundTaskEngine, inputs: ReconcileInputs, *, task_id: str | None = None) -> str:
    return engine.create(
        "Extract reconciliation", "analysis", WORKFLOW_GOAL,
        steps=["Compare extracts by business key"], checkpoint=inputs.model_dump(), task_id=task_id,
    )


def enqueue_scheduled(engine: BackgroundTaskEngine, payload: dict[str, Any]) -> str:
    inputs = ReconcileInputs.model_validate(payload["config"]["inputs"])
    occurrence = payload["_task"]
    fingerprint = f'{occurrence["id"]}/{occurrence["next_run_at"]}'
    run_id = sha256(fingerprint.encode()).hexdigest()[:32]
    return enqueue(engine, inputs, task_id=run_id)


async def run_task(task_id: str, engine: BackgroundTaskEngine, progress: ProgressFn) -> dict[str, Any]:
    task = engine.get_task(task_id)
    if task is None or task["goal"] != WORKFLOW_GOAL:
        raise ValueError("This background task has no workflow executor.")
    inputs = ReconcileInputs.model_validate(task["checkpoint"])
    engine.update_step(task_id, 0, "running")
    await progress("Comparing extracts", 0.1)
    try:
        result = await asyncio.to_thread(
            reconcile, **inputs.model_dump(exclude={"export_workbook"}), include_workbook=inputs.export_workbook,
        )
        workbook = result.pop("workbook", None)
        if workbook is not None:
            engine.save_workbook(task_id, workbook)
            result["workbook_available"] = True
    except (OSError, ValueError) as exc:
        engine.update_step(task_id, 0, "failed")
        raise ValueError(str(exc)) from exc
    # Keep database results within the runner's transport limit without cutting JSON.
    while len(json.dumps(result, default=str)) > 45_000:
        result["sample"].pop()
    engine.update_step(task_id, 0, "done", result["counts"])
    return result


def check_workflow() -> dict[str, Any]:
    return test_fixture()

SAVED_KEY = "saved_extract_workflows"


def handle_request(
    request: RunCenter, engine: BackgroundTaskEngine, store: Store, scheduler: Scheduler,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Dispatch explicit HUD actions. Only this interface creates persistent workflow schedules."""
    action = request.action
    if action == "list":
        return None, None
    if action == "handover":
        state = store.kv("workflow_handover")
        previous_id = state.get("last_audit_id", 0)
        audit_rows, has_more = store.query_audit(limit=50)
        new_actions = [
            {key: row[key] for key in ("id", "ts", "tool", "status")}
            for row in audit_rows if row["id"] > previous_id
        ]
        open_runs = [
            task for task in engine.list_tasks(limit=200)
            if task["status"] in ("queued", "running", "paused", "failed")
        ]
        now = datetime.now(UTC).isoformat()
        store.kv("workflow_handover", {
            "last_review": now,
            "last_audit_id": audit_rows[0]["id"] if audit_rows else previous_id,
        })
        return None, {
            "since": state.get("last_review"),
            "generated_at": now,
            "recent_actions": new_actions,
            "older_actions_omitted": has_more and len(new_actions) == 50,
            "open_runs": [
                {key: task[key] for key in ("id", "name", "status", "progress", "error")}
                for task in open_runs
            ],
        }
    if action == "test":
        return None, check_workflow()
    if action == "reconcile":
        inputs = ReconcileInputs.model_validate(request.inputs)
        return None, {"run_id": enqueue(engine, inputs)}
    if action in ("detail", "save"):
        task = engine.get_task(request.id)
        if not task:
            raise ValueError("Run not found.")
        if action == "detail":
            task.pop("checkpoint", None)
            return task, None
        if task["goal"] != WORKFLOW_GOAL or task["status"] != "done":
            raise ValueError("Only completed reconciliation runs can be saved as workflows.")
        name = request.name.strip()
        if not name:
            raise ValueError("Enter a workflow name.")
        workflow_id = uuid4().hex
        store.kv(SAVED_KEY, {workflow_id: {
            "id": workflow_id, "name": name, "version": 1, "inputs": task["checkpoint"],
        }})
        return None, {"saved": name}

    if action in ("pause_schedule", "resume_schedule"):
        schedule = next((item for item in scheduler.list_routines(ROUTINE_TYPE)
                         if item["id"] == request.id), None)
        if not schedule:
            raise ValueError("Workflow schedule not found.")
        update = scheduler.pause if action == "pause_schedule" else scheduler.resume
        if not update(request.id):
            raise ValueError("Workflow schedule could not be updated.")
        return None, None
    saved = store.kv(SAVED_KEY).get(request.id)
    if not saved:
        raise ValueError("Saved workflow not found.")
    inputs = ReconcileInputs.model_validate({**saved["inputs"], **request.inputs})
    if action == "run":
        return None, {"run_id": enqueue(engine, inputs)}
    if action == "revise":
        name = request.name.strip() or saved["name"]
        revision_id = uuid4().hex
        store.kv(SAVED_KEY, {revision_id: {
            "id": revision_id, "name": name, "version": saved["version"] + 1,
            "previous_id": saved["id"], "inputs": inputs.model_dump(),
        }})
        return None, {"saved": name, "revision_id": revision_id, "version": saved["version"] + 1}
    if action == "health":
        return None, {
            "first_extract": check_extract(inputs.left, inputs.keys, inputs.left_sheet),
            "second_extract": check_extract(inputs.right, inputs.keys, inputs.right_sheet),
        }
    schedule = request.schedule.strip().lower()
    if not re.fullmatch(r"(?:every [1-9][0-9]{0,3}[smhd]|daily [0-9]{2}:[0-9]{2})", schedule):
        raise ValueError("Use an interval such as 'every 2h' or a UTC time such as 'daily 09:00'.")
    if _parse_schedule(schedule) is None:
        raise ValueError("Enter a valid UTC schedule.")
    schedule_id = scheduler.add(
        saved["name"], "routine", schedule,
        payload={"routine_type": ROUTINE_TYPE, "config": {"inputs": inputs.model_dump()}},
    )
    return None, {"schedule_id": schedule_id}
