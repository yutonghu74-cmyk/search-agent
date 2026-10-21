from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ServiceError(Exception):
    code: str
    message: str
    status: int
    retryable: bool = False
    field_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, *, trace_id: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "trace_id": trace_id,
        }
        if self.field_path is not None:
            body["field_path"] = self.field_path
        body.update(self.details)
        return body


def invalid(message: str, field_path: str | None = None) -> ServiceError:
    return ServiceError("INVALID_SPEC", message, 422, field_path=field_path)

