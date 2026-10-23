from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import normalize_grouped_expansion
from dsh_retrieval.intent_model import ModelConfig
from dsh_retrieval.model_runtime import ModelProfile, ModelRuntime, load_model_runtime
from dsh_retrieval.query_branches import compile_query_branches


class Transport:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(json.loads(request.data))
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return BytesIO(json.dumps({'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(output)}}]}).encode())


class ModelRuntimeTest(unittest.TestCase):
    def test_http_selection_draft_confirmation_and_history(self):
        from http.server import ThreadingHTTPServer
        import threading
        from urllib.request import Request, urlopen
        from dsh_retrieval.api import create_handler
        from dsh_retrieval.service import InMemoryTaskRepository
        from draft_helpers import BODY, make_service
        runtime = self.runtime([{'parent_label': None, 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': ['柯基']}]}])
        service = make_service(InMemoryTaskRepository())
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(service, model_runtime=runtime))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def call(path, body=None, key='test'):
            headers = {'X-Tenant-Id': 'tenant', 'X-User-Id': 'owner',
                       'Content-Type': 'application/json', 'Idempotency-Key': key}
            data = json.dumps(body).encode() if body is not None else None
            with urlopen(Request(f'http://127.0.0.1:{server.server_port}' + path, data=data, headers=headers)) as response:
                return json.load(response)
        try:
            self.assertEqual(len(call('/v1/retrieval/projects/demo/models')['items']), 2)
            expanded = call('/v1/retrieval/projects/demo/expansions', {
                'raw_request': '狗，不要人物', 'dimension_id': 'default',
                'model_selection': {'llm': 'text', 'vlm': 'vision'}})
            body = deepcopy(BODY)
            body['query_spec']['grouped_expansion'] = expanded['plan']
            draft = call('/v1/retrieval/projects/demo/drafts', body)
            task = call('/v1/retrieval/tasks', {'draft_id': draft['draft_id'],
                'expected_draft_version': draft['draft_version'], 'proposal_digest': draft['proposal_digest'], 'action': 'confirm'})
            revisions = call('/v1/retrieval/tasks/' + task['task_id'] + '/revisions')
            self.assertEqual(revisions['items'][0]['proposal']['query_spec']['grouped_expansion']['model_binding'],
                             runtime.binding('text', 'vision'))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def runtime(self, outputs):
        self.transport = Transport(outputs)
        return ModelRuntime([
            ModelProfile('text', 'llm', ModelConfig('fixture', 'text-v1', 'https://fixture.invalid/v1', 'test-secret')),
            ModelProfile('vision', 'vlm', ModelConfig('fixture', 'vision-v1', 'https://fixture.invalid/v1', 'test-secret')),
        ], opener=self.transport)

    def test_selected_llm_expansion_and_vlm_verdict(self):
        runtime = self.runtime([{'parent_label': '动物', 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': ['柯基']}]},
            {'verdict': 'unknown', 'reason': '主体被遮挡'}])
        expansion = runtime.expand('狗，不要人物', 'text', 'vision')
        plan = expansion['plan']
        self.assertEqual(normalize_grouped_expansion(plan), plan)
        self.assertEqual(plan['generator'], 'llm@1')
        query = compile_query_branches({'raw_request': '狗，不要人物', 'grouped_expansion': plan})
        self.assertEqual(query['branches'][0]['query_text'], '狗，不要人物')
        verdict = runtime.assess('狗，不要人物', b'\xff\xd8\xfffixture', 'image/jpeg', plan['model_binding'])
        self.assertEqual(verdict['verdict'], 'unknown')
        self.assertEqual([call['model'] for call in self.transport.calls], ['text-v1', 'vision-v1'])
        self.assertEqual(self.transport.calls[1]['messages'][1]['content'][1]['type'], 'image_url')
        self.assertNotIn('test-secret', json.dumps(runtime.catalog()))
        self.assertNotIn('fixture.invalid', json.dumps(runtime.catalog()))
        self.assertNotIn('test-secret', json.dumps(expansion))
        changed = deepcopy(plan)
        changed['model_binding']['vlm'] = 'different'
        with self.assertRaises(ValueError):
            normalize_grouped_expansion(changed)

    def test_wrong_capability_or_missing_model_never_calls_transport(self):
        runtime = self.runtime([])
        for ids in (('vision', 'text'), ('missing', 'vision')):
            with self.assertRaises(ServiceError):
                runtime.expand('狗', *ids)
        self.assertEqual(self.transport.calls, [])

    def test_failures_never_become_pass_and_do_not_leak(self):
        for output in (OSError('test-secret'), {'verdict': 'pass'},
                       {'verdict': True, 'reason': 'bad'}, {'verdict': 'pass', 'reason': '', 'approved': True}):
            runtime = self.runtime([output])
            with self.assertRaises(ServiceError) as raised:
                runtime.assess('狗', b'\xff\xd8\xfffixture', 'image/jpeg', runtime.binding('text', 'vision'))
            self.assertNotIn('test-secret', str(raised.exception))

    def test_config_drift_and_remote_image_are_rejected_before_call(self):
        runtime = self.runtime([])
        binding = runtime.binding('text', 'vision')
        with self.assertRaises(ServiceError):
            runtime.assess('狗', 'https://private.invalid/image', 'image/jpeg', binding)
        binding['config_digest'] = 'changed'
        with self.assertRaises(ServiceError):
            runtime.assess('狗', b'\xff\xd8\xfffixture', 'image/jpeg', binding)
        self.assertEqual(self.transport.calls, [])

    def test_config_loader_uses_environment_and_empty_registry_is_explicit(self):
        self.assertEqual(ModelRuntime().catalog(), [])
        config = {'models': [{'id': 'text', 'kind': 'llm', 'provider': 'fixture', 'model': 'text-v1',
                             'base_url': 'https://fixture.invalid/v1', 'api_key_env': 'DSH_TEST_MODEL_KEY'}]}
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'models.json'
            path.write_text(json.dumps(config))
            with patch.dict(os.environ, {'DSH_TEST_MODEL_KEY': 'test-secret'}):
                runtime = load_model_runtime(path)
            self.assertEqual(runtime.catalog()[0]['id'], 'text')
            self.assertNotIn('test-secret', repr(runtime.profiles))


if __name__ == '__main__':
    unittest.main()
