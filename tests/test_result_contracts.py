from dataclasses import replace
import unittest

from dsh_retrieval.multi_rule_contracts import (
    ApprovedCleaningJob, CleaningBinding, CleaningCandidateRef, CleaningCandidateVerdict,
    CleaningJobResult, CleaningRecipe, CleaningRule, CleaningRuleVerdict, DetectionStep,
    bytes_digest, candidate_manifest_digest,
)
from dsh_retrieval.query_spec import digest
from dsh_retrieval.result_contracts import build_exact_result_set


class ExactResultContractTests(unittest.TestCase):
    def setUp(self):
        bodies = (b"duplicate", b"unique-two", b"duplicate", b"bad", b"unknown", b"unique-six")
        refs = tuple(CleaningCandidateRef(f"c{i}", "v1", bytes_digest(body))
                     for i, body in enumerate(bodies))
        step = DetectionStep("step", "fixture.detector", "fixture.validator", "fixture.eval")
        recipe = CleaningRecipe("image", digest({"review": 1}), digest({"registry": 1}),
                                (step,), (CleaningRule("r1", "step", "text.present@1", "eq", False),
                                          CleaningRule("r2", "step", "watermark.present@1", "eq", False)))
        binding = CleaningBinding("tenant", "owner", "project", "task", 1, 2, 3, "operation",
                                  candidate_manifest_digest(refs), recipe.recipe_digest)
        self.job = ApprovedCleaningJob(binding, recipe, refs)
        status = ("pass", "pass", "pass", "fail", "unknown", "pass")
        def rules(value):
            second = "unknown" if value == "unknown" else value
            return (CleaningRuleVerdict("r1", "pass", "MATCH", "evidence.1"),
                    CleaningRuleVerdict("r2", second,
                                        "FACT_UNAVAILABLE" if second == "unknown" else "MATCH",
                                        None if second == "unknown" else "evidence.2"))
        self.result = CleaningJobResult(binding, tuple(
            CleaningCandidateVerdict(ref.candidate_id, ref.asset_version, verdict, rules(verdict))
            for ref, verdict in zip(refs, status)))

    def test_exact_dedup_and_target_split(self):
        preview = build_exact_result_set(self.job, self.result, 2)
        self.assertEqual(tuple(ref.candidate_id for ref in preview.accepted), ("c0", "c1"))
        self.assertEqual(tuple(ref.candidate_id for ref in preview.reserve), ("c5",))
        self.assertEqual((preview.accepted_count, preview.duplicate_count,
                          preview.fail_count, preview.unknown_count, preview.gap), (2, 1, 1, 1, 0))
        self.assertEqual(preview.stop_reason, "TARGET_MET")
        self.assertEqual(preview.result_set_digest,
                         build_exact_result_set(self.job, self.result, 2).result_set_digest)

    def test_input_only_shortfall_is_explicit(self):
        preview = build_exact_result_set(self.job, self.result, 5)
        self.assertEqual(preview.accepted_count, 3)
        self.assertEqual(preview.reserve, ())
        self.assertEqual(preview.gap, 2)
        self.assertEqual(preview.stop_reason, "SEARCH_STRATEGIES_EXHAUSTED")

    def test_complete_result_and_binding_are_required(self):
        for changed in (replace(self.result, candidates=self.result.candidates[:-1]),
                        replace(self.result, binding=replace(self.result.binding, generation=3))):
            with self.assertRaises(ValueError):
                build_exact_result_set(self.job, changed, 2)
        with self.assertRaises(ValueError):
            build_exact_result_set(self.job, self.result, True)
        with self.assertRaises(ValueError):
            build_exact_result_set(self.job, self.result, 0)

    def test_fail_unknown_never_become_representatives(self):
        preview = build_exact_result_set(self.job, self.result, 10)
        chosen = {ref.candidate_id for ref in preview.accepted + preview.reserve}
        self.assertEqual(chosen, {"c0", "c1", "c5"})
        self.assertNotIn("c3", chosen)
        self.assertNotIn("c4", chosen)

    def test_result_change_changes_digest(self):
        original = build_exact_result_set(self.job, self.result, 2)
        changed = replace(self.result, candidates=(replace(self.result.candidates[0],
            rules=(replace(self.result.candidates[0].rules[0], reason_code="VERIFIED"),
                   self.result.candidates[0].rules[1])),) + self.result.candidates[1:])
        self.assertNotEqual(original.result_set_digest,
                            build_exact_result_set(self.job, changed, 2).result_set_digest)


if __name__ == "__main__":
    unittest.main()
