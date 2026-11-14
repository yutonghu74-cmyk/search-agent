import json
import threading
import unittest

from dsh_retrieval.conversation_timing import ConversationTiming, increment, timed_call
from dsh_retrieval.model_runtime import ModelRuntime, ModelProfile
from dsh_retrieval.intent_model import ModelConfig
from test_model_runtime import Transport


class ConversationTimingTest(unittest.TestCase):
    def test_repair_is_separate_and_trace_contains_no_content_or_credentials(self):
        transport = Transport([{'kind': 'invalid', 'target_count': None, 'requested_terms_per_group': None},
                               '可以继续描述。'])
        runtime = ModelRuntime([ModelProfile('text', 'llm', ModelConfig(
            'fixture', 'text', 'https://fixture.invalid/v1', 'private-key'))], opener=transport)
        trace = ConversationTiming()
        with trace.activate():
            result = runtime.classify_followup('private-request', [], 'private-message', 'text')
        self.assertEqual(result['kind'], 'answer')
        metrics = trace.snapshot()
        self.assertEqual(metrics['stages']['model_initial']['calls'], 1)
        self.assertEqual(metrics['stages']['model_repair']['calls'], 1)
        self.assertEqual(metrics['stages']['proposal_validation']['failures'], 1)
        self.assertEqual(metrics['counters']['model_http_attempts'], 2)
        for secret in ('private-request', 'private-message', 'private-key', 'fixture.invalid', '可以继续描述'):
            self.assertNotIn(secret, json.dumps(metrics, ensure_ascii=False))

    def test_overlapping_requests_keep_counters_and_failures_isolated(self):
        barrier = threading.Barrier(2)
        snapshots = {}
        def run(name, count, fail):
            trace = ConversationTiming()
            with trace.activate():
                barrier.wait()
                for _ in range(count):
                    increment('model_http_attempts')
                def operation():
                    if fail:
                        raise ValueError('private failed content')
                try:
                    timed_call('draft_save', operation)
                except ValueError:
                    pass
            increment('outside_scope')
            snapshots[name] = trace.snapshot()
        threads = [threading.Thread(target=run, args=('one', 1, False)),
                   threading.Thread(target=run, args=('two', 2, True))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(snapshots['one']['counters'], {'model_http_attempts': 1})
        self.assertEqual(snapshots['two']['counters'], {'model_http_attempts': 2})
        self.assertEqual(snapshots['one']['stages']['draft_save']['failures'], 0)
        self.assertEqual(snapshots['two']['stages']['draft_save']['failures'], 1)
