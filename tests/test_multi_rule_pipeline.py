from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
import unittest
from unittest.mock import patch

from core import test_multi_rule_approval as fixture
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.intent_compiler import DetectorBinding
from dsh_retrieval.intent_review import Capability
from dsh_retrieval.multi_rule_contracts import (
    CleaningCandidateVerdict, CleaningJobResult, CleaningRuleVerdict, DetectionResult,
)
from dsh_retrieval.multi_rule_pipeline import run_approved_multi_rule
from dsh_retrieval.released_detectors import ReleasedDetectorRegistry


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
        self.f = fixture.MultiRuleApprovalTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.actor = self.f.f.actor
        self.task_id = self.f.f.tid
        self.gateway = Gateway(self.f.f.auth)
        self.detector = Detector()
        self.capabilities = tuple(Capability(ref, "constraint", "image", ("eq",),
            "boolean", "verified", "fixture.validator", "fixture.eval")
            for ref in ("text.present@1", "watermark.present@1"))
        self.bindings = tuple(DetectorBinding(ref, "image", "fixture.validator",
            "fixture.detector", "fixture.eval")
            for ref in ("text.present@1", "watermark.present@1"))
        self.registry = ReleasedDetectorRegistry(self.capabilities, self.bindings,
                                                 {self.detector.backend_ref: self.detector})
        self.f.recipe = replace(self.f.recipe, registry_digest=self.registry.registry_digest)
        self.f.proposal = self.f.prepare()
        self.approval, _ = self.f.confirm()
        self.repo = self.f.reopen()
        self.operation_id = self.approval.operation_id

    def make_result(self):
        candidate = self.approval.job.candidates[0]
        rules = tuple(CleaningRuleVerdict(rule.rule_id, "pass", "MATCH", "evidence.1")
                      for rule in self.approval.job.recipe.rules)
        return CleaningJobResult(self.approval.job.binding,
            (CleaningCandidateVerdict(candidate.candidate_id, candidate.asset_version,
                                      "pass", rules),))

    def run_pipeline(self):
        return run_approved_multi_rule(self.repo, self.actor, self.task_id,
            self.operation_id, gateway=self.gateway,
            registry=self.registry)

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
        self.assertEqual(len(self.f.state()["result_sets"]), 1)

    def test_result_already_persisted_skips_media_and_detector(self):
        result = self.make_result()
        self.repo.publish_result(self.actor, self.task_id, self.operation_id, result)
        view, replayed = self.run_pipeline()
        self.assertFalse(replayed)
        self.assertEqual(view["accepted_count"], 1)
        self.assertEqual((self.gateway.reads, self.detector.calls), (0, 0))

    def test_failure_after_result_write_resumes_without_rerun(self):
        with patch.object(self.repo, "publish_preview", side_effect=RuntimeError("interrupt")):
            with self.assertRaises(RuntimeError):
                self.run_pipeline()
        self.assertEqual(len(self.f.state()["results"]), 1)
        self.assertEqual(len(self.f.state()["result_sets"]), 0)
        view, replayed = self.run_pipeline()
        self.assertFalse(replayed)
        self.assertEqual(view["accepted_count"], 1)
        self.assertEqual((self.gateway.reads, self.detector.calls), (1, 1))

    def test_revocation_or_stale_generation_never_runs(self):
        self.f.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", self.run_pipeline)
        self.f.f.auth.denied = False
        self.f.f.change_task(run_generation=self.approval.proposal.generation + 1)
        self.assert_code("REVISION_CONFLICT", self.run_pipeline)
        self.assertEqual((self.gateway.reads, self.detector.calls), (0, 0))
        self.assertEqual(self.f.state()["results"], {})

    def test_revoked_replay_does_not_expose_preview(self):
        self.run_pipeline()
        self.f.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", self.run_pipeline)

    def test_revocation_during_detection_writes_nothing(self):
        self.detector.after_detect = lambda: setattr(self.f.f.auth, "denied", True)
        self.assert_code("ACCESS_DENIED", self.run_pipeline)
        state = self.f.state()
        self.assertEqual(state["results"], {})
        self.assertEqual(state["result_sets"], {})

    def test_missing_detector_fails_before_media_read(self):
        missing = ReleasedDetectorRegistry(self.capabilities, self.bindings, {})
        self.assert_code("DETECTOR_NOT_RELEASED", lambda: run_approved_multi_rule(
            self.repo, self.actor, self.task_id, self.operation_id,
            gateway=self.gateway, registry=missing))
        self.assertEqual(self.gateway.reads, 0)


if __name__ == "__main__":
    unittest.main()
