"""Per-task read isolation, migration, invalidation and atomicity on real SQLite."""
from contextlib import closing
from dataclasses import replace
import json
from unittest.mock import patch
import unittest

from dsh_retrieval.adapters.task_storage import plan_read_views
from dsh_retrieval.adapters.task_storage.plan_sets import empty_state, serialize, restore, purge_for_tasks
from dsh_retrieval.errors import ServiceError
from tests.core import test_plan_set_progress as fixtures


class PlanReadViewsTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PlanSetProgressTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo = self.fixture.repo
        self.actor = self.fixture.actor
        self.task_id = self.fixture.fixture.task_id

    def read(self):
        return self.repo.get_progress(self.actor, self.fixture.approved)

    def test_ready_view_never_selects_full_ledgers(self):
        queries = []
        connect = self.repo.tasks._connect
        def traced():
            db = connect()
            db.set_trace_callback(queries.append)
            return db
        with patch.object(self.repo.tasks, '_connect', side_effect=traced):
            self.repo.get_current(self.actor, self.task_id)
            self.read()
        self.assertFalse(any('from plan_set_ledger' in q.lower() or
                             'from local_ledger' in q.lower() for q in queries), queries)

    def test_legacy_database_rebuild_and_restart_preserve_source(self):
        self.fixture.reserve(self.fixture.first, 'pending')
        expected = self.read()
        with closing(self.repo.tasks._connect()) as db, db:
            before = db.execute('SELECT payload FROM plan_set_ledger').fetchone()[0]
            for event in ('insert', 'update', 'delete'):
                db.execute('DROP TRIGGER plan_task_read_dirty_' + event)
            db.execute('DROP TABLE plan_task_read_views')
            db.execute('DROP TABLE plan_task_read_meta')
        self.repo = self.fixture.fixture.reopen()
        self.assertEqual(self.read(), expected)
        with closing(self.repo.tasks._connect()) as db:
            self.assertEqual(db.execute('SELECT payload FROM plan_set_ledger').fetchone()[0], before)
            self.assertEqual(db.execute('SELECT dirty FROM plan_task_read_meta').fetchone()[0], 0)
        self.repo = self.fixture.fixture.reopen()
        self.assertEqual(self.read(), expected)

    def test_failed_migration_is_atomic_and_retryable(self):
        with closing(self.repo.tasks._connect()) as db, db:
            before = db.execute('SELECT payload FROM plan_set_ledger').fetchone()[0]
            for event in ('insert', 'update', 'delete'):
                db.execute('DROP TRIGGER plan_task_read_dirty_' + event)
            db.execute('DROP TABLE plan_task_read_views')
            db.execute('DROP TABLE plan_task_read_meta')
        with patch.object(plan_read_views, 'sync', side_effect=RuntimeError('migration failure')):
            with self.assertRaises(RuntimeError):
                self.fixture.fixture.reopen()
        with closing(self.repo.tasks._connect()) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='plan_task_read_views'").fetchone())
            self.assertEqual(db.execute('SELECT payload FROM plan_set_ledger').fetchone()[0], before)
        self.repo = self.fixture.fixture.reopen()
        self.assertEqual(self.read()[0].committed_pages, 0)

    def test_cascade_purge_removes_task_read_view_in_same_transaction(self):
        with closing(self.repo.tasks._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            self.repo.tasks._purge_plan_sets(db, {self.task_id}, restore, purge_for_tasks, serialize)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_task_read_views').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT dirty FROM plan_task_read_meta').fetchone()[0], 0)
        self.assertIsNone(self.repo.get_current(self.actor, self.task_id))

    def test_legacy_writer_invalidates_and_falls_back_without_stale_plan(self):
        with closing(self.repo.tasks._connect()) as db, db:
            db.execute('UPDATE plan_set_ledger SET payload=? WHERE id=1', (serialize(empty_state()),))
        self.assertIsNone(self.repo.get_current(self.actor, self.task_id))
        with closing(self.repo.tasks._connect()) as db:
            self.assertEqual(db.execute('SELECT dirty FROM plan_task_read_meta').fetchone()[0], 1)
        self.repo = self.fixture.fixture.reopen()
        with closing(self.repo.tasks._connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_task_read_views').fetchone()[0], 0)

    def test_projection_failure_rolls_back_source_and_view_together(self):
        before = self.read()
        with patch.object(plan_read_views, 'sync', side_effect=RuntimeError('injected write failure')):
            with self.assertRaises(RuntimeError):
                self.fixture.reserve(self.fixture.first, 'rollback')
        self.assertEqual(self.read(), before)
        with closing(self.repo.tasks._connect()) as db:
            self.assertEqual(db.execute('SELECT dirty FROM plan_task_read_meta').fetchone()[0], 0)
        self.repo = self.fixture.fixture.reopen()
        self.assertEqual(self.read(), before)

    def seed_other_history(self):
        operation = self.fixture.reserve(self.fixture.first, 'seed-history')
        self.fixture.commit(operation, self.fixture.first)
        def seed(memory, state):
            saved, plan_id, request, page = state['operations'][operation]
            scope = replace(saved.scope, task_id='unrelated-task')
            historical = replace(saved, scope=scope, members=tuple(
                replace(member, plan=replace(member.plan, scope=scope)) for member in saved.members))
            state['operations']['historical-operation'] = (historical, plan_id, request, page)
        self.repo._transaction(True, seed)

    def test_page_write_serializes_only_target_view_and_preserves_other_payload(self):
        self.seed_other_history()
        with closing(self.repo.tasks._connect()) as db:
            before = db.execute("SELECT payload FROM plan_task_read_views WHERE task_id='unrelated-task'").fetchone()[0]
        from dsh_retrieval.adapters.task_storage import plan_sets
        original, serialized_tasks = plan_sets.serialize, []
        def serialize_view(state):
            if not state['proposals'] and not state['reserve_keys']:
                serialized_tasks.extend(record[0].scope.task_id for record in state['operations'].values())
            return original(state)
        with patch.object(plan_sets, 'serialize', side_effect=serialize_view):
            self.fixture.reserve(self.fixture.second, 'target-only')
        self.assertIn(self.task_id, serialized_tasks)
        self.assertNotIn('unrelated-task', serialized_tasks)
        with closing(self.repo.tasks._connect()) as db:
            self.assertEqual(db.execute("SELECT payload FROM plan_task_read_views WHERE task_id='unrelated-task'").fetchone()[0], before)

    def test_dirty_source_requires_full_sync_even_for_single_task_page(self):
        self.seed_other_history()
        with closing(self.repo.tasks._connect()) as db, db:
            payload = json.loads(db.execute('SELECT payload FROM plan_set_ledger').fetchone()[0])
            for name, section in payload['value']:
                if name == 'operations':
                    section['value'] = [pair for pair in section['value'] if pair[0] != 'historical-operation']
            db.execute('UPDATE plan_set_ledger SET payload=? WHERE id=1', (json.dumps(payload),))
        with patch.object(plan_read_views, 'sync', wraps=plan_read_views.sync) as sync:
            self.fixture.reserve(self.fixture.second, 'after-legacy-write')
            self.assertIsNone(sync.call_args.kwargs['task_ids'])
        with closing(self.repo.tasks._connect()) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM plan_task_read_views WHERE task_id='unrelated-task'").fetchone())
            self.assertEqual(db.execute('SELECT dirty FROM plan_task_read_meta').fetchone()[0], 0)

    def test_identical_page_and_reservation_replay_issue_no_mutating_sql(self):
        operation = self.fixture.reserve(self.fixture.first, 'same-key')
        page = self.fixture.commit(operation, self.fixture.first)
        queries, connect = [], self.repo.tasks._connect
        def traced():
            db = connect()
            db.set_trace_callback(queries.append)
            return db
        with patch.object(self.repo.tasks, '_connect', side_effect=traced):
            self.assertEqual(self.fixture.reserve(self.fixture.first, 'same-key'), operation)
            self.assertTrue(self.repo.commit_page(self.actor, self.fixture.approved,
                operation_id=operation, plan_id=self.fixture.first.plan_id,
                request=self.fixture.first, page=page))
        self.assertFalse(any(q.lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for q in queries), queries)
        # A different reservation key must still be persisted, despite replay=True.
        self.fixture.reserve(self.fixture.first, 'new-key-for-existing-operation')
        self.repo = self.fixture.fixture.reopen()
        keys = self.repo._transaction(False, lambda memory, state: state['reserve_keys'])
        self.assertIn((self.actor.tenant_id, self.actor.user_id, self.task_id, 'new-key-for-existing-operation'), keys)
        self.fixture.fixture.denied.add('library:lib1')
        with self.assertRaises(ServiceError) as error:
            self.repo.commit_page(self.actor, self.fixture.approved,
                operation_id=operation, plan_id=self.fixture.first.plan_id,
                request=self.fixture.first, page=page)
        self.assertEqual(error.exception.code, 'ACCESS_DENIED')

    def test_missing_row_falls_back_but_corrupt_row_fails_closed(self):
        with closing(self.repo.tasks._connect()) as db, db:
            db.execute('DELETE FROM plan_task_read_views WHERE task_id=?', (self.task_id,))
        self.assertEqual(self.read()[0].committed_pages, 0)
        self.repo = self.fixture.fixture.reopen()
        with closing(self.repo.tasks._connect()) as db, db:
            db.execute("UPDATE plan_task_read_views SET payload='{}' WHERE task_id=?", (self.task_id,))
        with self.assertRaises(ServiceError) as error:
            self.read()
        self.assertEqual(error.exception.code, 'STORAGE_CORRUPT')
