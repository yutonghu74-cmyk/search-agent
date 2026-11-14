from pathlib import Path
from tempfile import TemporaryDirectory
import os
import subprocess
import sys
import unittest
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.dialogue import DialogueCoordinator, DialogueStore
from dsh_retrieval.models import Actor
from dsh_retrieval.task_commands import ConversationCommandStore
from dsh_retrieval.errors import ServiceError
from test_dialogue import FakeRuntime, proposal


class Crash(BaseException):
    pass


class DialogueCommandsTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'dialogue.sqlite3'
        self.store = DialogueStore(self.path)
        self.addCleanup(lambda: self.store.close())
        self.commands = ConversationCommandStore(self.store)
        self.addCleanup(self.commands.close)
        self.actor = Actor('tenant', 'owner')
        self.projects = LocalProjectAccess([('tenant', 'owner', 'demo', 'default')])
        self.runtime = FakeRuntime(proposal('update', subject='狗', media_type='image', target_count=10),
                                   proposal('update', target_count=20))
        self.coordinator = DialogueCoordinator(self.store, self.runtime, self.projects)
        self.scope = self.commands.dialogue_scope('demo')
        self.body = {'session_id': None, 'expected_version': 0, 'message': '找10张狗图片',
                     'llm_id': 'text', 'phase': 'request'}

    def turn(self, body=None, key='turn'):
        body = body or self.body
        return self.commands.execute(self.actor, self.scope, key, body,
            lambda receipt: self.coordinator.turn(self.actor, 'demo', body, receipt=receipt))

    def test_first_turn_and_followup_replay_once_with_no_formal_task(self):
        first, replayed = self.turn()
        self.assertFalse(replayed)
        repeated, replayed = self.turn()
        self.assertTrue(replayed)
        self.assertEqual(first, repeated)
        self.assertEqual(len(self.runtime.calls), 1)
        body = {**self.body, 'session_id': first['session_id'], 'expected_version': first['version'],
                'message': '改成20张'}
        second, _ = self.turn(body, 'second')
        self.assertEqual(second['version'], first['version'] + 1)
        self.assertEqual(second['request']['target_count'], 20)
        self.turn(body, 'second')
        self.assertEqual(len(self.store.messages(self.actor, 'demo', first['session_id'])), 4)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM dialogue_sessions').fetchone()[0], 1)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM task_dialogue_turns').fetchone()[0], 0)

    def test_http_receipts_are_scoped_and_obsolete_replies_do_not_revert_a_chat(self):
        from dsh_retrieval.api import create_handler
        from dsh_retrieval.adapters.memory import InMemoryTaskRepository
        from draft_helpers import make_service
        service = make_service(InMemoryTaskRepository())
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(service, dialogue=self.coordinator))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def call(path, body=None, key='first', owner='owner'):
            request = Request(f'http://127.0.0.1:{server.server_port}/v1/retrieval/projects/demo/{path}',
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Content-Type': 'application/json', 'X-Tenant-Id': 'tenant',
                         'X-User-Id': owner, 'Idempotency-Key': key})
            try:
                with urlopen(request) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.load(error)
        try:
            status, first = call('dialogue-command', self.body)
            self.assertEqual(status, 200, first)
            status, replay = call('dialogue-command', self.body)
            self.assertEqual(status, 200, replay)
            self.assertEqual(first['session_id'], replay['session_id'])
            self.assertEqual(first['version'], replay['version'])
            self.assertEqual(len(self.runtime.calls), 1)
            status, receipt = call('dialogue-commands/first')
            self.assertEqual(status, 200, receipt)
            self.assertEqual(receipt['schema_version'], 'dialogue-command@1')
            self.assertTrue(receipt['result']['is_current'])
            status, _ = call('dialogue-commands/first', owner='other')
            self.assertNotEqual(status, 200)
            status, _ = call('dialogue-command', {**self.body, 'message': '另一条消息'})
            self.assertEqual(status, 409)
            second_body = {**self.body, 'session_id': first['session_id'], 'expected_version': first['version'],
                           'message': '改成20张'}
            status, second = call('dialogue-command', second_body, key='second')
            self.assertEqual(status, 200, second)
            status, old = call('dialogue-command', self.body)
            self.assertEqual(status, 200, old)
            self.assertFalse(old['is_current'])
            self.assertEqual(len(self.store.messages(self.actor, 'demo', first['session_id'])), 4)
            self.assertEqual(service.list_tasks(self.actor, 'demo'), ())
            self.store.delete(self.actor, 'demo', first['session_id'])
            status, _ = call('dialogue-command', self.body)
            self.assertEqual(status, 404)
            self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM dialogue_sessions').fetchone()[0], 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_async_http_accepts_before_model_finishes_and_duplicate_does_not_start_another_turn(self):
        from dsh_retrieval.api import create_handler
        from dsh_retrieval.adapters.memory import InMemoryTaskRepository
        from draft_helpers import make_service
        started, release = threading.Event(), threading.Event()
        base = self.runtime
        class SlowRuntime:
            def interpret_dialogue_turn(self, *args, **kwargs):
                started.set()
                if not release.wait(10):
                    raise AssertionError('test release timed out')
                return base.interpret_dialogue_turn(*args, **kwargs)
        self.coordinator.runtime = SlowRuntime()
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(
            make_service(InMemoryTaskRepository()), dialogue=self.coordinator))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def send():
            request = Request(f'http://127.0.0.1:{server.server_port}/v1/retrieval/projects/demo/dialogue-command',
                data=json.dumps(self.body).encode(), headers={'Content-Type': 'application/json',
                'X-Tenant-Id': 'tenant', 'X-User-Id': 'owner', 'Idempotency-Key': 'async', 'Prefer': 'respond-async'})
            with urlopen(request, timeout=2) as response:
                return response.status, json.load(response)
        try:
            status, accepted = send()
            self.assertEqual(status, 202, accepted)
            self.assertEqual(accepted['status'], 'understanding')
            self.assertTrue(started.wait(2))
            status, duplicate = send()
            self.assertEqual(status, 202, duplicate)
            self.assertEqual(duplicate['attempts'], 1)
            release.set()
            import time
            for _ in range(100):
                receipt = self.commands.read(self.actor, self.scope, 'async')
                if receipt['status'] == 'completed':
                    break
                time.sleep(.01)
            self.assertEqual(receipt['status'], 'completed', receipt)
            self.assertEqual(len(self.runtime.calls), 1)
            self.assertEqual(len(self.store.messages(self.actor, 'demo', receipt['result']['session_id'])), 2)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_prepared_turn_survives_restart_without_a_second_model_call(self):
        def crash(receipt):
            self.coordinator.turn(self.actor, 'demo', self.body, receipt=receipt)
            raise Crash()
        with self.assertRaises(Crash):
            self.commands.execute(self.actor, self.scope, 'turn', self.body, crash)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM dialogue_sessions').fetchone()[0], 0)
        self.store.close()
        self.store = DialogueStore(self.path)
        self.commands = ConversationCommandStore(self.store)
        self.coordinator = DialogueCoordinator(self.store, self.runtime, self.projects)
        self.commands.recover_interrupted()
        result, _ = self.turn()
        self.assertEqual(result['version'], 1)
        self.assertEqual(len(self.runtime.calls), 1)
        self.assertEqual(len(self.store.messages(self.actor, 'demo', result['session_id'])), 2)

    def test_process_exit_inside_session_write_rolls_back_session_and_transcript(self):
        script = r'''
import os,sys
from pathlib import Path
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.dialogue import DialogueStore,DialogueCoordinator
from dsh_retrieval.models import Actor
from dsh_retrieval.task_commands import ConversationCommandStore
from test_dialogue import FakeRuntime,proposal
store=DialogueStore(Path(sys.argv[1]));commands=ConversationCommandStore(store)
actor=Actor('tenant','owner');projects=LocalProjectAccess([('tenant','owner','demo','default')])
runtime=FakeRuntime(proposal('update',subject='狗',media_type='image',target_count=10))
coordinator=DialogueCoordinator(store,runtime,projects)
original=store._save
def crash(*args,**kwargs):
 original(*args,**kwargs)
 os._exit(24)
store._save=crash
body={'session_id':None,'expected_version':0,'message':'找10张狗图片','llm_id':'text','phase':'request'}
commands.execute(actor,commands.dialogue_scope('demo'),'turn',body,lambda receipt:coordinator.turn(actor,'demo',body,receipt=receipt))
'''
        result = subprocess.run([sys.executable, '-c', script, str(self.path)],
                                capture_output=True, text=True, env=os.environ.copy(), timeout=10)
        self.assertEqual(result.returncode, 24, result.stderr)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM dialogue_sessions').fetchone()[0], 0)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM dialogue_transcript').fetchone()[0], 0)
        self.commands.recover_interrupted()
        turn, _ = self.turn()
        self.assertEqual(len(self.runtime.calls), 0)
        self.assertEqual(len(self.store.messages(self.actor, 'demo', turn['session_id'])), 2)

    def test_stale_prepared_turn_cannot_overwrite_a_newer_version(self):
        first, _ = self.turn()
        body = {**self.body, 'session_id': first['session_id'], 'expected_version': first['version'], 'message': '改成20张'}
        def crash(receipt):
            self.coordinator.turn(self.actor, 'demo', body, receipt=receipt)
            raise Crash()
        with self.assertRaises(Crash):
            self.commands.execute(self.actor, self.scope, 'second', body, crash)
        self.coordinator.turn(self.actor, 'demo', {**body, 'message': '改成30张'})
        self.commands.recover_interrupted()
        with self.assertRaises(ServiceError) as caught:
            self.turn(body, 'second')
        self.assertEqual(caught.exception.code, 'DIALOGUE_CONFLICT')
        self.assertEqual(len(self.store.messages(self.actor, 'demo', first['session_id'])), 4)


if __name__ == '__main__':
    unittest.main()
