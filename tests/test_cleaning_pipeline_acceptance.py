"""Cleaning acceptance cases: real temporary SQLite, deterministic detectors.

Failed assertions are unmet acceptance criteria, not expectedFailure markers.
No live model, existing task database, or running service is modified.
"""
from itertools import product
import unittest
from unittest.mock import Mock, patch

from tests.cleaning import test_generic_runner as runner
from tests import test_multi_rule_pipeline as pipeline
from dsh_retrieval.cleaning_dispatch import CleaningDispatchCoordinator
from dsh_retrieval.cleaning_step_views import cleaning_step_view
from dsh_retrieval.confirmed_cleaning import ConfirmedCleaningService
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.multi_rule_contracts import DetectionResult


class CleaningTruthTableAcceptance(unittest.TestCase):
    def test_all_nine_rule_combinations_and_conservation(self):
        combinations = tuple(product(('pass', 'fail', 'unknown'), repeat=2))
        bodies = tuple(('synthetic-%d' % i).encode() for i in range(9))
        job = runner.make_job(bodies)
        outputs = {}
        expected = []
        for i, pair in enumerate(combinations):
            facts = {}
            for name, wanted, verdict in zip(('has_text', 'wide_enough'), (False, True), pair):
                if verdict != 'unknown':
                    facts[name] = wanted if verdict == 'pass' else not wanted
            outputs[f'asset.{i}'] = (runner.ok(**facts) if facts else
                DetectionResult({}, None, 'fixture.validator', 'unknown', '证据不足'))
            expected.append('fail' if 'fail' in pair else 'unknown' if 'unknown' in pair else 'pass')
        detector = runner.Detector(outputs)
        executor = runner.GenericCleaningExecutor(
            guard=runner.Guard(), reader=runner.Reader(dict(zip(outputs, bodies))),
            detectors={detector.backend_ref: detector})
        result = executor.run(job)
        self.assertEqual([row.verdict for row in result.candidates], expected)
        self.assertEqual([tuple(rule.verdict for rule in row.rules) for row in result.candidates],
                         list(combinations))
        step = cleaning_step_view(job, result)['steps'][0]
        self.assertEqual(step['counts'], dict(input=9, retained=1, rejected=5,
                                              review=3, pending=0, blocked=0))
        self.assertEqual(sum(step['counts'][name] for name in ('retained', 'rejected', 'review')), 9)
        self.assertEqual(len(detector.calls), 9, 'Shared step must run once, not once per rule')


class CleaningPipelineFaultAcceptance(unittest.TestCase):
    def setUp(self):
        self.f = pipeline.MultiRulePipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.now = 1000.0

    def service(self, repo=None):
        return ConfirmedCleaningService(repo or self.f.repo,
            registry=self.f.registry, gateway=self.f.gateway)

    def worker(self, service=None, repo=None):
        return CleaningDispatchCoordinator(repo or self.f.repo,
            service or self.service(repo), start=False, clock=lambda: self.now)

    def status(self, worker):
        return worker.status(self.f.actor, self.f.task_id, self.f.operation_id)

    def assert_unpublished(self):
        state = self.f.f.state()
        self.assertEqual(state['results'], {})
        self.assertEqual(state['result_sets'], {})

    def test_fail_is_not_accepted_and_short_reason_survives_restart(self):
        observed = DetectionResult({'text.present@1': True, 'watermark.present@1': False},
                                   'fixture.evidence', 'fixture.validator', 'ok', '画面包含文字')
        with patch.object(self.f.detector, 'detect', return_value=observed) as detect:
            view, replayed = self.f.run_pipeline()
            self.assertEqual(view['accepted_count'], 0)
            self.assertEqual(view['gap'], 10)
            self.assertFalse(replayed)
            reopened = self.f.f.reopen()
            steps = self.service(reopened).steps(self.f.actor, self.f.task_id)
            step = steps['runs'][0]['steps'][0]
            self.assertEqual(step['counts'], dict(input=1, retained=0, rejected=1,
                                                  review=0, pending=0, blocked=0))
            reasons = step['items'][0]['reasons']
            self.assertEqual([r['reason_summary'] for r in reasons], ['画面包含文字', None])
            self.worker(repo=reopened)._drain()
            self.assertEqual(detect.call_count, 1)

    def test_unknown_keeps_gap_and_is_not_business_rejection(self):
        observed = DetectionResult({}, None, 'fixture.validator', 'unknown', '主体被遮挡')
        with patch.object(self.f.detector, 'detect', return_value=observed):
            view, _ = self.f.run_pipeline()
        self.assertEqual((view['accepted_count'], view['gap']), (0, 10))
        step = self.service().steps(self.f.actor, self.f.task_id)['runs'][0]['steps'][0]
        self.assertEqual(step['counts'], dict(input=1, retained=0, rejected=0,
                                              review=1, pending=0, blocked=0))
        self.assertTrue(all(r['verdict'] == 'unknown' for r in step['items'][0]['reasons']))

    def test_service_error_does_not_publish_partial_or_pass_result(self):
        with patch.object(self.f.detector, 'detect', side_effect=ServiceError(
                'MODEL_REQUEST_FAILED', 'synthetic unavailable', 503, retryable=True)):
            with self.assertRaises(ServiceError):
                self.f.run_pipeline()
        self.assert_unpublished()

    def test_generation_changed_during_detection_rejects_late_result(self):
        self.f.detector.after_detect = lambda: self.f.f.f.change_task(
            run_generation=self.f.approval.job.binding.generation + 1)
        with self.assertRaises(ServiceError):
            self.f.run_pipeline()
        self.assert_unpublished()

    def test_transient_error_recovers_and_completion_is_idempotent(self):
        real_service = self.service()
        execute = Mock(side_effect=[ServiceError('MODEL_REQUEST_FAILED', 'temporary', 503,
                                                retryable=True), None])
        proxy = Mock()
        def call(*args):
            execute(*args)
            return real_service.execute(*args)
        proxy.execute.side_effect = call
        worker = self.worker(proxy)
        worker._drain()
        self.assert_unpublished()
        self.assertEqual(self.status(worker)['status'], 'retry_wait')
        worker._drain()
        self.assertEqual(execute.call_count, 1, 'Backoff must prevent immediate retry')
        self.now += 5
        worker._drain()
        worker._drain()
        self.assertEqual(self.status(worker)['status'], 'completed')
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(self.f.detector.calls, 1)

    def test_repeated_retryable_failure_must_not_call_on_every_poll(self):
        service = Mock()
        service.execute.side_effect = ServiceError('MODEL_REQUEST_FAILED', 'temporary', 503,
                                                   retryable=True)
        worker = self.worker(service)
        for _ in range(20):
            worker._drain()
        self.assert_unpublished()
        self.assertLess(service.execute.call_count, 20,
                        'No retry budget or backoff: 20 consecutive drains caused 20 calls')

    def test_terminal_failure_must_remain_failed_after_restart(self):
        service = Mock()
        service.execute.side_effect = ServiceError('MODEL_BINDING_CHANGED', 'reconfirm required', 409)
        worker = self.worker(service)
        worker._drain()
        self.assertEqual(self.status(worker)['status'], 'failed')
        reopened = self.f.f.reopen()
        restarted = self.worker(service, reopened)
        self.assertEqual(self.status(restarted)['status'], 'failed',
                         'Restart must not silently turn a terminal failure back into queued')
        restarted._drain()
        self.assertEqual(service.execute.call_count, 1)
