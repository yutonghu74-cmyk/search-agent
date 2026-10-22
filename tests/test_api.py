from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.api import create_handler
from dsh_retrieval.service import InMemoryTaskRepository, TaskService
from draft_helpers import BODY


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        service = TaskService(InMemoryTaskRepository(), LocalProjectAccess([
            ("tenant-a", "user-a", "demo", "default")]))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(service))
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


if __name__ == "__main__":
    unittest.main()
