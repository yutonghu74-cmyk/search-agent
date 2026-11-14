"""Exercise the existing HTTP draft route and its video execution boundary."""
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.api import create_handler
from draft_helpers import make_service
from test_video_query import video_body


class VideoAPITest(unittest.TestCase):
    def test_draft_save_read_edit_and_confirmation_gate(self):
        repo = InMemoryTaskRepository()
        service = make_service(repo)
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(service))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(path, body=None, key='video', user='user-a'):
            headers = {'X-Tenant-Id': 'tenant-a', 'X-User-Id': user,
                       'Idempotency-Key': key, 'Content-Type': 'application/json'}
            req = Request(f'http://127.0.0.1:{server.server_port}/v1/retrieval/' + path,
                          data=json.dumps(body).encode() if body is not None else None, headers=headers)
            try:
                with urlopen(req) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.load(error)
        try:
            status, draft = request('projects/demo/drafts', video_body())
            self.assertEqual(status, 200)
            self.assertFalse(draft['video_summary']['execution']['available'])
            path = 'projects/demo/drafts/' + draft['draft_id']
            fetched = request(path)[1]
            self.assertEqual({k: v for k, v in fetched.items() if k != 'trace_id'},
                             {k: v for k, v in draft.items() if k != 'trace_id'})
            self.assertEqual(request(path, user='other')[0], 404)
            status, error = request('tasks', {'draft_id': draft['draft_id'],
                'expected_draft_version': 1, 'proposal_digest': draft['proposal_digest'], 'action': 'confirm'})
            self.assertEqual(status, 422)
            self.assertEqual(error['code'], 'VIDEO_EXECUTION_UNAVAILABLE')
            self.assertEqual(repo._tasks, {})
            self.assertEqual(repo._batches, {})
            changed = video_body()
            changed['expected_draft_version'] = 1
            changed['query_spec']['video_conditions']['clauses'][0]['predicate'] = '室外有人喝水'
            status, updated = request(path, changed, key='edit')
            self.assertEqual(status, 200)
            self.assertEqual(updated['draft_version'], 2)
            self.assertNotEqual(updated['proposal_digest'], draft['proposal_digest'])
            self.assertEqual(updated['status'], 'draft')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
