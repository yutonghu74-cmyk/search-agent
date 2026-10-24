"""Chat LLM preview is authorized, grounded and never a task write."""
from io import BytesIO
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from draft_helpers import make_service
from dsh_retrieval.api import create_handler
from dsh_retrieval.intent_model import ModelConfig
from dsh_retrieval.model_runtime import ModelProfile, ModelRuntime
from dsh_retrieval.service import InMemoryTaskRepository


class Transport:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(json.loads(request.data))
        return BytesIO(json.dumps({'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(self.result)}}]}).encode())


class ChatIntentTest(unittest.TestCase):
    def setUp(self):
        self.transport = Transport({'media_type': 'image', 'items': [{
            'quote': '找狗图', 'occurrence': 0, 'kind': 'intent', 'hardness': 'hard',
            'predicate_ref': 'subject.dog@1', 'operator': 'eq', 'value': True,
            'question': None}]})
        profile = ModelProfile('text', 'llm', ModelConfig(
            'fixture', 'text-v1', 'https://fixture.invalid/v1', 'private-test-key'))
        self.runtime = ModelRuntime([profile], opener=self.transport)
        self.service = make_service(InMemoryTaskRepository())
        self.server = ThreadingHTTPServer(('127.0.0.1', 0),
            create_handler(self.service, model_runtime=self.runtime))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def call(self, body, *, user='owner', tenant='tenant'):
        headers = {'X-Tenant-Id': tenant, 'X-User-Id': user,
                   'Content-Type': 'application/json'}
        request = Request(f'http://127.0.0.1:{self.server.server_port}/v1/retrieval/projects/demo/intent-preview',
            data=json.dumps(body).encode(), headers=headers)
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_selected_llm_reviews_each_utterance_without_creating_task(self):
        body = {'raw_request': '找狗图', 'dimension_id': 'default', 'llm_id': 'text'}
        status, data = self.call(body)
        self.assertEqual(status, 200)
        self.assertEqual(data['mode'], 'LIVE_MODEL_INTERPRETATION')
        self.assertEqual(data['review']['items'][0]['quote'], '找狗图')
        self.assertFalse(data['review']['execution_allowed'])
        self.assertFalse(data['review']['creates_task'])
        self.assertEqual(len(self.transport.calls), 1)
        self.assertNotIn('private-test-key', json.dumps(data))

    def test_unauthorized_or_missing_model_does_not_call_provider(self):
        body = {'raw_request': '找狗图', 'dimension_id': 'default', 'llm_id': 'text'}
        self.assertEqual(self.call(body, user='other')[0], 403)
        self.assertEqual(self.call(body, user='')[0], 401)
        self.assertEqual(self.call(dict(body, llm_id='missing'))[0], 422)
        self.assertEqual(self.call(dict(body, extra=True))[0], 422)
        self.assertEqual(self.transport.calls, [])

    def test_model_quote_must_match_original(self):
        self.transport.result['items'][0]['quote'] = '猫'
        status, data = self.call({'raw_request': '找狗图', 'dimension_id': 'default', 'llm_id': 'text'})
        self.assertEqual(status, 502)
        self.assertEqual(data['code'], 'MODEL_GROUNDING_FAILED')


if __name__ == '__main__':
    unittest.main()
