from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.api import create_handler, source_version
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import preview_edited_groups
from dsh_retrieval.service import InMemoryTaskRepository, TaskService
from draft_helpers import BODY


class ResultReader:
    def __init__(self):
        self.items = {}
        self.review_calls = []

    def get_current_result_set(self, actor, task_id):
        if task_id not in self.items:
            raise ServiceError("NOT_FOUND", "Current result set not found", 404)
        return self.items[task_id]

    def near_duplicate_reviews(self, actor, task_id):
        return {"result_set_id": "resultset-review",
                "result_set_digest": "sha256:" + "b" * 64,
                "task_version": 4, "pending_cluster_count": 1,
                "clusters": [], "decisions": [], "counts": {}}

    def review_near_duplicate_cluster(self, actor, task_id, **values):
        self.review_calls.append((actor, task_id, values))
        return {"result_set_id": "resultset-reviewed",
                "pending_cluster_count": 0}, False

    def exclude_result_candidates(self, actor, task_id, **values):
        self.review_calls.append((actor, task_id, values))
        return {"result_set_id": "resultset-excluded",
                "accepted_count": 1, "gap": 9}, False

    def review_unknown_candidate(self, actor, task_id, **values):
        self.review_calls.append((actor, task_id, values))
        return {"result_set_id": "resultset-manually-reviewed",
                "accepted_count": 2, "pending_review_count": 0}, False


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        service = TaskService(InMemoryTaskRepository(), LocalProjectAccess([
            ("tenant-a", "user-a", "demo", "default")]))
        cls.results = ResultReader()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(
            service, cls.results, build_info={'commit': 'abc123', 'source_clean': True,
                                              'model_engine': 'dsh', 'retrieval_mode': 'local_fixture'}))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_task_summary_route_does_not_build_heavy_snapshot(self):
        handler = self.server.RequestHandlerClass
        summary = {'task_id': 'task-summary', 'task_version': 7,
                   'progress': {'task_version': 7, 'nodes': []}}
        with patch.object(handler.service, 'task_read_summary', return_value=summary) as read, \
                patch.object(handler, '_task_snapshot', side_effect=AssertionError('heavy read')):
            status, body, _ = self.request('/v1/retrieval/tasks/task-summary/summary')
            self.assertEqual((status, body), (200, summary))
            self.assertEqual(read.call_args.args[1], 'task-summary')
            status, _, _ = self.request('/v1/retrieval/tasks/task-summary/summary?items=all')
            self.assertEqual(status, 422)
        with patch.object(handler.service, 'task_read_summary',
                          side_effect=ServiceError('FORBIDDEN', 'denied', 403)):
            status, _, _ = self.request('/v1/retrieval/tasks/task-summary/summary')
            self.assertEqual(status, 403)

    def test_browse_only_rejects_writes_and_keeps_page_readable(self):
        handler = create_handler(self.server.RequestHandlerClass.service, browse_only=True)
        with ThreadingHTTPServer(('127.0.0.1', 0), handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base + '/production') as response:
                    self.assertEqual(response.status, 200)
                for method in ('POST', 'DELETE'):
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(Request(base + '/v1/retrieval/tasks/missing', method=method))
                    self.assertEqual(caught.exception.code, 503)
                    self.assertEqual(json.load(caught.exception)['code'], 'BROWSE_ONLY')
            finally:
                server.shutdown()
                thread.join()

    def request(self, path, *, body=None, key=None, identity=True, method=None):
        headers = {}
        if identity:
            headers.update({"X-Tenant-Id": "tenant-a", "X-User-Id": "user-a"})
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        if key:
            headers["Idempotency-Key"] = key
        if method is None:
            method = "POST" if body is not None else "GET"
        request = Request(self.base + path, headers=headers, data=data, method=method)
        try:
            response = urlopen(request)
        except HTTPError as error:
            return error.code, json.loads(error.read()), error.headers
        payload = response.read()
        if response.headers.get_content_type() == "application/json":
            payload = json.loads(payload)
        return response.status, payload, response.headers

    def test_production_home_and_chat_route_have_security_headers(self):
        status, body, headers = self.request("/", identity=False)
        self.assertEqual(status, 200)
        self.assertIn('数据生产线路'.encode(), body)
        self.assertIn(b'/production.js', body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, body, _ = self.request("/chat", identity=False)
        self.assertEqual(status, 200)
        self.assertIn(b'/chat.js', body)
        status, body, _ = self.request("/production.js", identity=False)
        self.assertEqual(status, 200)
        self.assertIn(b'function selectLine(id)', body)

    def test_build_reports_process_source_and_runtime_without_identity(self):
        status, body, _ = self.request('/v1/build', identity=False)
        self.assertEqual(status, 200)
        self.assertEqual(body['commit'], 'abc123')
        self.assertTrue(body['source_clean'])
        self.assertEqual(body['model_engine'], 'dsh')
        self.assertEqual(body['retrieval_mode'], 'local_fixture')

    def test_non_git_source_is_unknown(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            self.assertEqual(source_version(Path(directory)), (None, None))

    def test_ticket_feedback_confirmation_and_revisions(self):
        status, expansion, _ = self.request("/v1/retrieval/projects/demo/expansions", body={
            "raw_request": "动物，狗扩20个词，猫扩30个词", "dimension_id": "default"})
        self.assertEqual(status, 200)
        self.assertEqual([group["primary_label"] for group in expansion["plan"]["groups"]],
                         ["狗", "猫"])
        self.assertEqual([len(group["auxiliary_terms"]) for group in expansion["plan"]["groups"]],
                         [20, 30])
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
        status, history_page, _ = self.request(
            "/v1/retrieval/projects/demo/tasks?limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(history_page['items'][0]['task_id'], task['task_id'])
        status, revisions, _ = self.request(f"/v1/retrieval/tasks/{task['task_id']}/revisions")
        self.assertEqual([item["revision"] for item in revisions["items"]], [1, 2])
        status, binding, _ = self.request(
            f"/v1/retrieval/tasks/{task['task_id']}/read-binding")
        self.assertEqual(status, 200)
        self.assertEqual(binding['revision'], 2)
        self.assertEqual(binding['proposal_digest'], revisions['items'][-1]['proposal_digest'])
        status, conversations, _ = self.request("/v1/retrieval/projects/demo/conversations")
        self.assertEqual(status, 200)
        summary = next(item for item in conversations["items"]
                       if item["conversation_id"] == draft["conversation_id"])
        self.assertEqual((summary["message_count"], summary["confirmation_count"]), (2, 2))
        status, conversation, _ = self.request(
            f"/v1/retrieval/conversations/{draft['conversation_id']}")
        self.assertEqual([item["kind"] for item in conversation["messages"]], ["request", "feedback"])
        self.assertEqual([item["revision"] for item in conversation["confirmations"]], [1, 2])
        self.assertEqual(conversation["executions"], [{"task_id": task["task_id"], "runs": []}])
        status, runs, _ = self.request(f"/v1/retrieval/tasks/{task['task_id']}/agent-runs")
        self.assertEqual((status, runs["engine"], runs["items"]), (200, "unconfigured", []))
        status, _, _ = self.request("/v1/retrieval/tasks/missing/agent-runs")
        self.assertEqual(status, 404)
        status, _, _ = self.request(f"/v1/retrieval/tasks/{task['task_id']}/agent-runs", identity=False)
        self.assertIn(status, (400, 401, 403))

        status, progress, _ = self.request(f"/v1/retrieval/tasks/{task['task_id']}/progress")
        self.assertEqual(status, 200)
        self.assertEqual(progress["revision"], 2)
        self.assertEqual([item["node_status"] for item in progress["nodes"]],
                         ["running", "pending", "pending", "pending", "pending"])
        self.assertEqual(progress["counts"]["target"], 10)
        self.assertIsNone(progress["counts"]["accepted"])
        self.assertGreaterEqual(progress["as_of_sequence"], 1)
        status, events, _ = self.request(
            f"/v1/retrieval/tasks/{task['task_id']}/events?after_sequence=0&limit=20")
        self.assertEqual((status, events["task_id"], events["schema_version"]),
                         (200, task["task_id"], "task-events@1"))
        self.assertEqual(events["snapshot"]["progress"]["as_of_sequence"],
                         events["as_of_sequence"])
        self.assertEqual(events["events"][0]["kind"], "task.snapshot")
        handler = self.server.RequestHandlerClass
        stream_settings = (handler.event_stream_duration,
                           handler.event_stream_poll_interval)
        handler.event_stream_duration = 0.01
        handler.event_stream_poll_interval = 0.001
        try:
            status, stream, stream_headers = self.request(
                f"/v1/retrieval/tasks/{task['task_id']}/events/stream?after_sequence=0")
        finally:
            (handler.event_stream_duration,
             handler.event_stream_poll_interval) = stream_settings
        self.assertEqual(status, 200)
        self.assertEqual(stream_headers.get_content_type(), "text/event-stream")
        self.assertIn(b"event: task-event\n", stream)
        self.assertIn(f"id: {task['task_id']}:1\n".encode(), stream)
        self.assertIn(b"retry: 1000\n\n", stream)
        status, _, _ = self.request(
            f"/v1/retrieval/tasks/{task['task_id']}/events/stream?after_sequence=-1")
        self.assertEqual(status, 422)
        status, _, _ = self.request(
            f"/v1/retrieval/tasks/{task['task_id']}/events/stream", identity=False)
        self.assertEqual(status, 401)
        status, empty_events, _ = self.request(
            f"/v1/retrieval/tasks/{task['task_id']}/events?after_sequence={events['as_of_sequence']}")
        self.assertEqual((status, empty_events["events"]), (200, []))
        status, _, _ = self.request(
            f"/v1/retrieval/tasks/{task['task_id']}/events?after_sequence=-1")
        self.assertEqual(status, 422)
        status, _, _ = self.request(
            f"/v1/retrieval/tasks/{task['task_id']}/events", identity=False)
        self.assertEqual(status, 401)
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
        with patch.object(self.results, 'get_result_read_page', create=True,
                          return_value={'indexed': True}) as indexed:
            status, page, _ = self.request(
                f"/v1/retrieval/tasks/{task['task_id']}/results?view=accepted&limit=1")
            self.assertEqual(status, 200)
            self.assertTrue(page['indexed'])
            indexed.assert_called_once()

    def test_deferred_near_duplicate_review_routes_are_not_exposed(self):
        status, review, _ = self.request(
            "/v1/retrieval/tasks/task-review/near-duplicate-reviews")
        self.assertEqual((status, review["code"]), (404, "NOT_FOUND"))
        body = {"expected_task_version": 4,
                "result_set_digest": "sha256:" + "b" * 64,
                "cluster_id": "ndc_" + "1" * 24,
                "action": "merge",
                "representative_candidate_id": "asset-a"}
        status, reviewed, _ = self.request(
            "/v1/retrieval/tasks/task-review/near-duplicate-reviews",
            body=body, key="review-key")
        self.assertEqual((status, reviewed["code"]), (404, "NOT_FOUND"))
        self.assertEqual(self.results.review_calls, [])

    def test_result_exclusion_route_binds_version_scope_reason_and_key(self):
        body = {"expected_task_version": 4,
                "result_set_digest": "sha256:" + "b" * 64,
                "candidate_id": "asset-a", "scope": "candidate",
                "reason": "这张图构图不适合"}
        status, changed, _ = self.request(
            "/v1/retrieval/tasks/task-review/result-exclusions",
            body=body, key="exclude-key")
        self.assertEqual((status, changed["result_set_id"]),
                         (200, "resultset-excluded"))
        _, task_id, values = self.results.review_calls.pop()
        self.assertEqual(task_id, "task-review")
        self.assertEqual(values, {**body, "key": "exclude-key"})

    def test_manual_review_route_binds_result_candidate_action_reason_and_key(self):
        body = {"expected_task_version": 4,
                "result_set_digest": "sha256:" + "b" * 64,
                "candidate_id": "asset-maybe", "asset_version": "v1",
                "action": "accept", "reason": "人工确认主体符合需求"}
        status, changed, _ = self.request(
            "/v1/retrieval/tasks/task-review/manual-reviews",
            body=body, key="manual-review-key")
        self.assertEqual((status, changed["result_set_id"]),
                         (200, "resultset-manually-reviewed"))
        _, task_id, values = self.results.review_calls.pop()
        self.assertEqual(task_id, "task-review")
        self.assertEqual(values, {**body, "key": "manual-review-key"})

    def test_existing_feedback_draft_can_be_revised_with_expansion(self):
        status, draft, _ = self.request('/v1/retrieval/projects/demo/drafts', body=BODY, key='revise-draft')
        self.assertEqual(status, 200)
        status, task, _ = self.request('/v1/retrieval/tasks', key='revise-task', body={
            'draft_id': draft['draft_id'], 'expected_draft_version': draft['draft_version'],
            'proposal_digest': draft['proposal_digest'], 'action': 'confirm'})
        self.assertEqual(status, 202)
        task_id = task['task_id']
        status, pending, _ = self.request(f'/v1/retrieval/tasks/{task_id}/feedback', key='old-feedback', body={
            'text': '要五个召回词', 'scope': 'all', 'expected_revision': 1,
            'expected_task_version': task['task_version']})
        self.assertEqual(status, 200)
        plan = preview_edited_groups(None, [{'primary_label': '狗', 'auxiliary_terms': [
            '狗在草地奔跑', '狗接飞盘', '狗在公园散步', '狗与主人玩耍', '狗在户外训练']}])['plan']
        status, revised, _ = self.request(
            f"/v1/retrieval/tasks/{task_id}/feedback/{pending['draft_id']}", key='revised-feedback', body={
                'text': '只需要5张图，要五个召回词，主体是狗，不是狗的情绪',
                'scope': 'all', 'expected_revision': 1, 'expected_task_version': task['task_version'],
                'expected_draft_version': pending['draft_version'],
                'target_count': 5, 'grouped_expansion': plan})
        self.assertEqual(status, 200)
        self.assertEqual(revised['draft_id'], pending['draft_id'])
        self.assertEqual(revised['draft_version'], pending['draft_version'] + 1)
        self.assertEqual(revised['proposal']['query_spec']['target_count'], 5)

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
        status, body, _ = self.request("/v1/retrieval/projects/demo/expansions", body={
            "raw_request": "动物，狗扩20个词", "dimension_id": "default"}, identity=False)
        self.assertEqual((status, body["code"]), (401, "UNAUTHENTICATED"))
        status, body, _ = self.request("/v1/retrieval/tasks/missing/progress", identity=False)
        self.assertEqual((status, body["code"]), (401, "UNAUTHENTICATED"))
        status, body, _ = self.request("/v1/retrieval/tasks/missing/results", identity=False)
        self.assertEqual((status, body["code"]), (401, "UNAUTHENTICATED"))

    def test_force_delete_cascades_confirmed_chat_and_legacy_task(self):
        status, draft, _ = self.request("/v1/retrieval/projects/demo/drafts", body=BODY, key="del-draft")
        self.assertEqual(status, 200)
        status, task, _ = self.request("/v1/retrieval/tasks", key="del-task", body={
            "draft_id": draft["draft_id"], "expected_draft_version": draft["draft_version"],
            "proposal_digest": draft["proposal_digest"], "action": "confirm"})
        self.assertEqual(status, 202)
        conversation_id = draft["conversation_id"]
        task_id = task["task_id"]

        # Default delete is still blocked on a confirmed Chat.
        status, body, _ = self.request(f"/v1/retrieval/conversations/{conversation_id}",
                                       method="DELETE")
        self.assertEqual((status, body["code"]), (409, "CONVERSATION_DELETE_FORBIDDEN"))

        # The force path cascades the Chat and its linked task together.
        status, cascade, _ = self.request(
            f"/v1/retrieval/conversations/{conversation_id}?force=true", method="DELETE")
        self.assertEqual(status, 200)
        self.assertTrue(cascade["deleted"])
        self.assertTrue(cascade["force"])
        self.assertIn(task_id, cascade["tasks"])
        status, _, _ = self.request(f"/v1/retrieval/tasks/{task_id}")
        self.assertEqual(status, 404)
        status, _, _ = self.request(f"/v1/retrieval/conversations/{conversation_id}")
        self.assertEqual(status, 404)

        # A conversation-linked task refuses the task-scoped force path.
        status, draft2, _ = self.request("/v1/retrieval/projects/demo/drafts", body=BODY, key="del-draft-2")
        status, task2, _ = self.request("/v1/retrieval/tasks", key="del-task-2", body={
            "draft_id": draft2["draft_id"], "expected_draft_version": draft2["draft_version"],
            "proposal_digest": draft2["proposal_digest"], "action": "confirm"})
        status, linked, _ = self.request(
            f"/v1/retrieval/tasks/{task2['task_id']}?force=true", method="DELETE")
        self.assertEqual((status, linked["code"]), (409, "TASK_HAS_CONVERSATION"))
        # The task is still present after the refusal.
        status, _, _ = self.request(f"/v1/retrieval/tasks/{task2['task_id']}")
        self.assertEqual(status, 200)

        # A task-scoped force delete without force=true is not offered.
        status, body, _ = self.request(f"/v1/retrieval/tasks/{task2['task_id']}", method="DELETE")
        self.assertEqual((status, body["code"]), (404, "NOT_FOUND"))


if __name__ == "__main__":
    unittest.main()
