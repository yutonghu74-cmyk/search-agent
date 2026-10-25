import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from dsh_retrieval.adapters.dsh.runtime import DshModelRuntime
from dsh_retrieval.adapters.dsh.tools_server import ProposalTools, serve
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.intent_model import ModelConfig
from dsh_retrieval.model_runtime import ModelProfile


class ProposalToolTest(unittest.TestCase):
    def test_context_required_and_no_arbitrary_tools_or_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'context.json').write_text('{"only":"this turn"}')
            tools = ProposalTools(root)
            for name, arguments in [('bash', {'command': 'ls'}),
                                    ('get_context', {'task_id': 'other'}),
                                    ('submit_proposal', {'proposal': {}})]:
                with self.assertRaises(ValueError):
                    tools.call(name, arguments)
            self.assertEqual(tools.call('get_context', {}), {'only': 'this turn'})
            self.assertFalse(tools.call('submit_proposal', {'proposal': {'action': 'clarify'}})['executed'])
            tools.call('submit_proposal', {'proposal': {'action': 'clarify'}})
            with self.assertRaises(ValueError):
                tools.call('submit_proposal', {'proposal': {'action': 'execute'}})

    def test_protocol_has_exact_tool_allowlist(self):
        source = io.StringIO('\n'.join(json.dumps({'jsonrpc': '2.0', 'id': n, 'method': method})
                                       for n, method in enumerate(['initialize', 'tools/list', 'run_shell'])))
        sink = io.StringIO()
        serve('/unused', source, sink)
        rows = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual({t['name'] for t in rows[1]['result']['tools']}, {'get_context', 'submit_proposal'})
        self.assertEqual(rows[2]['error']['code'], -32601)


class RuntimeTest(unittest.TestCase):
    def test_rate_limit_is_retryable_and_sanitized(self):
        from unittest.mock import Mock
        process = Mock(returncode=1)
        process.communicate.return_value = (b'{"error":"RATE_LIMIT"}', None)
        with tempfile.TemporaryDirectory() as home:
            profile = ModelProfile('text', 'llm', ModelConfig('test', 'test', 'https://example.invalid/v1', 'fake-key'))
            runtime = DshModelRuntime([profile], python=sys.executable, home=home)
            with patch('dsh_retrieval.adapters.dsh.runtime.subprocess.Popen', return_value=process), \
                    patch.object(runtime, '_terminate'):
                with self.assertRaises(ServiceError) as error:
                    runtime._request(profile, 'system', 'message')
            self.assertEqual(error.exception.code, 'MODEL_RATE_LIMIT')
            self.assertTrue(error.exception.retryable)
            self.assertNotIn('fake-key', str(error.exception))

    def test_two_principals_never_share_home_or_session(self):
        from unittest.mock import Mock
        launches = []
        def launch(*args, **kwargs):
            process = Mock(returncode=0)
            def communicate(payload, timeout):
                request = json.loads(payload)
                launches.append(request)
                return json.dumps({'session_id': request['run_id'], 'runtime_version': '0.2.1rc2',
                    'proposal': {}, 'finish_reason': 'completed', 'event_types': []}).encode(), None
            process.communicate.side_effect = communicate
            return process
        with tempfile.TemporaryDirectory() as home:
            profile = ModelProfile('text', 'llm', ModelConfig('test', 'test', 'https://example.invalid/v1', 'fake'))
            runtime = DshModelRuntime([profile], python=sys.executable, home=home)
            with patch('dsh_retrieval.adapters.dsh.runtime.subprocess.Popen', side_effect=launch), \
                    patch.object(runtime, '_terminate'):
                for tenant in ('tenant-a', '../../other-tenant'):
                    with runtime.request_scope(tenant, 'user', 'trace', '/route'):
                        runtime._request(profile, 'system', tenant)
            self.assertNotEqual(Path(launches[0]['turn_dir']).parent, Path(launches[1]['turn_dir']).parent)
            self.assertNotEqual(launches[0]['run_id'], launches[1]['run_id'])
            for request in launches:
                self.assertTrue(Path(request['turn_dir']).is_relative_to(home))
                self.assertNotIn('tenant_id', request['context'])

    def test_timeout_cancels_process_tree_and_does_not_accept_late_output(self):
        from unittest.mock import Mock
        process = Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired('worker', 1)
        with tempfile.TemporaryDirectory() as home:
            profile = ModelProfile('text', 'llm', ModelConfig('test', 'test', 'https://example.invalid/v1', 'fake'))
            runtime = DshModelRuntime([profile], python=sys.executable, home=home, timeout=1)
            with patch('dsh_retrieval.adapters.dsh.runtime.subprocess.Popen', return_value=process), \
                    patch.object(runtime, '_terminate') as terminate:
                with self.assertRaises(ServiceError) as error:
                    runtime._request(profile, 'system', 'message')
            self.assertEqual(error.exception.code, 'DSH_TIMEOUT')
            terminate.assert_called_once_with(process)
            self.assertEqual(json.loads(next(Path(home).glob('*/*/audit.json')).read_text())['status'], 'timeout')

    def test_runtime_change_invalidates_old_model_binding(self):
        from dsh_retrieval.model_runtime import ModelRuntime
        profiles = [ModelProfile('text', 'llm', ModelConfig('test', 'test', 'https://example.invalid/v1', 'fake')),
                    ModelProfile('vision', 'vlm', ModelConfig('test', 'test', 'https://example.invalid/v1', 'fake'))]
        with tempfile.TemporaryDirectory() as home:
            runtime = DshModelRuntime(profiles, python=sys.executable, home=home)
            self.assertNotEqual(runtime.binding('text', 'vision'), ModelRuntime(profiles).binding('text', 'vision'))

    def test_missing_quantity_cannot_be_invented_through_count_operation(self):
        from dsh_retrieval.model_runtime import ModelRuntime
        state = {'target_count': None, 'question': None}
        value = {'action': 'update', 'subject': '猫', 'media_type': 'image',
                 'count_edits': [{'target': 'images', 'labels': [], 'operation': 'set', 'value': 5}],
                 'expansion_instruction': '找5张猫图片'}
        result = ModelRuntime._validate_dialogue_proposal(value, state, '找一些猫图片', None)
        self.assertIsNone(result['target_count'])
        self.assertEqual(result['count_edits'], [])
        self.assertIsNone(result['expansion_instruction'])

    def test_environment_strips_other_secrets_and_scope_is_reset(self):
        with tempfile.TemporaryDirectory() as home:
            runtime = DshModelRuntime(python=sys.executable, home=home)
            with patch.dict(os.environ, {'UNRELATED_API_KEY': 'secret', 'DSH_HOME': '/other'}):
                env = runtime._environment()
            self.assertNotIn('UNRELATED_API_KEY', env)
            self.assertNotIn('DSH_HOME', env)
            self.assertEqual(Path(env['PYTHONPATH']).name, 'src')
            from dsh_retrieval.adapters.dsh.runtime import _scope
            with runtime.request_scope('tenant', 'user', 'request', '/route'):
                self.assertEqual(_scope.get()['tenant_id'], 'tenant')
            self.assertIsNone(_scope.get())

    def test_missing_runtime_fails_without_http_fallback(self):
        with tempfile.TemporaryDirectory() as home:
            profile = ModelProfile('text', 'llm', ModelConfig('test', 'test', 'https://example.invalid/v1', 'fake'))
            runtime = DshModelRuntime([profile], python='/no/such/python', home=home)
            with self.assertRaises(ServiceError) as raised:
                runtime._request(profile, 'Return JSON', 'test')
            self.assertEqual(raised.exception.code, 'DSH_TURN_FAILED')
            audit = json.loads(next(Path(home).glob('*/*/audit.json')).read_text())
            self.assertEqual(audit['status'], 'failed')
            self.assertNotIn('fake', json.dumps(audit))


