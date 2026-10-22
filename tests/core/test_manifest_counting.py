from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from retrieval import test_materialization as fixture
import test_manifest_cleaning as cleaning_fixture
from dsh_retrieval.adapters.task_storage.candidates import SQLiteCandidateManifestRepository
from dsh_retrieval.candidate_publication import CandidatePublicationService
from dsh_retrieval.cleaning_service import CleaningService
from dsh_retrieval.core.counting import CountingPreviewService
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.manifest_cleaning import PublishedManifestCatalog, ManifestCleaningExecutor


class ManifestCountingTest(unittest.TestCase):
    def setUp(self):
        self.m = fixture.MaterializationTest()
        self.m.setUp()
        self.addCleanup(self.m.doCleanups)
        self.f = self.m.f
        self.m.two_hits()
        self.m.transform = lambda r: (r[0], replace(r[1], media=replace(self.m.record,
                                     source_namespace="other", authorized_media_ref="trusted:other")))
        self.manifests = SQLiteCandidateManifestRepository(self.f.path, projects=self.f.projects, authorization=self.f.auth)
        publication = CandidatePublicationService(self.m.subject, self.manifests)
        view = publication.preview(self.f.actor, self.f.tid, self.m.oid)
        selected, _ = publication.confirm(self.f.actor, self.f.tid, self.m.oid,
            proposal_digest=view["proposal_digest"], expected_task_version=view["expected_task_version"],
            key="select", action="confirm")
        self.ref = selected["manifest_ref"]
        self.catalog = PublishedManifestCatalog(self.manifests, self.f.actor, self.f.tid)
        self.backend = cleaning_fixture.CapturingExecutor()
        executor = ManifestCleaningExecutor(self.catalog, self.m, self.backend, Path(self.f.temp.name) / "inputs")
        self.cleaning = CleaningService(self.f.tasks, self.f.projects, self.catalog, executor)
        proposal = self.cleaning.prepare(self.f.actor, self.f.tid, self.ref)
        self.op, _ = self.cleaning.start(self.f.actor, "clean", proposal["proposal_id"], proposal["proposal_digest"],
                                      proposal["expected_task_version"], action="confirm")
        self.counter = CountingPreviewService(self.f.tasks, self.f.projects, self.catalog)

    def finish(self, verdicts=("pass", "pass")):
        original = self.backend.poll
        def poll(operation):
            result = original(operation)
            for row, verdict in zip(result["results"], verdicts):
                row["verdict"] = verdict
            return result
        with patch.object(self.backend, "poll", side_effect=poll):
            self.cleaning.poll(self.f.actor, self.op["operation_id"])

    def preview(self):
        return self.counter.preview(self.f.actor, self.f.tid)

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as error:
            call()
        self.assertEqual(error.exception.code, code)

    def test_logical_inputs_dedup_without_download_or_state_write(self):
        self.finish()
        task = self.f.tasks.get(self.f.actor, self.f.tid)
        manifest = self.manifests.get(self.f.actor, self.f.tid, self.ref)
        opens = self.m.opens
        result = self.preview()
        self.assertEqual((result["unique_pass_count"], result["duplicate_count"]), (1, 1))
        self.assertEqual(len(result["representatives"]), 1)
        self.assertIsNone(result["accepted_count"])
        self.assertFalse(result["refill_dispatched"])
        self.assertEqual(self.preview(), result)
        self.assertEqual(self.m.opens, opens)
        self.assertEqual(self.f.tasks.get(self.f.actor, self.f.tid), task)
        self.assertEqual(self.manifests.get(self.f.actor, self.f.tid, self.ref), manifest)
        self.assertEqual(len(manifest.proposal.items), 2)
        operation = self.f.tasks.get_cleaning_operation(self.f.actor, self.op["operation_id"])
        self.assertTrue(all(c.source_path == "" for c in operation.proposal.candidates))

    def test_failed_and_unknown_copies_never_count(self):
        self.finish(("fail", "unknown"))
        result = self.preview()
        self.assertEqual(result["unique_pass_count"], 0)
        self.assertEqual(result["duplicate_count"], 0)
        self.assertEqual(result["cleaning_counts"], {"pass": 0, "fail": 1, "unknown": 1})

    def test_failed_copy_not_used_as_representative(self):
        self.finish(("fail", "pass"))
        result = self.preview()
        self.assertEqual(result["unique_pass_count"], 1)
        self.assertEqual(result["duplicate_count"], 0)
        operation = self.f.tasks.get_cleaning_operation(self.f.actor, self.op["operation_id"])
        self.assertEqual(result["representatives"][0]["candidate_id"], operation.proposal.candidates[1].candidate_id)

    def test_revoked_duplicate_source_does_not_silently_substitute(self):
        self.finish()
        def authorize(context, ref, action):
            if ref == "trusted:other":
                raise ServiceError("ACCESS_DENIED", "revoked duplicate", 403)
        with patch.object(self.f.auth, "require_access", side_effect=authorize):
            self.assert_code("ACCESS_DENIED", self.preview)

    def test_revoked_after_initial_resolve_rechecked(self):
        self.finish()
        resolve = self.catalog.resolve
        def revoke(*args):
            result = resolve(*args)
            self.f.auth.denied = True
            return result
        with patch.object(self.catalog, "resolve", side_effect=revoke):
            self.assert_code("ACCESS_DENIED", self.preview)

    def test_version_change_during_counting_rejected(self):
        self.finish()
        resolve = self.catalog.resolve
        def change(*args):
            result = resolve(*args)
            task = self.f.tasks.get(self.f.actor, self.f.tid)
            self.f.change_task(task_version=task.task_version + 1)
            return result
        with patch.object(self.catalog, "resolve", side_effect=change):
            self.assert_code("REVISION_CONFLICT", self.preview)

    def test_incomplete_cleaning_not_counted(self):
        self.assert_code("CLEANING_NOT_READY", self.preview)

    def test_second_resolve_change_rejected(self):
        self.finish()
        resolve = self.catalog.resolve
        calls = []
        def change(*args):
            rows, width = resolve(*args)
            calls.append(1)
            return (rows, width) if len(calls) == 1 else (rows, width + 1)
        with patch.object(self.catalog, "resolve", side_effect=change):
            self.assert_code("INPUT_SNAPSHOT_CHANGED", self.preview)
