from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from dsh_retrieval.api import create_handler
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.model_runtime import ModelRuntime
from dsh_retrieval.model_settings import ModelSettings, SecretBox, base_url
from dsh_retrieval.service import InMemoryTaskRepository, TaskService


class Transport:
    def __init__(self):
        self.payload = {'data': [{'id': 'text-one'}, {'id': 'vision-one'}, {'id': 'text-one'}]}
        self.error = None
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request)
        if self.error:
            raise self.error
        return BytesIO(json.dumps(self.payload).encode())


class ModelSettingsTest(unittest.TestCase):
    def test_independent_dialogue_default_is_scoped_and_legacy_default_updates_preserve_it(self):
        with self.store.scope('t', 'u'):
            identifier = self.register()
            llm, vlm = self.bind(identifier), self.bind(identifier, 'vlm')
            dialogue = self.store.bind(identifier, {'model': 'conversation-fast', 'kind': 'llm'})['profile_id']
            saved = self.store.save_defaults({'llm': llm, 'vlm': vlm, 'dialogue_llm': dialogue}, self.runtime)
            self.assertEqual(saved['defaults']['dialogue_llm'], dialogue)
            self.assertEqual(self.store.save_defaults({'llm': llm, 'vlm': vlm}, self.runtime)['defaults']['dialogue_llm'], dialogue)
            with self.assertRaises(ServiceError):
                self.store.save_defaults({'llm': llm, 'vlm': vlm, 'dialogue_llm': vlm}, self.runtime)
            self.assertEqual(self.store.defaults()['dialogue_llm'], dialogue)
        with self.store.scope('t', 'other'):
            self.assertEqual(self.store.defaults(), {})
            with self.assertRaises(ServiceError):
                self.store.save_defaults({'llm': '', 'vlm': '', 'dialogue_llm': dialogue}, self.runtime)
        reopened = ModelSettings(self.path/'settings.db', self.path/'key', ['https://models.example/v1'])
        self.addCleanup(reopened.close)
        with reopened.scope('t', 'u'):
            self.assertEqual(reopened.defaults()['dialogue_llm'], dialogue)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.transport = Transport()
        self.store = ModelSettings(self.path/'settings.db', self.path/'key',
                                   ['https://models.example/v1'], opener=self.transport)
        self.addCleanup(self.store.close)
        self.runtime = ModelRuntime()
        self.runtime.settings = self.store

    def register(self):
        return self.store.register({'name': '我的服务', 'provider': 'openai-compatible',
            'base_url': 'https://models.example/v1', 'api_key': 'test-secret-not-a-real-key'})['id']

    def bind(self, identifier, kind='llm'):
        return self.store.bind(identifier, {'model': 'text-one', 'kind': kind})['profile_id']

    def test_encryption_persistence_and_no_public_secret(self):
        with self.store.scope('t', 'u'):
            identifier = self.register()
            profile_id = self.bind(identifier)
            self.store.save_defaults({'llm': profile_id, 'vlm': ''}, self.runtime)
            public = json.dumps(self.store.read())
            self.assertNotIn('test-secret', public)
        self.assertNotIn(b'test-secret', (self.path/'settings.db').read_bytes())
        reopened = ModelSettings(self.path/'settings.db', self.path/'key', ['https://models.example/v1'])
        self.addCleanup(reopened.close)
        with reopened.scope('t', 'u'):
            self.assertEqual(reopened.defaults()['llm'], profile_id)
            self.assertEqual(reopened.select(profile_id, 'llm').config.api_key, 'test-secret-not-a-real-key')
        self.assertEqual((self.path/'key').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.path/'settings.db').stat().st_mode & 0o777, 0o600)

    def test_tampering_and_cross_owner_secret_substitution_fail_closed(self):
        blob = self.store.box.seal('secret', 'one')
        for bad, associated in ((blob[:-1]+bytes([blob[-1] ^ 1]), 'one'), (blob, 'two')):
            with self.assertRaises(ServiceError):
                self.store.box.open(bad, associated)

    def test_isolation_including_parallel_request_context(self):
        with self.store.scope('t', 'u'):
            identifier = self.register()
            profile = self.bind(identifier)
        def check(user):
            with self.store.scope('t', user):
                return len(self.store.catalog()), self.store.select(profile, 'llm') is not None
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(list(pool.map(check, ['u', 'other'])), [(1, True), (0, False)])
        with self.assertRaises(ServiceError):
            self.runtime.select(profile, 'llm')
        with self.store.scope('other-tenant', 'u'):
            self.assertEqual(self.store.read()['connections'], [])
            with self.assertRaises(ServiceError):
                self.store.rotate(identifier, {'api_key': 'new-key'})

    def test_url_validation_and_admin_approval_before_network(self):
        for url in ('http://models.example/v1', 'https://u:p@models.example',
                    'https://models.example/v1?key=a', 'https://models.example/v1/chat/completions',
                    'https://models.example/%2e%2e', 'https://models.example/a/../b'):
            with self.assertRaises(ServiceError):
                base_url(url)
        with self.store.scope('t', 'u'):
            with self.assertRaises(ServiceError):
                self.store.register({'name': 'x', 'provider': 'openai-compatible',
                    'base_url': 'https://untrusted.example', 'api_key': 'secret'})
        self.assertFalse(self.transport.calls)

    def test_discovery_is_bounded_and_failed_refresh_preserves_catalog(self):
        with self.store.scope('t', 'u'):
            identifier = self.register()
            result = self.store.discover(identifier)
            self.assertEqual(result['models'], ['text-one', 'vision-one'])
            self.assertEqual(self.transport.calls[0].full_url, 'https://models.example/v1/models')
            self.assertEqual(self.transport.calls[0].get_header('Authorization'), 'Bearer test-secret-not-a-real-key')
            for payload in ({'data': [{'id': 'one'}], 'has_more': True}, {'oops': 'secret'},
                            {'data': [{'id': 'x'*1_000_001}]}):
                self.transport.payload = payload
                with self.assertRaises(ServiceError):
                    self.store.discover(identifier)
                self.assertEqual(self.store.read()['connections'][0]['models'], result['models'])

    def test_manual_model_supported_and_rotation_keeps_binding_identity(self):
        with self.store.scope('t', 'u'):
            identifier = self.register()
            llm = self.bind(identifier)
            vlm = self.bind(identifier, 'vlm')
            before = self.runtime.binding(llm, vlm)
            self.assertEqual(self.bind(identifier), llm)
            self.store.rotate(identifier, {'api_key': 'rotated-secret'})
            self.assertEqual(self.runtime.binding(llm, vlm), before)
            self.assertEqual(self.runtime.select(llm, 'llm').config.api_key, 'rotated-secret')
            with self.assertRaises(ServiceError):
                self.store.save_defaults({'llm': vlm, 'vlm': llm}, self.runtime)
            self.assertEqual(self.store.defaults(), {})

    def test_registered_profile_is_used_by_actual_runtime_transport(self):
        self.runtime.opener = self.transport
        self.transport.payload = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]}
        with self.store.scope('t', 'u'):
            profile = self.runtime.select(self.bind(self.register()), 'llm')
            self.assertEqual(self.runtime._request(profile, 'Return JSON', 'test'), {'ok': True})
        request = self.transport.calls[0]
        self.assertEqual(request.full_url, 'https://models.example/v1/chat/completions')
        self.assertEqual(json.loads(request.data)['model'], 'text-one')
        self.assertNotIn(b'test-secret', request.data)

    def test_errors_never_include_upstream_secret_body(self):
        self.transport.error = HTTPError('https://models.example', 401, 'test-secret', {}, BytesIO(b'test-secret'))
        with self.store.scope('t', 'u'):
            with self.assertRaises(ServiceError) as caught:
                self.store.discover(self.register())
        self.assertNotIn('test-secret', str(caught.exception))

    def test_probe_uses_selected_model_and_image_and_keeps_defaults(self):
        self.runtime.opener = self.transport
        with self.store.scope('t', 'u'):
            identifier = self.register()
            llm, vlm = self.bind(identifier), self.bind(identifier, 'vlm')
            self.store.save_defaults({'llm': llm, 'vlm': vlm}, self.runtime)
            self.transport.payload = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]}
            self.assertEqual(self.store.probe({'profile_id': llm, 'kind': 'llm'}, self.runtime)['profile_id'], llm)
            self.transport.payload['choices'][0]['message']['content'] = '{"color":"red"}'
            self.store.probe({'profile_id': vlm, 'kind': 'vlm'}, self.runtime)
            content = json.loads(self.transport.calls[-1].data)['messages'][-1]['content']
            self.assertTrue(content[1]['image_url']['url'].startswith('data:image/png;base64,'))
            self.transport.payload['choices'][0]['message']['content'] = '{"color":"blue"}'
            with self.assertRaises(ServiceError):
                self.store.probe({'profile_id': vlm, 'kind': 'vlm'}, self.runtime)
            self.assertEqual(self.store.defaults(), {'llm': llm, 'vlm': vlm})
            self.store.rotate(identifier, {'api_key': 'rotated'})
            self.assertTrue(all('verified_at' not in p for p in self.store.catalog()))

    def test_missing_master_key_never_silently_creates_new_key(self):
        missing = self.path/'missing-key'
        with self.assertRaises(ValueError):
            ModelSettings(self.path/'settings.db', missing, [])
        self.assertFalse(missing.exists())

    def test_http_registration_defaults_and_access_checks(self):
        projects = LocalProjectAccess([('tenant', 'user', 'demo', 'default')])
        service = TaskService(InMemoryTaskRepository(), projects)
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(service, model_runtime=self.runtime))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        prefix = f'http://127.0.0.1:{server.server_port}/v1/retrieval/projects/demo/'
        def call(path, body=None, user='user', origin=None):
            headers = {'X-Tenant-Id': 'tenant', 'X-User-Id': user, 'Content-Type': 'application/json'}
            if origin:
                headers['Origin'] = origin
            request = Request(prefix+path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
            with urlopen(request) as response:
                return json.load(response)
        connection = call('model-settings/connections', {'name': 'test', 'provider': 'openai-compatible',
            'base_url': 'https://models.example/v1', 'api_key': 'private-test-key'})['connection']
        identifier = connection['id']
        profile = call(f'model-settings/connections/{identifier}/bind', {'model': 'text-one', 'kind': 'llm'})['profile_id']
        call('model-settings/defaults', {'llm': profile, 'vlm': ''})
        self.assertEqual(call('models')['defaults']['llm'], profile)
        self.assertEqual(call('models')['items'][0]['id'], profile)
        for user, origin in [('someone', None), ('user', 'https://evil.example')]:
            with self.assertRaises(HTTPError) as caught:
                call('model-settings/defaults', {'llm': '', 'vlm': ''}, user, origin)
            self.assertEqual(caught.exception.code, 403)
        self.assertNotIn('private-test-key', json.dumps(call('model-settings')))


if __name__ == '__main__':
    unittest.main()
