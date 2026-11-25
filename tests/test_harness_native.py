"""Opt-in real DSH SDK/native runtime with a deterministic local model endpoint."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import replace

import test_harness_port as fixtures
from dsh_retrieval.adapters.harness.dsh import DshAdapter
from dsh_retrieval.adapters.harness.dsh_releases import LEGACY_DSH, CANDIDATE_DSH


@unittest.skipUnless(os.environ.get('DSH_TEST_PYTHON'), 'real DSH requires an explicit isolated interpreter')
class NativeHarnessTest(unittest.TestCase):
    python_env = 'DSH_TEST_PYTHON'
    sdk_release = LEGACY_DSH
    def test_real_sdk_two_roles_tool_inventory_and_rejected_source(self):
        for role, reject_source in (('retrieval', False), ('cleaning', False), ('retrieval', True)):
            with self.subTest(role=role, reject_source=reject_source):
                self.run_role(role, reject_source)

    def run_role(self, role, reject_source):
        case = fixtures.HarnessPortTest(); case.setUp()
        calls = []
        if role == 'cleaning':
            case.stage = replace(case.stage, role='cleaning'); case.current = case.stage
        tool_name = 'submit_retrieval_strategy' if role == 'retrieval' else 'submit_processing_plan'
        proposal = case.proposal if role == 'retrieval' else {
            'proposal': {'steps': [{'requirement_id': 'watermark-tag', 'step_id': 'tag'}]}}
        if reject_source: proposal['proposal']['binding_id'] = 'forbidden'

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(body)
                number = len(calls)
                if number <= 2:
                    name = 'get_stage_context' if number == 1 else tool_name
                    args = {} if number == 1 else proposal
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'call-{number}',
                        'type': 'function', 'function': {'name': 'mcp__business__' + name,
                                                       'arguments': json.dumps(args)}}]}
                    finish = 'tool_calls'
                else:
                    delta, finish = {'role': 'assistant', 'content': 'planning turn ended'}, 'stop'
                chunks = [{'id': 'test', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'test',
                           'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                          {'id': 'test', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'test',
                           'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                           'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}]
                payload = (''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n').encode()
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='hn-', dir='/tmp') as home:
                native_options = {}
                if self.sdk_release.runtime_mode == 'npm':
                    native_options = dict(dsh_bin=os.environ['DSH_CANDIDATE_TEST_DSH_BIN'],
                        node=os.environ['DSH_CANDIDATE_TEST_NODE'], npm_lock=os.environ['DSH_CANDIDATE_TEST_NPM_LOCK'])
                adapter = DshAdapter(**native_options, python=os.environ[self.python_env], home=home,
                    release=self.sdk_release, adapter_id=self.sdk_release.release_id,
                    model_environment=lambda _: {'RETRIEVAL_MODEL_KEY': 'test-placeholder',
                        'RETRIEVAL_MODEL_URL': f'http://127.0.0.1:{server.server_port}/v1',
                        'RETRIEVAL_MODEL_ID': 'test', 'RETRIEVAL_MAX_TOKENS': '12000'})
                self.assertTrue(adapter.probe().available)
                from dsh_retrieval.adapters.harness.composition import build_planning_runner
                from dsh_retrieval.harness.stage_tools import StageAuthority
                item = dict(adapter_id=adapter.adapter_id, sdk_release=self.sdk_release.release_id,
                            python=os.environ[self.python_env], home=home)
                item.update(native_options)
                runner = build_planning_runner({
                    'schema_version': 'harness-routes@1', 'releases': {'tested-release': item},
                    'roles': {'retrieval': 'tested-release', 'cleaning': 'tested-release'}},
                    authority=lambda inv: StageAuthority(case.actor, case.task, case.current,
                        case.spec, 5, (case.binding,), (case.cap,), ('facts-1',)),
                    model_environment=adapter.model_environment,
                    model_binding=lambda ref: case.stage.model_binding_digest)
                case.deadline = time.time() + 40
                handle = case.start(runner)
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    events = runner.poll(handle)
                    if events and events[-1].kind in ('finished', 'failed'): break
                    time.sleep(.05)
                else: self.fail('native SDK did not complete within deadline')
                try:
                    self.assertEqual(events[-1].kind, 'failed' if reject_source else 'finished', events)
                    self.assertEqual(len(calls), 3, events)
                    expected = {'mcp__business__get_stage_context', 'mcp__business__' + tool_name,
                                'mcp__business__submit_stage_report'}
                    self.assertEqual({t['function']['name'] for t in calls[0]['tools']}, expected)
                    self.assertEqual(calls[0]['max_tokens'], 12000)
                    self.assertNotIn('dsh_session_log', calls[0])
                    self.assertNotIn('dsh_plugin_packages', calls[0])
                    if reject_source:
                        self.assertFalse(any(e.kind == 'proposal_submitted' for e in events))
                    else:
                        self.assertEqual(runner.outputs(handle)[0]['arguments'], proposal)
                finally: runner.close(handle)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


@unittest.skipUnless(os.environ.get('DSH_CANDIDATE_TEST_PYTHON'), 'candidate SDK requires its own isolated interpreter')
class CandidateNativeHarnessTest(unittest.TestCase):
    python_env = 'DSH_CANDIDATE_TEST_PYTHON'
    sdk_release = CANDIDATE_DSH
    run_role = NativeHarnessTest.run_role
    test_real_sdk_two_roles_tool_inventory_and_rejected_source = NativeHarnessTest.test_real_sdk_two_roles_tool_inventory_and_rejected_source


if __name__ == '__main__': unittest.main()
