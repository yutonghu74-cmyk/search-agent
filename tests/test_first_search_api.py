"""HTTP first-search flow over an isolated local SQLite ledger and fixture."""
from http.server import ThreadingHTTPServer
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

    def test_explicit_choice_confirmation_execution_and_safe_candidates(self):
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
        replay = self.request('/confirm', method='POST', key='approve', body={
            'proposal_digest': proposal['proposal_digest'],
            'expected_task_version': proposal['expected_task_version'], 'action': 'confirm'})
        self.assertEqual(replay[2].get('Idempotent-Replayed'), 'true')
        status, batch, _ = self.request('/tick', method='POST', body={'max_requests': 2})
        self.assertEqual((status, batch['network_attempts']), (200, 2))
        status, candidates, _ = self.request('/candidates')
        self.assertEqual((status, candidates['raw_hit_count']), (200, 4))
        self.assertIsNone(candidates['accepted_count'])
        raw = json.dumps(candidates)
        for secret in ('authorized_media_ref', 'media:dog-1', 'filter_template_ref', 'cursor_secret'):
            self.assertNotIn(secret, raw)
        self.assertEqual(self.request('/candidates', user='other')[0], 404)

    def test_disabled_invalid_and_unauthenticated_requests(self):
        self.assertEqual(self.request(user=None)[0], 401)
        self.assertEqual(self.request('?cursor=bad')[0], 422)
        self.assertEqual(self.request('/prepare', method='POST', body={'choices': []})[0], 422)
        self.handler.first_search = None
        self.assertEqual(self.request()[1]['code'], 'LOCAL_SEARCH_NOT_CONFIGURED')


if __name__ == '__main__':
    unittest.main()
