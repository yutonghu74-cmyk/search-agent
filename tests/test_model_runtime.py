from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

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

    def test_feedback_cleaning_suggestion_is_advisory_and_bounded(self):
        runtime = self.runtime([{'requirement': '必须是狗，且没有可见文字',
                                 'rationale': '保留原主体并加入反馈排除项',
                                 'needs_clarification': False}])
        suggestion = runtime.suggest_cleaning('寻找狗', '去掉文字', 'text', 'vision')
        self.assertEqual(suggestion['requirement'], '必须是狗，且没有可见文字')
        self.assertEqual(suggestion['model_binding'], runtime.binding('text', 'vision'))
        self.assertFalse(suggestion['needs_clarification'])
        self.assertEqual(self.transport.calls[0]['model'], 'text-v1')
        self.assertNotIn('secret', json.dumps(suggestion))
        for invalid in ({'requirement': '狗', 'rationale': '说明'},
                        {'requirement': '', 'rationale': '说明', 'needs_clarification': False},
                        {'requirement': '狗', 'rationale': '说明', 'needs_clarification': 'false'},
                        {'requirement': '狗', 'rationale': '说明', 'needs_clarification': False, 'execute': True}):
            runtime = self.runtime([invalid])
            with self.assertRaises(ServiceError) as caught:
                runtime.suggest_cleaning('寻找狗', '去掉文字', 'text', 'vision')
            self.assertEqual(caught.exception.code, 'MODEL_OUTPUT_INVALID')

    def test_feedback_suggestion_http_requires_current_owner_draft(self):
        from http.server import ThreadingHTTPServer
        import threading
        from urllib.request import Request, urlopen
        from dsh_retrieval.api import create_handler
        from dsh_retrieval.service import InMemoryTaskRepository
        from draft_helpers import BODY, make_service
        runtime = self.runtime([{'requirement': '狗且无文字', 'rationale': '排除文字',
                                 'needs_clarification': False}])
        service = make_service(InMemoryTaskRepository())
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(service, model_runtime=runtime))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def call(path, body=None, *, owner='owner', key='test'):
            headers = {'X-Tenant-Id': 'tenant', 'X-User-Id': owner,
                       'Content-Type': 'application/json', 'Idempotency-Key': key}
            data = json.dumps(body).encode() if body is not None else None
            try:
                with urlopen(Request(f'http://127.0.0.1:{server.server_port}' + path,
                                     data=data, headers=headers)) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.load(error)
        try:
            _, draft = call('/v1/retrieval/projects/demo/drafts', deepcopy(BODY), key='draft')
            _, task = call('/v1/retrieval/tasks', {'draft_id': draft['draft_id'],
                'expected_draft_version': draft['draft_version'],
                'proposal_digest': draft['proposal_digest'], 'action': 'confirm'}, key='task')
            task_id = task['task_id']
            _, feedback = call(f'/v1/retrieval/tasks/{task_id}/feedback', {
                'text': '去掉文字', 'scope': 'all', 'expected_revision': 1,
                'expected_task_version': task['task_version']}, key='feedback')
            body = {'draft_id': feedback['draft_id'],
                    'expected_draft_version': feedback['draft_version'],
                    'proposal_digest': feedback['proposal_digest'],
                    'model_selection': {'llm': 'text', 'vlm': 'vision'}}
            path = f'/v1/retrieval/tasks/{task_id}/feedback-suggestions'
            status, foreign = call(path, body, owner='intruder')
            self.assertEqual(status, 404)
            self.assertEqual(len(self.transport.calls), 0)
            status, suggested = call(path, body)
            self.assertEqual((status, suggested['status']), (200, 'advisory_only'))
            self.assertEqual(suggested['draft_id'], feedback['draft_id'])
            self.assertEqual(suggested['proposal_digest'], feedback['proposal_digest'])
            self.assertEqual(len(self.transport.calls), 1)
            current_status, current = call(f'/v1/retrieval/tasks/{task_id}')
            self.assertEqual((current_status, current['confirmed_revision']), (200, 1))
            self.assertEqual(current['pending_draft']['draft_id'], feedback['draft_id'])
            status, rejected = call(path, dict(body, expected_draft_version=99))
            self.assertEqual((status, rejected['code']), (409, 'REVISION_CONFLICT'))
            self.assertEqual(len(self.transport.calls), 1)
            def concurrent_confirm(*args):
                confirmed_status, _ = call(f'/v1/retrieval/tasks/{task_id}/confirmations', {
                    'draft_id': feedback['draft_id'],
                    'expected_draft_version': feedback['draft_version'],
                    'proposal_digest': feedback['proposal_digest'],
                    'expected_task_version': feedback['base_task_version'],
                    'action': 'confirm'}, key='confirm-during-model')
                self.assertEqual(confirmed_status, 202)
                return {'requirement': '狗且无文字', 'rationale': '排除文字',
                        'needs_clarification': False}
            with patch.object(runtime, '_request', side_effect=concurrent_confirm):
                status, stale = call(path, body)
            self.assertEqual((status, stale['code']), (409, 'REVISION_CONFLICT'))
            status, current = call(f'/v1/retrieval/tasks/{task_id}')
            self.assertEqual((status, current['confirmed_revision']), (200, 2))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

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

    def test_temporary_upstream_limit_retries_with_bounded_wait(self):
        limited = HTTPError('https://fixture.invalid/v1/chat/completions', 429,
                            'private upstream error', {'Retry-After': '99'}, BytesIO(b'private'))
        runtime = self.runtime([limited, {'parent_label': None, 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': []}]}])
        with patch('dsh_retrieval.model_runtime.time.sleep') as sleep:
            result = runtime.expand('狗', 'text', 'vision')
        self.assertEqual(result['plan']['groups'][0]['primary_label'], '狗')
        self.assertEqual(len(self.transport.calls), 2)
        sleep.assert_called_once_with(5)

    def test_permanent_http_invalid_response_and_retry_exhaustion_fail_closed(self):
        def error(code):
            return HTTPError('https://fixture.invalid/v1/chat/completions', code,
                             'test-secret', {}, BytesIO(b'test-secret'))
        for outputs, expected_calls in (([error(400)], 1), (['not an object'], 1),
                                        ([error(503), error(503), error(503)], 3)):
            runtime = self.runtime(outputs)
            with patch('dsh_retrieval.model_runtime.time.sleep') as sleep:
                with self.assertRaises(ServiceError) as raised:
                    runtime.expand('狗', 'text', 'vision')
            self.assertEqual(len(self.transport.calls), expected_calls)
            self.assertNotIn('test-secret', str(raised.exception))
            self.assertEqual(sleep.call_count, expected_calls - 1)

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
