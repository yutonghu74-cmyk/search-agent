from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from .errors import ServiceError, invalid
from .models import Actor, Task
from .query_spec import digest
from .ports import TaskRepository
# Compatibility export for existing local callers.
from .adapters.memory import InMemoryTaskRepository


CREATE_FIELDS = {"request", "media_type", "target_count", "business_domain", "auto_refill", "execution_mode"}


class TaskService:
    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository

    def create_task(self, actor: Actor, idempotency_key: str, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if not idempotency_key.strip():
            raise ServiceError("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required", 400)
        if not isinstance(body, dict):
            raise invalid("request body must be an object", "$")
        unknown = sorted(set(body) - CREATE_FIELDS)
        if unknown:
            raise invalid(f"unknown field: {unknown[0]}", f"$.{unknown[0]}")
        missing = sorted({"request", "media_type", "target_count", "business_domain"} - set(body))
        if missing:
            raise invalid("required field is missing", f"$.{missing[0]}")
        if not isinstance(body["request"], str) or not body["request"].strip():
            raise invalid("must be a non-empty string", "$.request")
        if body["media_type"] != "image":
            raise ServiceError("FEATURE_NOT_SUPPORTED", "P0/P1 enables image tasks only", 422, field_path="$.media_type")
        if isinstance(body["target_count"], bool) or not isinstance(body["target_count"], int) or body["target_count"] <= 0:
            raise invalid("must be a positive integer", "$.target_count")
        if not isinstance(body["business_domain"], str) or not body["business_domain"].strip():
            raise invalid("must be a non-empty string", "$.business_domain")
        auto_refill = body.get("auto_refill", True)
        if not isinstance(auto_refill, bool):
            raise invalid("must be a boolean", "$.auto_refill")
        execution_mode = body.get("execution_mode", "interactive")
        if execution_mode != "interactive":
            raise ServiceError("FEATURE_NOT_SUPPORTED", "background execution is not enabled in P0/P1", 422, field_path="$.execution_mode")

        normalized = {
            "request": body["request"],
            "media_type": body["media_type"],
            "target_count": body["target_count"],
            "business_domain": body["business_domain"],
            "auto_refill": auto_refill,
            "execution_mode": execution_mode,
        }
        request_digest = digest(normalized)
        task = Task(
            task_id="rt_" + uuid.uuid4().hex,
            tenant_id=actor.tenant_id,
            owner_user_id=actor.user_id,
            raw_request=normalized["request"],
            media_type=normalized["media_type"],
            target_count=normalized["target_count"],
            business_domain=normalized["business_domain"],
            auto_refill=normalized["auto_refill"],
            execution_mode=normalized["execution_mode"],
            request_digest=request_digest,
            created_at=datetime.now(UTC),
        )
        stored, replayed = self.repository.create(actor, idempotency_key, request_digest, task)
        return stored.to_summary(), replayed

    def get_task(self, actor: Actor, task_id: str) -> dict[str, Any]:
        return self.repository.get(actor, task_id).to_summary()
