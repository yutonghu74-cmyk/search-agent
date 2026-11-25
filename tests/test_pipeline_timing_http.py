import unittest

from tools.report_pipeline_timings import records


class PipelineTimingHttpTest(unittest.TestCase):
    def test_real_http_receives_current_request_timing_including_authorization_failure(self):
        from tests.test_first_search_api import FirstSearchApiTest
        fixture = FirstSearchApiTest(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        with self.assertLogs('dsh_retrieval.pipeline_timing') as captured:
            status, body, headers = fixture.request('/progress')
            failed_status, _, failed_headers = fixture.request('/progress', user=None)
        self.assertEqual(status, 200)
        self.assertEqual(body['status'], 'unapproved')
        self.assertEqual(failed_status, 401)
        self.assertRegex(headers['X-Performance-Trace-Id'], r'^[a-f0-9]{32}$')
        self.assertIn('pipeline;dur=', headers['Server-Timing'])
        self.assertNotEqual(headers['X-Performance-Trace-Id'], failed_headers['X-Performance-Trace-Id'])
        summaries = list(records(captured.output))
        self.assertEqual(len(summaries), 2)
        self.assertEqual(summaries[0]['http_status'], 200)
        self.assertEqual(summaries[1]['outcome'], 'failed')
        self.assertEqual(summaries[1]['task_id'], fixture.fixture.task_id)
        self.assertEqual(summaries[0]['stages']['response_encode']['calls'], 1)
        self.assertEqual(summaries[0]['stages']['retrieval_progress_read']['calls'], 1)
