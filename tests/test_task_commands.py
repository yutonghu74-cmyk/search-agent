from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.dialogue import DialogueStore
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.task_commands import TaskCommandStore
from dsh_retrieval.task_conversation import TaskConversationCoordinator
from draft_helpers import BODY, confirmation, make_service


class Crash(BaseException):
    pass


class TaskCommandsTest(unittest.TestCase):
    def test_async_pool_is_bounded_and_rejected_command_can_retry(self):
        import time
        commands = TaskCommandStore(self.store, max_workers=1, max_pending=2)
        started, release = threading.Event(), threading.Event()
        observed = []
        def run(receipt):
            observed.append(receipt.scope[-1]); started.set()
            if not release.wait(5):
                raise AssertionError('gate timed out')
            receipt.checkpoint({'task': self.task})
            return {'kind': 'answer', 'reply': '已核对', 'queue_wait_ms': receipt.queue_wait_ms}
        try:
            commands.submit(self.actor, self.task['task_id'], 'first', self.request, run)
            self.assertTrue(started.wait(2))
            commands.submit(self.actor, self.task['task_id'], 'second', self.request, run)
            with self.assertRaises(ServiceError) as caught:
                commands.submit(self.actor, self.task['task_id'], 'third', self.request, run)
            self.assertEqual(caught.exception.code, 'COMMAND_QUEUE_FULL')
            receipt, replayed = commands.submit(self.actor, self.task['task_id'], 'first', self.request, run)
            self.assertTrue(replayed); self.assertEqual(receipt['attempts'], 1)
            self.assertEqual(observed, ['first'])
            self.assertEqual(commands.read(self.actor, self.task['task_id'], 'third')['status'], 'failed')
            release.set()
            for _ in range(200):
                if commands.read(self.actor, self.task['task_id'], 'second')['status'] == 'completed':
                    break
                time.sleep(.01)
            commands.submit(self.actor, self.task['task_id'], 'third', self.request, run, retry_failed=True)
        finally:
            release.set(); commands.close()
        self.assertEqual(observed, ['first', 'second', 'third'])
        self.assertEqual(commands.read(self.actor, self.task['task_id'], 'third')['attempts'], 2)

    def test_http_task_async_accepts_before_model_and_duplicates_do_not_repeat(self):
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from types import SimpleNamespace
        import json, time
        from dsh_retrieval.api import create_handler
        started, release = threading.Event(), threading.Event()
        calls = []
        class SlowRuntime:
            def conversation_runtime(self): return self
            def classify_followup(self, *args, **kwargs):
                calls.append(1); started.set()
                if not release.wait(5): raise AssertionError('gate timed out')
                return {'kind': 'answer', 'reply': '当前条件已说明。'}
        handler = create_handler(self.service, model_runtime=SlowRuntime(), dialogue=SimpleNamespace(store=self.store))
        handler.task_commands = TaskCommandStore(self.store, max_workers=1, max_pending=1)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        body = {'text': '解释当前条件', 'llm_id': 'text', 'expected_revision': 1,
                'expected_task_version': self.task['task_version']}
        base = f'http://127.0.0.1:{server.server_port}/v1/retrieval/tasks/{self.task["task_id"]}'
        def call(suffix, payload=None, owner='owner', command_key='async-task'):
            request = Request(base + suffix, data=json.dumps(payload).encode() if payload is not None else None,
                headers={'Content-Type': 'application/json', 'X-Tenant-Id': 'tenant', 'X-User-Id': owner,
                         'Idempotency-Key': command_key, 'Prefer': 'respond-async'})
            with urlopen(request, timeout=2) as response: return response.status, json.load(response)
        try:
            start = time.perf_counter(); status, receipt = call('/conversation-turn', body)
            self.assertEqual(status, 202, receipt)
            self.assertFalse(release.is_set()); self.assertTrue(started.wait(2))
            self.assertLess(time.perf_counter()-start, 2)
            status, duplicate = call('/conversation-turn', body)
            self.assertEqual(status, 202); self.assertEqual(duplicate['attempts'], 1)
            status, fast = call('/conversation-turn', {**body, 'text': '再加5张'}, command_key='fast-quantity')
            self.assertEqual(status, 200, fast)
            self.assertEqual(fast['feedback_draft']['proposal']['query_spec']['target_count'], 15)
            self.assertEqual(calls, [1]); self.assertFalse(release.is_set())
            with self.assertRaises(HTTPError) as denied:
                call('/conversation-turns/async-task', owner='intruder')
            self.assertEqual(denied.exception.code, 404)
            release.set()
            for _ in range(100):
                _, receipt = call('/conversation-turns/async-task')
                if receipt['status'] == 'completed': break
                time.sleep(.01)
            self.assertEqual(receipt['status'], 'completed', receipt)
            self.assertTrue(receipt['result']['is_current'])
            self.assertIn('queue_wait_ms', receipt['result']['timings'])
            self.assertEqual(calls, [1])
            draft = fast['feedback_draft']
            self.service.confirm_feedback(self.actor, self.task['task_id'], 'advance-version', {
                'draft_id': draft['draft_id'], 'expected_draft_version': draft['draft_version'],
                'proposal_digest': draft['proposal_digest'], 'expected_task_version': self.task['task_version'],
                'action': 'confirm'})
            _, repeated = call('/conversation-turn', body)
            self.assertTrue(repeated['command_replayed']); self.assertEqual(calls, [1])
            self.assertFalse(repeated['result']['is_current'])
            self.assertEqual(len(self.store.task_history(self.actor, 'demo', self.task['task_id'])), 4)
        finally:
            release.set(); server.shutdown(); server.server_close(); thread.join(); handler.task_commands.close()

    def test_http_mixed_edits_bind_pending_quantity_and_replay_without_model(self):
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        from types import SimpleNamespace
        import json
        from dsh_retrieval.api import create_handler
        from dsh_retrieval.model_runtime import ModelRuntime, ModelProfile
        from dsh_retrieval.intent_model import ModelConfig
        from test_model_runtime import Transport
        outputs = [{'kind': 'requirement', 'target_count': None, 'requested_terms_per_group': None,
                    'count_edits': [{'target': 'images', 'labels': [], 'operation': 'set', 'value': 20}],
                    'cleaning_changes': [{'op': 'add', 'condition_id': None, 'source_text': '无水印', 'group_id': None}]},
                   ]
        from dsh_retrieval.requirement_changes import conditions_for
        from dsh_retrieval.expansion_models import preview_edited_groups
        rabbit_id = preview_edited_groups(None, [{'primary_label': '兔子', 'auxiliary_terms': []}])['plan']['groups'][0]['group_id']
        outputs.extend([
            {'kind': 'requirement', 'target_count': None, 'requested_terms_per_group': None,
             'retrieval_changes': [{'op': 'upsert', 'group_id': None, 'primary_label': '兔子',
                                   'auxiliary_terms': ['草地兔子'], 'source_text': '主体换成兔子，词用草地兔子'}],
             'cleaning_changes': [{'op': 'replace', 'condition_id': conditions_for(self.spec)[0]['condition_id'],
                                   'source_text': '兔子', 'group_id': None}]},
            {'kind': 'requirement', 'target_count': None, 'requested_terms_per_group': None,
             'retrieval_changes': [{'op': 'upsert', 'group_id': rabbit_id, 'primary_label': '兔子',
                                   'auxiliary_terms': ['奔跑兔子'], 'source_text': '词改为奔跑兔子'}],
             'cleaning_changes': [{'op': 'add', 'condition_id': None, 'source_text': '画面清晰', 'group_id': None}]}])
        transport = Transport(outputs)
        runtime = ModelRuntime([ModelProfile('text', 'llm', ModelConfig('fixture', 'text', 'https://fixture.invalid/v1', 'secret'))], opener=transport)
        handler = create_handler(self.service, model_runtime=runtime, dialogue=SimpleNamespace(store=self.store))
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))
        def call(text, key, ref=None):
            body = {'text': text, 'llm_id': 'text', 'expected_revision': self.task['confirmed_revision'],
                    'expected_task_version': self.task['task_version']}
            if ref:
                body['feedback_ref'] = {k: ref[k] for k in ('draft_id', 'draft_version', 'proposal_digest')}
            request = Request(f'http://127.0.0.1:{server.server_port}/v1/retrieval/tasks/{self.task["task_id"]}/conversation-turn',
                data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
                    'X-Tenant-Id': 'tenant', 'X-User-Id': 'owner', 'Idempotency-Key': key})
            with urlopen(request) as response:
                return json.load(response)
        first = call('总共20张，只要无水印', 'mixed-http-1')
        with (patch.object(handler, '_task_workspace', side_effect=AssertionError('unneeded execution read')),
              patch.object(self.store, 'task_history', side_effect=AssertionError('unneeded history read'))):
            second = call('再多5张', 'mixed-http-2', first['feedback_draft'])
        spec = second['feedback_draft']['proposal']['query_spec']
        self.assertEqual(spec['target_count'], 25)
        self.assertEqual(spec['cleaning_conditions'][-1]['text'], '无水印')
        self.assertEqual(second['cleaning_review']['cleaning_plan_preview']['target'], {'before': 10, 'after': 25})
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(second['timings']['counters']['explicit_requirement_hits'], 1)
        self.assertNotIn('model_initial', second['timings']['stages'])
        third = call('主体换成兔子，词用草地兔子', 'mixed-http-3', second['feedback_draft'])
        fourth = call('词改为奔跑兔子，加画面清晰', 'mixed-http-4', third['feedback_draft'])
        spec = fourth['feedback_draft']['proposal']['query_spec']
        self.assertEqual(spec['target_count'], 25)
        self.assertEqual(spec['grouped_expansion']['groups'][0]['auxiliary_terms'], ['奔跑兔子'])
        self.assertEqual([row['text'] for row in spec['cleaning_conditions']], ['兔子', '无水印', '画面清晰'])
        context = json.loads(transport.calls[-1]['messages'][1]['content'])
        self.assertEqual(context['current_groups'][0]['auxiliary_terms'], ['草地兔子'])
        self.assertNotIn('auxiliary_terms', context['task_context']['requirement_spec']['grouped_expansion']['groups'][0])
        self.assertEqual(self.service.get_task(self.actor, self.task['task_id'])['confirmed_revision'], 1)
        contexts = [json.loads(message['content']) for message in transport.calls[1]['messages']
                    if message['role'] == 'user' and message['content'].startswith('{')]
        self.assertEqual(contexts[0]['task_context']['target_count'], 25)
        self.assertEqual(self.service.get_task(self.actor, self.task['task_id'])['confirmed_revision'], 1)
        replay = call('再多5张', 'mixed-http-2', first['feedback_draft'])
        self.assertEqual(replay['feedback_draft']['draft_version'], 2)
        self.assertFalse(replay['is_current'])
        self.assertEqual(len(transport.calls), 3)

    def test_mixed_command_recovery_replays_both_changes_once(self):
        self.request = {'text': '多5张，加无水印，检索词换成明亮客厅'}
        self.intent = {'kind': 'requirement', 'count_edits': [
            {'target': 'images', 'labels': [], 'operation': 'add', 'value': 5}],
            'cleaning_changes': [{'op': 'add', 'condition_id': None, 'source_text': '无水印', 'group_id': None}],
            'retrieval_changes': [{'op': 'upsert', 'group_id': None, 'primary_label': '客厅',
                                   'auxiliary_terms': ['明亮客厅'], 'source_text': '检索词换成明亮客厅'}]}
        def crash(receipt):
            self.result(self.plan(receipt))
            raise Crash()
        with self.assertRaises(Crash):
            self.execute(crash)
        self.commands.recover_interrupted()
        self.service = make_service(SQLiteTaskRepository(self.task_path))
        self.coordinator = TaskConversationCoordinator(self.service)
        result, _ = self.execute(lambda receipt: self.result(receipt.plan))
        spec = result['feedback_draft']['proposal']['query_spec']
        self.assertEqual(spec['target_count'], 15)
        self.assertEqual(spec['cleaning_conditions'][-1]['text'], '无水印')
        self.assertEqual(spec['grouped_expansion']['groups'][0]['auxiliary_terms'], ['明亮客厅'])
        self.assertEqual(result['feedback_draft']['draft_version'], 1)
        replay, cached = self.execute(lambda receipt: self.fail('must not repeat add'))
        self.assertTrue(cached)
        self.assertEqual(replay, result)

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'dialogue.sqlite3'
        self.store = DialogueStore(self.path)
        self.addCleanup(lambda: self.store.close())
        self.commands = TaskCommandStore(self.store)
        self.addCleanup(self.commands.close)
        self.actor = Actor('tenant', 'owner')
        self.task_path = Path(self.directory.name) / 'tasks.sqlite3'
        self.service = make_service(SQLiteTaskRepository(self.task_path))
        body = deepcopy(BODY)
        body['query_spec']['rules'] = [{'rule_id': 'semantic-match', 'predicate_ref': 'image.semantic-match@1',
            'operator': 'eq', 'value': True, 'hardness': 'hard', 'origin': 'user_confirmed'}]
        self.task, _ = self.service.create_task(self.actor, 'create', confirmation(self.service, self.actor, body=body))
        self.spec = body['query_spec']
        self.coordinator = TaskConversationCoordinator(self.service)
        self.request = {'text': '增加无水印'}
        self.intent = {'kind': 'cleaning', 'cleaning_changes': [
            {'op': 'add', 'condition_id': None, 'source_text': '无水印', 'group_id': None}]}

    def execute(self, callback, key='turn', retry=False, request=None):
        return self.commands.execute(self.actor, self.task['task_id'], key, request or self.request,
                                     callback, retry_failed=retry)

    def plan(self, receipt):
        plan = self.coordinator.prepare(self.actor, self.task, self.spec, None,
                                        self.request['text'], 'feedback-command', self.intent)
        receipt.checkpoint(plan)
        return plan

    def result(self, plan):
        return {'task_id': self.task['task_id'], 'base_task_version': self.task['task_version'],
                **self.coordinator.commit(self.actor, plan)}

    def test_completed_receipt_survives_reopen_and_does_not_repeat_model_write_or_transcript(self):
        calls = []
        def run(receipt):
            calls.append('model')
            return self.result(self.plan(receipt))
        result, replayed = self.execute(run)
        self.assertFalse(replayed)
        self.store.close()
        self.store = DialogueStore(self.path)
        self.commands = TaskCommandStore(self.store)
        repeated, replayed = self.execute(lambda receipt: self.fail('must not reinterpret'))
        self.assertTrue(replayed)
        self.assertEqual(result, repeated)
        self.assertEqual(calls, ['model'])
        history = self.store.task_history(self.actor, 'demo', self.task['task_id'])
        self.assertEqual(len(history), 2)
        self.assertEqual(self.commands.read(self.actor, self.task['task_id'], 'turn')['status'], 'completed')

    def test_crash_after_business_write_replays_frozen_write_once(self):
        def crash(receipt):
            result = self.result(self.plan(receipt))
            self.assertEqual(result['feedback_draft']['draft_version'], 1)
            raise Crash()
        with self.assertRaises(Crash):
            self.execute(crash)
        self.assertEqual(self.commands.recover_interrupted(), 1)
        self.service = make_service(SQLiteTaskRepository(self.task_path))
        self.coordinator = TaskConversationCoordinator(self.service)
        result, replayed = self.execute(lambda receipt: self.result(receipt.plan))
        self.assertFalse(replayed)
        self.assertEqual(result['feedback_draft']['draft_version'], 1)
        self.assertEqual(len(result['feedback_draft']['proposal']['query_spec']['cleaning_conditions']), 2)
        self.assertEqual(len(self.store.task_history(self.actor, 'demo', self.task['task_id'])), 2)

    def test_abrupt_process_exit_between_task_commit_and_receipt_commit_recovers(self):
        script = r'''
import os,sys,json
from pathlib import Path
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.dialogue import DialogueStore
from dsh_retrieval.models import Actor
from dsh_retrieval.task_commands import TaskCommandStore
from dsh_retrieval.task_conversation import TaskConversationCoordinator
from draft_helpers import make_service
root=Path(sys.argv[1]);task_id=sys.argv[2];actor=Actor('tenant','owner')
service=make_service(SQLiteTaskRepository(root/'tasks.sqlite3'))
task=service.get_task(actor,task_id)
spec=service.current_read_binding(actor,task_id)[1]['query_spec']
coordinator=TaskConversationCoordinator(service)
commands=TaskCommandStore(DialogueStore(root/'dialogue.sqlite3'))
def crash(receipt):
 intent={'kind':'cleaning','cleaning_changes':[{'op':'add','condition_id':None,'source_text':'无水印','group_id':None}]}
 plan=coordinator.prepare(actor,task,spec,None,'增加无水印','process-feedback',intent)
 receipt.checkpoint(plan)
 coordinator.commit(actor,plan)
 os._exit(23)
commands.execute(actor,task_id,'process-turn',{'text':'增加无水印'},crash)
'''
        result = subprocess.run([sys.executable, '-c', script, self.directory.name, self.task['task_id']],
                                capture_output=True, text=True, env=os.environ.copy(), timeout=10)
        self.assertEqual(result.returncode, 23, result.stderr)
        before = self.service.get_task(self.actor, self.task['task_id'])['pending_draft']
        self.assertEqual(before['draft_version'], 1)
        self.assertEqual(self.commands.recover_interrupted(), 1)
        recovered, _ = self.execute(lambda receipt: self.result(receipt.plan), key='process-turn')
        self.assertEqual(recovered['feedback_draft']['draft_id'], before['draft_id'])
        self.assertEqual(recovered['feedback_draft']['draft_version'], 1)
        self.assertEqual(len(self.store.task_history(self.actor, 'demo', self.task['task_id'])), 2)

    def test_crash_before_business_write_resumes_checkpoint_without_reinterpretation(self):
        def crash(receipt):
            self.plan(receipt)
            raise Crash()
        with self.assertRaises(Crash):
            self.execute(crash)
        self.assertIsNone(self.service.get_task(self.actor, self.task['task_id'])['pending_draft'])
        self.commands.recover_interrupted()
        result, _ = self.execute(lambda receipt: self.result(receipt.plan))
        self.assertEqual(result['feedback_draft']['draft_version'], 1)

    def test_interrupted_understanding_requires_explicit_retry_and_old_owner_is_fenced(self):
        receipts = []
        def crash(receipt):
            receipts.append(receipt)
            raise Crash()
        with self.assertRaises(Crash):
            self.execute(crash)
        self.commands.recover_interrupted()
        with self.assertRaises(ServiceError) as caught:
            self.execute(lambda receipt: self.fail('implicit retry'))
        self.assertEqual(caught.exception.code, 'COMMAND_INTERRUPTED')
        with self.assertRaises(ServiceError):
            self.plan(receipts[0])
        self.execute(lambda receipt: self.result(self.plan(receipt)), retry=True)

    def test_concurrent_duplicate_is_not_executed_and_payload_or_actor_cannot_reuse_receipt(self):
        started, release = threading.Event(), threading.Event()
        errors = []
        def run(receipt):
            started.set()
            if not release.wait(5):
                raise AssertionError('test release timed out')
            return self.result(self.plan(receipt))
        def execute():
            try:
                self.execute(run)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=execute)
        thread.start()
        self.assertTrue(started.wait(5))
        try:
            with self.assertRaises(ServiceError) as caught:
                self.execute(lambda receipt: self.fail('duplicate callback'))
            self.assertEqual(caught.exception.code, 'COMMAND_RUNNING')
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(errors)
        with self.assertRaises(ServiceError) as caught:
            self.execute(lambda receipt: None, request={'text': '另一条消息'})
        self.assertEqual(caught.exception.code, 'IDEMPOTENCY_CONFLICT')
        with self.assertRaises(ServiceError) as caught:
            self.commands.read(Actor('tenant', 'other'), self.task['task_id'], 'turn')
        self.assertEqual(caught.exception.code, 'NOT_FOUND')

    def test_failed_model_is_cached_until_explicit_retry(self):
        def fail(receipt):
            raise ServiceError('MODEL_REQUEST_FAILED', '模型暂不可用', 502, True)
        with self.assertRaises(ServiceError):
            self.execute(fail)
        with self.assertRaises(ServiceError):
            self.execute(lambda receipt: self.fail('unexpected retry'))
        result, _ = self.execute(lambda receipt: self.result(self.plan(receipt)), retry=True)
        self.assertEqual(result['kind'], 'cleaning')
        self.assertEqual(self.commands.read(self.actor, self.task['task_id'], 'turn')['attempts'], 2)

    def test_background_submission_keeps_actor_context(self):
        from contextvars import ContextVar
        identity = ContextVar('command-test-identity', default=None)
        token = identity.set('owner-scope')
        done = threading.Event()
        observed = []
        def run(receipt):
            observed.append(identity.get())
            result = self.result(self.plan(receipt))
            done.set()
            return result
        try:
            self.commands.submit(self.actor, self.task['task_id'], 'context', self.request, run)
        finally:
            identity.reset(token)
        self.assertTrue(done.wait(5))
        import time
        for _ in range(100):
            receipt = self.commands.read(self.actor, self.task['task_id'], 'context')
            if receipt['status'] == 'completed':
                break
            time.sleep(.01)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(observed, ['owner-scope'])


if __name__ == '__main__':
    unittest.main()
