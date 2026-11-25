"""HTTP first-search flow over an isolated local SQLite ledger and fixture."""
from http.server import ThreadingHTTPServer
from contextlib import contextmanager
from io import BytesIO
import hashlib
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dsh_retrieval.api import create_handler
from tests.test_first_search_local import FirstSearchLocalTest


class FirstSearchApiTest(unittest.TestCase):
    def setUp(self):
        self.fixture = FirstSearchLocalTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.handler = create_handler(self.fixture.tasks, first_search=self.fixture.local,
                                      plan_set_repository=self.fixture.local.repository)
        self.addCleanup(self.handler.task_commands.store.connection.close)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.base = (f'http://127.0.0.1:{self.server.server_port}/v1/retrieval/tasks/'
                     f'{self.fixture.task_id}/first-search')

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, suffix='', *, method='GET', body=None, user='user-a', key=None):
        headers = {'X-Tenant-Id': 'tenant-a', 'Content-Type': 'application/json'}
        if user is not None:
            headers['X-User-Id'] = user
        if key is not None:
            headers['Idempotency-Key'] = key
        data = json.dumps(body).encode() if body is not None else None
        try:
            response = urlopen(Request(self.base + suffix, data=data,
                                       headers=headers, method=method))
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), dict(response.headers)

    def test_progress_separates_raw_hits_from_effective_materials(self):
        self.fixture.select_and_approve()
        self.fixture.local.tick(self.fixture.actor, self.fixture.task_id, max_requests=2)
        expected = self.fixture.local.candidates(self.fixture.actor, self.fixture.task_id, limit=1)['raw_hit_count']
        url = self.base.removesuffix('/first-search') + '/progress'
        original = self.fixture.local.browser.remember_many
        self.fixture.local.browser.remember_many = lambda *args, **kwargs: self.fail(
            'progress read must not update candidate browsing state')
        try:
            with urlopen(Request(url, headers={'X-Tenant-Id': 'tenant-a', 'X-User-Id': 'user-a'})) as response:
                progress = json.loads(response.read())
        finally:
            self.fixture.local.browser.remember_many = original
        self.assertEqual(progress['counts']['returned'], expected)
        self.assertIsNone(progress['counts']['accepted'])

    def test_candidate_preview_requires_committed_authorized_media(self):
        content = b'\xff\xd8\xff' + b'synthetic candidate preview'
        expected = 'sha256:' + hashlib.sha256(content).hexdigest()
        fixture = json.loads(self.fixture.fixture_path.read_text())
        fixture['records'][0]['media'][0]['content_digest'] = expected
        self.fixture.fixture_path.write_text(json.dumps(fixture))

        class Gateway:
            by_ref = {'media:dog-1': (None, expected)}

            @contextmanager
            def open_read(self, context, media_ref, *, max_bytes):
                assert context.purpose == 'candidate_preview'
                assert media_ref == 'media:dog-1'
                assert len(content) <= max_bytes
                yield BytesIO(content)

        self.fixture.local.media_gateway = Gateway()
        self.fixture.select_and_approve()
        self.fixture.local.tick(self.fixture.actor, self.fixture.task_id, max_requests=2)
        candidates = self.fixture.local.candidates(self.fixture.actor, self.fixture.task_id)
        dog = next(item for item in candidates['items'] if item['media_unit_id'] == 'dog-1')
        self.assertEqual(dog['preview_status'], 'available')
        url = self.base + '/candidates/' + dog['candidate_id'] + '/preview'

        def preview(user='user-a'):
            request = Request(url, headers={'X-Tenant-Id': 'tenant-a', 'X-User-Id': user})
            try:
                response = urlopen(request)
            except HTTPError as error:
                response = error
            with response:
                return response.status, response.read(), dict(response.headers)

        status, body, headers = preview()
        self.assertEqual((status, body, headers['Content-Type']), (200, content, 'image/jpeg'))
        self.assertEqual(headers['Cache-Control'], 'private, no-store')
        self.assertEqual(preview('other-user')[0], 404)
        self.assertEqual(self.request('/candidates/candidate_' + '0' * 64 + '/preview')[0], 404)
        self.fixture.local.media_gateway = None
        self.assertEqual(preview()[0], 404)

    def test_budget_stop_workspace_and_sse_remain_readable(self):
        from dsh_retrieval.search_budget import SearchBudget
        from dsh_retrieval.search_scheduling import SearchLimits
        self.fixture.local.close()
        self.fixture.select_and_approve()
        self.fixture.local.search_budget = SearchBudget(self.fixture.ledger, SearchLimits(max_calls=1))
        self.assertEqual(self.request('/tick', method='POST', body={'max_requests': 1})[0], 200)
        status, body, _ = self.request('/tick', method='POST', body={'max_requests': 1})
        self.assertEqual(status, 409)
        self.assertEqual(body['code'], 'SEARCH_BUDGET_EXHAUSTED')
        task_url = self.base.removesuffix('/first-search')
        headers = {'X-Tenant-Id': 'tenant-a', 'X-User-Id': 'user-a'}
        with urlopen(Request(task_url + '/workspace', headers=headers)) as response:
            workspace = json.load(response)
        self.assertEqual(workspace['workflow']['state'], 'retrieval_budget_exhausted')
        self.handler.event_stream_duration = 0
        with urlopen(Request(task_url + '/events?after_sequence=0', headers=headers)) as response:
            self.assertEqual(response.status, 200)
            events = response.read()
        self.assertIn(b'SEARCH_BUDGET_EXHAUSTED', events)

    def test_concurrent_tick_returns_retryable_busy_without_spending_budget(self):
        self.fixture.local.close()
        self.fixture.select_and_approve()
        approved = self.fixture.local.repository.get_current(self.fixture.actor, self.fixture.task_id)
        budget = self.fixture.local.search_budget
        with budget.execution(approved.scope):
            status, body, _ = self.request('/tick', method='POST', body={'max_requests': 1})
            self.assertEqual(status, 503)
            self.assertEqual(body['code'], 'SEARCH_BUSY')
            self.assertTrue(body['retryable'])
            self.assertEqual(budget.snapshot(approved.scope)['calls_reserved'], 0)
        status, body, _ = self.request('/tick', method='POST', body={'max_requests': 1})
        self.assertEqual(status, 200)
        self.assertEqual(body['network_attempts'], 1)

    def test_explicit_choice_confirmation_execution_and_safe_candidates(self):
        self.assertEqual(self.request('/progress')[1]['status'], 'unapproved')
        status, preview, _ = self.request()
        self.assertEqual((status, preview['mode'], preview['status']), (200, 'LOCAL_EXACT', 'unapproved'))
        route = preview['routes'][0]
        choices = [{'branch_id': route['branch_id'], 'binding_id': option['binding_id'], 'batch_size': 2}
                   for option in route['options']]
        self.assertEqual(self.request('/tick', method='POST', body={'max_requests': 1})[1]['code'],
                         'PLAN_NOT_APPROVED')
        status, proposal, _ = self.request('/prepare', method='POST', body={
            'routing_digest': preview['routing_digest'], 'choices': choices})
        self.assertEqual((status, len(proposal['members'])), (200, 2))
        self.assertEqual(self.request('/confirm', method='POST', body={
            'proposal_digest': proposal['proposal_digest'],
            'expected_task_version': proposal['expected_task_version'], 'action': 'confirm'})[0], 400)
        status, approved, _ = self.request('/confirm', method='POST', key='approve', body={
            'proposal_digest': proposal['proposal_digest'],
            'expected_task_version': proposal['expected_task_version'], 'action': 'confirm'})
        self.assertEqual((status, approved['status']), (200, 'approved'))
        self.assertEqual(self.request('/progress')[1]['status'], 'approved')
        replay = self.request('/confirm', method='POST', key='approve', body={
            'proposal_digest': proposal['proposal_digest'],
            'expected_task_version': proposal['expected_task_version'], 'action': 'confirm'})
        self.assertEqual(replay[2].get('Idempotent-Replayed'), 'true')
        # Confirmation already dispatched background work. Test completed-page replay
        # after that worker settles; concurrent execution is covered separately.
        self.fixture.wait_stopped(self.fixture.local, self.fixture.task_id)
        status, batch, _ = self.request('/tick', method='POST', body={'max_requests': 2})
        self.assertEqual(status, 200)
        self.assertIn(batch['network_attempts'], (0, 1, 2))
        self.fixture.wait_stopped(self.fixture.local, self.fixture.task_id)
        status, candidates, _ = self.request('/candidates')
        self.assertEqual((status, candidates['raw_hit_count']), (200, 5))
        page_status, page, _ = self.request('/candidates?limit=2')
        self.assertEqual((page_status, page['returned']), (200, 2))
        self.assertTrue(page['next_cursor'])
        second_status, second, _ = self.request('/candidates?limit=2&cursor=' + page['next_cursor'])
        self.assertEqual((second_status, second['returned']), (200, 2))
        self.assertFalse({item['hit_sequence'] for item in page['items']} &
                         {item['hit_sequence'] for item in second['items']})
        self.assertEqual(self.request('/candidates?limit=2&cursor=bad')[1]['code'], 'INVALID_CURSOR')
        self.assertEqual(self.request('/candidates?limit=2&cursor=' + page['next_cursor'] + '&query=other')[0], 409)
        changed_status, changed, _ = self.request('/candidates/changes?limit=2&cursor=' + page['changes_cursor'])
        self.assertEqual((changed_status, changed['returned']), (200, 2))
        self.assertEqual(self.request('/progress')[1]['task_id'], self.fixture.task_id)
        self.assertIsNone(candidates['accepted_count'])
        raw = json.dumps(candidates)
        for secret in ('authorized_media_ref', 'media:dog-1', 'filter_template_ref', 'cursor_secret'):
            self.assertNotIn(secret, raw)
        self.assertEqual(self.request('/candidates', user='other')[0], 404)
        self.assertEqual(self.request('/progress', user='other')[0], 404)
        status, manifest_preview, _ = self.request('/manifest')
        self.assertEqual((status, manifest_preview['kind']),
                         (200, 'candidate_manifest_proposal'))
        confirmation = {'proposal_digest': manifest_preview['proposal_digest'],
            'expected_task_version': manifest_preview['expected_task_version'],
            'action': 'confirm'}
        self.assertEqual(self.request('/manifest', method='POST', body=confirmation)[0], 400)
        status, manifest, headers = self.request(
            '/manifest', method='POST', body=confirmation, key='fix-candidates')
        self.assertEqual((status, manifest['kind']), (202, 'candidate_input_manifest'))
        replay = self.request('/manifest', method='POST', body=confirmation,
                              key='fix-candidates')
        self.assertEqual(replay[1]['manifest_ref'], manifest['manifest_ref'])
        self.assertEqual(replay[2].get('Idempotent-Replayed'), 'true')
        status, restored, _ = self.request('/manifest')
        self.assertEqual((status, restored['kind'], restored['confirmed']),
                         (200, 'candidate_input_manifest', True))
        self.assertEqual(restored['manifest_ref'], manifest['manifest_ref'])

    def test_disabled_invalid_and_unauthenticated_requests(self):
        self.assertEqual(self.request(user=None)[0], 401)
        self.assertEqual(self.request('?cursor=bad')[0], 422)
        self.assertEqual(self.request('/prepare', method='POST', body={'choices': []})[0], 422)
        self.handler.first_search = None
        self.assertEqual(self.request()[1]['code'], 'LOCAL_SEARCH_NOT_CONFIGURED')


if __name__ == '__main__':
    unittest.main()
