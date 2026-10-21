"""Local development adapter. Data disappears on process exit."""

import threading
from dataclasses import replace

from ..errors import ServiceError
from ..models import Actor, Task


class InMemoryTaskRepository:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._idempotency: dict[tuple[str, str, str, str], tuple[str, str]] = {}

    def create(self, actor: Actor, key: str, request_digest: str, task: Task) -> tuple[Task, bool]:
        if (task.tenant_id, task.owner_user_id) != (actor.tenant_id, actor.user_id):
            raise ServiceError("ACCESS_DENIED", "task identity does not match caller", 403)
        scope = (actor.tenant_id, actor.user_id, "POST:/v1/retrieval/tasks", key)
        with self._lock:
            existing = self._idempotency.get(scope)
            if existing is not None:
                existing_digest, task_id = existing
                if existing_digest != request_digest:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "idempotency key was used with another body", 409)
                return replace(self._tasks[task_id]), True
            if task.task_id in self._tasks:
                raise ServiceError("TASK_ID_CONFLICT", "task identifier is already in use", 409)
            self._tasks[task.task_id] = replace(task)
            self._idempotency[scope] = (request_digest, task.task_id)
            return replace(task), False

    def get(self, actor: Actor, task_id: str) -> Task:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.tenant_id != actor.tenant_id or task.owner_user_id != actor.user_id:
                raise ServiceError("NOT_FOUND", "task was not found", 404)
            return replace(task)
