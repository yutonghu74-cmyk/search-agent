import unittest
import json

from dsh_retrieval.incremental_recompute import (build_recompute_report,
                                                 plan_incremental_recompute)
from dsh_retrieval.multi_rule_contracts import (
    CleaningApproval, CleaningApprovalProposal, CleaningBinding,
    CleaningCandidateRef, CleaningCandidateVerdict, CleaningJobResult,
    CleaningRecipe, CleaningRule, CleaningRuleVerdict, DetectionStep,
)
from dsh_retrieval.adapters.task_storage.multi_rule import empty_state, load_state
from dsh_retrieval.adapters.task_storage.sqlite import encode


H = 'sha256:' + '1' * 64
R = 'sha256:' + '2' * 64


def recipe(value=True, *, instruction='dog without text', backend='vlm@1'):
    return CleaningRecipe('image', H, H,
        (DetectionStep('step', backend, 'validator@1', 'evidence@1'),),
        (CleaningRule('rule', 'step', 'matches', 'eq', value),),
        (instruction,))


def history(verdict='fail'):
    candidate = CleaningCandidateRef('candidate', 'v1', H)
    old_recipe = recipe()
    proposal = CleaningApprovalProposal('tenant', 'owner', 'project', 'task',
        1, 1, 1, H, 'manifest', old_recipe, (candidate,), R)
    approval = CleaningApproval(proposal, 'operation', 2)
    rule = CleaningRuleVerdict('rule', verdict,
        'MATCH' if verdict == 'pass' else 'MISMATCH', 'evidence')
    item = CleaningCandidateVerdict('candidate', 'v1', verdict, (rule,))
    return approval, CleaningJobResult(approval.job.binding, (item,))


class IncrementalRecomputeTest(unittest.TestCase):
    def test_v3_cleaning_ledger_migrates_with_empty_recompute_state(self):
        legacy = empty_state()
        legacy.pop('recompute_manifests')
        legacy.pop('recompute_reuse')
        restored = load_state((3, json.dumps(encode(legacy))))
        self.assertEqual(restored['recompute_manifests'], {})
        self.assertEqual(restored['recompute_reuse'], {})

    def test_identical_release_instruction_and_rule_reuses_verdict(self):
        approval, result = history()
        plan = plan_incremental_recompute(approval, result, recipe(), R)
        self.assertEqual(plan['candidate_source'], 'historical_input_manifest')
        self.assertEqual(plan['reused_candidate_count'], 1)
        self.assertEqual(plan['restorable_rejected_count'], 1)
        self.assertEqual(plan['candidates'][0]['action'], 'reuse_verdicts')

    def test_rule_or_model_change_revalidates_previous_rejection(self):
        approval, result = history()
        for changed in (recipe(False), recipe(instruction='allow text'), recipe(backend='vlm@2')):
            plan = plan_incremental_recompute(approval, result, changed, R)
            self.assertEqual(plan['revalidated_candidate_count'], 1)
            self.assertEqual(plan['candidates'][0]['revalidate_rule_ids'], ('rule',))
        self.assertEqual(plan_incremental_recompute(
            approval, result, recipe(), 'sha256:' + '3' * 64
        )['revalidated_candidate_count'], 1)

    def test_report_shows_restoration_and_count_deltas(self):
        approval, old = history('fail')
        passed = CleaningCandidateVerdict('candidate', 'v1', 'pass',
            (CleaningRuleVerdict('rule', 'pass', 'MATCH', 'evidence'),))
        binding = CleaningBinding('tenant', 'owner', 'project', 'task', 2, 2, 3,
                                  'new_operation', H, recipe(False).recipe_digest, R)
        new = CleaningJobResult(binding, (passed,))
        report = build_recompute_report((old,), (new,),
                                        previous_accepted=0, current_accepted=1)
        self.assertEqual(report['restored_candidate_ids'], ['candidate'])
        self.assertEqual(report['counts']['delta']['accepted'], 1)
        self.assertEqual(report['counts']['delta']['pass'], 1)


if __name__ == '__main__':
    unittest.main()
