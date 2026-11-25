from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository, dump
from dsh_retrieval.adapters.task_storage.retrieval import SQLiteRetrievalPlanRepository, load_state
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import IndexBinding, QueryVector, SearchRequest, SearchPage, SearchHit
from dsh_retrieval.models import Actor, RunStatus
from dsh_retrieval.retrieval_contracts import RetrievalPlan, RetrievalScope
from draft_helpers import make_service, confirmation


class Authority:
    def __init__(self):
        self.denied = False
        self.down = False

    def require_access(self, context, resource_ref, action):
        if self.down:
            raise ServiceError("AUTH_UNAVAILABLE", "unavailable", 503)
        if self.denied:
            raise ServiceError("ACCESS_DENIED", "revoked", 403)


class RetrievalStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.tasks = SQLiteTaskRepository(self.path)
        self.actor = Actor("tenant", "owner")
        service = make_service(self.tasks)
        self.projects = service.projects
        self.auth = Authority()
        task, _ = service.create_task(self.actor, "task", confirmation(service, self.actor))
        self.tid = task["task_id"]
        self.repo = self.reopen()
        self.binding = IndexBinding("binding", "library", "index-v1", "encoder-v1", 2, "COSINE")
        self.request = SearchRequest("plan-1", 1, self.binding,
                                     QueryVector("encoder-v1", (0.5, 0.5)), "filter-v1", 2)
        self.proposal = self.propose()
        self.hit = SearchHit(self.binding, 1, 0.9)
        self.first = SearchPage((self.hit,), "cursor-2", "more_available", "snapshot", "snap-v1")
        self.empty = SearchPage((), None, "exhausted", "snapshot", "snap-v1")

    def reopen(self):
        return SQLiteRetrievalPlanRepository(self.path, projects=self.projects, authorization=self.auth)

    def propose(self, epoch=1, plan_id="plan-1"):
        task = self.tasks.get(self.actor, self.tid)
        proposal = RetrievalPlan(RetrievalScope.from_task(task), epoch, replace(self.request, plan_id=plan_id))
        self.repo.put_proposal(self.actor, proposal)
        return proposal

    def confirm(self, proposal=None, key="approve", repo=None, **changes):
        proposal = proposal or self.proposal
        args = dict(key=key, proposal_digest=proposal.proposal_digest,
                    expected_task_version=proposal.scope.task_version, action="confirm")
        args.update(changes)
        return (repo or self.repo).confirm(self.actor, self.tid, proposal.request.plan_id, **args)

    def state(self):
        with closing(sqlite3.connect(self.path)) as db:
            return load_state(db.execute("SELECT format,payload FROM retrieval_ledger").fetchone())

    def change_task(self, **changes):
        # Trusted fixture for pause/revision transitions, not a production API.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            memory = self.tasks._load(db.execute("SELECT format,payload FROM local_ledger").fetchone())
            memory._tasks[self.tid] = replace(memory._tasks[self.tid], **changes)
            db.execute("UPDATE local_ledger SET payload=?", (dump(memory),))

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as error:
            call()
        self.assertEqual(error.exception.code, code)

    def test_confirm_atomic_and_same_or_different_key_replays(self):
        version = self.tasks.get(self.actor, self.tid).task_version
        plan, replay = self.confirm()
        self.assertFalse(replay)
        self.assertEqual(plan.scope.task_version, version + 1)
        self.assertEqual(self.tasks.get(self.actor, self.tid).task_version, version + 1)
        self.assertEqual(self.confirm(), (plan, True))
        self.assertEqual(self.confirm(key="another"), (plan, True))
        self.assertEqual(len(self.state()["events"]), 1)

    def test_concurrent_connections_have_one_confirmation_effect(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda i: self.confirm(key=str(i), repo=self.reopen()), range(18)))
        self.assertEqual(sum(not result[1] for result in results), 1)
        self.assertTrue(all(result[0] == results[0][0] for result in results))
        self.assertEqual(len(self.state()["events"]), 1)

    def test_confirmation_failure_rolls_back_task_and_plan(self):
        before = self.tasks.get(self.actor, self.tid)
        with patch("dsh_retrieval.adapters.task_storage.ledger_rows.prepare_state", side_effect=RuntimeError("fault")):
            with self.assertRaises(RuntimeError):
                self.confirm()
        self.assertEqual(self.tasks.get(self.actor, self.tid), before)
        self.assertIsNone(self.reopen().get_current(self.actor, self.tid))
        self.assertFalse(self.confirm()[1])

    def test_explicit_confirmation_and_stale_digest_required(self):
        self.assert_code("CONFIRMATION_REQUIRED", lambda: self.confirm(action="auto"))
        self.assert_code("PLAN_DIGEST_MISMATCH", lambda: self.confirm(proposal_digest="changed"))
        self.assert_code("REVISION_CONFLICT", lambda: self.confirm(expected_task_version=True))
        self.assertFalse(self.state()["approved"])

    def test_confirmation_key_conflict_and_immutable_proposal(self):
        self.confirm()
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: self.confirm(proposal_digest="changed"))
        self.assert_code("PLAN_ID_CONFLICT", lambda: self.repo.put_proposal(self.actor, replace(
            self.proposal, request=replace(self.request, batch_size=1))))

    def test_epoch_is_monotonic_and_competing_proposal_stale(self):
        other = self.propose(plan_id="alternative")
        self.confirm()
        self.assert_code("REVISION_CONFLICT", lambda: self.confirm(other, key="other"))
        self.assert_code("PLAN_EPOCH_CONFLICT", lambda: self.propose(epoch=8, plan_id="jump"))

    def test_old_confirmation_does_not_reactivate_superseded_plan(self):
        old, _ = self.confirm()
        second = self.propose(epoch=2, plan_id="plan-2")
        current, _ = self.confirm(second, key="second")
        self.assertEqual(self.confirm(), (old, True))
        self.assertEqual(self.confirm(key="old-new-key"), (old, True))
        self.assertEqual(self.repo.get_current(self.actor, self.tid), current)
        self.assert_code("REVISION_CONFLICT", lambda: self.repo.reserve_page(self.actor, old, old.request, "late"))

    def test_reservation_survives_restart_and_deduplicates_keys(self):
        plan, _ = self.confirm()
        operation, replay = self.repo.reserve_page(self.actor, plan, plan.request, "page")
        self.assertFalse(replay)
        self.assertEqual(self.reopen().reserve_page(self.actor, plan, plan.request, "page"), (operation, True))
        self.assertEqual(self.reopen().reserve_page(self.actor, plan, plan.request, "different"), (operation, True))
        self.assertEqual(len(self.state()["operations"]), 1)

    def test_page_commit_replay_is_atomic_and_immutable(self):
        plan, _ = self.confirm()
        operation, _ = self.repo.reserve_page(self.actor, plan, plan.request, "page")
        self.assertFalse(self.repo.commit_page(self.actor, operation, plan, plan.request, self.first))
        self.assertTrue(self.reopen().commit_page(self.actor, operation, plan, plan.request, self.first))
        self.assertEqual(len(self.state()["events"]), 2)
        self.assert_code("RESULT_CONFLICT", lambda: self.repo.commit_page(
            self.actor, operation, plan, plan.request, self.empty))

    def test_unknown_or_uncommitted_cursor_rejected(self):
        plan, _ = self.confirm()
        request = replace(plan.request, cursor="cursor-2")
        self.assert_code("CURSOR_INVALID", lambda: self.repo.reserve_page(self.actor, plan, request, "page2"))
        operation, _ = self.repo.reserve_page(self.actor, plan, plan.request, "page1")
        self.repo.commit_page(self.actor, operation, plan, plan.request, self.first)
        second, _ = self.repo.reserve_page(self.actor, plan, request, "page2")
        self.assertFalse(self.repo.commit_page(self.actor, second, plan, request, self.empty))
        self.assertEqual(len(self.state()["operations"]), 2)
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: self.repo.reserve_page(self.actor, plan, request, "page1"))

    def test_bad_page_rolls_back_and_can_be_retried(self):
        plan, _ = self.confirm()
        operation, _ = self.repo.reserve_page(self.actor, plan, plan.request, "page")
        self.assert_code("INVALID_SEARCH_PAGE", lambda: self.repo.commit_page(
            self.actor, operation, plan, plan.request, replace(self.first, snapshot_ref=None)))
        self.assertIsNone(self.state()["operations"][operation][2])
        with patch("dsh_retrieval.adapters.task_storage.ledger_rows.prepare_state", side_effect=RuntimeError("fault")):
            with self.assertRaises(RuntimeError):
                self.repo.commit_page(self.actor, operation, plan, plan.request, self.first)
        self.assertEqual(len(self.state()["events"]), 1)
        self.assertFalse(self.state()["cursors"])
        self.assertFalse(self.repo.commit_page(self.actor, operation, plan, plan.request, self.first))

    def test_empty_page_does_not_create_cleaning_or_finish_task(self):
        plan, _ = self.confirm()
        before = self.tasks.get(self.actor, self.tid)
        operation, _ = self.repo.reserve_page(self.actor, plan, plan.request, "page")
        self.repo.commit_page(self.actor, operation, plan, plan.request, self.empty)
        self.assertEqual(self.tasks.get(self.actor, self.tid), before)
        self.assertEqual(before.run_status, RunStatus.RUNNING)
        self.assertIsNone(before.active_cleaning_operation_id)
        self.assertIsNone(self.tasks.cleaning_results(self.actor, self.tid))

    def test_cancel_or_generation_change_rejects_late_page(self):
        plan, _ = self.confirm()
        operation, _ = self.repo.reserve_page(self.actor, plan, plan.request, "page")
        self.change_task(run_generation=plan.scope.generation + 1)
        self.assert_code("REVISION_CONFLICT", lambda: self.repo.commit_page(
            self.actor, operation, plan, plan.request, self.first))
        self.change_task(run_status=RunStatus.CANCELLED)
        self.assert_code("INVALID_STATE_TRANSITION", lambda: self.repo.commit_page(
            self.actor, operation, plan, plan.request, self.first))
        self.assertIsNone(self.state()["operations"][operation][2])

    def test_wrong_operation_binding_rejected(self):
        plan, _ = self.confirm()
        self.assert_code("OPERATION_MISMATCH", lambda: self.repo.commit_page(
            self.actor, "made-up", plan, plan.request, self.first))

    def test_current_authority_rechecked_for_replay_and_commit(self):
        plan, _ = self.confirm()
        operation, _ = self.repo.reserve_page(self.actor, plan, plan.request, "page")
        self.auth.denied = True
        for call in (lambda: self.confirm(), lambda: self.repo.get_current(self.actor, self.tid),
                     lambda: self.repo.reserve_page(self.actor, plan, plan.request, "page"),
                     lambda: self.repo.commit_page(self.actor, operation, plan, plan.request, self.first)):
            self.assert_code("ACCESS_DENIED", call)
        self.auth.denied = False
        self.auth.down = True
        self.assert_code("AUTH_UNAVAILABLE", lambda: self.repo.get_current(self.actor, self.tid))
        self.assertIsNone(self.state()["operations"][operation][2])

    def test_cross_user_tenant_and_project_denied(self):
        for actor in (Actor("tenant", "other"), Actor("other", "owner")):
            self.assert_code("NOT_FOUND", lambda: self.repo.get_current(actor, self.tid))
        self.projects.grants.clear()
        self.assert_code("PROJECT_ACCESS_DENIED", lambda: self.confirm())

    def test_new_process_recovers_approved_plan_and_reservation(self):
        plan, _ = self.confirm()
        operation, _ = self.repo.reserve_page(self.actor, plan, plan.request, "page")
        code = """import json,sys
from dsh_retrieval.adapters.task_storage.retrieval import SQLiteRetrievalPlanRepository
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.models import Actor
class FixtureAuthority:
    def require_access(self,*args): pass
r=SQLiteRetrievalPlanRepository(sys.argv[1],projects=LocalProjectAccess([('tenant','owner','demo','default')]),authorization=FixtureAuthority())
a=Actor('tenant','owner'); p=r.get_current(a,sys.argv[2]); op,replay=r.reserve_page(a,p,p.request,'page')
print(json.dumps({'digest':p.proposal_digest,'operation':op,'replay':replay}))
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.path), self.tid],
                                text=True, capture_output=True, check=True)
        data = json.loads(result.stdout)
        self.assertEqual(data, {"digest": plan.proposal_digest, "operation": operation, "replay": True})

    def test_task_ledger_compatibility_and_retrieval_corruption_fail_closed(self):
        self.confirm()
        self.assertEqual(SQLiteTaskRepository(self.path).get(self.actor, self.tid).task_id, self.tid)
        for sql, expected in (("UPDATE retrieval_ledger SET format=99", "STORAGE_FORMAT_UNSUPPORTED"),
                              ("UPDATE retrieval_ledger SET format=1,payload='bad'", "STORAGE_CORRUPT")):
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute(sql)
            self.assert_code(expected, self.reopen)
        self.assertEqual(SQLiteTaskRepository(self.path).get(self.actor, self.tid).task_id, self.tid)

    def test_final_authorization_denial_rolls_back_confirmation(self):
        before = self.tasks.get(self.actor, self.tid)
        calls = []
        def revoke(context, resource_ref, action):
            calls.append(resource_ref)
            if len(calls) == 2:
                raise ServiceError("ACCESS_DENIED", "revoked at final check", 403)
        with patch.object(self.auth, "require_access", side_effect=revoke):
            self.assert_code("ACCESS_DENIED", lambda: self.confirm())
        self.assertEqual(self.tasks.get(self.actor, self.tid), before)
        self.assertFalse(self.state()["approved"])

    def test_simulated_gateway_pages_commit_through_ledger(self):
        from dsh_retrieval.adapters.retrieval.simulated import ApprovedFixture, SimulatedRetrieval
        from dsh_retrieval.integration_models import AccessContext, Library
        plan, _ = self.confirm()
        context = AccessContext(self.actor, self.tid, "retrieval", "current")
        hits = tuple(replace(self.hit, vector_id=i) for i in range(3))
        gateway = SimulatedRetrieval(self.auth,
            libraries=(Library("library", "synthetic", "image", (self.binding,)),), vectors={},
            approved=(ApprovedFixture(context, plan.request, hits),), mappings={})
        request = plan.request
        total = 0
        while True:
            operation, _ = self.repo.reserve_page(self.actor, plan, request, "page:" + str(request.cursor))
            page = gateway.search(context, request)
            self.assertFalse(self.repo.commit_page(self.actor, operation, plan, request, page))
            total += len(page.hits)
            if page.next_cursor is None:
                break
            request = replace(plan.request, cursor=page.next_cursor)
        self.assertEqual(total, 3)
        self.assertEqual(len(self.state()["events"]), 3)
        self.assertIsNone(self.tasks.cleaning_results(self.actor, self.tid))

    def test_concurrent_reservation_and_commit_have_one_effect(self):
        plan, _ = self.confirm()
        with ThreadPoolExecutor(max_workers=6) as pool:
            reservations = list(pool.map(lambda i: self.reopen().reserve_page(
                self.actor, plan, plan.request, str(i)), range(12)))
        self.assertEqual(len({result[0] for result in reservations}), 1)
        self.assertEqual(sum(not result[1] for result in reservations), 1)
        operation = reservations[0][0]
        with ThreadPoolExecutor(max_workers=6) as pool:
            commits = list(pool.map(lambda _: self.reopen().commit_page(
                self.actor, operation, plan, plan.request, self.first), range(12)))
        self.assertEqual(commits.count(False), 1)
        self.assertEqual(len(self.state()["events"]), 2)


if __name__ == "__main__":
    unittest.main()
