from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .errors import ServiceError, invalid
from .models import Actor
from .ports import TaskRepository, ProjectContextProvider
from .proposals import Draft, normalize_proposal
from .adapters.memory import InMemoryTaskRepository  # compatibility import
from .adapters.projects import LocalProjectAccess


def require_key(key: str) -> None:
    if not isinstance(key, str) or not key.strip() or len(key) > 255:
        raise ServiceError("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key must be 1-255 characters", 400)


def require_version(value: Any) -> None:
    if type(value) is not int or value < 1:
        raise invalid("expected_draft_version must be a positive integer", "$.expected_draft_version")


class TaskService:
    def __init__(self, repository: TaskRepository, projects: ProjectContextProvider | None = None) -> None:
        self.repository = repository
        self.projects = projects if projects is not None else LocalProjectAccess()

    def save_draft(self, actor: Actor, project_id: str, key: str, body: dict,
                   draft_id: str | None = None) -> tuple[dict, bool]:
        require_key(key)
        if not isinstance(body, dict):
            raise invalid("body must be an object", "$")
        raw = dict(body)
        version = raw.pop("expected_draft_version", None) if draft_id else None
        if draft_id:
            require_version(version)
            existing = self.repository.get_draft(actor, draft_id)
            if existing.project_id != project_id:
                raise ServiceError("NOT_FOUND", "draft was not found", 404)
        proposal = normalize_proposal(raw)
        self.projects.require_access(actor, project_id, proposal["dimension_id"], "create")
        draft = Draft(draft_id or "draft_" + uuid.uuid4().hex, actor.tenant_id, actor.user_id,
                      project_id, proposal["dimension_id"], json.dumps(proposal, sort_keys=True, ensure_ascii=False))
        stored, replayed = self.repository.save_draft(actor, key, draft, version)
        return stored.to_summary(), replayed

    def get_draft(self, actor: Actor, project_id: str, draft_id: str) -> dict:
        draft = self.repository.get_draft(actor, draft_id)
        if draft.project_id != project_id:
            raise ServiceError("NOT_FOUND", "draft was not found", 404)
        self.projects.require_access(actor, draft.project_id, draft.dimension_id, "read")
        return draft.to_summary()

    def create_task(self, actor: Actor, idempotency_key: str, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        require_key(idempotency_key)
        fields = {"draft_id", "expected_draft_version", "proposal_digest", "action"}
        if not isinstance(body, dict) or set(body) != fields:
            raise ServiceError("CONFIRMATION_REQUIRED", "create requires an explicit draft confirmation", 422)
        require_version(body["expected_draft_version"])
        if body["action"] != "confirm" or not isinstance(body["draft_id"], str) or not body["draft_id"]:
            raise invalid("explicit confirm action and draft_id are required")
        if not isinstance(body["proposal_digest"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", body["proposal_digest"]):
            raise invalid("proposal_digest must be a SHA-256 digest", "$.proposal_digest")
        draft = self.repository.get_draft(actor, body["draft_id"])
        self.projects.require_access(actor, draft.project_id, draft.dimension_id, "approve")
        stored, replayed = self.repository.confirm_draft(actor, idempotency_key, body["draft_id"],
                                                        body["expected_draft_version"], body["proposal_digest"])
        return stored.to_summary(), replayed

    def get_task(self, actor: Actor, task_id: str) -> dict[str, Any]:
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, "read")
        return task.to_summary()
