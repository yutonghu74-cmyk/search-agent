import unittest
from unittest.mock import patch

from dsh_retrieval.cleaning_dispatch import CleaningDispatchCoordinator
from dsh_retrieval.confirmed_cleaning import ConfirmedCleaningService
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from tests.test_multi_rule_pipeline import MultiRulePipelineTests


class CleaningDispatchTest(unittest.TestCase):
    def setUp(self):
        self.fixture = MultiRulePipelineTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = ConfirmedCleaningService(
            self.fixture.repo, registry=self.fixture.registry,
            gateway=self.fixture.gateway)

    def coordinator(self, repository=None, service=None):
        return CleaningDispatchCoordinator(
            repository or self.fixture.repo, service or self.service,
            poll_seconds=0.01, start=False, clock=lambda: 1000.0)

    def test_approved_operation_is_discovered_executed_and_not_repeated(self):
        operation_id = self.fixture.operation_id
        pending = self.fixture.repo.pending_approved_operations()
        self.assertEqual([row['operation_id'] for row in pending], [operation_id])
        worker = self.coordinator()
        self.assertEqual(worker.status(self.fixture.actor, self.fixture.task_id,
                                       operation_id)['status'], 'queued')
        with self.assertRaises(ServiceError):
            worker.status(Actor(self.fixture.actor.tenant_id, 'other'),
                          self.fixture.task_id, operation_id)
        worker._drain()
        self.assertEqual(worker.status(self.fixture.actor, self.fixture.task_id,
                                       operation_id)['status'], 'completed')
        self.assertEqual((self.fixture.gateway.reads, self.fixture.detector.calls), (1, 1))
        worker._drain()
        self.assertEqual((self.fixture.gateway.reads, self.fixture.detector.calls), (1, 1))
        self.assertEqual(self.fixture.f.reopen().pending_approved_operations(), ())

    def test_restart_resumes_saved_result_without_rerunning_detector(self):
        operation_id = self.fixture.operation_id
        with patch.object(self.fixture.repo, 'publish_preview',
                          side_effect=RuntimeError('simulated crash after result')):
            self.coordinator()._drain()
        self.assertEqual((self.fixture.gateway.reads, self.fixture.detector.calls), (1, 1))
        self.assertEqual(len(self.fixture.f.state()['results']), 1)
        self.assertEqual(len(self.fixture.f.state()['result_sets']), 0)

        reopened = self.fixture.f.reopen()
        resumed = ConfirmedCleaningService(
            reopened, registry=self.fixture.registry, gateway=self.fixture.gateway)
        # A persisted result is reused after the persisted retry delay.
        CleaningDispatchCoordinator(reopened, resumed, start=False, clock=lambda: 1005.0)._drain()
        self.assertEqual((self.fixture.gateway.reads, self.fixture.detector.calls), (1, 1))
        self.assertEqual(len(self.fixture.f.state()['result_sets']), 1)
        self.assertEqual(reopened.pending_approved_operations(), ())
        self.assertEqual(self.coordinator(reopened, resumed).status(
            self.fixture.actor, self.fixture.task_id, operation_id)['status'], 'completed')

    def test_nonretryable_failure_stops_hot_loop_without_publishing(self):
        operation_id = self.fixture.operation_id
        self.fixture.model_path.write_bytes(b'changed model weights')
        worker = self.coordinator()
        worker._drain()
        status = worker.status(self.fixture.actor, self.fixture.task_id, operation_id)
        self.assertEqual((status['status'], status['code'], status['retryable']),
                         ('failed', 'DETECTOR_NOT_RELEASED', False))
        worker._drain()
        self.assertEqual((self.fixture.gateway.reads, self.fixture.detector.calls), (0, 0))
        self.assertEqual(self.fixture.f.state()['results'], {})


if __name__ == '__main__':
    unittest.main()
