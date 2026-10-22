from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from core import test_candidate_storage as fixture
from test_cleaning import FakeExecutor
from dsh_retrieval.cleaning_service import CleaningService
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.manifest_cleaning import PublishedManifestCatalog, ManifestCleaningExecutor
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.adapters.dj_cleaning import DJCleaningExecutor


class CapturingExecutor(FakeExecutor):
    captures_inputs_on_submit = True
    path_independent_identity = True

    def __init__(self):
        super().__init__()
        self.copied, self.paths, self.cancelled = {}, [], []
        self.after_submit = lambda: None

    def submit(self, operation):
        self.paths.extend(c.source_path for c in operation.proposal.candidates)
        self.copied[operation.operation_id] = tuple(Path(c.source_path).read_bytes() for c in operation.proposal.candidates)
        result = super().submit(operation)
        self.after_submit()
        return result

    def cancel(self, operation):
        self.cancelled.append(operation.operation_id)
        return super().cancel(operation)


class ManifestCleaningTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture.CandidatePublicationIntegrationTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.m, self.s = self.f.m, self.f.f
        view = self.f.service.preview(self.s.actor, self.s.tid, self.m.oid)
        result, _ = self.f.service.confirm(self.s.actor, self.s.tid, self.m.oid,
            proposal_digest=view["proposal_digest"], expected_task_version=view["expected_task_version"],
            key="publish", action="confirm")
        self.ref = result["manifest_ref"]
        self.catalog = PublishedManifestCatalog(self.f.repo, self.s.actor, self.s.tid)
        self.backend = CapturingExecutor()
        self.root = Path(self.s.temp.name) / "cleaning-temp"
        self.executor = ManifestCleaningExecutor(self.catalog, self.m, self.backend, self.root)
        self.service = CleaningService(self.s.tasks, self.s.projects, self.catalog, self.executor)
        self.proposal = self.service.prepare(self.s.actor, self.s.tid, self.ref)

    def start(self, **changes):
        args = dict(proposal_id=self.proposal["proposal_id"], proposal_digest=self.proposal["proposal_digest"],
                    expected_task_version=self.proposal["expected_task_version"], action="confirm")
        args.update(changes)
        return self.service.start(self.s.actor, "clean", **args)

    def clean(self):
        self.assertFalse(self.root.exists() and list(self.root.iterdir()))
        self.assertTrue(all(not Path(p).exists() for p in self.backend.paths))

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as error:
            call()
        self.assertEqual(error.exception.code, code)

    def test_explicit_confirm_then_results_and_cleanup(self):
        self.assertEqual(self.backend.jobs, {})
        stored = self.s.tasks.get_cleaning_proposal(self.s.actor, self.proposal["proposal_id"])
        self.assertEqual(stored.candidates[0].source_path, "")
        self.assert_code("CONFIRMATION_REQUIRED", lambda: self.start(action="auto"))
        view, replay = self.start()
        self.assertFalse(replay)
        self.clean()
        self.assertEqual(self.backend.copied[view["operation_id"]], (self.m.body,))
        result = self.service.poll(self.s.actor, view["operation_id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.service.results(self.s.actor, self.s.tid)["counts"]["pass"], 1)
        self.assertIsNone(self.service.results(self.s.actor, self.s.tid)["accepted_count"])

    def test_replay_no_second_submit_and_restart_uses_logical_inputs(self):
        first, _ = self.start()
        paths = list(self.backend.paths)
        self.service = CleaningService(SQLiteTaskRepository(self.s.path), self.s.projects, self.catalog, self.executor)
        self.assertEqual(self.start(), (first, True))
        self.assertEqual(paths, self.backend.paths)
        self.assertEqual(self.service.poll(self.s.actor, first["operation_id"])["status"], "succeeded")

    def test_transport_retry_new_paths_same_operation(self):
        self.backend.fail_submit = True
        self.assert_code("TRANSPORT", self.start)
        self.clean()
        first_id = next(iter(self.backend.jobs))
        view, replay = self.start()
        self.assertTrue(replay)
        self.assertEqual(view["operation_id"], first_id)
        self.assertEqual(len(set(self.backend.paths)), 2)
        self.clean()

    def test_changed_bytes_rejected_without_backend_submit(self):
        self.m.body = b"changed"
        self.assert_code("INPUT_SNAPSHOT_CHANGED", self.start)
        self.assertEqual(self.backend.jobs, {})
        self.clean()

    def test_limit_rejected_without_backend_submit(self):
        self.executor.item_limit = 1
        self.assert_code("MEDIA_LIMIT_EXCEEDED", self.start)
        self.assertEqual(self.backend.jobs, {})
        self.clean()

    def test_revocation_during_download_rejected(self):
        self.m.after_read = lambda: setattr(self.s.auth, "denied", True)
        self.assert_code("ACCESS_DENIED", self.start)
        self.assertEqual(self.backend.jobs, {})
        self.clean()

    def test_revocation_after_submit_cancels_backend(self):
        self.backend.after_submit = lambda: setattr(self.s.auth, "denied", True)
        self.assert_code("ACCESS_DENIED", self.start)
        self.assertEqual(len(self.backend.cancelled), 1)
        self.clean()

    def test_terminal_results_and_poll_require_current_media_access(self):
        view, _ = self.start()
        self.service.poll(self.s.actor, view["operation_id"])
        self.s.auth.denied = True
        self.assert_code("ACCESS_DENIED", lambda: self.service.poll(self.s.actor, view["operation_id"]))
        self.assert_code("ACCESS_DENIED", lambda: self.service.results(self.s.actor, self.s.tid))

    def test_new_generation_rejects_before_submit(self):
        self.s.change_task(run_generation=self.m.plan.scope.generation + 1)
        self.assert_code("REVISION_CONFLICT", self.start)
        self.assertEqual(self.backend.jobs, {})

    def test_unsupported_recipe_and_cross_task_catalog(self):
        self.assert_code("FEATURE_NOT_SUPPORTED", lambda: self.service.prepare(self.s.actor, self.s.tid, self.ref, "arbitrary"))
        task = self.s.tasks.get(self.s.actor, self.s.tid)
        self.assert_code("NOT_FOUND", lambda: self.catalog.require_scope(replace(task, task_id="other"), self.ref))

    def test_unsafe_backend_rejected(self):
        with self.assertRaises(ValueError):
            ManifestCleaningExecutor(self.catalog, self.m, FakeExecutor(), self.root)

    def test_changed_recipe_config_cannot_execute_old_proposal(self):
        self.catalog.min_width = 64
        self.assert_code("INPUT_SNAPSHOT_CHANGED", self.start)
        self.assertEqual(self.backend.jobs, {})

    def test_empty_published_manifest_cannot_prepare_cleaning(self):
        plan = self.m.plan
        request = replace(plan.request, cursor=self.s.first.next_cursor)
        oid, _ = self.s.repo.reserve_page(self.s.actor, plan, request, "empty")
        self.s.repo.commit_page(self.s.actor, oid, plan, request, self.s.empty)
        existing = self.f.repo.get(self.s.actor, self.s.tid, self.ref).proposal
        proposal = replace(existing, operation_id=oid, items=())
        self.f.repo.put_proposal(self.s.actor, proposal)
        manifest, _ = self.f.repo.confirm(self.s.actor, proposal, key="empty",
            expected_task_version=proposal.scope.task_version, action="confirm")
        self.assert_code("INPUT_LIMIT", lambda: self.service.prepare(self.s.actor, self.s.tid, manifest.manifest_ref))
        self.assertEqual(self.backend.jobs, {})

    def test_running_poll_revocation_does_not_publish(self):
        view, _ = self.start()
        self.s.auth.denied = True
        self.assert_code("ACCESS_DENIED", lambda: self.service.poll(self.s.actor, view["operation_id"]))
        self.assertEqual(self.s.tasks.get_cleaning_operation(self.s.actor, view["operation_id"]).status, "running")
        self.assertIsNone(self.s.tasks.cleaning_results(self.s.actor, self.s.tid))

    def test_new_process_reads_operation_without_dead_temporary_paths(self):
        view, _ = self.start()
        script = '''import json,sys
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.models import Actor
r=SQLiteTaskRepository(sys.argv[1])
op=r.get_cleaning_operation(Actor('tenant','owner'),sys.argv[2])
print(json.dumps({'status':op.status,'paths':[c.source_path for c in op.proposal.candidates]}))
'''
        result = subprocess.run([sys.executable, "-c", script, str(self.s.path), view["operation_id"]],
                                check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), {"status": "running", "paths": [""]})

    def test_dj_adapter_explicitly_sends_ephemeral_mode_without_launching_dj(self):
        view, _ = self.start()
        operation = self.s.tasks.get_cleaning_operation(self.s.actor, view["operation_id"])
        backend = DJCleaningExecutor(sys.executable, ".", self.s.temp.name, "unused.py", ephemeral_inputs=True)
        with patch("dsh_retrieval.adapters.dj_cleaning.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, '{"ok":true,"result":{"status":"running"}}')
            self.assertEqual(backend.poll(operation), {"status": "running"})
            self.assertTrue(json.loads(run.call_args.kwargs["input"])["ephemeral_inputs"])


class BridgeFingerprintTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("bridge_fingerprint_test", Path(__file__).parents[1] / "tools/dj_cleaning_bridge.py")
        self.bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bridge)
        self.job = {"binding": {"operation_id": "op"}, "recipe_ref": "image-width@1",
                    "min_width": 32, "candidates": [{"candidate_id": "c", "asset_version": "v1",
                    "content_digest": "sha256:fixture", "source_path": "/temp/one"}]}

    def test_default_exact_legacy_fingerprint(self):
        expected = self.bridge.sha(json.dumps(self.job, sort_keys=True, separators=(",", ":")).encode())
        self.assertEqual(self.bridge.job_fingerprint(self.job), expected)
        self.assertNotEqual(self.bridge.job_fingerprint(self.job, True), expected)

    def test_ephemeral_paths_ignored_but_content_and_recipe_not(self):
        old = self.bridge.job_fingerprint(self.job, True)
        self.job["candidates"][0]["source_path"] = "/temp/two"
        self.assertEqual(self.bridge.job_fingerprint(self.job, True), old)
        for field, value in (("content_digest", "changed"), ("asset_version", "v2"), ("candidate_id", "other")):
            candidate = dict(self.job["candidates"][0], **{field: value})
            self.assertNotEqual(self.bridge.job_fingerprint(dict(self.job, candidates=[candidate]), True), old)
        self.assertNotEqual(self.bridge.job_fingerprint(dict(self.job, min_width=64), True), old)
