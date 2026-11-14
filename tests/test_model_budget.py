"""Virtual-clock checks of a shared queue/retry/repair budget; no model/network calls."""
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from dsh_retrieval import model_budget
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.task_commands import TaskCommandStore
import test_model_runtime as models
import test_task_commands as commands_fixture


class ModelBudgetTest(unittest.TestCase):
    def test_retry_after_cannot_overrun_remaining_budget(self):
        fixture = models.ModelRuntimeTest()
        runtime = fixture.runtime([HTTPError('https://fixture.invalid', 429, 'limited', {'Retry-After': '20'}, None)])
        profile = runtime.select('text', 'llm')
        with model_budget.budget(5), patch('dsh_retrieval.model_runtime.time.sleep') as sleep:
            with self.assertRaises(ServiceError) as error:
                runtime._request(profile, 'fixture', 'fixture')
        self.assertEqual(error.exception.code, 'MODEL_TIME_BUDGET_EXCEEDED')
        sleep.assert_not_called()
        self.assertEqual(len(fixture.transport.calls), 1)

    def test_repair_uses_remaining_time_and_late_response_is_not_accepted(self):
        fixture = models.ModelRuntimeTest()
        runtime = fixture.runtime([{'kind': 'invalid'}, '可以按画面条件筛选。'])
        transport, clock, timeouts = runtime.opener, [0.0], []
        original = transport.open
        def open(request, timeout):
            timeouts.append(timeout)
            clock[0] += 18 if len(timeouts) == 1 else 13
            return original(request, timeout)
        with patch.object(model_budget.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(transport, 'open', side_effect=open):
            with self.assertRaises(ServiceError) as error:
                runtime.classify_followup('动物图片', [], '可以如何筛选合适的画面', 'text')
        self.assertEqual(error.exception.code, 'MODEL_TIME_BUDGET_EXCEEDED')
        self.assertEqual(timeouts, [30.0, 12.0])
        self.assertEqual(len(transport.calls), 2)

    def test_late_proposal_validation_cannot_return_a_new_proposal(self):
        clock = [0.0]
        @model_budget.inference
        def validate():
            clock[0] = 2
            return {'kind': 'requirement'}
        with patch.object(model_budget.time, 'monotonic', side_effect=lambda: clock[0]):
            with model_budget.budget(1):
                with self.assertRaises(ServiceError) as error:
                    validate()
        self.assertEqual(error.exception.code, 'MODEL_TIME_BUDGET_EXCEEDED')

    def test_trickling_response_is_checked_between_reads(self):
        clock, reads = [0.0], []
        class Body:
            def read1(self, limit):
                reads.append(limit)
                clock[0] += .6
                return b'x'
            def read(self, limit):
                raise AssertionError('unbounded buffered read')
        with patch.object(model_budget.time, 'monotonic', side_effect=lambda: clock[0]):
            with model_budget.budget(1):
                with self.assertRaises(ServiceError) as error:
                    model_budget.read_response(Body(), 100, 60)
        self.assertEqual(error.exception.code, 'MODEL_TIME_BUDGET_EXCEEDED')
        self.assertEqual(len(reads), 2)

    def test_dsh_worker_inherits_budget_instead_of_extending_it(self):
        import sys
        from tempfile import TemporaryDirectory
        from tests.retrieval import test_dsh_runtime as harness
        from dsh_retrieval.adapters.dsh.runtime import DshModelRuntime
        from dsh_retrieval.intent_model import ModelConfig
        from dsh_retrieval.model_runtime import ModelProfile
        launches = []
        profile = ModelProfile('slow', 'llm', ModelConfig('fixture', 'fixture', 'https://fixture.invalid', 'fake', request_timeout_seconds=180))
        with TemporaryDirectory() as home:
            runtime = DshModelRuntime([profile], python=sys.executable, home=home)
            with patch('dsh_retrieval.adapters.dsh.runtime.subprocess.Popen',
                       side_effect=harness.RuntimeTest._completing_launch(launches)), patch.object(runtime, '_terminate'):
                with model_budget.budget(5):
                    runtime._request(profile, 'fixture', 'fixture')
        self.assertLessEqual(launches[0][0]['request_timeout_seconds'], 5)
        self.assertLessEqual(launches[0][1], 5)

    def test_expired_queue_does_not_call_model_and_can_retry_same_key(self):
        fixture = commands_fixture.TaskCommandsTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        commands = TaskCommandStore(fixture.store, max_workers=1, max_pending=2)
        release, started = threading.Event(), threading.Event()
        clock, observed = [0.0], []
        def first(receipt):
            started.set()
            if not release.wait(5): raise AssertionError('gate timeout')
            receipt.checkpoint({'task': fixture.task})
            return {'kind': 'answer', 'reply': '已记录'}
        def second(receipt):
            observed.append('called')
            receipt.checkpoint({'task': fixture.task})
            return {'kind': 'answer', 'reply': '已重试'}
        def wait_status(key, expected):
            for _ in range(300):
                if commands.read(fixture.actor, fixture.task['task_id'], key)['status'] == expected: return
                time.sleep(.005)
            self.fail('receipt did not reach ' + expected)
        try:
            with patch.object(model_budget.time, 'monotonic', side_effect=lambda: clock[0]):
                with model_budget.budget(1):
                    commands.submit(fixture.actor, fixture.task['task_id'], 'occupy', fixture.request, first)
                    self.assertTrue(started.wait(2))
                    commands.submit(fixture.actor, fixture.task['task_id'], 'expired', fixture.request, second)
                clock[0] = 2
                release.set()
                wait_status('expired', 'failed')
                self.assertFalse(observed)
                receipt = commands.read(fixture.actor, fixture.task['task_id'], 'expired')
                self.assertEqual(receipt['error']['code'], 'MODEL_TIME_BUDGET_EXCEEDED')
                commands.submit(fixture.actor, fixture.task['task_id'], 'expired', fixture.request, second, retry_failed=True)
                wait_status('expired', 'completed')
                self.assertEqual(observed, ['called'])
        finally:
            release.set()
            commands.close()
