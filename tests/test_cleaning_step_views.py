import unittest

from core import test_multi_rule_approval as approval_fixture
from dsh_retrieval.models import Actor
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.cleaning_step_outputs import (
    StepOutputCursorError, build_step_output_page,
)
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
        self.assertEqual(step['counts'], dict(input=1, retained=0, rejected=1,
                                              review=0, pending=0, blocked=0))
        self.assertEqual(len(step['items'][0]['reasons']), 2)
        self.assertRegex(step['step_execution_id'], r'^step_[0-9a-f]{64}$')
        self.assertEqual(step['input_version'], job.binding.input_manifest_digest)
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
        self.assertEqual(counts, dict(input=1, retained=0, rejected=0,
                                      review=1, pending=0, blocked=0))

    def test_running_checkpoint_has_fixed_pending_pages_and_no_fake_blocking(self):
        f = approval_fixture.MultiRuleApprovalTest()
        f.fixture_candidate_count = 2
        f.setUp()
        self.addCleanup(f.doCleanups)
        proposal = f.prepare()
        approval, _ = f.confirm(proposal)
        job = approval.job
        execution = {'backend_ref': 'fixture.detector', 'instruction': 'fixture',
                     'prompt_version': 'fixture@1',
                     'config_digest': 'sha256:' + 'a' * 64,
                     'model': {'id': 'fixture', 'provider': 'synthetic', 'name': 'v1'}}
        f.repo.start_cleaning_checkpoint(
            f.f.actor, f.f.tid, approval.operation_id, execution)

        def verdict(candidate):
            rules = tuple(CleaningRuleVerdict(
                rule.rule_id, 'pass', 'MATCH', 'fixture_evidence')
                for rule in job.recipe.rules)
            return CleaningCandidateVerdict(
                candidate.candidate_id, candidate.asset_version, 'pass', rules)

        partial = verdict(job.candidates[0])
        f.repo.save_cleaning_step_checkpoint(
            f.f.actor, f.f.tid, approval.operation_id, job.candidates[0],
            job.recipe.steps[0], partial.rules)
        view = f.reopen().step_results(f.f.actor, f.f.tid)
        self.assertTrue(view['available'])
        run = view['runs'][0]
        step = run['steps'][0]
        self.assertEqual((run['status'], step['status']), ('running', 'running'))
        self.assertEqual(step['counts'], dict(
            input=2, retained=1, rejected=0, review=0, pending=1, blocked=0))
        self.assertFalse(run['candidate_preview_available'])
        pending = build_step_output_page(
            {**view, 'candidate_preview_available': False},
            operation_id=run['operation_id'],
            step_execution_id=step['step_execution_id'],
            output_version=run['output_version'], view_type='input', limit=1)
        self.assertEqual(pending['total_items'], 2)
        self.assertEqual(pending['items'][0]['preview_status'], 'unavailable')
        self.assertIsNotNone(pending['next_cursor'])
        self.assertEqual(build_step_output_page(
            {**view, 'candidate_preview_available': False},
            operation_id=run['operation_id'],
            step_execution_id=step['step_execution_id'],
            output_version=run['output_version'], view_type='blocked')['items'], [])

        f.repo.save_cleaning_checkpoint(
            f.f.actor, f.f.tid, approval.operation_id, partial)
        changed = f.reopen().step_results(f.f.actor, f.f.tid)
        self.assertNotEqual(changed['runs'][0]['output_version'], run['output_version'])
        with self.assertRaises(StepOutputCursorError):
            build_step_output_page(
                {**changed, 'candidate_preview_available': False},
                operation_id=run['operation_id'],
                step_execution_id=step['step_execution_id'],
                output_version=run['output_version'], view_type='input',
                cursor=pending['next_cursor'])
