import unittest

from core import test_multi_rule_approval as approval_fixture
from dsh_retrieval.models import Actor
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.multi_rule_contracts import (
    CleaningJobResult, CleaningCandidateVerdict, CleaningRuleVerdict,
)


class CleaningStepViewsTest(unittest.TestCase):
    def setUp(self):
        self.fixture = approval_fixture.MultiRuleApprovalTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_persisted_step_counts_filters_and_scope(self):
        f = self.fixture
        self.assertFalse(f.repo.step_results(f.f.actor, f.f.tid)['available'])
        proposal = f.prepare()
        approval, _ = f.confirm(proposal)
        job = approval.job
        candidate = job.candidates[0]
        result = CleaningJobResult(job.binding, (CleaningCandidateVerdict(
            candidate.candidate_id, candidate.asset_version, 'fail', (
                CleaningRuleVerdict('text', 'pass', 'MATCH', 'fixture_evidence'),
                CleaningRuleVerdict('mark', 'fail', 'MISMATCH', 'fixture_evidence'),
            )),))
        f.repo.publish_result(f.f.actor, f.f.tid, approval.operation_id, result)
        view = f.reopen().step_results(f.f.actor, f.f.tid)
        step = view['runs'][0]['steps'][0]
        self.assertEqual(step['counts'], dict(input=1, retained=0, rejected=1, review=0))
        self.assertEqual(len(step['items'][0]['reasons']), 2)
        self.assertEqual(view['runs'][0]['output_version'], result.result_digest)
        self.assertNotIn('trusted:ref', str(view))
        with self.assertRaises(ServiceError):
            f.repo.step_results(Actor(f.f.actor.tenant_id, 'other'), f.f.tid)

    def test_unknown_remains_review_not_pass(self):
        f = self.fixture
        proposal = f.prepare()
        approval, _ = f.confirm(proposal)
        job = approval.job
        candidate = job.candidates[0]
        result = CleaningJobResult(job.binding, (CleaningCandidateVerdict(
            candidate.candidate_id, candidate.asset_version, 'unknown', tuple(
                CleaningRuleVerdict(rule.rule_id, 'unknown', 'FACT_UNAVAILABLE', None)
                for rule in job.recipe.rules)),))
        f.repo.publish_result(f.f.actor, f.f.tid, approval.operation_id, result)
        counts = f.repo.step_results(f.f.actor, f.f.tid)['runs'][0]['steps'][0]['counts']
        self.assertEqual(counts, dict(input=1, retained=0, rejected=0, review=1))
