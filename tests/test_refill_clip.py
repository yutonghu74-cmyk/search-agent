from dataclasses import replace
from datetime import UTC, datetime
import json
import unittest

from dsh_retrieval.adapters.retrieval.clip_search import CLIPTextSearchRequest
from dsh_retrieval.integration_models import IndexBinding, QueryVector, SearchHit, SearchPage, SearchRequest
from dsh_retrieval.models import Actor, RunStatus, Task
from dsh_retrieval.multi_branch_contracts import PlanSetMemberProgress, RetrievalPlanSet
from dsh_retrieval.physical_query_preparation import PreparedQuery
from dsh_retrieval.query_branches import compile_query_branches
from dsh_retrieval.refill_clip import CombinedCLIPRefillAdapter, LocalCLIPRefillAdapter
from dsh_retrieval.refill_planning import RefillReservation, parse_refill_position
from dsh_retrieval.refill_search import ConfirmedRefillSearchHandler
from dsh_retrieval.retrieval_contracts import RetrievalPlan, RetrievalScope
from draft_helpers import BODY


class Tasks:
    def __init__(self, task):
        self.task = task

    def get(self, actor, task_id):
        return self.task


def reservation(position, task):
    return RefillReservation(
        "refill-1", task.task_id, task.confirmed_revision, task.run_generation,
        task.task_version, 1, position.library_id, position.fingerprint,
        "policy", datetime.now(UTC), position.binding_json)


class RefillCLIPAdapterTest(unittest.TestCase):
    def setUp(self):
        self.actor = Actor("tenant", "owner")
        self.task = Task(
            "task-1", "tenant", "owner", "找 10 张客厅图片", "image", 10, "home",
            False, "manual", "request", task_version=3, confirmed_revision=2,
            run_generation=4, run_status=RunStatus.RUNNING, stage="deciding",
            project_id="demo", dimension_id="default",
            confirmed_proposal_json=json.dumps(BODY, ensure_ascii=False))
        self.binding = IndexBinding(
            "binding-1", "library-a", "index-v1", "encoder-v1", 2, "COSINE")

    def test_local_clip_resolves_and_executes_one_exact_approved_page(self):
        logical = compile_query_branches(BODY["query_spec"])
        branch = logical["branches"][0]
        scope = RetrievalScope.from_task(self.task)
        request = SearchRequest(
            "plan-1", 1, self.binding, QueryVector("encoder-v1", (0.1, 0.2)),
            "filter-v1", 10)
        plan = RetrievalPlan(scope, 1, request)
        member = PreparedQuery(plan, branch["branch_id"], branch["query_fingerprint"],
                               logical["preview_digest"])
        approved = RetrievalPlanSet(scope, 1, (member,))

        class Repository:
            def __init__(inner):
                inner.page = None
                inner.operation_id = None

            def get_browse_current(inner, actor, task_id):
                return approved

            def get_progress(inner, actor, selected, **kwargs):
                return (PlanSetMemberProgress(
                    request.plan_id, request, inner.operation_id, inner.page,
                    1 if inner.page else 0),)

            def reserve_page(inner, actor, selected, *, plan_id, request, key, **kwargs):
                inner.operation_id = "operation-1"
                return inner.operation_id, False

            def commit_page(inner, actor, selected, *, page, **kwargs):
                inner.page = page
                return False

        repository = Repository()

        class Gateway:
            def search(inner, context, selected):
                hit = SearchHit(selected.binding, "candidate-1", 0.9)
                return SearchPage((hit,), None, "exhausted", "snapshot", "snap-1")

        class Search:
            mode = "LOCAL_CLIP"

            def _adapter_for_plan(inner, actor, task_id, selected):
                return Gateway()

        search = Search()
        search.service = type("Service", (), {"repository": Tasks(self.task)})()
        search.repository = repository
        adapter = LocalCLIPRefillAdapter(search)
        positions = adapter.list_refill_positions(self.actor, self.task.task_id,
                                                  revision=2, generation=4)
        self.assertEqual(len(positions), 1)
        saved = reservation(positions[0], self.task)
        result = ConfirmedRefillSearchHandler(adapter, adapter).dispatch_refill(
            self.actor, saved, key=saved.reservation_id)
        self.assertEqual(result["status"], "committed")
        self.assertIsNotNone(repository.page)

    def test_combined_clip_resolves_current_cursor_and_calls_refill_tick(self):
        request = CLIPTextSearchRequest(
            "clip-plan", 4, self.binding, "客厅", "filter-v1", 20, "cursor-2")

        class Jobs:
            def current_for_browse(inner, scope):
                return "job-1"

            def pending_requests(inner, job_id):
                return (("branch-1", request, 1),)

        class Executor:
            def __init__(inner):
                inner.calls = []

            def tick_refill(inner, job_id, *, plan_id, expected_cursor):
                inner.calls.append((job_id, plan_id, expected_cursor))
                return {"outcomes": [{"plan_id": plan_id, "status": "committed"}]}

        executor = Executor()

        class Search:
            mode = "CLIP_COMBINED"

        search = Search()
        search.service = type("Service", (), {"repository": Tasks(self.task)})()
        search.jobs = Jobs()
        search.executor = executor
        adapter = CombinedCLIPRefillAdapter(search)
        position = adapter.list_refill_positions(
            self.actor, self.task.task_id, revision=2, generation=4)[0]
        self.assertEqual(parse_refill_position(position)["position"]["cursor"], "cursor-2")
        saved = reservation(position, self.task)
        result = ConfirmedRefillSearchHandler(adapter, adapter).dispatch_refill(
            self.actor, saved, key=saved.reservation_id)
        self.assertEqual(result["status"], "committed")
        self.assertEqual(executor.calls, [("job-1", "clip-plan", "cursor-2")])


if __name__ == "__main__":
    unittest.main()
