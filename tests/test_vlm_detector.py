import unittest
from unittest.mock import patch

from dsh_retrieval.adapters.cleaning import GenericCleaningExecutor
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.intent_model import ModelConfig
from dsh_retrieval.model_runtime import ModelProfile, ModelRuntime
from dsh_retrieval.multi_rule_contracts import (
    ApprovedCleaningJob, CleaningBinding, CleaningCandidateRef, CleaningRecipe,
    CleaningRule, DetectionStep, bytes_digest, candidate_manifest_digest,
)
from dsh_retrieval.vlm_detector import VLMRequirementDetector


class VLMDetectorTests(unittest.TestCase):
    def setUp(self):
        self.runtime = ModelRuntime((
            ModelProfile("text", "llm", ModelConfig("fixture", "text-v1", "https://fixture.invalid/v1", "secret")),
            ModelProfile("vision", "vlm", ModelConfig("fixture", "vision-v1", "https://fixture.invalid/v1", "secret")),
        ))
        self.binding = self.runtime.binding("text", "vision")
        self.review = "sha256:" + "1" * 64
        self.artifact = "sha256:" + "2" * 64

    def detector(self, requirement="一只狗", binding=None):
        return VLMRequirementDetector(self.runtime, requirement,
            self.binding if binding is None else binding, self.review, self.artifact)

    def test_verdicts_map_to_facts_without_unknown_pass(self):
        detector = self.detector()
        for verdict, status, facts in (
                ("pass", "ok", {"image.semantic-match@1": True}),
                ("fail", "ok", {"image.semantic-match@1": False}),
                ("unknown", "unknown", {})):
            with patch.object(self.runtime, "assess", return_value={"verdict": verdict}) as assess:
                result = detector.detect(b"\xff\xd8\xffimage", None, None)
            assess.assert_called_once_with("一只狗", b"\xff\xd8\xffimage", "image/jpeg", self.binding)
            self.assertEqual((result.status, result.facts), (status, facts))
            self.assertEqual(result.evidence_ref,
                             None if verdict == "unknown" else detector.evidence_ref)

    def test_requirement_and_model_selection_change_release_digest(self):
        original = self.detector()
        changed = self.detector("一只猫")
        self.assertNotEqual(original.release_config_digest, changed.release_config_digest)
        original._requirement = "一只猫"
        self.assertEqual(original.release_config_digest, changed.release_config_digest)

    def test_model_config_drift_and_errors_fail_closed(self):
        stale = dict(self.binding, prompt_version="changed")
        with self.assertRaises(ServiceError) as caught:
            self.detector(binding=stale)
        self.assertEqual(caught.exception.code, "MODEL_BINDING_CHANGED")
        with patch.object(self.runtime, "assess", side_effect=ServiceError("MODEL_REQUEST_FAILED", "failed", 502)):
            with self.assertRaises(ServiceError) as caught:
                self.detector().detect(b"\xff\xd8\xffimage", None, None)
        self.assertEqual(caught.exception.code, "MODEL_REQUEST_FAILED")
        with self.assertRaises(ServiceError) as caught:
            self.detector().detect(b"not image", None, None)
        self.assertEqual(caught.exception.code, "INVALID_MEDIA")

    def test_approved_worker_maps_three_vlm_outcomes(self):
        bodies = {f"asset.{index}": b"\xff\xd8\xff" + bytes([index]) for index in range(3)}
        candidates = tuple(CleaningCandidateRef(key, "v1", bytes_digest(value))
                           for key, value in bodies.items())
        detector = self.detector()
        step = DetectionStep("semantic", detector.backend_ref,
                             detector.validator_ref, detector.evidence_ref)
        recipe = CleaningRecipe("image", self.review, self.review, (step,),
            (CleaningRule("match", "semantic", detector.predicate_ref, "eq", True),))
        binding = CleaningBinding("tenant", "owner", "project", "task", 1, 1, 1,
            "operation", candidate_manifest_digest(candidates), recipe.recipe_digest)
        job = ApprovedCleaningJob(binding, recipe, candidates)

        class Guard:
            def require_current(self, current):
                if current != binding:
                    raise AssertionError("stale binding")

        class Reader:
            def read(self, candidate, current):
                return bodies[candidate.candidate_id]

        with patch.object(self.runtime, "assess", side_effect=(
                {"verdict": "pass"}, {"verdict": "fail"}, {"verdict": "unknown"})):
            result = GenericCleaningExecutor(guard=Guard(), reader=Reader(),
                detectors={detector.backend_ref: detector}).run(job)
        self.assertEqual([row.verdict for row in result.candidates],
                         ["pass", "fail", "unknown"])


if __name__ == "__main__":
    unittest.main()
