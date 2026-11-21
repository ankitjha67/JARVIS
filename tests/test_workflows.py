from __future__ import annotations

import asyncio

from jarvis.background_tasks import BackgroundTaskEngine, TaskRunner
from jarvis.protocol import RunCenter
from jarvis.scheduler import Scheduler
from jarvis.store import Store
from jarvis.workflows import WORKFLOW_GOAL, enqueue_scheduled, handle_request, run_task


async def test_workflow_persists_inputs_and_runs_after_restart(tmp_path):
    left = tmp_path / "before.csv"
    right = tmp_path / "after.csv"
    left.write_text("Account,RWA\n123,100\n234,50\n", encoding="utf-8")
    right.write_text("Account,RWA\n123,120\n345,75\n", encoding="utf-8")
    path = tmp_path / "background.db"
    engine = BackgroundTaskEngine(path)
    store = Store(":memory:")
    scheduler = Scheduler(":memory:")
    request = RunCenter(type="runs.action", action="reconcile", inputs={
        "left": str(left), "right": str(right), "keys": ["Account"],
    })
    _, result = handle_request(request, engine, store, scheduler)
    task_id = result["run_id"]
    engine.set_status(task_id, "running")
    engine.close()

    engine = BackgroundTaskEngine(path)
    completed = asyncio.Event()

    async def on_update(_task_id, task):
        if task["status"] in ("done", "failed"):
            completed.set()

    runner = TaskRunner(engine, on_update=on_update)
    runner.register_runner(WORKFLOW_GOAL, run_task)
    try:
        assert engine.queued_ids((WORKFLOW_GOAL,)) == [task_id]
        assert await runner.submit(task_id)
        await asyncio.wait_for(completed.wait(), 5)
        task = engine.get_task(task_id)
        assert task["status"] == "done", task["error"]
        assert task["result"]["counts"] == {"changed": 1, "left_only": 1, "right_only": 1, "unchanged": 0}
        assert task["result"]["sample"][0] == {
            "key": ["123"], "status": "changed", "left_row": 2, "right_row": 2,
            "changed_columns": ["RWA"],
        }

        request = RunCenter(type="runs.action", action="save", id=task_id, name="Daily compare")
        handle_request(request, engine, store, scheduler)
        workflow_id = next(iter(store.kv("saved_extract_workflows")))
        request = RunCenter(type="runs.action", action="schedule", id=workflow_id, schedule="every 5m")
        _, scheduled = handle_request(request, engine, store, scheduler)

        async def fire(payload):
            return enqueue_scheduled(engine, payload)

        scheduler.on_fire("routine", fire)
        task = scheduler.list_tasks()[0]
        assert task["id"] == scheduled["schedule_id"]
        await scheduler._fire(task)
        await scheduler._fire(task)  # same occurrence, as after an interruption
        assert len(engine.queued_ids((WORKFLOW_GOAL,))) == 1
    finally:
        await runner.stop()
        scheduler.close()
        store.close()
        engine.close()
