from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.api import create_handler
from draft_helpers import make_service


class CleaningFacade:
    def __init__(self):
        self.calls = []

    def prepare(self, actor, task_id, manifest_ref):
        self.calls.append(("prepare", actor, task_id, manifest_ref))
        return {"kind": "cleaning_approval_proposal", "proposal_digest": "sha256:" + "1" * 64}

    def confirm(self, actor, task_id, proposal_digest, *, key,
                expected_task_version, action):
        self.calls.append(("confirm", actor, task_id, proposal_digest, key,
                           expected_task_version, action))
        return ({"kind": "approved_cleaning_operation", "operation_id": "clean_op"}, True)

    def execute(self, actor, task_id, operation_id):
        self.calls.append(("execute", actor, task_id, operation_id))
        return ({"kind": "cleaning_preview", "accepted_count": 2}, False)

    def steps(self, actor, task_id):
        self.calls.append(("steps", actor, task_id))
        return {"available": True, "runs": []}

    def candidate_preview(self, actor, task_id, candidate_id):
        self.calls.append(("candidate_preview", actor, task_id, candidate_id))
        return "image/jpeg", b"\xff\xd8\xffpreview"


class DispatchFacade:
    def __init__(self):
        self.notifications = 0

    def notify(self):
        self.notifications += 1

    def status(self, actor, task_id, operation_id):
        return {"status": "queued", "operation_id": operation_id}


class ConfirmedCleaningApiTest(unittest.TestCase):
    def test_cleaning_repository_is_also_the_default_result_reader(self):
        reader = object()
        handler = create_handler(make_service(InMemoryTaskRepository()), cleaning_reader=reader)
        self.assertIs(handler.result_reader, reader)
        explicit = object()
        handler = create_handler(make_service(InMemoryTaskRepository()), cleaning_reader=reader,
                                 result_reader=explicit)
        self.assertIs(handler.result_reader, explicit)

    def setUp(self):
        self.cleaning = CleaningFacade()
        handler = create_handler(make_service(InMemoryTaskRepository()),
                                 cleaning_service=self.cleaning)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.base = (f"http://127.0.0.1:{self.server.server_port}"
                     "/v1/retrieval/tasks/task_1/cleaning")

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, suffix, *, method="POST", body=None, key=None):
        headers = {"X-Tenant-Id": "tenant", "X-User-Id": "owner",
                   "Content-Type": "application/json"}
        if key is not None:
            headers["Idempotency-Key"] = key
        data = json.dumps(body).encode() if body is not None else None
        try:
            response = urlopen(Request(self.base + suffix, data=data,
                                       headers=headers, method=method))
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), dict(response.headers)

    def test_prepare_confirm_execute_and_steps(self):
        self.assertEqual(self.request("/prepare", body={"manifest_ref": "manifest_1"})[0], 200)
        status, approved, headers = self.request("/confirm", key="approve-cleaning", body={
            "proposal_digest": "sha256:" + "1" * 64,
            "expected_task_version": 3,
            "action": "confirm",
        })
        self.assertEqual((status, approved["operation_id"]), (202, "clean_op"))
        self.assertEqual(headers.get("Idempotent-Replayed"), "true")
        self.assertEqual(self.request("/execute", body={"operation_id": "clean_op"})[1][
            "accepted_count"], 2)
        self.assertTrue(self.request("/steps", method="GET")[1]["available"])
        self.assertEqual([call[0] for call in self.cleaning.calls],
                         ["prepare", "confirm", "execute", "steps"])

    def test_completed_candidate_preview_is_private_binary(self):
        candidate_id = 'candidate_' + 'a' * 64
        request = Request(self.base + '/candidates/' + candidate_id + '/preview',
                          headers={"X-Tenant-Id": "tenant", "X-User-Id": "owner"})
        with urlopen(request) as response:
            self.assertEqual((response.status, response.headers['Content-Type'], response.read()),
                             (200, 'image/jpeg', b'\xff\xd8\xffpreview'))
            self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(self.cleaning.calls[-1][0], 'candidate_preview')

    def test_rejects_client_recipe_and_disabled_service(self):
        status, body, _ = self.request("/prepare", body={
            "manifest_ref": "manifest_1", "recipe": {"backend_ref": "client.code"}})
        self.assertEqual((status, body["code"]), (422, "INVALID_CLEANING_REQUEST"))
        self.server.RequestHandlerClass.cleaning_service = None
        self.assertEqual(self.request("/prepare", body={"manifest_ref": "manifest_1"})[1]["code"],
                         "CLEANING_NOT_CONFIGURED")

    def test_confirm_wakes_background_dispatch_and_exposes_status(self):
        dispatch = DispatchFacade()
        self.server.RequestHandlerClass.cleaning_dispatch = dispatch
        status, approved, _ = self.request("/confirm", key="approve-cleaning", body={
            "proposal_digest": "sha256:" + "1" * 64,
            "expected_task_version": 3,
            "action": "confirm",
        })
        self.assertEqual((status, approved["status"], dispatch.notifications),
                         (202, "queued", 1))
        status, body, _ = self.request(
            "/status?operation_id=clean_op", method="GET")
        self.assertEqual((status, body["status"]), (200, "queued"))
        self.assertEqual(self.request("/status", method="GET")[1]["code"],
                         "INVALID_CLEANING_REQUEST")


if __name__ == "__main__":
    unittest.main()
