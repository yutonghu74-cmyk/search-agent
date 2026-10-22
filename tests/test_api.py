from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.api import create_handler
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.service import InMemoryTaskRepository, TaskService
from draft_helpers import BODY


class ResultReader:
    def __init__(self):
        self.items = {}

    def get_current_result_set(self, actor, task_id):
        if task_id not in self.items:
            raise ServiceError("NOT_FOUND", "Current result set not found", 404)
        return self.items[task_id]


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        service = TaskService(InMemoryTaskRepository(), LocalProjectAccess([
            ("tenant-a", "user-a", "demo", "default")]))
        cls.results = ResultReader()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(service, cls.results))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, *, body=None, key=None, identity=True):
        headers = {}
        if identity:
            headers.update({"X-Tenant-Id": "tenant-a", "X-User-Id": "user-a"})
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        if key:
            headers["Idempotency-Key"] = key
        request = Request(self.base + path, headers=headers, data=data,
                          method="POST" if body is not None else "GET")
        try:
            response = urlopen(request)
        except HTTPError as error:
            return error.code, json.loads(error.read()), error.headers
        payload = response.read()
        if response.headers.get_content_type() == "application/json":
            payload = json.loads(payload)
        return response.status, payload, response.headers

    def test_static_chat_has_security_headers(self):
        status, body, headers = self.request("/", identity=False)
        self.assertEqual(status, 200)
        self.assertIn(b"Chat", body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_ticket_feedback_confirmation_and_revisions(self):
        status, draft, _ = self.request("/v1/retrieval/projects/demo/drafts", body=BODY, key="draft")
        self.assertEqual(status, 200)
        status, task, _ = self.request("/v1/retrieval/tasks", key="task", body={
            "draft_id": draft["draft_id"], "expected_draft_version": draft["draft_version"],
            "proposal_digest": draft["proposal_digest"], "action": "confirm"})
        self.assertEqual(status, 202)
        status, feedback, _ = self.request(f"/v1/retrieval/tasks/{task['task_id']}/feedback", key="feedback", body={
            "text": "再去掉文字", "scope": "all", "expected_revision": 1,
            "expected_task_version": task["task_version"]})
        self.assertEqual(status, 200)
        status, current, _ = self.request(f"/v1/retrieval/tasks/{task['task_id']}")
        self.assertEqual(current["pending_draft"]["draft_id"], feedback["draft_id"])
        status, result, _ = self.request(f"/v1/retrieval/tasks/{task['task_id']}/confirmations", key="apply", body={
            "draft_id": feedback["draft_id"], "expected_draft_version": feedback["draft_version"],
            "proposal_digest": feedback["proposal_digest"],
            "expected_task_version": feedback["base_task_version"], "action": "confirm"})
        self.assertEqual((status, result["task"]["confirmed_revision"]), (202, 2))
        status, history, _ = self.request("/v1/retrieval/projects/demo/tasks")
        self.assertEqual(history["items"][0]["task_id"], task["task_id"])
        self.assertIn("再去掉文字", history["items"][0]["raw_request"])
        status, revisions, _ = self.request(f"/v1/retrieval/tasks/{task['task_id']}/revisions")
        self.assertEqual([item["revision"] for item in revisions["items"]], [1, 2])
        status, conversations, _ = self.request("/v1/retrieval/projects/demo/conversations")
        self.assertEqual(status, 200)
        summary = next(item for item in conversations["items"]
                       if item["conversation_id"] == draft["conversation_id"])
        self.assertEqual((summary["message_count"], summary["confirmation_count"]), (2, 2))
        status, conversation, _ = self.request(
            f"/v1/retrieval/conversations/{draft['conversation_id']}")
        self.assertEqual([item["kind"] for item in conversation["messages"]], ["request", "feedback"])
        self.assertEqual([item["revision"] for item in conversation["confirmations"]], [1, 2])
        status, progress, _ = self.request(f"/v1/retrieval/tasks/{task['task_id']}/progress")
        self.assertEqual(status, 200)
        self.assertEqual(progress["revision"], 2)
        self.assertEqual([item["node_status"] for item in progress["nodes"]],
                         ["running", "pending", "pending", "pending", "pending"])
        self.assertEqual(progress["counts"]["target"], 10)
        self.assertIsNone(progress["counts"]["accepted"])
        status, unavailable, _ = self.request(
            f"/v1/retrieval/tasks/{task['task_id']}/results?view=accepted&limit=1")
        self.assertEqual(status, 200)
        self.assertFalse(unavailable["available"])
        self.assertIsNone(unavailable["counts"]["accepted"])
        current_task = result["task"]
        self.results.items[task["task_id"]] = {
            "result_set_id": "resultset-api", "status": "published",
            "as_of_task_version": current_task["task_version"],
            "generation": current_task["run_generation"],
            "result_set_digest": "sha256:" + "a" * 64, "target_count": 10,
            "eligible_unique_count": 2, "accepted_count": 2, "reserve_count": 0,
            "duplicate_count": 1, "failed_count": 1, "pending_review_count": 1,
            "counts_status": "fresh", "gap": 8,
            "stop_reason": "SEARCH_STRATEGIES_EXHAUSTED",
            "accepted": ({"candidate_id": "asset-a", "asset_version": "v1"},
                         {"candidate_id": "asset-b", "asset_version": "v1"}),
            "reserve": (),
        }
        status, first, _ = self.request(
            f"/v1/retrieval/tasks/{task['task_id']}/results?view=accepted&limit=1")
        self.assertEqual((status, first["available"], first["counts"]["accepted"]),
                         (200, True, 2))
        self.assertEqual(first["items"][0]["candidate_id"], "asset-a")
        status, second, _ = self.request(
            f"/v1/retrieval/tasks/{task['task_id']}/results?view=accepted&limit=1&cursor={first['next_cursor']}")
        self.assertEqual(second["items"][0]["candidate_id"], "asset-b")
        self.assertIsNone(second["next_cursor"])

    def test_feedback_requires_identity_and_idempotency_key(self):
        status, body, _ = self.request("/v1/retrieval/tasks/missing/feedback", body={}, identity=False)
        self.assertEqual((status, body["code"]), (401, "UNAUTHENTICATED"))
        status, body, _ = self.request("/v1/retrieval/tasks/missing/feedback", body={})
        self.assertEqual((status, body["code"]), (400, "IDEMPOTENCY_KEY_REQUIRED"))

    def test_project_history_requires_identity(self):
        status, body, _ = self.request("/v1/retrieval/projects/demo/tasks", identity=False)
        self.assertEqual((status, body["code"]), (401, "UNAUTHENTICATED"))
        status, body, _ = self.request("/v1/retrieval/projects/demo/conversations", identity=False)
        self.assertEqual((status, body["code"]), (401, "UNAUTHENTICATED"))
        status, body, _ = self.request("/v1/retrieval/tasks/missing/progress", identity=False)
        self.assertEqual((status, body["code"]), (401, "UNAUTHENTICATED"))
        status, body, _ = self.request("/v1/retrieval/tasks/missing/results", identity=False)
        self.assertEqual((status, body["code"]), (401, "UNAUTHENTICATED"))


if __name__ == "__main__":
    unittest.main()
