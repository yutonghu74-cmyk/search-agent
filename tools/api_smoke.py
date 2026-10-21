"""Loopback HTTP end-to-end diagnostic; writes only var/api-smoke.json."""
import json
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from dsh_retrieval.api import create_handler
from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.service import TaskService


def main():
    root = Path(__file__).resolve().parents[1]
    body = json.loads((root / "contracts/examples/draft.json").read_text())
    repository = InMemoryTaskRepository()
    service = TaskService(repository, LocalProjectAccess([("tenant-a", "user-a", "demo", "default")]))
    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(service))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def call(path, payload=None, key="key", user="user-a"):
        request = Request(f"http://127.0.0.1:{server.server_port}" + path,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Content-Type": "application/json", "Idempotency-Key": key,
                                   "X-Tenant-Id": "tenant-a", "X-User-Id": user})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response), dict(response.headers)
        except HTTPError as error:
            return error.code, json.load(error), dict(error.headers)

    try:
        status, draft, _ = call("/v1/retrieval/projects/demo/drafts", body)
        assert status == 200, draft
        assert not repository._tasks and not repository._batches
        confirm = {"draft_id": draft["draft_id"], "expected_draft_version": draft["draft_version"],
                   "proposal_digest": draft["proposal_digest"], "action": "confirm"}
        assert call("/v1/retrieval/tasks", confirm, user="other")[0] == 404
        status, task, _ = call("/v1/retrieval/tasks", confirm)
        assert status == 202 and task["run_status"] == "PLANNING", task
        status, replay, headers = call("/v1/retrieval/tasks", confirm)
        assert replay["task_id"] == task["task_id"] and headers["Idempotent-Replayed"] == "true"
        assert call(task["status_url"])[0] == 200
        assert call("/v1/retrieval/tasks", {"request": "old direct request"})[0] == 422
        service.projects.grants.clear()
        assert call(task["status_url"])[0] == 403
        report = {"status": "PASS", "draft_request": body, "draft_response": draft,
                  "confirm_request": confirm, "task_response": task,
                  "checks": ["draft_without_task", "owner_only", "confirm", "replay", "read", "reject_legacy", "revocation"]}
        (root / "var").mkdir(exist_ok=True)
        (root / "var/api-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"status": "PASS", "checks": report["checks"]}))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


if __name__ == "__main__":
    main()
