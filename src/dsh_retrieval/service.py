from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
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


def require_positive(value: Any, field: str) -> None:
    if type(value) is not int or value < 1:
        raise invalid(f"{field} must be a positive integer", f"$.{field}")


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
        result = task.to_summary()
        result["pending_draft"] = self.repository.get_pending_feedback(actor, task_id)
        return result

    def save_feedback(self, actor: Actor, task_id: str, key: str, body: dict[str, Any],
                      draft_id: str | None = None) -> tuple[dict[str, Any], bool]:
        require_key(key)
        fields = {"text", "scope", "expected_revision", "expected_task_version"}
        update_fields = fields | {"expected_draft_version"}
        if not isinstance(body, dict) or set(body) != (update_fields if draft_id else fields):
            raise invalid("feedback requires text, scope, expected_revision and expected_task_version")
        require_positive(body["expected_revision"], "expected_revision")
        require_positive(body["expected_task_version"], "expected_task_version")
        if draft_id:
            require_positive(body["expected_draft_version"], "expected_draft_version")
        text = body["text"]
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 2_000:
            raise invalid("feedback text must be 1-2000 characters", "$.text")
        if body["scope"] != "all":
            raise ServiceError("FEATURE_NOT_SUPPORTED", "the local preview supports only all scope", 422,
                               field_path="$.scope")
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, "create")
        if task.confirmed_revision != body["expected_revision"] or task.task_version != body["expected_task_version"]:
            raise ServiceError("REVISION_CONFLICT", "feedback base is stale", 409)
        try:
            proposal = json.loads(task.confirmed_proposal_json)
        except (TypeError, json.JSONDecodeError):
            raise ServiceError("STORAGE_CORRUPT", "confirmed proposal is unavailable", 500) from None
        proposal = deepcopy(proposal)
        current = proposal.get("query_spec", {}).get("raw_request")
        if not isinstance(current, str) or not current:
            raise ServiceError("STORAGE_CORRUPT", "confirmed proposal is invalid", 500)
        proposal["query_spec"]["raw_request"] = current + "\n追加反馈：" + text.strip()
        normalized = normalize_proposal(proposal)
        proposal_json = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
        return self.repository.save_feedback_draft(
            actor, key, task_id, expected_revision=body["expected_revision"],
            expected_task_version=body["expected_task_version"], proposal_json=proposal_json,
            feedback_text=text.strip(), scope="all", draft_id=draft_id,
            expected_draft_version=body.get("expected_draft_version"),
        )

    def get_feedback(self, actor: Actor, task_id: str, draft_id: str) -> dict[str, Any]:
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, "read")
        return self.repository.get_feedback_draft(actor, task_id, draft_id)

    def confirm_feedback(self, actor: Actor, task_id: str, key: str,
                         body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        require_key(key)
        fields = {"draft_id", "expected_draft_version", "proposal_digest",
                  "expected_task_version", "action"}
        if not isinstance(body, dict) or set(body) != fields:
            raise invalid("confirmation requires draft/version/digest/task version/action")
        require_positive(body["expected_draft_version"], "expected_draft_version")
        require_positive(body["expected_task_version"], "expected_task_version")
        if body["action"] not in {"confirm", "reject"}:
            raise invalid("action must be confirm or reject", "$.action")
        if not isinstance(body["draft_id"], str) or not body["draft_id"]:
            raise invalid("draft_id is required", "$.draft_id")
        if not isinstance(body["proposal_digest"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", body["proposal_digest"]):
            raise invalid("proposal_digest must be a SHA-256 digest", "$.proposal_digest")
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, "approve")
        return self.repository.confirm_feedback_draft(
            actor, key, task_id, draft_id=body["draft_id"],
            expected_draft_version=body["expected_draft_version"],
            proposal_digest=body["proposal_digest"],
            expected_task_version=body["expected_task_version"], action=body["action"],
        )

    def list_revisions(self, actor: Actor, task_id: str) -> tuple[dict[str, Any], ...]:
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, "read")
        return self.repository.list_revisions(actor, task_id)

    def list_tasks(self, actor: Actor, project_id: str) -> tuple[dict[str, Any], ...]:
        if not isinstance(project_id, str) or not project_id.strip():
            raise invalid("project_id is required", "$.project_id")
        result = []
        for task in self.repository.list_tasks(actor, project_id):
            try:
                self.projects.require_access(actor, task.project_id, task.dimension_id, "read")
            except ServiceError as error:
                if error.code == "PROJECT_ACCESS_DENIED":
                    continue
                raise
            try:
                proposal = json.loads(task.confirmed_proposal_json)
                raw_request = proposal["query_spec"]["raw_request"]
            except (TypeError, KeyError, json.JSONDecodeError):
                raise ServiceError("STORAGE_CORRUPT", "confirmed proposal is invalid", 500) from None
            view = task.to_summary()
            view.update(raw_request=raw_request, created_at=task.created_at.isoformat(),
                        pending_draft=self.repository.get_pending_feedback(actor, task.task_id))
            result.append(view)
        return tuple(result)
