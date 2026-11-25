import json
from contextvars import copy_context
import threading
import unittest
from unittest.mock import patch

from dsh_retrieval.conversation_timing import ConversationTiming, increment, timed_call
from dsh_retrieval.pipeline_timing import PipelineTiming, measured, response_headers
from tools.report_pipeline_timings import records, summarize


class PipelineTimingTest(unittest.TestCase):
    def test_numeric_clock_failure_and_nested_conversation_share_trace_without_content(self):
        clock = [0.0]
        trace = None
        def model():
            clock[0] += .125
            raise ValueError('private-prompt https://private.invalid/token secret-key')
        with patch('dsh_retrieval.conversation_timing.perf_counter', side_effect=lambda: clock[0]), \
                self.assertLogs('dsh_retrieval.pipeline_timing') as captured:
            trace = PipelineTiming('http_post', task_id='rt_test', revision=1, generation=2)
            with trace.activate():
                child = ConversationTiming()
                with child.activate():
                    increment('model_http_attempts')
                    with self.assertRaises(ValueError):
                        timed_call('model_request', model)
                headers = response_headers(504)
            summary = trace.snapshot('failed')
        self.assertEqual(summary['stages']['model_request'], {'ms': 125.0, 'calls': 1, 'failures': 1})
        self.assertEqual(summary['counters']['model_http_attempts'], 1)
        self.assertEqual(child.snapshot()['trace_id'], summary['trace_id'])
        self.assertEqual(headers['X-Performance-Trace-Id'], summary['trace_id'])
        self.assertEqual(summary['http_status'], 504)
        self.assertEqual(headers['Server-Timing'], 'pipeline;dur=125.000')
        serialized = json.dumps([summary, captured.output])
        for secret in ('private-prompt', 'private.invalid', 'secret-key'):
            self.assertNotIn(secret, serialized)
        self.assertTrue(any('"event":"failed"' in line for line in captured.output))

    def test_copied_worker_context_gets_independent_trace_and_parent_link(self):
        parent = PipelineTiming('http_post')
        @measured('keyword_expansion')
        def worker():
            increment('worker_calls')
        with self.assertLogs('dsh_retrieval.pipeline_timing') as captured:
            with parent.activate():
                context = copy_context()
                thread = threading.Thread(target=context.run, args=(worker,))
                thread.start(); thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
        summaries = list(records(captured.output))
        self.assertEqual(len(summaries), 1)
        self.assertNotEqual(summaries[0]['trace_id'], parent.trace_id)
        self.assertEqual(summaries[0]['parent_trace_id'], parent.trace_id)
        self.assertEqual(summaries[0]['counters'], {'worker_calls': 1})
        self.assertEqual(parent.counters, {})
        self.assertEqual(parent.stages, {})

    def test_broken_log_sink_never_changes_return_or_exception(self):
        @measured('query_encoding')
        def call(fail=False):
            if fail:
                raise LookupError('original')
            return object_value
        object_value = object()
        with patch('dsh_retrieval.pipeline_timing._log.info', side_effect=OSError('disk full')):
            self.assertIs(call(), object_value)
            with self.assertRaisesRegex(LookupError, 'original'):
                call(True)

    def test_real_coordinator_reports_retry_separately_without_fake_success(self):
        from tests.test_retrieval_coordinator import CoordinatorTest
        fixture = CoordinatorTest(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.fail = True
        with self.assertLogs('dsh_retrieval.pipeline_timing') as captured:
            result = fixture.coordinator.tick(fixture.fixture.actor, fixture.fixture.task_id, max_requests=1)
        self.assertEqual(result['outcomes'][0]['status'], 'retry_pending')
        summary = list(records(captured.output))[0]
        self.assertEqual(summary['task_id'], fixture.fixture.task_id)
        self.assertEqual(summary['generation'], fixture.approved.scope.generation)
        self.assertEqual(summary['stages']['vector_search']['failures'], 1)
        self.assertEqual(summary['counters']['retrieval_retries'], 1)
        self.assertNotIn('retrieval_commit', summary['stages'])

    def test_report_ignores_other_log_content_and_preserves_sample_denominators(self):
        rows = list(records(['private-key in unrelated log', 'pipeline_timing invalid',
            'pipeline_timing ' + json.dumps({'schema_version': 'pipeline-timing@1', 'event': 'completed',
                'operation': 'retrieval_tick', 'outcome': 'succeeded', 'total_ms': 10,
                'stages': {'vector_search': {'ms': 7, 'calls': 2, 'failures': 1}}})]))
        report = summarize(rows)
        self.assertTrue(report['stages_are_inclusive'])
        self.assertEqual(report['groups']['retrieval_tick.vector_search'],
                         {'samples': 1, 'calls': 2, 'failures': 1, 'p50_ms': 7, 'p95_ms': 7})
        self.assertNotIn('private-key', json.dumps(report))

    def test_local_pipeline_distinguishes_encoding_search_and_candidate_validation(self):
        from tests.test_first_search_local import FirstSearchLocalTest
        fixture = FirstSearchLocalTest(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.local.close()
        fixture.local = fixture.make_local(start=False)
        with self.assertLogs('dsh_retrieval.pipeline_timing') as captured:
            fixture.select_and_approve()
            for _ in range(10):
                if fixture.local.tick(fixture.actor, fixture.task_id, max_requests=2)['pagination_stopped']:
                    break
            else:
                self.fail('bounded local fixture never finished')
            proposal = fixture.local.preview_manifest(fixture.actor, fixture.task_id)
        self.assertGreater(proposal['verified_count'], 0)
        summaries = list(records(captured.output))
        preparation = next(row for row in summaries if row['operation'] == 'search_preparation')
        self.assertEqual(preparation['stages']['query_encoding']['calls'], 2)
        self.assertNotIn('vector_search', preparation['stages'])
        retrieval = [row for row in summaries if row['operation'] == 'search_activation']
        self.assertTrue(retrieval)
        self.assertTrue(all(row['stages']['vector_search']['calls'] == row['stages']['retrieval_commit']['calls'] for row in retrieval))
        validation = next(row for row in summaries if row['operation'] == 'candidate_validation')
        self.assertEqual(validation['stages']['candidate_resolution']['calls'], 1)
        self.assertEqual(validation['task_id'], fixture.task_id)
        self.assertEqual(validation['generation'], 1)
