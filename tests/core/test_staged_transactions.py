"""Preparation, permission recheck and atomic publication of non-page mutations."""
from contextlib import closing
from dataclasses import replace
import sqlite3
import unittest
from unittest.mock import patch

from dsh_retrieval.adapters.task_storage import ledger_rows, transactions
from dsh_retrieval.adapters.task_storage.sqlite import dump
from dsh_retrieval.errors import ServiceError
from tests.core import test_plan_set_storage as fixtures


class StagedTransactionTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.PlanSetStorageTest();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_nonpage_preparation_does_not_own_live_write_lock(self):
        observations=[]
        original=ledger_rows.prepare_state
        def prepare(*args):
            result=original(*args)
            with closing(sqlite3.connect(self.f.path,timeout=.1)) as db:
                db.execute('BEGIN IMMEDIATE')
                observations.append(db.execute('SELECT COUNT(*) FROM ledger_items WHERE ledger=? AND section=?',('plan_set_ledger','proposals')).fetchone()[0])
                db.rollback()
            return result
        with patch.object(ledger_rows,'prepare_state',side_effect=prepare):
            self.f.repo.put_proposal(self.f.actor,self.f.proposal)
        self.assertTrue(observations)
        self.assertEqual(set(observations),{0})
        state=self.f.repo._transaction(False,lambda memory,state:state)
        self.assertEqual(len(state['proposals']),1)

    def test_permission_revoked_after_prepare_prevents_publication(self):
        original=ledger_rows.prepare_state
        def prepare(*args):
            result=original(*args)
            self.f.denied.add('library:lib0')
            return result
        with patch.object(ledger_rows,'prepare_state',side_effect=prepare):
            with self.assertRaises(ServiceError) as error:
                self.f.repo.put_proposal(self.f.actor,self.f.proposal)
        self.assertEqual(error.exception.code,'ACCESS_DENIED')
        self.f.denied.clear()
        self.assertFalse(self.f.repo._transaction(False,lambda memory,state:state['proposals']))

    def test_concurrent_generation_change_is_not_overwritten(self):
        original=ledger_rows.prepare_state; changed=[]
        def prepare(*args):
            result=original(*args)
            if not changed:
                changed.append(True)
                with closing(sqlite3.connect(self.f.path)) as db, db:
                    row=db.execute('SELECT format,payload FROM local_ledger').fetchone()
                    memory=self.f.repo.tasks._load(row);task=memory._tasks[self.f.task_id]
                    memory._tasks[self.f.task_id]=replace(task,run_generation=task.run_generation+1)
                    db.execute('UPDATE local_ledger SET payload=?',(dump(memory),))
            return result
        with patch.object(ledger_rows,'prepare_state',side_effect=prepare):
            with self.assertRaises(ServiceError) as error:
                self.f.repo.put_proposal(self.f.actor,self.f.proposal)
        self.assertEqual(error.exception.code,'REVISION_CONFLICT')
        self.assertFalse(self.f.repo._transaction(False,lambda memory,state:state['proposals']))

    def test_repeated_contention_uses_bounded_serial_fallback(self):
        from dsh_retrieval.adapters.task_storage import transactions
        original=transactions._snapshot; attempts=[]
        def snapshot(*args):
            original(*args); attempts.append(True)
            if len(attempts)<=3:
                with closing(sqlite3.connect(self.f.path)) as db, db:
                    db.execute('UPDATE task_read_summaries_meta SET dirty=1-dirty WHERE id=1')
        with patch.object(transactions,'_snapshot',side_effect=snapshot):
            self.f.repo.put_proposal(self.f.actor,self.f.proposal)
        self.assertEqual(len(attempts),4)
        self.assertEqual(len(self.f.repo._transaction(False,lambda memory,state:state['proposals'])),1)

    def test_replay_failure_rolls_back_earlier_record_changes(self):
        connect=self.f.repo.tasks._connect
        class Failing:
            def __init__(self,db):self.db=db
            def execute(self,*args,**kwargs):return self.db.execute(*args,**kwargs)
            def executemany(self,sql,rows):
                rows=list(rows)
                if 'INSERT INTO ledger_items' in sql and rows:
                    self.db.execute(sql,rows[0])
                    raise sqlite3.OperationalError('injected publication failure')
                return self.db.executemany(sql,rows)
            def close(self):self.db.close()
            def __getattr__(self,name):return getattr(self.db,name)
        def factory():
            db=connect()
            return db if isinstance(db,transactions.Lease) else Failing(db)
        with patch.object(self.f.repo.tasks,'_connect',side_effect=factory):
            with self.assertRaises(ServiceError) as error:
                self.f.repo.put_proposal(self.f.actor,self.f.proposal)
        self.assertEqual(error.exception.code,'STORAGE_UNAVAILABLE')
        self.assertFalse(self.f.repo._transaction(False,lambda memory,state:state['proposals']))
