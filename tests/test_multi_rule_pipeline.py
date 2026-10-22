from contextlib import contextmanager
from io import BytesIO
import unittest
from unittest.mock import patch

from core import test_cleaning_result_storage as fixture
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.multi_rule_contracts import DetectionResult
from dsh_retrieval.multi_rule_pipeline import run_approved_multi_rule


class Gateway:
    def __init__(self, authority):
        self.authority = authority
        self.reads = 0

    @contextmanager
    def open_read(self, context, ref, *, max_bytes):
        self.authority.require_access(context, ref, "read")
        self.reads += 1
        if ref != "trusted:ref":
            raise AssertionError("unapproved media reference")
        with BytesIO(b"fixture") as stream:
            yield stream


class Detector:
    backend_ref = "fixture.detector"
    validator_ref = "fixture.validator"
    evidence_ref = "fixture.eval"

    def __init__(self):
        self.calls = 0
        self.after_detect = None

    def detect(self, content, candidate, binding):
        self.calls += 1
        if self.after_detect is not None:
            self.after_detect()
        return DetectionResult({"text.present@1": False,
                                "watermark.present@1": False},
                               "fixture.evidence", self.validator_ref, "ok")


class MultiRulePipelineTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.CleaningResultStorageTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.repo = self.f.repo
        self.actor = self.f.f.f.actor
        self.task_id = self.f.f.f.tid
        self.operation_id = self.f.approval.operation_id
        self.gateway = Gateway(self.f.f.f.auth)
        self.detector = Detector()

    def run_pipeline(self):
        return run_approved_multi_rule(self.repo, self.actor, self.task_id,
            self.operation_id, gateway=self.gateway,
            detectors={self.detector.backend_ref: self.detector})

    def assert_code(self, code, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_approved_job_executes_persists_and_replays_without_rerun(self):
        view, replayed = self.run_pipeline()
        self.assertFalse(replayed)
        self.assertEqual((view["accepted_count"], view["gap"], view["kind"]),
                         (1, 9, "preview"))
        self.assertEqual((self.gateway.reads, self.detector.calls), (1, 1))
        self.assertEqual(self.run_pipeline(), (view, True))
        self.assertEqual((self.gateway.reads, self.detector.calls), (1, 1))
        self.assertEqual(len(self.f.f.state()["result_sets"]), 1)

    def test_result_already_persisted_skips_media_and_detector(self):
        result = self.f.make_result()
        self.repo.publish_result(self.actor, self.task_id, self.operation_id, result)
        view, replayed = self.run_pipeline()
        self.assertFalse(replayed)
        self.assertEqual(view["accepted_count"], 1)
        self.assertEqual((self.gateway.reads, self.detector.calls), (0, 0))

    def test_failure_after_result_write_resumes_without_rerun(self):
        with patch.object(self.repo, "publish_preview", side_effect=RuntimeError("interrupt")):
            with self.assertRaises(RuntimeError):
                self.run_pipeline()
        self.assertEqual(len(self.f.f.state()["results"]), 1)
        self.assertEqual(len(self.f.f.state()["result_sets"]), 0)
        view, replayed = self.run_pipeline()
        self.assertFalse(replayed)
        self.assertEqual(view["accepted_count"], 1)
        self.assertEqual((self.gateway.reads, self.detector.calls), (1, 1))

    def test_revocation_or_stale_generation_never_runs(self):
        self.f.f.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", self.run_pipeline)
        self.f.f.f.auth.denied = False
        self.f.f.f.change_task(run_generation=self.f.approval.proposal.generation + 1)
        self.assert_code("REVISION_CONFLICT", self.run_pipeline)
        self.assertEqual((self.gateway.reads, self.detector.calls), (0, 0))
        self.assertEqual(self.f.f.state()["results"], {})

    def test_revoked_replay_does_not_expose_preview(self):
        self.run_pipeline()
        self.f.f.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", self.run_pipeline)

    def test_revocation_during_detection_writes_nothing(self):
        self.detector.after_detect = lambda: setattr(self.f.f.f.auth, "denied", True)
        self.assert_code("ACCESS_DENIED", self.run_pipeline)
        state = self.f.f.state()
        self.assertEqual(state["results"], {})
        self.assertEqual(state["result_sets"], {})

    def test_missing_detector_fails_before_media_read(self):
        self.assert_code("DETECTOR_NOT_RELEASED", lambda: run_approved_multi_rule(
            self.repo, self.actor, self.task_id, self.operation_id,
            gateway=self.gateway, detectors={"wrong": self.detector}))
        self.assertEqual(self.gateway.reads, 0)


if __name__ == "__main__":
    unittest.main()
