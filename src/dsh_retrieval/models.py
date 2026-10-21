from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class RunStatus(StrEnum):
    CREATED = "CREATED"
    PARSING = "PARSING"
    AWAITING_INPUT = "AWAITING_INPUT"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskLifecycle(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class Actor:
    tenant_id: str
    user_id: str


@dataclass(slots=True)
class Task:
    task_id: str
    tenant_id: str
    owner_user_id: str
    raw_request: str
    media_type: str
    target_count: int
    business_domain: str
    auto_refill: bool
    execution_mode: str
    request_digest: str
    task_version: int = 1
    lifecycle: TaskLifecycle = TaskLifecycle.ACTIVE
    confirmed_revision: int | None = None
    pending_revision: int | None = None
    run_generation: int = 1
    run_status: RunStatus = RunStatus.CREATED
    stage: str | None = None
    created_at: datetime = datetime.min.replace(tzinfo=UTC)

    def to_summary(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "confirmed_revision": self.confirmed_revision,
            "pending_revision": self.pending_revision,
            "task_version": self.task_version,
            "task_lifecycle": self.lifecycle.value,
            "revision_status": None,
            "run_generation": self.run_generation,
            "run_status": self.run_status.value,
            "stage": self.stage,
            "status_url": f"/v1/retrieval/tasks/{self.task_id}",
            "events_url": f"/v1/retrieval/tasks/{self.task_id}/events",
        }

