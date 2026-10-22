from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
import unittest

from core import test_multi_rule_approval as fixture
from dsh_retrieval.adapters.cleaning import ApprovedManifestAccess, GenericCleaningExecutor
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.multi_rule_contracts import CleaningCandidateRef, DetectionResult, bytes_digest


class Gateway:
    def __init__(self, authority):
        self.authority = authority
        self.body = b"fixture"
        self.calls = []
        self.after_read = None

    @contextmanager
    def open_read(self, context, ref, *, max_bytes):
        self.authority.require_access(context, ref, "read")
        self.calls.append((context.task_id, ref, max_bytes))
        class Stream(BytesIO):
            def read(inner, size=-1):
                result = super().read(size)
                if self.after_read is not None:
                    callback, self.after_read = self.after_read, None
                    callback()
                return result
        with Stream(self.body) as stream:
            yield stream


class Detector:
    backend_ref = "fixture.detector"
    validator_ref = "fixture.validator"
    evidence_ref = "fixture.eval"

    def detect(self, content, candidate, binding):
        return DetectionResult({"text.present@1": False, "watermark.present@1": False},
                               "fixture.evidence", self.validator_ref, "ok")


class ApprovedInputTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.MultiRuleApprovalTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        proposal = self.fixture.prepare()
        self.approval, _ = self.fixture.confirm(proposal)
        self.gateway = Gateway(self.fixture.f.auth)
        self.access = ApprovedManifestAccess(self.fixture.repo, self.fixture.f.actor,
                                             self.fixture.f.tid, self.gateway)

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def run_job(self):
        executor = GenericCleaningExecutor(guard=self.access, reader=self.access,
                                           detectors={Detector.backend_ref: Detector()})
        return executor.run(self.approval.job)

    def test_current_approved_job_reads_only_manifest_media(self):
        result = self.run_job()
        self.assertEqual([v.verdict for v in result.candidates], ["pass"])
        self.assertEqual([r.verdict for r in result.candidates[0].rules], ["pass", "pass"])
        self.assertEqual(self.gateway.calls[0][:2], (self.fixture.f.tid, "trusted:ref"))

    def test_unapproved_binding_and_candidate_never_reach_gateway(self):
        binding = self.approval.job.binding
        self.assert_code("NOT_FOUND", lambda: self.access.require_current(
            replace(binding, operation_id="forged")))
        self.assert_code("INPUT_SNAPSHOT_CHANGED", lambda: self.access.read(
            CleaningCandidateRef("other", "v1", bytes_digest(b"fixture")), binding))
        self.assert_code("INPUT_SNAPSHOT_CHANGED", lambda: self.access.read(
            replace(self.approval.job.candidates[0], content_digest=bytes_digest(b"other")), binding))
        self.assertEqual(self.gateway.calls, [])

    def test_revocation_and_new_generation_stop_execution(self):
        self.fixture.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", self.run_job)
        self.assertEqual(self.gateway.calls, [])
        self.fixture.f.auth.denied = False
        self.fixture.f.change_task(run_generation=self.approval.proposal.generation + 1)
        self.assert_code("REVISION_CONFLICT", self.run_job)
        self.assertEqual(self.gateway.calls, [])

    def test_changed_bytes_or_oversize_never_return_verdict(self):
        self.gateway.body = b"changed"
        self.assert_code("INPUT_SNAPSHOT_CHANGED", self.run_job)
        self.gateway.body = b"x" * (10 * 1024 * 1024 + 1)
        self.assert_code("MEDIA_LIMIT_EXCEEDED", self.run_job)

    def test_generation_change_during_read_stops_after_stream(self):
        self.gateway.after_read = lambda: self.fixture.f.change_task(
            run_generation=self.approval.proposal.generation + 1)
        self.assert_code("REVISION_CONFLICT", self.run_job)


if __name__ == "__main__":
    unittest.main()
