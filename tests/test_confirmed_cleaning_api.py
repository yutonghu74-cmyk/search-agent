from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.api import create_handler
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from draft_helpers import make_service, confirmation


class CleaningFacade:
    def __init__(self):
        self.calls = []

    def prepare(self, actor, task_id, manifest_ref):
        self.calls.append(("prepare", actor, task_id, manifest_ref))
        return {"kind": "cleaning_approval_proposal", "proposal_digest": "sha256:" + "1" * 64}

    def prepare_incremental_recompute(self, actor, task_id):
        self.calls.append(("prepare_recompute", actor, task_id))
        return {"kind": "cleaning_approval_proposal", "candidate_count": 4,
                "proposal_digest": "sha256:" + "2" * 64}

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
        return {"task_id": task_id, "revision": 1, "generation": 1,
                "available": True, "candidate_preview_available": True,
                "runs": [{"operation_id": "clean_op", "output_version": "sha256:" + "c" * 64,
                    "execution_mode": "shared_input_detection", "steps": [{
                        "step_id": "semantic", "step_execution_id": "step_" + "a" * 64,
                        "input_version": "sha256:" + "b" * 64, "backend_ref": "vlm@1",
                        "status": "completed", "counts": {"input": 1, "retained": 0,
                            "rejected": 1, "review": 0, "pending": 0, "blocked": 0},
                        "rules": [], "items": [{"candidate_id": "candidate_" + "d" * 64,
                            "asset_version": "v1", "status": "rejected",
                            "preview_status": "unavailable", "reasons": [{
                                "rule_id": "semantic", "verdict": "fail",
                                "reason_code": "MISMATCH", "reason_summary": "不匹配",
                                "reason_truncated": False}]}]}]}]}

    def get_current_result_set(self, actor, task_id):
        raise ServiceError("NOT_FOUND", "Current result set not found", 404)

    def incremental_recompute(self, actor, task_id):
        self.calls.append(("recompute", actor, task_id))
        return {"available": True, "candidate_count": 3,
                "revalidated_candidate_count": 2}

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
        service = make_service(InMemoryTaskRepository())
        actor = Actor("tenant", "owner")
        task, _ = service.create_task(actor, "task", confirmation(service, actor))
        self.task_id = task["task_id"]
        handler = create_handler(service,
                                 cleaning_service=self.cleaning)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.base = (f"http://127.0.0.1:{self.server.server_port}"
                     f"/v1/retrieval/tasks/{self.task_id}/cleaning")

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
        summary = self.request("/steps?summary=true", method="GET")[1]
        self.assertNotIn("items", summary["runs"][0]["steps"][0])
        self.assertEqual([call[0] for call in self.cleaning.calls],
                         ["prepare", "confirm", "execute", "steps", "steps"])

    def test_step_outputs_are_fixed_version_and_paginated(self):
        query = (f"?operation_id=clean_op&step_execution_id=step_{'a' * 64}"
                 f"&output_version=sha256:{'c' * 64}&view_type=rejected&limit=1")
        path = (f"http://127.0.0.1:{self.server.server_port}/v1/retrieval/tasks/"
                f"{self.task_id}/nodes/cleaning/outputs{query}")
        request = Request(path, headers={"X-Tenant-Id": "tenant", "X-User-Id": "owner"})
        with urlopen(request) as response:
            body = json.loads(response.read())
        self.assertEqual((body["schema_version"], body["total_items"],
                          body["items"][0]["status"]),
                         ("cleaning-step-outputs@1", 1, "rejected"))
        self.assertEqual(body["items"][0]["preview_status"], "available")

    def test_projected_step_page_skips_full_snapshot_and_forwards_filters(self):
        handler = self.server.RequestHandlerClass
        path = (f"http://127.0.0.1:{self.server.server_port}/v1/retrieval/tasks/"
                f"{self.task_id}/nodes/cleaning/outputs?operation_id=op"
                "&step_execution_id=s&output_version=v&view_type=input&limit=12"
                "&rule_id=semantic&query=dog")
        request = Request(path, headers={"X-Tenant-Id": "tenant", "X-User-Id": "owner"})
        with patch.object(self.cleaning, 'step_output_read_page', create=True,
                          return_value={'items': [], 'next_cursor': None}) as reader, \
                patch.object(handler, '_task_snapshot', side_effect=AssertionError('full read')):
            with urlopen(request) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(reader.call_args.kwargs['rule_id'], 'semantic')
            self.assertEqual(reader.call_args.kwargs['query'], 'dog')
            self.assertEqual(reader.call_args.kwargs['limit'], 12)
            reader.side_effect = ServiceError('FORBIDDEN', 'scope revoked', 403)
            with self.assertRaises(HTTPError) as raised:
                urlopen(request)
            self.assertEqual(raised.exception.code, 403)

    def test_completed_candidate_preview_is_private_binary(self):
        candidate_id = 'candidate_' + 'a' * 64
        request = Request(self.base + '/candidates/' + candidate_id + '/preview',
                          headers={"X-Tenant-Id": "tenant", "X-User-Id": "owner"})
        with urlopen(request) as response:
            self.assertEqual((response.status, response.headers['Content-Type'], response.read()),
                             (200, 'image/jpeg', b'\xff\xd8\xffpreview'))
            self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(self.cleaning.calls[-1][0], 'candidate_preview')

    def test_incremental_recompute_view_is_read_only(self):
        status, body, _ = self.request('/recompute', method='GET')
        self.assertEqual((status, body['candidate_count'], body['revalidated_candidate_count']),
                         (200, 3, 2))
        self.assertEqual(self.cleaning.calls[-1][0], 'recompute')

    def test_incremental_recompute_can_prepare_current_revision_job(self):
        status, body, _ = self.request('/prepare', body={'incremental_recompute': True})
        self.assertEqual((status, body['candidate_count']), (200, 4))
        self.assertEqual(self.cleaning.calls[-1][0], 'prepare_recompute')

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
