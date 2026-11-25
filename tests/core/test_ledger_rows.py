"""Atomic row migration, compatibility and scope-safe deltas on real SQLite."""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.adapters.task_storage import ledger_rows, transactions
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository, dump, encode
from dsh_retrieval.adapters.task_storage.plan_sets import empty_state, serialize, restore
from dsh_retrieval.errors import ServiceError


class LedgerRowsTest(unittest.TestCase):
    def test_failed_migration_restores_original_table_and_payload(self):
        with sqlite3.connect(':memory:') as db:
            payload = dump(InMemoryTaskRepository())
            db.execute('CREATE TABLE local_ledger (id INTEGER PRIMARY KEY,format,payload)')
            db.execute('INSERT INTO local_ledger VALUES(1,1,?)', (payload,)); db.commit()
            calls = []
            def validate(row):
                SQLiteTaskRepository._load(row); calls.append(True)
                if len(calls) == 2: raise RuntimeError('post-copy failure')
            with self.assertRaises(RuntimeError):
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    ledger_rows.migrate(db, 'local_ledger', validate)
            self.assertEqual(ledger_rows.kind(db,'local_ledger'), 'table')
            self.assertEqual(db.execute('SELECT payload FROM local_ledger').fetchone()[0], payload)
            self.assertIsNone(ledger_rows.kind(db,'ledger_items'))
            with db:
                ledger_rows.migrate(db,'local_ledger',SQLiteTaskRepository._load)
            self.assertEqual(ledger_rows.kind(db,'local_ledger'),'view')
            self.assertEqual(db.execute('SELECT error_payload FROM ledger_registry').fetchone()[0], None)
            self.assertNotIn('payload', [row[1] for row in db.execute('PRAGMA table_info(ledger_registry)')])

    def test_escaped_unicode_legacy_keys_update_without_duplicates(self):
        state=empty_state(); key=('租户','用户','任务','重试键')
        state['reserve_keys'][key]=('fingerprint','operation')
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE plan_set_ledger (id INTEGER PRIMARY KEY,format,payload)')
            db.execute('INSERT INTO plan_set_ledger VALUES(1,3,?)',(serialize(state),))
            ledger_rows.migrate(db,'plan_set_ledger',restore)
            db.execute('UPDATE plan_set_ledger SET payload=?',(json.dumps(encode(state)),))
            after=deepcopy(state); after['reserve_keys'][key]=('changed','operation')
            prepared=ledger_rows.prepare_subset(db,'plan_set_ledger',3,state,after)
            ledger_rows.apply(db,prepared)
            value=restore(db.execute('SELECT format,payload FROM plan_set_ledger').fetchone())
            self.assertEqual(value,after)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ledger_items WHERE section='reserve_keys'").fetchone()[0],1)

    def test_subset_cannot_bypass_legacy_aggregate_size_limit(self):
        state=empty_state()
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE plan_set_ledger(id,format,payload)')
            db.execute('INSERT INTO plan_set_ledger VALUES(1,3,?)',(serialize(state),))
            ledger_rows.migrate(db,'plan_set_ledger',restore)
            before=db.execute('SELECT payload FROM plan_set_ledger').fetchone()[0]
            after=deepcopy(state);after['epochs']['new-task']=5
            with patch('dsh_retrieval.adapters.task_storage.sqlite.MAX_BYTES',len(before.encode())):
                with self.assertRaises(ServiceError) as error:
                    ledger_rows.prepare_subset(db,'plan_set_ledger',3,state,after)
            self.assertEqual(error.exception.code,'STORAGE_LIMIT')
            self.assertEqual(db.execute('SELECT payload FROM plan_set_ledger').fetchone()[0],before)

    def test_malformed_nested_container_is_not_silently_reset(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE local_ledger(id,format,payload)')
            original=dump(InMemoryTaskRepository())
            db.execute('INSERT INTO local_ledger VALUES(1,1,?)',(original,))
            ledger_rows.migrate(db,'local_ledger',SQLiteTaskRepository._load)
            value=json.loads(original)
            value['value'][0][1]['value']=None
            invalid=json.dumps(value)
            db.execute('UPDATE local_ledger SET payload=?',(invalid,))
            self.assertEqual(db.execute('SELECT payload FROM local_ledger').fetchone()[0],invalid)
            with self.assertRaises(ServiceError) as error:
                SQLiteTaskRepository._load(db.execute('SELECT format,payload FROM local_ledger').fetchone())
            self.assertEqual(error.exception.code,'STORAGE_CORRUPT')

    def test_unknown_root_tags_remain_corrupt_in_compatibility_view(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE local_ledger(id,format,payload)')
            original=dump(InMemoryTaskRepository())
            db.execute('INSERT INTO local_ledger VALUES(1,1,?)',(original,))
            ledger_rows.migrate(db,'local_ledger',SQLiteTaskRepository._load)
            value=json.loads(original);value['unknown']=True
            db.execute('UPDATE local_ledger SET payload=?',(json.dumps(value),))
            with self.assertRaises(ServiceError) as error:
                SQLiteTaskRepository._load(db.execute('SELECT format,payload FROM local_ledger').fetchone())
            self.assertEqual(error.exception.code,'STORAGE_CORRUPT')

    def test_unknown_storage_version_fails_closed(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE local_ledger(id,format,payload)')
            db.execute('INSERT INTO local_ledger VALUES(1,1,?)',(dump(InMemoryTaskRepository()),))
            ledger_rows.migrate(db,'local_ledger',SQLiteTaskRepository._load)
            db.execute('UPDATE ledger_storage_meta SET version=99')
            with self.assertRaises(ServiceError) as error:
                ledger_rows.migrate(db,'local_ledger',SQLiteTaskRepository._load)
            self.assertEqual(error.exception.code,'STORAGE_FORMAT_UNSUPPORTED')

    def test_snapshot_keeps_uploaded_bytes_lazy_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            repo=SQLiteTaskRepository(directory+'/state.sqlite3')
            with closing(repo._connect()) as db, db:
                db.execute('CREATE TABLE query_images(id TEXT PRIMARY KEY,content BLOB NOT NULL)')
                db.execute("INSERT INTO query_images VALUES('reference',zeroblob(8000000))")
            with closing(repo._connect()) as live, closing(sqlite3.connect(':memory:',uri=True)) as snapshot:
                transactions._snapshot(live,snapshot,str(repo.path))
                self.assertEqual(snapshot.execute('SELECT COUNT(*) FROM main.query_images').fetchone()[0],0)
                self.assertEqual(snapshot.execute('SELECT length(content) FROM query_images').fetchone()[0],8000000)
                with self.assertRaises(sqlite3.OperationalError):
                    snapshot.execute("DELETE FROM query_images")
