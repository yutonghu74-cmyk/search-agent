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
    def test_backend_inline_request_context_allows_only_request_draft_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'context.json').write_text(json.dumps({
                'tool_mode': 'request_draft', 'context_delivery': 'inline', 'messages': []}))
            tools = ProposalTools(root)
            for name in ('submit_proposal', 'save_expansion_draft', 'bash'):
                with self.assertRaises(ValueError):
                    tools.call(name, {'proposal': {}})
            self.assertFalse(tools.call('update_request_draft', {'proposal': {'action': 'update'}})['executed'])
            with self.assertRaises(ValueError):
                tools.call('update_request_draft', {'proposal': {'action': 'confirm'}})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'context.json').write_text(json.dumps({'tool_mode': 'request_draft'}))
            tools = ProposalTools(root)
            with self.assertRaises(ValueError):
                tools.call('update_request_draft', {'proposal': {}, 'context_delivery': 'inline'})

    def test_context_required_and_no_arbitrary_tools_or_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'context.json').write_text('{"only":"this turn","tool_mode":"proposal"}')
            tools = ProposalTools(root)
            for name, arguments in [('bash', {'command': 'ls'}),
                                    ('get_request_context', {'task_id': 'other'}),
                                    ('submit_proposal', {'proposal': {}})]:
                with self.assertRaises(ValueError):
                    tools.call(name, arguments)
            self.assertEqual(tools.call('get_request_context', {}),
                             {'only': 'this turn', 'tool_mode': 'proposal'})
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
        self.assertEqual({t['name'] for t in rows[1]['result']['tools']}, {
            'get_request_context', 'update_request_draft', 'save_expansion_draft', 'submit_proposal'})
        self.assertEqual(rows[2]['error']['code'], -32601)

    def test_draft_tools_are_scoped_by_server_selected_mode(self):
        for mode, accepted, rejected in (
                ('request_draft', 'update_request_draft', 'save_expansion_draft'),
                ('expansion_draft', 'save_expansion_draft', 'update_request_draft')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'context.json').write_text(json.dumps({'tool_mode': mode}))
                tools = ProposalTools(root)
                tools.call('get_request_context', {})
                with self.assertRaises(ValueError):
                    tools.call(rejected, {'proposal': {}})
                result = tools.call(accepted, {'proposal': {'action': 'draft'}})
                self.assertEqual(result, {'status': 'pending_backend_validation', 'executed': False})


