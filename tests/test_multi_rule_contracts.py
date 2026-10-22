from dataclasses import replace
import unittest

from dsh_retrieval.multi_rule_contracts import (
    ApprovedCleaningJob, CleaningApproval, CleaningApprovalProposal, CleaningBinding,
    CleaningCandidateRef, CleaningCandidateVerdict, CleaningJobResult, CleaningRecipe,
    CleaningRule, CleaningRuleVerdict, DetectionResult, DetectionStep, bytes_digest,
    require_complete_cleaning_result,
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

    def test_approval_proposal_binds_request_manifest_and_recipe(self):
        proposal = CleaningApprovalProposal("tenant", "owner", "project", "task", 1, 2, 3,
                                            digest({"confirmed": "query"}), "manifest_1",
                                            self.recipe, self.candidates)
        approval = CleaningApproval(proposal, "operation_1", 4)
        self.assertEqual(approval.job.binding.task_version, 4)
        self.assertEqual(approval.job.binding.input_manifest_digest, proposal.input_manifest_digest)
        self.assertEqual(approval.job.binding.recipe_digest, self.recipe.recipe_digest)
        self.assertNotEqual(replace(proposal, manifest_ref="manifest_2").proposal_digest,
                            proposal.proposal_digest)
        self.assertNotEqual(replace(proposal, confirmed_request_digest=digest({"other": True})).proposal_digest,
                            proposal.proposal_digest)
        self.assertNotEqual(replace(proposal, recipe=replace(self.recipe,
                            rules=self.recipe.rules[:1])).proposal_digest, proposal.proposal_digest)

    def test_approval_rejects_duplicate_candidate_and_wrong_version(self):
        proposal = CleaningApprovalProposal("tenant", "owner", "project", "task", 1, 2, 3,
                                            digest({"confirmed": "query"}), "manifest_1",
                                            self.recipe, self.candidates)
        with self.assertRaises(ValueError):
            replace(proposal, candidates=(self.candidates[0], self.candidates[0]))
        with self.assertRaises(ValueError):
            replace(proposal, manifest_ref="../../media")
        with self.assertRaises(ValueError):
            CleaningApproval(proposal, "operation_1", 3)

    def result(self):
        return CleaningJobResult(self.binding, (
            CleaningCandidateVerdict("c1", "v1", "pass", (
                CleaningRuleVerdict("r1", "pass", "MATCH", "evidence.1"),
                CleaningRuleVerdict("r2", "pass", "MATCH", "evidence.1"))),
            CleaningCandidateVerdict("c2", "v1", "unknown", (
                CleaningRuleVerdict("r1", "pass", "MATCH", "evidence.2"),
                CleaningRuleVerdict("r2", "unknown", "FACT_UNAVAILABLE", None))),
        ))

    def test_complete_result_is_bound_and_digest_is_stable(self):
        job = ApprovedCleaningJob(self.binding, self.recipe, self.candidates)
        result = self.result()
        self.assertIs(require_complete_cleaning_result(job, result), result)
        self.assertEqual(result.result_digest, self.result().result_digest)
        self.assertTrue(result.result_digest.startswith("sha256:"))

    def test_result_rejects_wrong_summary_evidence_and_duplicates(self):
        result = self.result()
        with self.assertRaises(ValueError):
            replace(result.candidates[1], verdict="pass")
        with self.assertRaises(ValueError):
            replace(result.candidates[0].rules[0], verdict="unknown")
        with self.assertRaises(ValueError):
            replace(result.candidates[0].rules[0], evidence_ref=None)
        with self.assertRaises(ValueError):
            replace(result, candidates=(result.candidates[0], result.candidates[0]))

    def test_complete_result_rejects_binding_candidates_and_rules(self):
        job = ApprovedCleaningJob(self.binding, self.recipe, self.candidates)
        result = self.result()
        for changed in (
            replace(result, binding=replace(self.binding, generation=2)),
            replace(result, candidates=result.candidates[:1]),
            replace(result, candidates=tuple(reversed(result.candidates))),
            replace(result, candidates=(replace(result.candidates[0],
                rules=tuple(reversed(result.candidates[0].rules))), result.candidates[1])),
        ):
            with self.assertRaises(ValueError):
                require_complete_cleaning_result(job, changed)


if __name__ == "__main__":
    unittest.main()
