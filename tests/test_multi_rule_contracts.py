from dataclasses import replace
import unittest

from dsh_retrieval.multi_rule_contracts import (
    ApprovedCleaningJob, CleaningBinding, CleaningCandidateRef, CleaningRecipe,
    CleaningRule, DetectionResult, DetectionStep, bytes_digest,
)
from dsh_retrieval.query_spec import digest


class MultiRuleContractTest(unittest.TestCase):
    def setUp(self):
        self.candidates = (CleaningCandidateRef("c1", "v1", bytes_digest(b"synthetic image 1")),
                           CleaningCandidateRef("c2", "v1", bytes_digest(b"synthetic image 2")))
        self.recipe = CleaningRecipe("image", digest({"review": "fixture"}),
                                     digest({"registry": "fixture"}),
                                     (DetectionStep("step_1", "fixture_detector@1", "fixture_validator@1", "fixture_eval@1"),),
                                     (CleaningRule("r1", "step_1", "text.present@1", "eq", False),
                                      CleaningRule("r2", "step_1", "watermark.present@1", "eq", False)))
        manifest = digest({"candidates": [{"candidate_id": c.candidate_id,
            "asset_version": c.asset_version, "content_digest": c.content_digest} for c in self.candidates]})
        self.binding = CleaningBinding("tenant", "owner", "project", "task", 1, 1, 1,
                                       "operation", manifest, self.recipe.recipe_digest)

    def test_immutable_job_binds_task_versions_recipe_and_candidates(self):
        job = ApprovedCleaningJob(self.binding, self.recipe, self.candidates)
        self.assertEqual(job.recipe.rules[0].value, False)
        with self.assertRaises(ValueError):
            replace(job, binding=replace(self.binding, generation=0))
        with self.assertRaises(ValueError):
            replace(job, candidates=self.candidates[:1])
        with self.assertRaises(ValueError):
            replace(job, recipe=replace(self.recipe, rules=self.recipe.rules[:1]))

    def test_duplicate_or_unmapped_rules_and_invalid_values_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.recipe, rules=(self.recipe.rules[0], self.recipe.rules[0]))
        with self.assertRaises(ValueError):
            replace(self.recipe, rules=(replace(self.recipe.rules[0], step_id="missing"),))
        for value in (None, float("nan"), [False]):
            with self.assertRaises(ValueError):
                replace(self.recipe.rules[0], value=value)
        with self.assertRaises(ValueError):
            replace(self.recipe.rules[0], hardness="soft")
        with self.assertRaises(ValueError):
            replace(self.recipe.steps[0], backend_ref="../../script.py")

    def test_facts_require_evidence_when_successful(self):
        result = DetectionResult({"text.present@1": False}, "fixture_evidence@1", "fixture_validator@1", "ok")
        self.assertIs(result.facts["text.present@1"], False)
        with self.assertRaises(ValueError):
            replace(result, evidence_ref=None)
        with self.assertRaises(ValueError):
            replace(result, status="unknown")
        with self.assertRaises(ValueError):
            replace(result, facts={"text.present@1": float("inf")})
        self.assertEqual(DetectionResult({}, None, "fixture_validator@1", "unknown").status, "unknown")


if __name__ == "__main__":
    unittest.main()
