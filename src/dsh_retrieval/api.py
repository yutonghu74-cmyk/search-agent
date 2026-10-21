from __future__ import annotations

import json
import re
import uuid
import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .errors import ServiceError
from .models import Actor
from .service import InMemoryTaskRepository, TaskService
from .adapters.projects import LocalProjectAccess


TASK_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)$")
DRAFT_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/drafts(?:/([^/]+))?$")


class Handler(BaseHTTPRequestHandler):
    server_version = "dsh-retrieval-p0"
    service: TaskService

    def _trace_id(self) -> str:
        return self.headers.get("X-Request-Id") or "tr_" + uuid.uuid4().hex

    def _actor(self) -> Actor:
        tenant_id = self.headers.get("X-Tenant-Id", "").strip()
        user_id = self.headers.get("X-User-Id", "").strip()
        if not tenant_id or not user_id:
            raise ServiceError("UNAUTHENTICATED", "trusted gateway identity is required", 401)
        return Actor(tenant_id, user_id)

    def _json(self, status: int, body: dict[str, Any], *, replayed: bool = False) -> None:
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        if replayed:
            self.send_header("Idempotent-Replayed", "true")
        self.end_headers()
        self.wfile.write(encoded)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError
            value = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            raise ServiceError("INVALID_JSON", "body must be valid JSON", 400) from None
        if not isinstance(value, dict):
            raise ServiceError("INVALID_SPEC", "body must be an object", 422, field_path="$")
        return value

    def _run(self, action: Any) -> None:
        trace_id = self._trace_id()
        try:
            status, body, replayed = action()
            body["trace_id"] = trace_id
            self._json(status, body, replayed=replayed)
        except ServiceError as error:
            self._json(error.status, error.as_dict(trace_id=trace_id))

    def do_POST(self) -> None:  # noqa: N802
        draft_match = DRAFT_PATH.fullmatch(self.path)
        if self.path != "/v1/retrieval/tasks" and draft_match is None:
            self._json(404, {"code": "NOT_FOUND", "message": "route was not found", "retryable": False, "trace_id": self._trace_id()})
            return

        def action() -> tuple[int, dict[str, Any], bool]:
            if draft_match:
                result, replayed = self.service.save_draft(self._actor(), draft_match.group(1),
                    self.headers.get("Idempotency-Key", ""), self._body(), draft_match.group(2))
                return HTTPStatus.OK, result, replayed
            result, replayed = self.service.create_task(self._actor(), self.headers.get("Idempotency-Key", ""), self._body())
            return HTTPStatus.ACCEPTED, result, replayed

        self._run(action)

    def do_GET(self) -> None:  # noqa: N802
        match = TASK_PATH.match(self.path)
        draft_match = DRAFT_PATH.fullmatch(self.path)
        if match is None and not (draft_match and draft_match.group(2)):
            self._json(404, {"code": "NOT_FOUND", "message": "route was not found", "retryable": False, "trace_id": self._trace_id()})
            return

        def action() -> tuple[int, dict[str, Any], bool]:
            if draft_match:
                return HTTPStatus.OK, self.service.get_draft(self._actor(), draft_match.group(1), draft_match.group(2)), False
            return HTTPStatus.OK, self.service.get_task(self._actor(), match.group(1)), False

        self._run(action)

    def log_message(self, format: str, *args: Any) -> None:
        # Production logging will be structured and redacted; avoid request-body logging here.
        super().log_message(format, *args)


def create_handler(service: TaskService) -> type[Handler]:
    """Bind one service instance per server, without global repository state."""
    class BoundHandler(Handler):
        pass

    BoundHandler.service = service
    return BoundHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Local development only; simulated identity headers, no W3")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--demo-project", action="store_true", help="grant tenant-a/user-a access to demo/default")
    args = parser.parse_args()
    grants = [("tenant-a", "user-a", "demo", "default")] if args.demo_project else []
    handler = create_handler(TaskService(InMemoryTaskRepository(), LocalProjectAccess(grants)))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"LOCAL ONLY: http://127.0.0.1:{server.server_port}; memory storage, simulated identity")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
