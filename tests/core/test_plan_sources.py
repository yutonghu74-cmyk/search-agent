"""Partitioned page persistence and optimistic commit fences on real SQLite."""
from contextlib import closing
from dataclasses import replace
from unittest.mock import patch
import unittest

from dsh_retrieval.adapters.task_storage import plan_sources, ledger_rows
from dsh_retrieval.adapters.task_storage.plan_sets import restore
from dsh_retrieval.adapters.task_storage.sqlite import dump
from dsh_retrieval.errors import ServiceError
from tests.core import test_plan_set_progress as fixtures
from draft_helpers import confirmation


class PlanSourcesTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PlanSetProgressTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.repo, self.actor = self.f.repo, self.f.actor
        self.task_id = self.f.fixture.task_id

    def test_shadow_and_partition_recover_identical_pages_after_restart(self):
        operation = self.f.reserve(self.f.first, 'source-page')
        self.f.commit(operation, self.f.first)
        with closing(self.repo.tasks._connect()) as db:
            state = restore(db.execute('SELECT format,payload FROM plan_set_ledger').fetchone())
            partition = plan_sources.load_task(db, self.task_id)
            self.assertEqual(partition['operations'], state['operations'])
            self.assertEqual(partition['events'], state['events'])
            self.assertEqual(partition['reserve_keys'], state['reserve_keys'])
        self.repo = self.f.fixture.reopen()
        self.assertEqual(self.repo.get_progress(self.actor, self.f.approved)[0].committed_pages, 1)

    def test_plan_history_is_retained_but_hot_load_reads_only_current_plan(self):
        first = self.f.reserve(self.f.first, 'old-plan')
        self.f.commit(first, self.f.first)
        proposal = self.f.fixture.make_proposal(2)
        self.repo.put_proposal(self.actor, proposal)
        approved, _ = self.f.fixture.confirm(proposal, key='new-plan')
        request = approved.members[0].plan.request
        operation, _ = self.repo.reserve_page(self.actor, approved, plan_id=request.plan_id,
                                               request=request, key='new-plan-page')
        with closing(self.repo.tasks._connect()) as db:
            current = plan_sources.load_task(db, self.task_id)
            self.assertEqual(list(current['operations']), [operation])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_page_sources WHERE task_id=?', (self.task_id,)).fetchone()[0], 2)
            legacy = restore(db.execute('SELECT format,payload FROM plan_set_ledger').fetchone())
            self.assertEqual(set(legacy['operations']), {first, operation})
        self.repo = self.f.fixture.reopen()
        self.assertEqual(self.repo.get_progress(self.actor, approved)[0].operation_id, operation)

    def test_failed_source_migration_rolls_back_and_retries_from_legacy(self):
        with closing(self.repo.tasks._connect()) as db, db:
            before = db.execute('SELECT payload FROM plan_set_ledger').fetchone()[0]
            for event in ('insert', 'update', 'delete'):
                db.execute('DROP TRIGGER plan_source_dirty_' + event)
            for table in ('plan_page_sources', 'plan_task_sources', 'plan_source_meta'):
                db.execute('DROP TABLE ' + table)
        with patch.object(plan_sources, 'sync', side_effect=RuntimeError('migration failure')):
            with self.assertRaises(RuntimeError):
                self.f.fixture.reopen()
        with closing(self.repo.tasks._connect()) as db:
            self.assertEqual(db.execute('SELECT payload FROM plan_set_ledger').fetchone()[0], before)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='plan_task_sources'").fetchone())
        self.repo = self.f.fixture.reopen()
        with closing(self.repo.tasks._connect()) as db:
            self.assertEqual(plan_sources.load_task(db, self.task_id)['current'][self.task_id], self.f.approved)

    def test_preparation_has_no_write_lock_and_concurrent_generation_change_is_fenced(self):
        original = ledger_rows.prepare_subset
        def concurrent(*args):
            value = original(*args)
            with closing(self.repo.tasks._connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                memory = self.repo.tasks._load(db.execute('SELECT format,payload FROM local_ledger').fetchone())
                task = memory._tasks[self.task_id]
                memory._tasks[self.task_id] = replace(task, run_generation=task.run_generation + 1)
                db.execute('UPDATE local_ledger SET payload=? WHERE id=1', (dump(memory),))
            return value
        with patch.object(ledger_rows, 'prepare_subset', side_effect=concurrent):
            with self.assertRaises(ServiceError) as error:
                self.f.reserve(self.f.first, 'racing-generation')
        self.assertEqual(error.exception.code, 'REVISION_CONFLICT')
        state = self.repo._transaction(False, lambda memory, state: state)
        self.assertFalse(state['operations'])
        self.assertFalse(state['reserve_keys'])

    def test_unrelated_commit_retries_without_duplicate_reservation(self):
        original, changes = ledger_rows.prepare_subset, []
        def concurrent(*args):
            value = original(*args)
            if not changes:
                changes.append(True)
                with closing(self.repo.tasks._connect()) as db, db:
                    db.execute('UPDATE local_ledger SET payload=payload WHERE id=1')
            return value
        with patch.object(ledger_rows, 'prepare_subset', side_effect=concurrent) as prepare:
            operation = self.f.reserve(self.f.first, 'retry-race')
            self.assertEqual(prepare.call_count, 2)
        state = self.repo._transaction(False, lambda memory, state: state)
        self.assertEqual(list(state['operations']), [operation])
        self.assertEqual(len(state['reserve_keys']), 1)

    def test_source_and_shadow_rollback_when_projection_write_fails(self):
        with closing(self.repo.tasks._connect()) as db:
            before = db.execute('SELECT payload FROM plan_page_sources').fetchall()
            shadow = db.execute('SELECT payload FROM plan_set_ledger').fetchone()[0]
        with patch('dsh_retrieval.adapters.task_storage.plan_read_views.sync', side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError):
                self.f.reserve(self.f.first, 'rollback-source')
        with closing(self.repo.tasks._connect()) as db:
            self.assertEqual(db.execute('SELECT payload FROM plan_page_sources').fetchall(), before)
            self.assertEqual(db.execute('SELECT payload FROM plan_set_ledger').fetchone()[0], shadow)
            self.assertEqual(db.execute('SELECT dirty FROM plan_source_meta').fetchone()[0], 0)

    def test_missing_plan_partition_is_corruption_and_legacy_write_invalidates(self):
        with closing(self.repo.tasks._connect()) as db, db:
            db.execute('DELETE FROM plan_page_sources')
        with self.assertRaises(ServiceError) as error:
            self.f.reserve(self.f.first, 'missing-partition')
        self.assertEqual(error.exception.code, 'STORAGE_CORRUPT')
        with closing(self.repo.tasks._connect()) as db, db:
            db.execute('UPDATE plan_set_ledger SET payload=payload WHERE id=1')
        self.f.reserve(self.f.first, 'legacy-recovery')
        with closing(self.repo.tasks._connect()) as db:
            self.assertEqual(db.execute('SELECT dirty FROM plan_source_meta').fetchone()[0], 0)
            self.assertTrue(plan_sources.load_task(db, self.task_id)['operations'])

    def test_page_summary_sync_does_not_rebuild_unrelated_task_or_chat(self):
        service = self.f.fixture.service
        other, _ = service.create_task(self.actor, 'other-task', confirmation(service, self.actor, key='other-draft'))
        with closing(self.repo.tasks._connect()) as db:
            before = db.execute('SELECT summary_json FROM task_read_summaries WHERE task_id=?', (other['task_id'],)).fetchone()[0]
            chats = db.execute('SELECT chat_id,summary_json FROM chat_read_summaries ORDER BY chat_id').fetchall()
        with patch('dsh_retrieval.adapters.task_storage.sqlite.SQLiteTaskRepository._task_progress_summary',
                   wraps=self.repo.tasks._task_progress_summary) as progress:
            self.f.reserve(self.f.first, 'one-summary')
            self.assertEqual(progress.call_count, 1)
            self.assertEqual(progress.call_args.args[1].task_id, self.task_id)
        with closing(self.repo.tasks._connect()) as db:
            self.assertEqual(db.execute('SELECT summary_json FROM task_read_summaries WHERE task_id=?', (other['task_id'],)).fetchone()[0], before)
            self.assertEqual(db.execute('SELECT chat_id,summary_json FROM chat_read_summaries ORDER BY chat_id').fetchall(), chats)

    def seed_unrelated_history(self):
        operation = self.f.reserve(self.f.first, 'history-seed')
        self.f.commit(operation, self.f.first)
        def seed(memory, state):
            record = state['operations'][operation]
            historical = replace(record[0], scope=replace(record[0].scope, task_id='history-task'))
            state['operations']['history-operation'] = (historical, *record[1:])
        self.repo._transaction(True, seed)

    def test_nonpage_confirm_prepares_only_its_task_and_reuses_validated_current(self):
        self.seed_unrelated_history()
        with closing(self.repo.tasks._connect()) as db:
            before = db.execute("SELECT payload,checksum FROM plan_page_sources WHERE task_id='history-task'").fetchall()
        proposal = self.f.fixture.make_proposal(2)
        original = self.repo.tasks._sync_task_read_summaries
        searches = []
        def summaries(*args, **kwargs):
            searches.append(kwargs.get('searches'))
            return original(*args, **kwargs)
        with patch.object(plan_sources, 'prepare', wraps=plan_sources.prepare) as prepare, \
                patch.object(self.repo.tasks, '_sync_task_read_summaries', side_effect=summaries):
            self.repo.put_proposal(self.actor, proposal)
            approved, _ = self.f.fixture.confirm(proposal, key='new-approval')
        self.assertEqual(prepare.call_count, 2)
        self.assertTrue(all(record[0].scope.task_id == self.task_id
                            for call in prepare.call_args_list
                            for record in call.args[0]['operations'].values()))
        self.assertTrue(all(isinstance(current, dict) for current in searches))
        self.assertEqual(searches[-1][self.task_id], approved)
        with closing(self.repo.tasks._connect()) as db:
            self.assertEqual(db.execute("SELECT payload,checksum FROM plan_page_sources WHERE task_id='history-task'").fetchall(), before)
            self.assertEqual(plan_sources.load_task(db, self.task_id)['current'][self.task_id], approved)

    def test_nonpage_dirty_source_repairs_unrelated_partitions_before_marking_clean(self):
        self.seed_unrelated_history()
        with closing(self.repo.tasks._connect()) as db, db:
            db.execute("UPDATE plan_task_sources SET payload='broken' WHERE task_id='history-task'")
            db.execute('UPDATE plan_set_ledger SET payload=payload WHERE id=1')
        proposal = self.f.fixture.make_proposal(2)
        with patch.object(plan_sources, 'prepare', wraps=plan_sources.prepare) as prepare:
            self.repo.put_proposal(self.actor, proposal)
        self.assertEqual(prepare.call_count, 2)
        with closing(self.repo.tasks._connect()) as db:
            row = db.execute("SELECT payload,checksum FROM plan_task_sources WHERE task_id='history-task'").fetchone()
            self.assertIsInstance(plan_sources._check(row), dict)
            self.assertEqual(db.execute('SELECT dirty FROM plan_source_meta').fetchone(), (0,))

    def test_targeted_rebuild_removes_only_obsolete_target_partitions(self):
        self.seed_unrelated_history()
        with closing(self.repo.tasks._connect()) as db, db:
            state = restore(db.execute('SELECT format,payload FROM plan_set_ledger').fetchone())
            before = db.execute("SELECT payload,checksum FROM plan_page_sources WHERE task_id='history-task'").fetchall()
            db.execute('INSERT INTO plan_page_sources VALUES(?,?,?,?)', (self.task_id, 'obsolete', '{}', 'invalid'))
            plan_sources.sync(db, state, task_ids={self.task_id})
            self.assertIsNone(db.execute("SELECT 1 FROM plan_page_sources WHERE plan_digest='obsolete'").fetchone())
            self.assertEqual(db.execute("SELECT payload,checksum FROM plan_page_sources WHERE task_id='history-task'").fetchall(), before)

    def test_nonpage_source_failure_keeps_ledger_and_projections_unchanged(self):
        def snapshot():
            with closing(self.repo.tasks._connect()) as db:
                return (db.execute('SELECT format,payload FROM plan_set_ledger').fetchall(),
                        db.execute('SELECT * FROM plan_task_sources ORDER BY task_id').fetchall(),
                        db.execute('SELECT * FROM plan_page_sources ORDER BY task_id,plan_digest').fetchall(),
                        db.execute('SELECT * FROM plan_source_meta').fetchall())
        before = snapshot()
        original = plan_sources._put
        def fail(*args):
            original(*args)
            raise RuntimeError('source write failed after mutation')
        with patch.object(plan_sources, '_put', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.repo.put_proposal(self.actor, self.f.fixture.make_proposal(2))
        self.assertEqual(snapshot(), before)
