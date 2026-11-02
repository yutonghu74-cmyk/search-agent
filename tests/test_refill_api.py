from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from urllib.request import Request, urlopen

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.api import create_handler
from dsh_retrieval.service import InMemoryTaskRepository, TaskService


class RefillService:
    def __init__(self):
        self.calls = []

    def preview(self, actor, task_id):
        self.calls.append(("preview", actor, task_id))
        return {"task_id": task_id, "action": "await_owner", "gap": 1,
                "next_position": {"position_fingerprint": "sha256:" + "1" * 64}}

    def confirm_next(self, actor, task_id, **kwargs):
        self.calls.append(("confirm", actor, task_id, kwargs))
        return {"status": "committed", "replayed": False,
                "reservation_id": "refill-1", "iteration": 1}


class RefillApiTest(unittest.TestCase):
    def setUp(self):
        service = TaskService(InMemoryTaskRepository(), LocalProjectAccess([]))
        self.refill = RefillService()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), create_handler(service, refill_service=self.refill))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + "/v1/retrieval/tasks/task-1/refill", method=method,
            data=data, headers={"X-Tenant-Id": "tenant", "X-User-Id": "owner",
                                "Idempotency-Key": "confirm-refill",
                                "Content-Type": "application/json"})
        with urlopen(request) as response:
            return response.status, json.loads(response.read()), response.headers

    def test_preview_and_confirm_only_current_position(self):
        status, preview, _ = self.request("GET")
        self.assertEqual((status, preview["action"]), (200, "await_owner"))
        fingerprint = preview["next_position"]["position_fingerprint"]
        status, result, headers = self.request("POST", {
            "expected_revision": 1, "expected_generation": 1,
            "expected_task_version": 2, "position_fingerprint": fingerprint,
            "action": "confirm",
        })
        self.assertEqual((status, result["status"]), (202, "committed"))
        self.assertIsNone(headers.get("Idempotent-Replayed"))
        self.assertEqual(self.refill.calls[1][3]["key"], "confirm-refill")


if __name__ == "__main__":
    unittest.main()