@unittest.skipUnless(os.environ.get('DSH_TEST_PYTHON'), 'opt-in pinned native DSH integration')
class NativeRuntimeTest(unittest.TestCase):
    def test_real_runtime_mcp_tools_and_provider_stream(self):
        """Real SDK/native DSH + MCP, deterministic local model (not a live model)."""
        calls = []
        proposal = {'action': 'clarify', 'question': '需要多少张图片？', 'reply': '需要多少张图片？'}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(body)
                number = len(calls)
                if number <= 2:
                    name = 'mcp__retrieval__get_context' if number == 1 else 'mcp__retrieval__submit_proposal'
                    args = {} if number == 1 else {'proposal': proposal}
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_' + str(number),
                        'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
                    finish = 'tool_calls'
                else:
                    delta, finish = {'role': 'assistant', 'content': 'proposal submitted'}, 'stop'
                chunks = [{'id': 'completion', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'test',
                           'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                          {'id': 'completion', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'test',
                           'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                           'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}]
                payload = ''.join('data: ' + json.dumps(row) + '\n\n' for row in chunks) + 'data: [DONE]\n\n'
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(payload.encode())))
                self.end_headers()
                self.wfile.write(payload.encode())

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as home:
                config = ModelConfig('test', 'test', 'https://example.invalid/v1', 'fake-not-a-secret')
                # Local deterministic fixture only; production still requires HTTPS.
                object.__setattr__(config, 'base_url', f'http://127.0.0.1:{server.server_port}/v1')
                profile = ModelProfile('text', 'llm', config)
                runtime = DshModelRuntime([profile], python=os.environ['DSH_TEST_PYTHON'], home=home, timeout=45)
                runtime.check_installation()
                result = runtime._request(profile, 'Return action, question and reply JSON.', '找猫图片')
                self.assertEqual(result, proposal)
                self.assertEqual(len(calls), 3)
                names = {item['function']['name'] for item in calls[0]['tools']}
                self.assertEqual(names, {'mcp__retrieval__get_context', 'mcp__retrieval__submit_proposal'})
                self.assertNotIn('dsh_session_log', calls[0])
                self.assertNotIn('dsh_plugin_packages', calls[0])
                self.assertIn('找猫图片', json.dumps(calls[1], ensure_ascii=False))
                audit = json.loads(next(Path(home).glob('*/*/audit.json')).read_text())
                self.assertEqual(audit['status'], 'proposal_received')
                self.assertIn('turn/end', audit['event_types'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
