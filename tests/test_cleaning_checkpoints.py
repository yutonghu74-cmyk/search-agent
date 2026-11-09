from dataclasses import replace
import unittest
from unittest.mock import patch, Mock

from tests import test_multi_rule_pipeline as fixture
from tests.cleaning import test_generic_runner as runner
from dsh_retrieval.confirmed_cleaning import ConfirmedCleaningService
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.multi_rule_pipeline import run_approved_multi_rule


class CleaningCheckpointTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture.MultiRulePipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.execution = {'backend_ref': 'fixture.detector', 'instruction': '不包含文字',
                          'prompt_version': 'fixture@1', 'config_digest': 'sha256:' + 'a' * 64,
                          'model': {'id': 'fixture', 'provider': 'synthetic', 'name': 'v1'}}

    def start(self, repo=None, execution=None):
        return (repo or self.f.repo).start_cleaning_checkpoint(self.f.actor, self.f.task_id,
            self.f.operation_id, execution or self.execution)

    def save(self, verdict=None):
        return self.f.repo.save_cleaning_checkpoint(self.f.actor, self.f.task_id,
            self.f.operation_id, verdict or self.f.make_result().candidates[0])

    def run_pipeline(self, repo=None):
        return run_approved_multi_rule(repo or self.f.repo, self.f.actor, self.f.task_id,
            self.f.operation_id, gateway=self.f.gateway, registry=self.f.registry, execution=self.execution)

    def test_crash_after_candidate_checkpoint_resumes_without_model_call(self):
        with patch.object(self.f.repo, 'publish_result', side_effect=RuntimeError('crash before final result')):
            with self.assertRaises(RuntimeError):
                self.run_pipeline()
        self.assertEqual(self.f.f.state()['results'], {})
        repo = self.f.f.reopen()
        status = repo.cleaning_dispatch_state(self.f.actor, self.f.task_id, self.f.operation_id, details=True)
        self.assertEqual(status['progress']['completed'], 1)
        self.assertEqual(status['progress']['pending'], 0)
        self.assertEqual(status['execution'], self.execution)
        view, _ = self.run_pipeline(repo)
        self.assertEqual(view['accepted_count'], 1)
        self.assertEqual(self.f.detector.calls, 1)
        self.assertEqual(self.f.gateway.reads, 2, 'Resume must revalidate authorized media bytes')

    def test_step_checkpoint_survives_restart_and_skips_completed_vlm_call(self):
        with patch.object(self.f.repo, 'save_cleaning_checkpoint',
                          side_effect=RuntimeError('crash after step')):
            with self.assertRaises(RuntimeError):
                self.run_pipeline()
        self.assertEqual(self.f.detector.calls, 1)
        repo = self.f.f.reopen()
        partial = repo.get_cleaning_step_checkpoint(self.f.actor, self.f.task_id,
                                                     self.f.operation_id)
        self.assertEqual(partial[0], self.f.approval.job.candidates[0].candidate_id)
        self.assertEqual(len(partial[1]), 1)
        view, _ = self.run_pipeline(repo)
        self.assertEqual(view['accepted_count'], 1)
        self.assertEqual(self.f.detector.calls, 1)
        self.assertEqual(self.f.gateway.reads, 2)

    def test_step_checkpoint_repeated_write_is_idempotent_and_conflict_fails(self):
        self.start()
        candidate = self.f.approval.job.candidates[0]
        step = self.f.approval.job.recipe.steps[0]
        verdicts = self.f.make_result().candidates[0].rules
        save = lambda value: self.f.repo.save_cleaning_step_checkpoint(
            self.f.actor, self.f.task_id, self.f.operation_id,
            candidate, step, value)
        self.assertTrue(save(verdicts))
        self.assertFalse(save(verdicts))
        progress = self.f.f.reopen().cleaning_dispatch_state(
            self.f.actor, self.f.task_id, self.f.operation_id, details=True)['progress']
        self.assertEqual(progress['completed'], 0)
        self.assertEqual(progress['partial_candidate'], candidate.candidate_id)
        self.assertEqual(progress['completed_nodes'][0]['step_id'], step.step_id)
        changed = tuple(replace(item, verdict='fail', reason_code='MISMATCH')
                        for item in verdicts)
        with self.assertRaises(ServiceError) as caught:
            save(changed)
        self.assertEqual(caught.exception.code, 'RESULT_CONFLICT')

    def test_running_step_summary_is_persisted_at_checkpoint_watermark(self):
        self.start()
        before = self.f.repo.step_results_summary(self.f.actor, self.f.task_id)
        self.assertEqual(before['runs'][0]['steps'][0]['counts']['pending'], 1)
        self.assertNotIn('items', before['runs'][0]['steps'][0])
        self.save()
        reopened = self.f.f.reopen()
        after = reopened.step_results_summary(self.f.actor, self.f.task_id)
        self.assertNotEqual(before['runs'][0]['output_version'],
                            after['runs'][0]['output_version'])
        self.assertEqual(after['runs'][0]['steps'][0]['counts']['retained'], 1)
        with patch.object(reopened, '_transaction',
                          side_effect=AssertionError('full ledger read')):
            self.assertEqual(reopened.step_results_summary(
                self.f.actor, self.f.task_id), after)

    def test_snapshot_survives_restart_and_does_not_consult_current_provider(self):
        self.run_pipeline()
        provider = Mock()
        provider.build.side_effect = AssertionError('Do not rebuild historical registry')
        provider.describe.side_effect = AssertionError('Do not read current configuration')
        service = ConfirmedCleaningService(self.f.f.reopen(), registry_provider=provider, gateway=self.f.gateway)
        view = service.steps(self.f.actor, self.f.task_id)
        self.assertEqual(view['execution'], self.execution)
        provider.describe.assert_not_called()
        provider.build.assert_not_called()

    def test_changed_execution_snapshot_rejected_before_reuse(self):
        self.start()
        with self.assertRaises(ServiceError) as caught:
            self.start(self.f.f.reopen(), {**self.execution, 'instruction': '变更条件'})
        self.assertEqual(caught.exception.code, 'MODEL_BINDING_CHANGED')

    def test_checkpoint_is_idempotent_but_conflicting_verdict_rejected(self):
        self.start()
        self.assertTrue(self.save())
        self.assertFalse(self.save())
        candidate = self.f.make_result().candidates[0]
        altered = replace(candidate, verdict='fail', rules=tuple(
            replace(rule, verdict='fail', reason_code='MISMATCH') for rule in candidate.rules))
        with self.assertRaises(ServiceError) as caught:
            self.save(altered)
        self.assertEqual(caught.exception.code, 'RESULT_CONFLICT')

    def test_incomplete_candidate_cannot_be_checkpointed(self):
        self.start()
        candidate = self.f.make_result().candidates[0]
        with self.assertRaises(ServiceError):
            self.save(replace(candidate, rules=candidate.rules[:1]))
        self.assertEqual(self.start(), ())

    def test_stale_generation_or_revocation_blocks_checkpoint_write(self):
        self.start()
        self.f.f.f.auth.denied = True
        with self.assertRaises(ServiceError):
            self.save()
        self.f.f.f.auth.denied = False
        self.f.f.f.change_task(run_generation=self.f.approval.job.binding.generation + 1)
        with self.assertRaises(ServiceError):
            self.save()

    def test_checkpoint_read_denies_other_owner(self):
        self.start()
        self.save()
        with self.assertRaises(ServiceError):
            self.f.repo.cleaning_dispatch_state(Actor(self.f.actor.tenant_id, 'other'),
                self.f.task_id, self.f.operation_id, details=True)

    def test_legacy_results_do_not_gain_fabricated_snapshot(self):
        self.f.repo.publish_result(self.f.actor, self.f.task_id, self.f.operation_id, self.f.make_result())
        self.f.repo.publish_preview(self.f.actor, self.f.task_id, self.f.operation_id)
        provider = Mock()
        service = ConfirmedCleaningService(self.f.repo, registry_provider=provider, gateway=self.f.gateway)
        view = service.steps(self.f.actor, self.f.task_id)
        self.assertIsNone(view['execution'])
        self.assertFalse(view['execution_snapshot_available'])
        provider.describe.assert_not_called()

    def test_completed_old_generation_exposes_no_current_progress(self):
        self.run_pipeline()
        self.f.f.f.change_task(run_generation=self.f.approval.job.binding.generation + 1)
        status = self.f.repo.cleaning_dispatch_state(self.f.actor, self.f.task_id,
            self.f.operation_id, details=True)
        self.assertEqual(status['status'], 'superseded')
        self.assertNotIn('progress', status)
        self.assertNotIn('execution', status)


