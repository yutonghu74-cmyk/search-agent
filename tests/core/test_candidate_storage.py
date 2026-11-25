from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_candidate_contracts as fixture
from retrieval import test_materialization as materialization_fixture
from dsh_retrieval.adapters.task_storage.candidates import SQLiteCandidateManifestRepository, load_state
from dsh_retrieval.candidate_publication import CandidatePublicationService
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor, RunStatus
from draft_helpers import make_service, confirmation


class CandidateStorageTest(unittest.TestCase):
    def setUp(self):
        self.c = fixture.CandidateContractTest()
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)
        self.f = self.c.f
        self.proposal = self.c.proposal
        self.repo = self.reopen()
        self.repo.put_proposal(self.f.actor, self.proposal)

    def reopen(self):
        return SQLiteCandidateManifestRepository(self.f.path, projects=self.f.projects, authorization=self.f.auth)

    def confirm(self, repo=None, **changes):
        args = dict(key="confirm", expected_task_version=self.proposal.scope.task_version, action="confirm")
        args.update(changes)
        return (repo or self.repo).confirm(self.f.actor, self.proposal, **args)

    def state(self):
        with closing(sqlite3.connect(self.f.path)) as db:
            return load_state(db.execute("SELECT format,payload FROM candidate_ledger WHERE id=1").fetchone())

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as error:
            call()
        self.assertEqual(error.exception.code, code)

    def test_publish_replay_restart_and_no_task_changes(self):
        task = self.f.tasks.get(self.f.actor, self.f.tid)
        manifest, replay = self.confirm()
        self.assertFalse(replay)
        self.assertEqual(self.confirm(repo=self.reopen()), (manifest, True))
        self.assertEqual(self.confirm(key="another"), (manifest, True))
        self.assertEqual(self.reopen().get(self.f.actor, self.f.tid, manifest.manifest_ref), manifest)
        self.assertEqual(len(self.state()["events"]), 1)
        self.assertEqual(self.f.tasks.get(self.f.actor, self.f.tid), task)
        self.assertNotIn("source_path", str(self.state()))

    def test_concurrent_multi_connection_confirmation_one_effect(self):
        repos = [self.reopen() for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda i: self.confirm(repo=repos[i], key=f"key-{i}"), range(6)))
        self.assertEqual(len({m.manifest_ref for m, _ in results}), 1)
        self.assertEqual(sum(not replay for _, replay in results), 1)
        self.assertEqual(len(self.state()["events"]), 1)

    def test_confirmation_key_changed_body_conflicts(self):
        self.confirm()
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: self.confirm(expected_task_version=999))
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: self.confirm(action="reject"))

    def test_explicit_confirmation_and_version_required(self):
        self.assert_code("CONFIRMATION_REQUIRED", lambda: self.confirm(action="skip"))
        self.assert_code("REVISION_CONFLICT", lambda: self.confirm(expected_task_version=999))
        self.assertEqual(self.state()["events"], [])

    def test_cannot_confirm_unpreviewed_or_incomplete_source(self):
        modified = replace(self.proposal, items=(replace(self.proposal.items[0],
                           media=replace(self.c.media, authorized_media_ref="other")),))
        self.assert_code("MANIFEST_NOT_PREVIEWED", lambda: self.repo.confirm(
            self.f.actor, modified, key="new", expected_task_version=modified.scope.task_version, action="confirm"))
        self.assert_code("MANIFEST_INVALID", lambda: self.repo.put_proposal(self.f.actor, replace(self.proposal, items=())))

    def test_owner_tenant_and_cross_task_isolation(self):
        manifest, _ = self.confirm()
        for actor in (Actor("tenant", "other"), Actor("other", "owner")):
            self.assert_code("NOT_FOUND", lambda: self.repo.get(actor, self.f.tid, manifest.manifest_ref))
            self.assert_code("NOT_FOUND", lambda: self.repo.put_proposal(actor, self.proposal))
        service = make_service(self.f.tasks)
        other, _ = service.create_task(self.f.actor, "other", confirmation(service, self.f.actor, "draft-other"))
        self.assert_code("NOT_FOUND", lambda: self.repo.get(self.f.actor, other["task_id"], manifest.manifest_ref))

    def test_read_after_task_version_increment_without_retrieval_bypass(self):
        manifest, _ = self.confirm()
        self.f.change_task(task_version=self.proposal.scope.task_version + 1)
        self.assertEqual(self.repo.get(self.f.actor, self.f.tid, manifest.manifest_ref), manifest)
        self.assert_code("REVISION_CONFLICT", lambda: self.f.repo.get_operation(
            self.f.actor, self.f.tid, self.proposal.operation_id))
        self.assert_code("REVISION_CONFLICT", self.confirm)

    def test_generation_and_requirement_changes_reject_read(self):
        manifest, _ = self.confirm()
        self.f.change_task(run_generation=self.proposal.scope.generation + 1)
        self.assert_code("REVISION_CONFLICT", lambda: self.repo.get(self.f.actor, self.f.tid, manifest.manifest_ref))
        self.f.change_task(run_generation=self.proposal.scope.generation, confirmed_proposal_json="changed")
        self.assert_code("REVISION_CONFLICT", lambda: self.repo.get(self.f.actor, self.f.tid, manifest.manifest_ref))

    def test_replaced_plan_rejects_old_manifest(self):
        manifest, _ = self.confirm()
        self.f.confirm(self.f.propose(epoch=2, plan_id="new"), key="new")
        self.assert_code("PLAN_NOT_CURRENT", lambda: self.repo.get(self.f.actor, self.f.tid, manifest.manifest_ref))

    def test_pause_cancel_and_finalizing_reject_reads(self):
        manifest, _ = self.confirm()
        for status in (RunStatus.PAUSED, RunStatus.CANCELLED):
            self.f.change_task(run_status=status)
            self.assert_code("INVALID_STATE_TRANSITION", lambda: self.repo.get(self.f.actor, self.f.tid, manifest.manifest_ref))
        self.f.change_task(run_status=RunStatus.RUNNING, stage="FINALIZING")
        self.assert_code("INVALID_STATE_TRANSITION", lambda: self.repo.get(self.f.actor, self.f.tid, manifest.manifest_ref))

    def test_media_revoke_and_outage_block_reads_and_replays(self):
        manifest, _ = self.confirm()
        original = self.f.auth.require_access
        def deny_media(context, ref, action):
            if ref == self.c.media.authorized_media_ref:
                raise ServiceError("ACCESS_DENIED", "revoked", 403)
            return original(context, ref, action)
        with patch.object(self.f.auth, "require_access", side_effect=deny_media):
            self.assert_code("ACCESS_DENIED", lambda: self.repo.get(self.f.actor, self.f.tid, manifest.manifest_ref))
            self.assert_code("ACCESS_DENIED", self.confirm)
        self.f.auth.down = True
        self.assert_code("AUTH_UNAVAILABLE", lambda: self.repo.get(self.f.actor, self.f.tid, manifest.manifest_ref))

    def test_final_authorization_failure_rolls_back(self):
        before = self.state()
        calls = []
        def revoke(context, ref, action):
            if ref == self.c.media.authorized_media_ref:
                calls.append(ref)
                if len(calls) == 2:
                    raise ServiceError("ACCESS_DENIED", "revoked", 403)
        with patch.object(self.f.auth, "require_access", side_effect=revoke):
            self.assert_code("ACCESS_DENIED", self.confirm)
        self.assertEqual(self.state(), before)

    def test_serialization_failure_has_no_partial_publication(self):
        before = self.state()
        with patch("dsh_retrieval.adapters.task_storage.ledger_rows.prepare_state", side_effect=ServiceError("STORAGE_LIMIT", "full", 507)):
            self.assert_code("STORAGE_LIMIT", self.confirm)
        self.assertEqual(self.state(), before)

    def test_get_does_not_write(self):
        manifest, _ = self.confirm()
        with patch("dsh_retrieval.adapters.task_storage.ledger_rows.prepare_state", side_effect=AssertionError("write")):
            self.assertEqual(self.repo.get(self.f.actor, self.f.tid, manifest.manifest_ref), manifest)

    def test_corrupt_storage_is_not_reset(self):
        with closing(sqlite3.connect(self.f.path)) as db, db:
            db.execute("UPDATE candidate_ledger SET payload='broken'")
        self.assert_code("STORAGE_CORRUPT", self.reopen)

    def test_new_process_recovers_manifest(self):
        manifest, _ = self.confirm()
        script = '''import sys
from dsh_retrieval.adapters.task_storage.candidates import SQLiteCandidateManifestRepository
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.models import Actor
class Authority:
    def require_access(self,*args): pass
r=SQLiteCandidateManifestRepository(sys.argv[1],projects=LocalProjectAccess([('tenant','owner','demo','default')]),authorization=Authority())
print(r.get(Actor('tenant','owner'),sys.argv[2],sys.argv[3]).proposal.proposal_digest)
'''
        result = subprocess.run([sys.executable, "-c", script, str(self.f.path), self.f.tid, manifest.manifest_ref],
                                check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), self.proposal.proposal_digest)

    def test_empty_page_publication_does_not_create_cleaning(self):
        plan = self.c.operation.plan
        request = replace(plan.request, cursor=self.f.first.next_cursor)
        oid, _ = self.f.repo.reserve_page(self.f.actor, plan, request, "empty-page")
        self.f.repo.commit_page(self.f.actor, oid, plan, request, self.f.empty)
        proposal = replace(self.proposal, operation_id=oid, items=())
        self.repo.put_proposal(self.f.actor, proposal)
        manifest, _ = self.repo.confirm(self.f.actor, proposal, key="empty",
            expected_task_version=proposal.scope.task_version, action="confirm")
        self.assertEqual(manifest.proposal.items, ())
        self.assertIsNone(self.f.tasks.get(self.f.actor, self.f.tid).active_cleaning_operation_id)

    def test_missing_event_detected_as_corruption(self):
        from dsh_retrieval.adapters.task_storage.candidates import dump_state
        self.confirm()
        state = self.state()
        state["events"] = []
        with closing(sqlite3.connect(self.f.path)) as db, db:
            db.execute("UPDATE candidate_ledger SET payload=?", (dump_state(state),))
        self.assert_code("STORAGE_CORRUPT", self.reopen)


class CandidatePublicationIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.m = materialization_fixture.MaterializationTest()
        self.m.setUp()
        self.addCleanup(self.m.doCleanups)
        self.f = self.m.f
        self.m.commit()
        self.repo = SQLiteCandidateManifestRepository(self.f.path, projects=self.f.projects, authorization=self.f.auth)
        self.service = CandidatePublicationService(self.m.subject, self.repo)

    def test_preview_confirm_rematerializes_and_persists_only_refs(self):
        before = self.f.tasks.get(self.f.actor, self.f.tid)
        preview = self.service.preview(self.f.actor, self.f.tid, self.m.oid)
        self.m.clean()
        result, replay = self.service.confirm(self.f.actor, self.f.tid, self.m.oid,
            proposal_digest=preview["proposal_digest"], expected_task_version=preview["expected_task_version"],
            key="confirm", action="confirm")
        self.assertFalse(replay)
        self.assertEqual(self.m.opens, 2)
        self.assertIsNone(result["accepted_count"])
        self.assertEqual(self.f.tasks.get(self.f.actor, self.f.tid), before)
        self.assertEqual(len(self.repo.get(self.f.actor, self.f.tid, result["manifest_ref"]).proposal.items), 1)
        self.m.clean()

    def test_changed_bytes_prevent_confirmation(self):
        preview = self.service.preview(self.f.actor, self.f.tid, self.m.oid)
        self.m.body = b"changed after preview"
        with self.assertRaises(ServiceError) as error:
            self.service.confirm(self.f.actor, self.f.tid, self.m.oid,
                proposal_digest=preview["proposal_digest"], expected_task_version=preview["expected_task_version"],
                key="confirm", action="confirm")
        self.assertEqual(error.exception.code, "INPUT_SNAPSHOT_CHANGED")
        with closing(sqlite3.connect(self.f.path)) as db:
            state = load_state(db.execute("SELECT format,payload FROM candidate_ledger WHERE id=1").fetchone())
        self.assertEqual(state["manifests"], {})
        self.m.clean()