class RuntimeTest(unittest.TestCase):
    def test_rate_limit_is_retryable_and_sanitized(self):
        from unittest.mock import Mock
        process = Mock(returncode=1, pid=os.getpid())
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
            process = Mock(returncode=0, pid=os.getpid())
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
        process = Mock(pid=os.getpid())
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

    @staticmethod
    def _completing_launch(launches):
        from unittest.mock import Mock
        def launch(*args, **kwargs):
            process = Mock(returncode=0, pid=os.getpid())
            def communicate(payload, timeout):
                request = json.loads(payload)
                launches.append((request, timeout))
                return json.dumps({'session_id': request['run_id'], 'runtime_version': '0.2.1rc2',
                    'proposal': {}, 'finish_reason': 'completed', 'event_types': []}).encode(), None
            process.communicate.side_effect = communicate
            return process
        return launch

    def test_per_model_options_reach_worker_and_extend_turn_budget(self):
        launches = []
        default = ModelProfile('text', 'llm', ModelConfig('test', 'test', 'https://example.invalid/v1', 'fake'))
        slow = ModelProfile('slow', 'llm', ModelConfig('test', 'reasoner', 'https://example.invalid/v1', 'fake',
                                                       max_tokens=16000, request_timeout_seconds=180))
        with tempfile.TemporaryDirectory() as home:
            runtime = DshModelRuntime([default, slow], python=sys.executable, home=home)
            with patch('dsh_retrieval.adapters.dsh.runtime.subprocess.Popen',
                       side_effect=self._completing_launch(launches)), patch.object(runtime, '_terminate'):
                runtime._request(default, 'system', 'a')
                runtime._request(slow, 'system', 'b')
        (first, first_timeout), (second, second_timeout) = launches
        self.assertEqual((first['max_tokens'], first['request_timeout_seconds'], first_timeout), (8000, 90, 120))
        self.assertEqual((second['max_tokens'], second['request_timeout_seconds'], second_timeout), (16000, 180, 210))

    def test_runs_map_to_task_revision_and_stay_tenant_scoped(self):
        launches = []
        with tempfile.TemporaryDirectory() as home:
            profile = ModelProfile('text', 'llm', ModelConfig('test', 'test', 'https://example.invalid/v1', 'fake'))
            runtime = DshModelRuntime([profile], python=sys.executable, home=home)
            with patch('dsh_retrieval.adapters.dsh.runtime.subprocess.Popen',
                       side_effect=self._completing_launch(launches)), patch.object(runtime, '_terminate'):
                with runtime.request_scope('tenant-a', 'user-a', 'trace-1', '/followup'):
                    with runtime.business_scope(project_id='demo', task_id='task-1', task_version=3,
                                                revision=2, generation=1):
                        runtime._request(profile, 'system', '保密的用户原文')
                    runtime._request(profile, 'system', 'no task binding')
            rows = runtime.agent_runs('tenant-a', 'task-1')
            self.assertEqual(len(rows), 1)
            self.assertEqual({key: rows[0][key] for key in ('run_id', 'revision', 'generation', 'task_version',
                                                             'status', 'model_profile', 'route')},
                             {'run_id': launches[0][0]['run_id'], 'revision': 2, 'generation': 1,
                              'task_version': 3, 'status': 'proposal_received', 'model_profile': 'text',
                              'route': '/followup'})
            self.assertNotIn('保密', json.dumps(rows, ensure_ascii=False))
            self.assertEqual(runtime.agent_runs('tenant-b', 'task-1'), [])
            # Reopening the index (service restart) keeps the mapping.
            self.assertEqual(DshModelRuntime([profile], python=sys.executable, home=home)
                             .agent_runs('tenant-a', 'task-1')[0]['run_id'], rows[0]['run_id'])

    def test_restart_recovery_closes_orphaned_runs_without_killing_foreign_pids(self):
        with tempfile.TemporaryDirectory() as home:
            runtime = DshModelRuntime(python=sys.executable, home=home)
            runtime.runs.start('retrieval-orphan', principal='p', tenant='tenant-a',
                               scope={'task_id': 'task-1', 'revision': 1}, model_profile='text')
            # Our own pid is not a DSH worker; recovery must not signal it.
            runtime.runs.attach_process('retrieval-orphan', os.getpid())
            legacy = Path(home) / 'p' / 'retrieval-legacy'
            legacy.mkdir(parents=True)
            (legacy / 'audit.json').write_text(json.dumps({'run_id': 'retrieval-legacy', 'status': 'running'}))
            done = Path(home) / 'p' / 'retrieval-done'
            done.mkdir()
            (done / 'audit.json').write_text(json.dumps({'status': 'proposal_received'}))
            with patch('dsh_retrieval.adapters.dsh.runs.os.killpg') as kill:
                restarted = DshModelRuntime(python=sys.executable, home=home)
                self.assertEqual(restarted.recover(), {'runs': 1, 'audits': 1})
            kill.assert_not_called()
            row, = restarted.agent_runs('tenant-a', 'task-1')
            self.assertEqual((row['status'], row['error_code']), ('interrupted', 'SERVICE_RESTARTED'))
            self.assertEqual(json.loads((legacy / 'audit.json').read_text())['status'], 'interrupted')
            self.assertEqual(json.loads((done / 'audit.json').read_text())['status'], 'proposal_received')
            self.assertEqual(restarted.recover(), {'runs': 0, 'audits': 0})

    def test_recovery_stops_an_orphaned_worker_process_group(self):
        with tempfile.TemporaryDirectory() as home:
            runtime = DshModelRuntime(python=sys.executable, home=home)
            runtime.runs.start('retrieval-orphan', principal='p', tenant='t', scope={}, model_profile='text')
            runtime.runs.attach_process('retrieval-orphan', 4242)
            with patch('dsh_retrieval.adapters.dsh.runs._is_worker', return_value=True), \
                    patch('dsh_retrieval.adapters.dsh.runs.os.killpg') as kill:
                runtime.recover()
            kill.assert_called_once()
            self.assertEqual(kill.call_args.args[0], 4242)


