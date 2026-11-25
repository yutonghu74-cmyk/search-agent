from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dsh_retrieval.adapters.retrieval.clip_search import (
    CLIPMatch, CLIPSearchBatch, CLIPTextSearchRequest,
)
from dsh_retrieval.clip_search_jobs import (
    CLIPJobMember, CLIPSearchExecutor, SQLiteCLIPSearchJobs,
)
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import IndexBinding, SearchHit, SearchPage
from dsh_retrieval.models import Actor, RunStatus, Task
from dsh_retrieval.retrieval_contracts import RetrievalScope


class Tasks:
    def __init__(self, actor, task):
        self.actor, self.task = actor, task

    def get(self, actor, task_id):
        if actor != self.actor or task_id != self.task.task_id:
            raise ServiceError("NOT_FOUND", "Task not found", 404)
        return self.task


class Client:
    def __init__(self):
        self.calls = []
        self.failures = 0
        self.hook = lambda: None

    def search_text(self, context, request):
        self.calls.append((context, request))
        self.hook()
        if self.failures:
            self.failures -= 1
            raise ServiceError("SEARCH_TIMEOUT", "timeout", 504, retryable=True)
        page_number = 1 if request.cursor is None else 2
        next_cursor = "page-2" if request.cursor is None else None
        hit = SearchHit(request.binding, f"hit-{page_number}", 0.4 - page_number / 100)
        match = CLIPMatch(hit, f"IMG_{page_number}.jpg", f"/mock/IMG_{page_number}.jpg",
            f"/local/IMG_{page_number}.jpg", "春季上新", "mock", "mock-images",
            "dataset-key", "dataset-v1", "shard_0.db")
        page = SearchPage((hit,), next_cursor,
            "more_available" if next_cursor else "depth_limit", "live", None)
        return CLIPSearchBatch(page, (match,))


class CLIPSearchJobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "clip-jobs.sqlite3"
        self.jobs = SQLiteCLIPSearchJobs(self.path)
        self.actor = Actor("tenant", "owner")
        self.task = Task("task", "tenant", "owner", "找图片", "image", 10, "demo",
            False, "manual", "request-digest", task_version=3, confirmed_revision=2,
            run_generation=4, run_status=RunStatus.PLANNING, stage="planning",
            project_id="demo", dimension_id="default",
            confirmed_proposal_json='{"query_spec":{"raw_request":"找图片"}}')
        self.scope = RetrievalScope.from_task(self.task)
        self.binding = IndexBinding("clip-v1", "clip-images", "index-v1", "clip@1", 4, "IP")
        self.request = CLIPTextSearchRequest("plan-1", self.scope.generation, self.binding,
            "红色连衣裙", "fashion-only", 2)
        self.members = (CLIPJobMember("branch-1", self.request),)
        self.tasks = Tasks(self.actor, self.task)
        self.client = Client()

    def test_shard_groups_persist_recover_and_reject_configuration_drift(self):
        from types import SimpleNamespace
        declared = tuple(f's{i}.db' for i in range(7))
        second = replace(self.request, plan_id='plan-2',
            binding=replace(self.binding, binding_id='clip-group-2'))
        self.members = (CLIPJobMember('branch-1', self.request, declared[:4], declared),
                        CLIPJobMember('branch-1', second, declared[4:], declared))
        job_id, _ = self.create()
        self.client.endpoints = {self.binding.binding_id: SimpleNamespace(
            shard_paths=declared[:4], declared_shards=declared)}
        executor = CLIPSearchExecutor(self.tasks, self.jobs, self.client)
        executor.tick(job_id)
        row = self.jobs.view(job_id)['coverage']['queries'][0]
        self.assertEqual((row['total'], row['visited'], row['pending']), (7, 4, 3))
        reopened = SQLiteCLIPSearchJobs(self.path)
        self.assertEqual(reopened.job(job_id).members, self.members)
        self.assertEqual(reopened.view(job_id)['coverage'], self.jobs.view(job_id)['coverage'])
        # A group must be executed using its frozen shard list. No HTTP call on drift.
        self.client.endpoints['clip-group-2'] = SimpleNamespace(
            shard_paths=('changed.db',), declared_shards=declared)
        calls = len(self.client.calls)
        with self.assertRaises(ServiceError) as caught:
            CLIPSearchExecutor(self.tasks, reopened, self.client).tick(job_id)
        self.assertEqual(caught.exception.code, 'SHARD_CATALOG_CHANGED')
        self.assertEqual(len(self.client.calls), calls)
        self.assertFalse(reopened.view(job_id)['coverage']['queries'][0]['complete'])

    def test_business_pause_does_not_reserve_or_call_network_and_can_resume(self):
        job_id, _ = self.create()
        gate = ['CLEANING_IN_PROGRESS']
        executor = CLIPSearchExecutor(self.tasks, self.jobs, self.client,
            continuation=lambda task, phase: gate[0])
        before = self.jobs.view(job_id)
        for reason in ('CLEANING_IN_PROGRESS', 'TARGET_MET', 'AWAIT_REFILL'):
            gate[0] = reason
            result = executor.tick(job_id)
            self.assertEqual((result['status'], result['pause_reason']), ('paused', reason))
            self.assertEqual(result['network_attempts'], 0)
            self.assertEqual(self.jobs.view(job_id), before)
            self.assertEqual(self.client.calls, [])
        gate[0] = None
        self.assertEqual(executor.tick(job_id)['network_attempts'], 1)
        gate[0] = 'TARGET_MET'
        pending = self.jobs.pending_requests(job_id)[0][1]
        result = executor.tick_refill(job_id, plan_id=pending.plan_id, expected_cursor=pending.cursor)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(len(self.client.calls), 1)

    def test_initial_window_skips_alternative_and_never_deepens_first_member(self):
        second = replace(self.request, plan_id='alternative')
        self.members = (CLIPJobMember('branch-1', self.request, windowed=True),
                        CLIPJobMember('branch-1', second, windowed=True))
        job_id, _ = self.create()
        def gate(task, phase):
            return 'CANDIDATE_WINDOW_READY' if self.jobs.view(job_id)['members'][0]['pages'] else None
        executor = CLIPSearchExecutor(self.tasks, self.jobs, self.client, continuation=gate,
            initial_selector=lambda task, job: frozenset({self.request.plan_id}))
        result = executor.tick(job_id, max_requests=3)
        self.assertEqual(result['pause_reason'], 'CANDIDATE_WINDOW_READY')
        self.assertEqual(result['network_attempts'], 1)
        self.assertEqual([r['pages'] for r in self.jobs.view(job_id)['members']], [1, 0])
        reopened = SQLiteCLIPSearchJobs(self.path)
        self.assertEqual(reopened.job(job_id).members, self.members)
        with self.assertRaises(ServiceError) as caught:
            CLIPSearchExecutor(self.tasks, reopened, self.client).tick(job_id)
        self.assertEqual(caught.exception.code, 'SEARCH_GATE_NOT_CONFIGURED')
        self.assertEqual(len(self.client.calls), 1)

    def create(self):
        return self.jobs.create(self.scope, "auth-v1", self.members)

    def assert_code(self, code, callback):
        with self.assertRaises(ServiceError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_create_is_idempotent_and_restart_discovers_job(self):
        job_id, replay = self.create()
        self.assertFalse(replay)
        self.assertEqual(self.create(), (job_id, True))
        reopened = SQLiteCLIPSearchJobs(self.path)
        self.assertEqual(reopened.recoverable(), (job_id,))
        job = reopened.job(job_id)
        self.assertEqual(job.scope, self.scope)
        self.assertEqual(job.members, self.members)

    def test_one_query_branch_can_use_multiple_preconfigured_indexes(self):
        other_binding = IndexBinding("clip-v2", "clip-archive", "index-v2", "clip@1", 4, "IP")
        second = replace(self.request, plan_id="plan-2", binding=other_binding)
        members = self.members + (CLIPJobMember("branch-1", second),)
        jobs = SQLiteCLIPSearchJobs(Path(self.tmp.name) / "multi-source.sqlite3")
        job_id, _ = jobs.create(self.scope, "auth-v1", members)
        view = CLIPSearchExecutor(self.tasks, jobs, self.client).tick(job_id, max_requests=2)
        self.assertEqual({row["plan_id"] for row in view["members"]}, {"plan-1", "plan-2"})
        self.assertEqual({row["plan_id"] for row in view["items"]}, {"plan-1", "plan-2"})

    def test_executor_persists_pages_sources_and_completion(self):
        job_id, _ = self.create()
        executor = CLIPSearchExecutor(self.tasks, self.jobs, self.client)
        first = executor.tick(job_id)
        self.assertEqual(first["status"], "running")
        self.assertEqual(first["raw_hit_count"], 1)
        self.assertEqual(first["members"][0]["pages"], 1)
        reopened = SQLiteCLIPSearchJobs(self.path)
        executor = CLIPSearchExecutor(self.tasks, reopened, self.client)
        final = executor.tick(job_id)
        self.assertEqual(final["status"], "complete")
        self.assertEqual(final["raw_hit_count"], 2)
        self.assertEqual([item["filename"] for item in final["items"]],
                         ["IMG_1.jpg", "IMG_2.jpg"])
        self.assertEqual(reopened.recoverable(), ())
        self.assertEqual(executor.tick(job_id)["network_attempts"], 0)

    def test_transient_failure_retries_same_page_with_limit(self):
        job_id, _ = self.create()
        executor = CLIPSearchExecutor(self.tasks, self.jobs, self.client)
        self.client.failures = 1
        failed = executor.tick(job_id)
        self.assertEqual(failed["outcomes"][0]["status"], "retry_pending")
        succeeded = executor.tick(job_id)
        self.assertEqual(succeeded["outcomes"][0]["status"], "committed")
        self.assertIsNone(self.client.calls[0][1].cursor)
        self.assertIsNone(self.client.calls[1][1].cursor)

        other = SQLiteCLIPSearchJobs(Path(self.tmp.name) / "failed.sqlite3")
        job_id, _ = other.create(self.scope, "auth-v1", self.members)
        client = Client()
        client.failures = 3
        executor = CLIPSearchExecutor(self.tasks, other, client)
        executor.tick(job_id)
        executor.tick(job_id)
        terminal = executor.tick(job_id)
        self.assertEqual(terminal["status"], "failed")
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(executor.tick(job_id)["network_attempts"], 0)

    def test_old_scope_never_commits_late_result(self):
        job_id, _ = self.create()
        executor = CLIPSearchExecutor(self.tasks, self.jobs, self.client)
        self.client.hook = lambda: setattr(self.tasks, "task", replace(self.task, task_version=4))
        result = executor.tick(job_id)
        self.assertEqual(result["status"], "superseded")
        self.assertEqual(result["raw_hit_count"], 0)
        self.assertEqual(result["outcomes"][0]["status"], "superseded")
        self.assertEqual(self.jobs.recoverable(), ())

    def test_refill_tick_allows_only_same_revision_generation_after_downstream_advance(self):
        job_id, _ = self.create()
        executor = CLIPSearchExecutor(self.tasks, self.jobs, self.client)
        first = executor.tick(job_id)
        self.assertEqual(first["members"][0]["pages"], 1)
        self.tasks.task = replace(self.task, task_version=self.task.task_version + 1,
                                  run_status=RunStatus.RUNNING, stage="deciding")
        pending = self.jobs.pending_requests(job_id)
        self.assertEqual((pending[0][1].plan_id, pending[0][1].cursor),
                         ("plan-1", "page-2"))
        result = executor.tick_refill(job_id, plan_id="plan-1",
                                      expected_cursor="page-2")
        self.assertEqual(result["outcomes"][0]["status"], "committed")
        self.assertEqual(result["status"], "complete")

        other = SQLiteCLIPSearchJobs(Path(self.tmp.name) / "stale-refill.sqlite3")
        stale_id, _ = other.create(self.scope, "auth-v1", self.members)
        stale_executor = CLIPSearchExecutor(self.tasks, other, Client())
        self.tasks.task = replace(self.tasks.task, run_generation=5)
        rejected = stale_executor.tick_refill(stale_id, plan_id="plan-1",
                                               expected_cursor=None)
        self.assertEqual(rejected["status"], "superseded")
        self.assertEqual(rejected["network_attempts"], 0)

    def test_invalid_members_and_commit_conflicts_fail_closed(self):
        bad = (CLIPJobMember("branch-1", replace(self.request, plan_version=99)),)
        self.assert_code("INVALID_RETRIEVAL_PLAN", lambda: self.jobs.create(
            self.scope, "auth-v1", bad))
        job_id, _ = self.create()
        _, request, cursor = self.jobs.next_request(job_id)
        batch = self.client.search_text(None, request)
        self.assert_code("PLAN_REQUEST_MISMATCH", lambda: self.jobs.commit(
            job_id, "missing", cursor, batch))
        self.jobs.commit(job_id, request.plan_id, cursor, batch)
        self.assert_code("PLAN_NOT_CURRENT", lambda: self.jobs.commit(
            job_id, request.plan_id, cursor, batch))


if __name__ == "__main__":
    unittest.main()
