"""Common root-task cancellation fence for authorized business commands."""
from ...errors import ServiceError
from ...models import RunStatus, TaskLifecycle
from . import ledger_rows


def cancel_task(db, repository, projects, actor, memory, task):
    if task.lifecycle != TaskLifecycle.ACTIVE or task.run_status not in (
            RunStatus.PLANNING, RunStatus.RUNNING, RunStatus.AWAITING_INPUT, RunStatus.PAUSED):
        raise ServiceError('INVALID_STATE_TRANSITION', 'Task cannot be cancelled', 409)
    projects.require_access(actor, task.project_id, task.dimension_id, 'write')
    task = memory._tasks[task.task_id]
    task.run_status = RunStatus.CANCELLED
    task.run_generation += 1
    task.task_version += 1
    task.active_cleaning_operation_id = None
    task.active_plan_digest = None
    ledger_rows.write_memory(db, memory)
    repository._sync_task_read_summaries(db, memory)
    return {'task_id': task.task_id, 'status': 'cancelled', 'task_version': task.task_version,
            'generation': task.run_generation, 'publication_fenced': True, 'termination': 'unknown'}
