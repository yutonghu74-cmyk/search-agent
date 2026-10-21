"""Local development adapter. Data disappears on process exit."""

import threading
import json
import uuid
from datetime import UTC, datetime
from dataclasses import replace

from ..errors import ServiceError
from ..models import Actor, Task, RunStatus
from ..proposals import Draft
from ..query_spec import digest
from .cleaning_memory import MemoryCleaningMixin


class InMemoryTaskRepository(MemoryCleaningMixin):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._idempotency: dict[tuple[str, str, str, str], tuple[str, str]] = {}
        self._drafts: dict[str, Draft] = {}
        self._draft_requests: dict[tuple, tuple[str, Draft]] = {}
        self._batches: dict[str, tuple[str, str, str]] = {}
        self._init_cleaning()

    def get_draft(self, actor: Actor, draft_id: str) -> Draft:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None or (draft.tenant_id, draft.owner_user_id) != (actor.tenant_id, actor.user_id):
                raise ServiceError("NOT_FOUND", "draft was not found", 404)
            return draft

    def save_draft(self, actor: Actor, key: str, draft: Draft, expected_version: int | None = None) -> tuple[Draft, bool]:
        if (draft.tenant_id, draft.owner_user_id) != (actor.tenant_id, actor.user_id):
            raise ServiceError("ACCESS_DENIED", "draft identity does not match caller", 403)
        route = ("create", draft.project_id) if expected_version is None else ("update", draft.draft_id)
        scope = (actor.tenant_id, actor.user_id, route, key)
        fingerprint = digest({"proposal": draft.proposal_digest, "expected_version": expected_version})
        with self._lock:
            if scope in self._draft_requests:
                previous, saved = self._draft_requests[scope]
                if previous != fingerprint:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "key was used with another proposal", 409)
                return saved, True
            if expected_version is not None:
                previous = self.get_draft(actor, draft.draft_id)
                if previous.project_id != draft.project_id or previous.dimension_id != draft.dimension_id:
                    raise ServiceError("PROJECT_MISMATCH", "draft binding cannot change", 409)
                if previous.confirmed_task_id or previous.draft_version != expected_version:
                    raise ServiceError("DRAFT_VERSION_CONFLICT", "draft is stale or already confirmed", 409)
                draft = replace(draft, draft_version=expected_version + 1)
            elif draft.draft_id in self._drafts:
                raise ServiceError("DRAFT_ID_CONFLICT", "draft identifier is already in use", 409)
            self._drafts[draft.draft_id] = draft
            self._draft_requests[scope] = (fingerprint, draft)
            return draft, False

    def confirm_draft(self, actor: Actor, key: str, draft_id: str, expected_version: int,
                      proposal_digest: str) -> tuple[Task, bool]:
        fingerprint = digest({"draft_id": draft_id, "expected_draft_version": expected_version,
                              "proposal_digest": proposal_digest, "action": "confirm"})
        scope = (actor.tenant_id, actor.user_id, "POST:/v1/retrieval/tasks", key)
        with self._lock:
            draft = self.get_draft(actor, draft_id)
            if scope in self._idempotency:
                previous, task_id = self._idempotency[scope]
                if previous != fingerprint:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "key was used with another confirmation", 409)
                return replace(self._tasks[task_id]), True
            if draft.draft_version != expected_version or draft.proposal_digest != proposal_digest:
                raise ServiceError("DRAFT_VERSION_CONFLICT", "draft version or proposal digest is stale", 409)
            if draft.confirmed_task_id:
                self._idempotency[scope] = (fingerprint, draft.confirmed_task_id)
                return replace(self._tasks[draft.confirmed_task_id]), True
            spec = json.loads(draft.proposal_json)["query_spec"]
            task = Task(
                task_id="rt_" + uuid.uuid4().hex, tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
                raw_request=spec["raw_request"], media_type=spec["media_type"], target_count=spec["target_count"],
                business_domain=spec["business_domain"], auto_refill=False, execution_mode="interactive",
                request_digest=proposal_digest, confirmed_revision=1, run_status=RunStatus.PLANNING,
                created_at=datetime.now(UTC), project_id=draft.project_id, dimension_id=draft.dimension_id,
                batch_id="batch_" + uuid.uuid4().hex, confirmed_proposal_json=draft.proposal_json,
            )
            self._tasks[task.task_id] = task
            self._batches[task.batch_id] = (actor.tenant_id, draft.project_id, task.task_id)
            self._drafts[draft_id] = replace(draft, confirmed_task_id=task.task_id)
            self._idempotency[scope] = (fingerprint, task.task_id)
            return replace(task), False

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
