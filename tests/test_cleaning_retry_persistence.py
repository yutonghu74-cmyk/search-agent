import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from tests import test_multi_rule_pipeline as fixture
from dsh_retrieval.cleaning_dispatch import CleaningDispatchCoordinator
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor


class CleaningRetryPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture.MultiRulePipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.now = 1000.0
        self.service = Mock()
        self.service.execute.side_effect = ServiceError('MODEL_REQUEST_FAILED', 'secret response', 503, retryable=True)

    def worker(self, repo=None):
        return CleaningDispatchCoordinator(repo or self.f.repo, self.service,
                                           start=False, clock=lambda: self.now)

    def status(self, worker):
        return worker.status(self.f.actor, self.f.task_id, self.f.operation_id)

    def test_budget_and_backoff_survive_each_restart_and_exhaust_at_three(self):
        for attempt, delay in ((1, 5), (2, 10), (3, 0)):
            worker = self.worker(self.f.f.reopen())
            worker._drain()
            status = self.status(worker)
            self.assertEqual(status['attempts'], attempt)
            self.assertEqual(self.service.execute.call_count, attempt)
            self.assertNotIn('secret', str(status))
            if delay:
                self.assertEqual(status['status'], 'retry_wait')
                self.assertEqual(status['next_attempt_at'], self.now + delay)
                self.now += delay - 1
                self.worker(self.f.f.reopen())._drain()
                self.assertEqual(self.service.execute.call_count, attempt)
                self.now += 1
        self.assertEqual(status['status'], 'failed')
        self.assertEqual(status['code'], 'RETRY_EXHAUSTED')
        self.assertFalse(status['retryable'])
        self.now += 100000
        self.worker(self.f.f.reopen())._drain()
        self.assertEqual(self.service.execute.call_count, 3)

    def test_unknown_exception_is_bounded_and_does_not_leak(self):
        self.service.execute.side_effect = RuntimeError('private key material')
        worker = self.worker()
        for advance in (0, 5, 10):
            self.now += advance
            worker._drain()
        status = self.status(worker)
        self.assertEqual(status['status'], 'failed')
        self.assertEqual(self.service.execute.call_count, 3)
        self.assertNotIn('private', str(status))
        self.assertEqual(self.f.f.state()['results'], {})

    def claim(self, repo=None):
        return (repo or self.f.repo).claim_cleaning_attempt(
            self.f.actor, self.f.task_id, self.f.operation_id, self.now)

    def test_crash_reservation_and_old_completion_token_are_fenced(self):
        old = self.claim()
        self.assertIsNone(self.claim(self.f.f.reopen()))
        self.now += 3600
        new = self.claim(self.f.f.reopen())
        self.assertNotEqual(old, new)
        self.assertFalse(self.f.repo.finish_cleaning_attempt(
            self.f.actor, self.f.task_id, self.f.operation_id, old, self.now,
            code='OLD_ERROR', retryable=False))
        status = self.status(self.worker())
        self.assertEqual((status['status'], status['attempts']), ('running', 2))

    def test_concurrent_claim_has_one_winner(self):
        repos = [self.f.f.reopen(), self.f.f.reopen()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            tokens = list(pool.map(self.claim, repos))
        self.assertEqual(sum(token is not None for token in tokens), 1)

    def test_wrong_owner_and_stale_generation_cannot_reserve(self):
        with self.assertRaises(ServiceError):
            self.f.repo.claim_cleaning_attempt(Actor(self.f.actor.tenant_id, 'other'),
                self.f.task_id, self.f.operation_id, self.now)
        self.f.f.f.change_task(run_generation=self.f.approval.job.binding.generation + 1)
        with self.assertRaises(ServiceError):
            self.claim()
        self.assertEqual(self.status(self.worker())['status'], 'superseded')

    def test_three_crashed_attempts_exhaust_budget_without_external_retry(self):
        for _ in range(3):
            self.assertIsNotNone(self.claim(self.f.f.reopen()))
            self.now += 3600
        self.assertIsNone(self.claim(self.f.f.reopen()))
        self.assertEqual(self.status(self.worker())['status'], 'failed')
        self.assertEqual(self.service.execute.call_count, 0)