class MultiImageResumeTest(unittest.TestCase):
    def test_second_image_failure_reuses_first_and_preserves_order(self):
        f = runner.GenericRunnerTests()
        f.setUp()
        saved = []
        f.detector.outputs['asset.1'] = ServiceError('MODEL_REQUEST_FAILED', 'temporary', 503, retryable=True)
        with self.assertRaises(ServiceError):
            f.runner().run(f.job, checkpoint=saved.append)
        self.assertEqual([row.candidate_id for row in saved], ['asset.0'])
        f.detector.outputs['asset.1'] = runner.ok(has_text=True, wide_enough=True)
        result = f.runner().run(f.job, completed=tuple(saved), checkpoint=saved.append)
        self.assertEqual(f.detector.calls, ['asset.0', 'asset.1', 'asset.1', 'asset.2'])
        self.assertEqual([item.verdict for item in result.candidates], ['pass', 'fail', 'unknown'])
        self.assertEqual([row.candidate_id for row in saved], ['asset.0', 'asset.1', 'asset.2'])

    def test_cached_verdict_does_not_bypass_media_hash_check(self):
        f = runner.GenericRunnerTests()
        f.setUp()
        result = f.runner().run(f.job)
        f.reader.bodies['asset.0'] = b'changed'
        with self.assertRaises(ServiceError) as caught:
            f.runner().run(f.job, completed=result.candidates)
        self.assertEqual(caught.exception.code, 'INPUT_SNAPSHOT_CHANGED')
