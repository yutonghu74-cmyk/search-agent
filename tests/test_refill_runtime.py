from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from dsh_retrieval.adapters.retrieval.clip_search import CLIPTextSearchRequest
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.integration_models import IndexBinding
from dsh_retrieval.models import Actor
from dsh_retrieval.refill_clip import LocalCLIPRefillAdapter
from dsh_retrieval.refill_runtime import build_refill_runtime
from draft_helpers import BODY, confirmation, make_service


class Authority:
    def __init__(self):
        self.calls = []

    def require_access(self, context, resource_ref, action):
        self.calls.append((context.purpose, resource_ref, action))
        if (context.purpose != "refill" or resource_ref != "library:library-a"
                or action != "search"):
            raise AssertionError("unexpected refill authority request")


class Jobs:
    def __init__(self, request):
        self.request = request

    def current_for_browse(self, scope):
        return "job-1"

    def pending_requests(self, job_id):
        return (("branch-1", self.request, 1),)


class Executor:
    def __init__(self):
        self.calls = []

    def tick_refill(self, job_id, *, plan_id, expected_cursor):
        self.calls.append((job_id, plan_id, expected_cursor))
        return {"outcomes": [{"plan_id": plan_id, "status": "committed"}]}


class RefillRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tasks.sqlite3"
        self.tasks = SQLiteTaskRepository(self.path)
        self.service = make_service(self.tasks)
        self.actor = Actor("tenant", "owner")
        body = deepcopy(BODY)
        body["query_spec"]["target_count"] = 2
        body["query_spec"]["refill_policy"] = {
            "mode": "maintain_target", "target_count": 2,
            "approved_library_ids": ["library-a"], "progression": "manual",
        }
        created, _ = self.service.create_task(
            self.actor, "task", confirmation(self.service, self.actor, body=body))
        self.task_id = created["task_id"]
        binding = IndexBinding(
            "binding-1", "library-a", "index-v1", "encoder-v1", 2, "COSINE")
        self.request = CLIPTextSearchRequest(
            "plan-1", 1, binding, "客厅", "filter-v1", 20, "cursor-2")

    def search(self):
        authority = Authority()

        class Search:
            mode = "CLIP_COMBINED"

            def _authority(inner):
                return authority

        search = Search()
        search.service = self.service
        search.jobs = Jobs(self.request)
        search.executor = Executor()
        search.authority = authority
        return search

    def test_restart_recovers_pending_reservation_through_selected_adapter(self):
        first_search = self.search()
        runtime = build_refill_runtime(
            self.path, self.service, first_search, start=False)
        position = runtime.adapter.list_refill_positions(
            self.actor, self.task_id, revision=1, generation=1)[0]
        task = self.tasks.get(self.actor, self.task_id)
        reservation, _ = runtime.reservations.reserve(
            self.actor, self.task_id, position,
            expected_revision=task.confirmed_revision,
            expected_generation=task.run_generation,
            expected_task_version=task.task_version)
        runtime.close()

        restarted_search = self.search()
        restarted = build_refill_runtime(
            self.path, self.service, restarted_search, start=False)
        self.addCleanup(restarted.close)
        result = restarted.dispatch_reservation(reservation.reservation_id)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(restarted_search.executor.calls,
                         [("job-1", "plan-1", "cursor-2")])
        self.assertEqual(restarted.reservations.pending_dispatches(), ())
        self.assertTrue(restarted_search.authority.calls)

    def test_unsupported_search_mode_does_not_enable_runtime(self):
        search = type("Search", (), {"mode": "LOCAL_EXACT"})()
        self.assertIsNone(build_refill_runtime(
            self.path, self.service, search, start=False))

    def test_local_clip_mode_selects_local_adapter(self):
        authority = Authority()

        class Search:
            mode = "LOCAL_CLIP"

            def _authority(inner):
                return authority

        runtime = build_refill_runtime(
            self.path, self.service, Search(), start=False)
        self.addCleanup(runtime.close)
        self.assertIsInstance(runtime.adapter, LocalCLIPRefillAdapter)


if __name__ == "__main__":
    unittest.main()
