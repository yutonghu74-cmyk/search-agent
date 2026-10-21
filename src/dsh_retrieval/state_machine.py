from __future__ import annotations

from .errors import ServiceError
from .models import RunStatus, Task


TERMINAL = {RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELLED}
TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.CREATED: {RunStatus.PARSING, RunStatus.CANCELLED},
    RunStatus.PARSING: {RunStatus.AWAITING_INPUT, RunStatus.PLANNING, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.PAUSED},
    RunStatus.AWAITING_INPUT: {RunStatus.PARSING, RunStatus.PLANNING, RunStatus.CANCELLED},
    RunStatus.PLANNING: {RunStatus.RUNNING, RunStatus.AWAITING_INPUT, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.PAUSED},
    RunStatus.RUNNING: {RunStatus.AWAITING_INPUT, RunStatus.PAUSED, RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.PAUSED: set(),
    RunStatus.COMPLETED: set(),
    RunStatus.PARTIAL: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


def transition(task: Task, target: RunStatus, *, expected_task_version: int) -> None:
    if task.task_version != expected_task_version:
        raise ServiceError(
            "REVISION_CONFLICT", "task version is stale", 409,
            details={"latest_task_version": task.task_version},
        )
    if target not in TRANSITIONS[task.run_status]:
        raise ServiceError("INVALID_STATE_TRANSITION", f"cannot move {task.run_status} to {target}", 409)
    if target in {RunStatus.PAUSED, RunStatus.CANCELLED}:
        task.run_generation += 1
    task.run_status = target
    task.task_version += 1


def accept_worker_commit(task: Task, generation: int) -> None:
    if generation != task.run_generation or task.run_status in TERMINAL | {RunStatus.PAUSED}:
        raise ServiceError("STALE_GENERATION", "worker result belongs to an inactive generation", 409)

