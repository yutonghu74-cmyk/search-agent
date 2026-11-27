from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository, encode, decode
from dsh_retrieval.api import create_handler
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.request_numbers import issue_number, normalize_number
from draft_helpers import BODY, confirmation, make_service


class RequestNumberTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'tasks.sqlite3'
        self.repo = SQLiteTaskRepository(self.path); self.service = make_service(self.repo)
        self.actor = Actor('tenant', 'owner')

    def create(self, key='one'):
        return self.service.create_task(self.actor, key,
            confirmation(self.service, self.actor, key='draft-' + key))

    def test_number_uses_shanghai_creation_time_and_retries_random_collision(self):
        fixed = datetime(2026, 11, 26, 16, 0, 1, tzinfo=UTC)
        with patch('dsh_retrieval.request_numbers.secrets.choice', side_effect=list('AAAAAABBBBBB')):
            number = issue_number(fixed, {'REQ-20261127-000001-AAAAAA'})
        self.assertEqual(number, 'REQ-20261127-000001-BBBBBB')
        self.assertEqual(normalize_number('  ' + number.lower() + '  '), number)
        with self.assertRaises(ServiceError):normalize_number('REQ-20260230-000001-AAAAAA')
        with patch('dsh_retrieval.request_numbers.secrets.choice', return_value='A'):
            with self.assertRaises(ServiceError):issue_number(fixed, {'REQ-20261127-000001-AAAAAA'})

    def test_draft_has_no_number_and_confirmation_replay_and_restart_keep_it(self):
        body = confirmation(self.service, self.actor)
        draft = self.service.get_draft(self.actor, 'demo', body['draft_id'])
        self.assertNotIn('request_number', draft)
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM task_request_numbers').fetchone()[0], 0)
        first, replayed = self.service.create_task(self.actor, 'confirm', body)
        self.assertFalse(replayed); self.assertRegex(first['request_number'], r'^REQ-\d{8}-\d{6}-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}$')
        replay, _ = self.service.create_task(self.actor, 'different-click', body)
        self.assertEqual((replay['task_id'], replay['request_number']), (first['task_id'], first['request_number']))
        restored = make_service(SQLiteTaskRepository(self.path))
        replay, repeated = restored.create_task(self.actor, 'confirm', body)
        self.assertTrue(repeated); self.assertEqual(replay['request_number'], first['request_number'])
        self.assertEqual(restored.lookup_request_number(self.actor, 'demo', first['request_number'])['task_id'], first['task_id'])

    def test_sqlite_collision_is_retried_and_unique_index_is_enforced(self):
        fixed = datetime(2026, 11, 27, tzinfo=UTC)
        with patch('dsh_retrieval.adapters.memory.datetime') as clock:
            clock.now.return_value = fixed
            with patch('dsh_retrieval.request_numbers.secrets.choice', return_value='A'):
                first, _ = self.create('first')
            with patch('dsh_retrieval.request_numbers.secrets.choice', side_effect=list('AAAAAABBBBBB')):
                second, _ = self.create('second')
        self.assertNotEqual(first['request_number'], second['request_number'])
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO task_request_numbers VALUES (?,?,?,?,?)',
                    (first['request_number'], 'rt_foreign', 'other', 'owner', 'demo'))
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM task_request_numbers').fetchone()[0], 2)

    def test_concurrent_confirmations_make_one_number_and_distinct_requests_are_unique(self):
        body = confirmation(self.service, self.actor)
        def same(_):return make_service(SQLiteTaskRepository(self.path)).create_task(self.actor, 'same', body)
        with ThreadPoolExecutor(max_workers=4) as pool:results = list(pool.map(same, range(8)))
        self.assertEqual(len({r[0]['request_number'] for r in results}), 1)
        self.assertEqual(sum(not r[1] for r in results), 1)
        bodies = [confirmation(self.service, self.actor, key='parallel-' + str(i)) for i in range(4)]
        def distinct(i):return make_service(SQLiteTaskRepository(self.path)).create_task(self.actor, 'distinct-' + str(i), bodies[i])[0]
        with ThreadPoolExecutor(max_workers=4) as pool:tasks = list(pool.map(distinct, range(4)))
        self.assertEqual(len({r['request_number'] for r in tasks}), 4)
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM task_request_numbers').fetchone()[0], 5)

    def test_failed_confirmation_leaves_no_number_and_edit_does_not_change_number(self):
        body = confirmation(self.service, self.actor)
        with self.assertRaises(ServiceError):self.service.create_task(self.actor, 'bad', {**body, 'expected_draft_version':99})
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM task_request_numbers').fetchone()[0], 0)
        task, _ = self.service.create_task(self.actor, 'good', body)
        draft, _ = self.service.save_feedback(self.actor, task['task_id'], 'change', {
            'text':'补充要求：画面清晰', 'scope':'all',
            'expected_revision':1, 'expected_task_version':task['task_version']})
        updated, _ = self.service.confirm_feedback(self.actor, task['task_id'], 'confirm-change', {
            'draft_id':draft['draft_id'], 'expected_draft_version':draft['draft_version'],
            'proposal_digest':draft['proposal_digest'], 'expected_task_version':task['task_version'], 'action':'confirm'})
        self.assertEqual(updated['task']['request_number'], task['request_number'])
        self.assertEqual(updated['task']['task_id'], task['task_id'])

    def test_legacy_decode_has_no_generated_number_and_delete_removes_lookup(self):
        task, _ = self.create()
        value = encode(self.repo.get(self.actor, task['task_id']))
        value['value'].pop('request_number'); value['value'].pop('conversation_id')
        legacy = decode(value)
        self.assertIsNone(legacy.request_number); self.assertNotIn('request_number', legacy.to_summary())
        self.repo.hard_delete_conversation(self.actor, task['conversation_id'])
        with self.assertRaises(ServiceError):self.service.lookup_request_number(self.actor, 'demo', task['request_number'])

    def test_http_lookup_is_scoped_and_does_not_start_work(self):
        task, _ = self.create()
        handler = create_handler(self.service); self.addCleanup(handler.task_commands.close)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), worker.join(2)))
        path = f'/v1/retrieval/projects/demo/tasks/by-number/{task["request_number"].lower()}'
        def call(actor, suffix=''):
            req = Request(f'http://127.0.0.1:{server.server_port}'+path+suffix,
                headers={'X-Tenant-ID':actor.tenant_id,'X-User-ID':actor.user_id})
            try:
                with urlopen(req) as r:return r.status,json.load(r)
            except HTTPError as e:return e.code,json.loads(e.read())
        status, found = call(self.actor)
        self.assertEqual(status, 200, found); self.assertEqual(found['task_id'], task['task_id'])
        self.assertEqual(found['request_number'], task['request_number'])
        self.assertEqual(call(Actor('tenant-a','user-a'))[0], 404)
        self.assertEqual(call(self.actor, '?extra=true')[0], 422)
        self.assertEqual(self.repo.get(self.actor, task['task_id']).task_version, 1)
        self.assertEqual(len(self.service.list_tasks(self.actor, 'demo')), 1)


