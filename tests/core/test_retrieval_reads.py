from contextlib import closing
from dataclasses import FrozenInstanceError
import json
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor, RunStatus
from draft_helpers import make_service, confirmation
from core import test_retrieval_storage as storage_fixture


class RetrievalReadTest(unittest.TestCase):
    def setUp(self):
        # Reuse setup/helpers, not the storage test methods (no duplicate suite).
        self.f = storage_fixture.RetrievalStorageTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.plan, _ = self.f.confirm()
        self.oid, _ = self.f.repo.reserve_page(self.f.actor, self.plan, self.plan.request, "page")

    def get(self, repo=None):
        return (repo or self.f.repo).get_operation(self.f.actor, self.f.tid, self.oid)

    def pending(self, repo=None):
        return (repo or self.f.repo).list_pending(self.f.actor, self.f.tid)

    def assert_code(self, code, call):
        self.f.assert_code(code, call)

    def test_pending_is_immutable_and_survives_new_connection(self):
        operation = self.get()
        self.assertEqual(operation.operation_id, self.oid)
        self.assertEqual(operation.status, "reserved")
        self.assertIsNone(operation.page)
        self.assertEqual(self.pending(self.f.reopen()), (operation,))
        with self.assertRaises(FrozenInstanceError):
            operation.page = self.f.first

    def test_completed_empty_page_is_readable_but_not_pending(self):
        self.f.repo.commit_page(self.f.actor, self.oid, self.plan, self.plan.request, self.f.empty)
        operation = self.get(self.f.reopen())
        self.assertEqual(operation.status, "committed")
        self.assertEqual(operation.page, self.f.empty)
        self.assertEqual(self.pending(), ())

    def test_nonempty_page_is_exact_and_reads_do_not_write(self):
        self.f.repo.commit_page(self.f.actor, self.oid, self.plan, self.plan.request, self.f.first)
        before = self.f.state()
        task = self.f.tasks.get(self.f.actor, self.f.tid)
        # Any write path would serialize both ledgers; deliberately fail if used.
        with patch("dsh_retrieval.adapters.task_storage.retrieval.dump_state", side_effect=AssertionError("write")):
            for _ in range(3):
                self.assertEqual(self.get().page, self.f.first)
                self.assertEqual(self.pending(), ())
        self.assertEqual(self.f.state(), before)
        self.assertEqual(self.f.tasks.get(self.f.actor, self.f.tid), task)

    def test_unknown_and_cross_task_ids_hidden(self):
        tasks = make_service(self.f.tasks)
        other, _ = tasks.create_task(self.f.actor, "task2", confirmation(tasks, self.f.actor, "draft2"))
        other_id = other["task_id"]
        self.assert_code("NOT_FOUND", lambda: self.f.repo.get_operation(self.f.actor, self.f.tid, "unknown"))
        self.assert_code("NOT_FOUND", lambda: self.f.repo.get_operation(self.f.actor, other_id, self.oid))
        self.assertEqual(self.f.repo.list_pending(self.f.actor, other_id), ())

    def test_wrong_owner_and_tenant_rejected(self):
        for actor in (Actor("tenant", "other"), Actor("other", "owner")):
            self.assert_code("NOT_FOUND", lambda: self.f.repo.get_operation(actor, self.f.tid, self.oid))
            self.assert_code("NOT_FOUND", lambda: self.f.repo.list_pending(actor, self.f.tid))

    def test_stale_generation_rejected_for_pending_and_committed(self):
        self.f.repo.commit_page(self.f.actor, self.oid, self.plan, self.plan.request, self.f.empty)
        self.f.change_task(run_generation=self.plan.scope.generation + 1)
        self.assert_code("REVISION_CONFLICT", self.get)
        self.assert_code("REVISION_CONFLICT", self.pending)

    def test_superseded_plan_not_returned_as_pending(self):
        proposal = self.f.propose(epoch=2, plan_id="second")
        self.f.confirm(proposal, key="second")
        self.assertEqual(self.pending(), ())
        self.assert_code("REVISION_CONFLICT", self.get)

    def test_pause_cancel_and_finalizing_reject_reads(self):
        for status in (RunStatus.PAUSED, RunStatus.CANCELLED):
            self.f.change_task(run_status=status)
            self.assert_code("INVALID_STATE_TRANSITION", self.get)
            self.assert_code("INVALID_STATE_TRANSITION", self.pending)
        self.f.change_task(run_status=RunStatus.RUNNING, stage="FINALIZING")
        self.assert_code("INVALID_STATE_TRANSITION", self.get)

    def test_revoked_project_or_library_and_auth_outage_fail_closed(self):
        self.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", self.get)
        self.assert_code("ACCESS_DENIED", self.pending)
        self.f.auth.denied = False
        self.f.auth.down = True
        self.assert_code("AUTH_UNAVAILABLE", self.pending)
        self.f.auth.down = False
        self.f.projects.grants.clear()
        self.assert_code("PROJECT_ACCESS_DENIED", self.get)

    def test_final_authority_recheck_blocks_return(self):
        for read in (self.get, self.pending):
            calls = []
            def revoke(*args):
                calls.append(args)
                if len(calls) == 2:
                    raise ServiceError("ACCESS_DENIED", "revoked", 403)
            with patch.object(self.f.auth, "require_access", side_effect=revoke):
                self.assert_code("ACCESS_DENIED", read)

    def test_storage_corruption_is_not_empty_pending_list(self):
        with closing(sqlite3.connect(self.f.path)) as db, db:
            db.execute("UPDATE retrieval_ledger SET payload='broken'")
        self.assert_code("STORAGE_CORRUPT", self.pending)
        self.assert_code("STORAGE_CORRUPT", self.get)

    def test_new_process_discovers_operation_without_old_key(self):
        code = """import json,sys
from dsh_retrieval.adapters.task_storage.retrieval import SQLiteRetrievalPlanRepository
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.models import Actor
class FixtureAuthority:
    def require_access(self,*args): pass
r=SQLiteRetrievalPlanRepository(sys.argv[1],projects=LocalProjectAccess([('tenant','owner','demo','default')]),authorization=FixtureAuthority())
a=Actor('tenant','owner'); operations=r.list_pending(a,sys.argv[2])
print(json.dumps([{'id':o.operation_id,'status':r.get_operation(a,sys.argv[2],o.operation_id).status} for o in operations]))
"""
        child = subprocess.run([sys.executable, "-c", code, str(self.f.path), self.f.tid],
                               text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(child.stdout), [{"id": self.oid, "status": "reserved"}])


if __name__ == "__main__":
    unittest.main()
