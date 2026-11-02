from io import BytesIO
import unittest

from PIL import Image

from dsh_retrieval.adapters.cleaning import GenericCleaningExecutor
from dsh_retrieval.adapters.cleaning.runner import MAX_CANDIDATE_BYTES
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.multi_rule_contracts import (
    ApprovedCleaningJob, CleaningBinding, CleaningCandidateRef, CleaningJobResult,
    CleaningRecipe, CleaningRule, DetectionResult, DetectionStep, bytes_digest,
)
from dsh_retrieval.query_spec import digest


HASH = bytes_digest(b"reviewed")


def make_job(contents=(b"first", b"second", b"third")):
    step = DetectionStep("features", "fixture.features", "fixture.validator", "release.eval")
    recipe = CleaningRecipe("image", HASH, HASH, (step,), (
        CleaningRule("no_text", "features", "has_text", "eq", False),
        CleaningRule("wide", "features", "wide_enough", "eq", True),
    ))
    candidates = tuple(CleaningCandidateRef(f"asset.{i}", "v1", bytes_digest(body))
                       for i, body in enumerate(contents))
    manifest = digest({"candidates": [{"candidate_id": c.candidate_id,
        "asset_version": c.asset_version, "content_digest": c.content_digest}
        for c in candidates]})
    binding = CleaningBinding("tenant", "owner", "project", "task", 1, 2, 3,
                              "operation", manifest, recipe.recipe_digest)
    return ApprovedCleaningJob(binding, recipe, candidates)


class Guard:
    def __init__(self, revoke_after=None):
        self.calls = 0
        self.revoke_after = revoke_after

    def require_current(self, binding):
        self.calls += 1
        if self.revoke_after is not None and self.calls >= self.revoke_after:
            raise ServiceError("STALE_GENERATION", "Task changed", 409)


class Reader:
    def __init__(self, bodies):
        self.bodies = bodies
        self.calls = []

    def read(self, candidate, binding):
        self.calls.append(candidate.candidate_id)
        return self.bodies[candidate.candidate_id]


class Detector:
    backend_ref = "fixture.features"
    validator_ref = "fixture.validator"
    evidence_ref = "release.eval"

    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def detect(self, content, candidate, binding):
        self.calls.append(candidate.candidate_id)
        result = self.outputs[candidate.candidate_id]
        if isinstance(result, Exception):
            raise result
        return result


def ok(**facts):
    return DetectionResult(facts, "run.evidence", "fixture.validator", "ok")


class GenericRunnerTests(unittest.TestCase):
    def setUp(self):
        self.bodies = {"asset.0": b"first", "asset.1": b"second", "asset.2": b"third"}
        self.job = make_job()
        self.guard = Guard()
        self.reader = Reader(self.bodies)
        self.detector = Detector({"asset.0": ok(has_text=False, wide_enough=True),
                                  "asset.1": ok(has_text=True, wide_enough=True),
                                  "asset.2": ok(has_text=False)})

    def runner(self):
        return GenericCleaningExecutor(guard=self.guard, reader=self.reader,
                                       detectors={self.detector.backend_ref: self.detector})

    def test_shared_step_and_three_state_per_candidate(self):
        result = self.runner().run(self.job)
        self.assertIs(type(result), CleaningJobResult)
        self.assertEqual(result.binding, self.job.binding)
        self.assertEqual([item.verdict for item in result.candidates], ["pass", "fail", "unknown"])
        self.assertEqual([[rule.verdict for rule in item.rules] for item in result.candidates],
                         [["pass", "pass"], ["fail", "pass"], ["pass", "unknown"]])
        self.assertEqual(self.detector.calls, list(self.bodies))
        self.assertEqual(self.reader.calls, list(self.bodies))
        self.assertEqual(result.candidates[2].rules[1].evidence_ref, None)
        self.assertGreaterEqual(self.guard.calls, 2 * len(self.bodies) + 2)
        self.assertEqual(result.fingerprints, ())

    def test_authorized_image_bytes_do_not_enable_deferred_perceptual_dedup(self):
        image = Image.new("RGB", (32, 24), "navy")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        body = buffer.getvalue()
        job = make_job((body,))
        reader = Reader({"asset.0": body})
        detector = Detector({"asset.0": ok(has_text=False, wide_enough=True)})
        result = GenericCleaningExecutor(
            guard=Guard(), reader=reader,
            detectors={detector.backend_ref: detector}).run(job)
        self.assertEqual(result.fingerprints, ())

    def test_rejects_unapproved_input_before_read(self):
        with self.assertRaises(TypeError):
            self.runner().run({"binding": self.job.binding})
        self.assertEqual(self.reader.calls, [])

    def test_missing_detector_rejected_before_read(self):
        self.detector.validator_ref = "another.validator"
        with self.assertRaises(ServiceError) as error:
            self.runner().run(self.job)
        self.assertEqual(error.exception.code, "DETECTOR_NOT_RELEASED")
        self.assertEqual(self.reader.calls, [])

    def test_changed_media_digest_aborts_job(self):
        self.reader.bodies["asset.1"] = b"tampered"
        with self.assertRaises(ServiceError) as error:
            self.runner().run(self.job)
        self.assertEqual(error.exception.code, "INPUT_SNAPSHOT_CHANGED")
        self.assertEqual(self.detector.calls, ["asset.0"])

    def test_revoked_generation_aborts_without_returning_results(self):
        self.guard.revoke_after = 3
        with self.assertRaises(ServiceError) as error:
            self.runner().run(self.job)
        self.assertEqual(error.exception.code, "STALE_GENERATION")
        self.assertEqual(self.reader.calls, ["asset.0"])

    def test_detector_failure_and_wrong_validator_are_unknown(self):
        self.detector.outputs["asset.0"] = RuntimeError("synthetic detector failed")
        self.detector.outputs["asset.1"] = DetectionResult(
            {"has_text": False, "wide_enough": True}, "run.evidence", "wrong.validator", "ok")
        result = self.runner().run(self.job)
        self.assertEqual([item.verdict for item in result.candidates],
                         ["unknown", "unknown", "unknown"])
        self.assertTrue(all(rule.evidence_ref is None for item in result.candidates[:2]
                            for rule in item.rules))

    def test_explicit_unknown_and_type_mismatch_do_not_pass(self):
        self.detector.outputs["asset.0"] = DetectionResult({}, None, "fixture.validator", "unknown")
        self.detector.outputs["asset.1"] = ok(has_text=0, wide_enough=1)
        result = self.runner().run(self.job)
        self.assertEqual([item.verdict for item in result.candidates[:2]], ["unknown", "unknown"])

    def test_detector_service_failure_propagates(self):
        self.detector.outputs["asset.0"] = ServiceError("AUTH_UNAVAILABLE", "No auth", 503)
        with self.assertRaises(ServiceError) as error:
            self.runner().run(self.job)
        self.assertEqual(error.exception.code, "AUTH_UNAVAILABLE")

    def test_media_limit_fails_closed(self):
        body = b"x" * (MAX_CANDIDATE_BYTES + 1)
        job = make_job((body,))
        self.reader.bodies = {"asset.0": body}
        with self.assertRaises(ServiceError) as error:
            self.runner().run(job)
        self.assertEqual(error.exception.code, "MEDIA_LIMIT_EXCEEDED")
        self.assertEqual(self.detector.calls, [])


if __name__ == "__main__":
    unittest.main()