class RequestNumberUiTest(unittest.TestCase):
    def test_display_copy_lookup_navigation_and_legacy_fallback(self):
        if not shutil.which('node'):self.skipTest('Node required')
        from dsh_retrieval.chat_ui import CHAT_JS
        from dsh_retrieval.production_board_ui import BOARD_JS
        source = CHAT_JS[CHAT_JS.index('function setTaskIdentity('):CHAT_JS.index('function chatStarted(')]
        source += BOARD_JS[BOARD_JS.index('function requestNumber('):BOARD_JS.index('function visible(')]
        script = r"""
const assert=require('node:assert/strict');
const number='REQ-20261127-143025-A7K9Q2',fields={'task-sidebar-id':{},'task-sidebar-id-label':{},'copy-request-number':{},status:{},'request-number-input':{value:number.toLowerCase()}},state={historyOpenToken:1};
let copied,opened,calls=0,cancelled=0,stale=false;
const funcs=new Function('$','state','navigator','api','cancelDialogueRequest','openTask',process.env.SOURCE+';return {setTaskIdentity,openRequestNumber,requestNumber}')(
 id=>fields[id],state,{clipboard:{writeText:async text=>copied=text}},async path=>{calls++;assert(path.endsWith(number));if(stale)state.historyOpenToken++;return {task_id:'rt_internal',request_number:number}},()=>cancelled++,async id=>opened=id);
(async()=>{funcs.setTaskIdentity({task_id:'rt_internal',request_number:number});assert.equal(fields['task-sidebar-id'].textContent,number);await fields['copy-request-number'].onclick();assert.equal(copied,number);
 await funcs.openRequestNumber();assert.equal(opened,'rt_internal');assert.equal(cancelled,1);
 opened=null;stale=true;await funcs.openRequestNumber();assert.equal(opened,null);
 fields['request-number-input'].value='123';await funcs.openRequestNumber();assert.equal(calls,2);
 funcs.setTaskIdentity({task_id:'rt_legacy'});assert.equal(fields['task-sidebar-id'].textContent,'rt_legacy');assert.equal(fields['copy-request-number'].hidden,true);
 assert.equal(funcs.requestNumber({task_id:'rt_internal',request_number:number}),number);
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node','-e',script],env={**os.environ,'SOURCE':source},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