@unittest.skipUnless(os.environ.get('DSH_TEST_PYTHON'), 'opt-in pinned native DSH integration')
class NativeRuntimeTest(unittest.TestCase):
    def test_real_runtime_mcp_tools_and_provider_stream(self):
        """Real SDK/native DSH + MCP, deterministic local model (not a live model)."""
        calls = []
        inline = False
        reject_submission = False
        expansion = False
        natural_text = None
        proposal = {'action': 'clarify', 'question': '需要多少张图片？', 'reply': '需要多少张图片？'}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(body)
                number = len(calls)
                if natural_text is not None:
                    delta, finish = {'role': 'assistant', 'content': natural_text}, 'stop'
                elif number <= (1 if inline else 2):
                    name = ('mcp__retrieval__update_request_draft' if inline else
                            'mcp__retrieval__get_request_context' if number == 1 else 'mcp__retrieval__submit_proposal')
                    if expansion and number == 2:
                        name = 'mcp__retrieval__save_expansion_draft'
                    if inline and reject_submission:
                        name = 'mcp__retrieval__save_expansion_draft'
                    args = {} if not inline and number == 1 else {'proposal': proposal}
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
                config = ModelConfig('test', 'test', 'https://example.invalid/v1', 'fake-not-a-secret',
                                     max_tokens=12000)
                # Local deterministic fixture only; production still requires HTTPS.
                object.__setattr__(config, 'base_url', f'http://127.0.0.1:{server.server_port}/v1')
                profile = ModelProfile('text', 'llm', config)
                runtime = DshModelRuntime([profile], python=os.environ['DSH_TEST_PYTHON'], home=home, timeout=45)
                runtime.check_installation()
                result = runtime._request(profile, 'Return action, question and reply JSON.', '找猫图片')
                self.assertEqual(result, proposal)
                self.assertEqual(len(calls), 2)
                # Per-model output budget reaches the provider request through the patch.
                self.assertEqual(calls[0].get('max_tokens'), 12000)
                self.assertNotIn('temperature', calls[0])
                names = {item['function']['name'] for item in calls[0]['tools']}
                self.assertEqual(names, {'mcp__retrieval__get_request_context',
                                         'mcp__retrieval__submit_proposal'})
                self.assertNotIn('dsh_session_log', calls[0])
                self.assertNotIn('dsh_plugin_packages', calls[0])
                self.assertIn('找猫图片', json.dumps(calls[1], ensure_ascii=False))
                audit = json.loads(next(Path(home).glob('*/*/audit.json')).read_text())
                self.assertEqual(audit['status'], 'proposal_received')
                self.assertEqual(audit['finish_reason'], 'proposal_submitted')
                self.assertIn('tool/result', audit['event_types'])
                calls.clear()
                inline = True
                from dsh_retrieval.dialogue import PROMPT
                result = runtime._request(profile, PROMPT, '想要猫和狗的照片，12张')
                self.assertEqual(result, proposal)
                self.assertEqual(len(calls), 1)
                first_input = json.dumps(calls[0]['messages'], ensure_ascii=False)
                self.assertIn('想要猫和狗的照片，12张', first_input)
                self.assertIn('context_delivery', first_input)
                self.assertEqual({tool['function']['name'] for tool in calls[0]['tools']},
                                 {'mcp__retrieval__update_request_draft'})
                calls.clear()
                reject_submission = True
                with self.assertRaises(ServiceError) as rejected:
                    runtime._request(profile, PROMPT, '找猫图片，20张')
                self.assertEqual(rejected.exception.code, 'DSH_TURN_FAILED')
                calls.clear()
                inline, reject_submission, expansion = False, False, True
                from dsh_retrieval.model_runtime import EXPANSION_PROMPT
                self.assertEqual(runtime._request(profile, EXPANSION_PROMPT, '生成猫的检索词'), proposal)
                self.assertEqual(len(calls), 2)
                calls.clear()
                inline, expansion = True, False
                natural_text = '可以先从画面内容聊起，数量确定后再整理检索词。'
                from dsh_retrieval.dialogue import PROMPT
                answer = runtime._request(profile, PROMPT, '你好，我想先了解怎么找素材')
                self.assertEqual(answer['action'], 'answer')
                self.assertEqual(answer['reply'], natural_text)
                self.assertIsNone(answer['target_count'])
                self.assertEqual(len(calls), 1)
                calls.clear()
                from dsh_retrieval.model_runtime import FOLLOWUP_ROUTING_PROMPT
                answer = runtime._request(profile, FOLLOWUP_ROUTING_PROMPT, '这些词是怎么选的？')
                self.assertEqual(answer['kind'], 'answer')
                self.assertEqual(len(calls), 1)
                self.assertEqual({tool['function']['name'] for tool in calls[0]['tools']},
                                 {'mcp__retrieval__submit_proposal'})
                calls.clear()
                tuned = ModelConfig('test', 'glm-5.3', 'https://example.invalid/v1', 'fake', max_tokens=12000)
                object.__setattr__(tuned, 'base_url', config.base_url)
                answer = runtime._request(ModelProfile('glm', 'llm', tuned), PROMPT, '你好')
                self.assertEqual(answer['action'], 'answer')
                self.assertEqual(calls[0].get('reasoning_effort'), 'low')
                calls.clear()
                natural_text = '我已经把目标改成3张。'
                with self.assertRaises(ServiceError) as fabricated:
                    runtime._request(profile, PROMPT, '为什么这样选词？')
                self.assertEqual(fabricated.exception.code, 'MODEL_OUTPUT_INVALID')
                natural_text = None
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
