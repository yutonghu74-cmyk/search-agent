import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from dsh_retrieval.chat_history import page
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.dialogue import DialogueStore
from dsh_retrieval.models import Actor
from dsh_retrieval.errors import ServiceError
from draft_helpers import make_service, BODY


class ChatHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.repo = SQLiteTaskRepository(Path(self.tmp.name)/'tasks.db')
        self.service = make_service(self.repo)
        self.actor = Actor('tenant-a','user-a')
        self.store = DialogueStore(); self.addCleanup(self.store.close)
        self.dialogue = SimpleNamespace(store=self.store)

    def test_pages_cover_more_than_old_hundred_and_never_read_ledger(self):
        for n in range(105):
            self.service.save_draft(self.actor,'demo',f'draft-{n}', BODY)
        with self.store.connection as db:
            for n in range(23):
                db.execute('INSERT INTO dialogue_sessions VALUES (?,?,?,?,?,?,?,?)',
                    (f'dlg_{n}', 'tenant-a','user-a','demo',1,
                     json.dumps({'messages':[{'role':'user','text':f'dialogue-{n}'}]}),
                     '2020-01-01', '2020-01-01'))
        seen=[]; cursor=None
        with patch.object(self.repo,'_load',side_effect=AssertionError('whole ledger')):
            while True:
                value=page(self.service,self.dialogue,self.actor,'demo',limit=20,cursor=cursor)
                self.assertLessEqual(len(value['items']),20)
                seen += [item['id'] for item in value['items']]
                cursor=value['next_cursor']
                if not cursor:break
        self.assertEqual(len(seen),128);self.assertEqual(len(set(seen)),128)
        value=page(self.service,self.dialogue,self.actor,'demo',limit=1)
        with self.assertRaises(ServiceError):
            page(self.service,self.dialogue,Actor('tenant-a','other'),'demo',cursor=value['next_cursor'])
        self.assertEqual(page(self.service,self.dialogue,Actor('other','user-a'),'demo')['items'],[])

    def test_conversation_deletion_updates_projection_and_revocation_filters(self):
        draft,_=self.service.save_draft(self.actor,'demo','one', BODY)
        item=page(self.service,self.dialogue,self.actor,'demo')['items'][0]
        self.assertEqual(item['id'],draft['conversation_id'])
        with patch.object(self.service.projects,'require_access',side_effect=ServiceError('PROJECT_ACCESS_DENIED','revoked',403)):
            self.assertEqual(page(self.service,self.dialogue,self.actor,'demo')['items'],[])
        self.service.delete_conversation(self.actor,item['id'])
        self.assertEqual(page(self.service,self.dialogue,self.actor,'demo')['items'],[])
