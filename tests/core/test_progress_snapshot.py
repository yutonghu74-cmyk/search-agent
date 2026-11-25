"""One authorized SQLite snapshot for a current plan and its validated page heads."""
from contextlib import closing
from dataclasses import replace
import unittest
from unittest.mock import patch

from dsh_retrieval.adapters.task_storage import plan_sets
from dsh_retrieval.adapters.task_storage.sqlite import dump
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.retrieval_coordinator import RetrievalCoordinator
from dsh_retrieval.retrieval_progress import read_retrieval_progress
from tests.core import test_plan_set_progress as fixtures


class LegacyProgressPort:
    get_progress_snapshot = None

    def __init__(self, repository):
        self.repository = repository

    def __getattr__(self, name):
        return getattr(self.repository, name)


class ProgressSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PlanSetProgressTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.repo, self.actor = self.f.repo, self.f.actor
        self.task_id = self.f.fixture.task_id

    def commit_first(self):
        operation = self.f.reserve(self.f.first, 'snapshot-page')
        self.f.commit(operation, self.f.first)

    def assert_code(self, code, function):
        with self.assertRaises(ServiceError) as caught:
            function()
        self.assertEqual(caught.exception.code, code)

    def test_progress_and_coordinator_use_one_snapshot_without_writing(self):
        self.commit_first()
        with closing(self.repo.tasks._connect()) as db:
            before = db.execute('SELECT payload FROM plan_set_ledger').fetchone()[0]
        with patch.object(self.repo, '_read_transaction', wraps=self.repo._read_transaction) as reads:
            expected = read_retrieval_progress(LegacyProgressPort(self.repo), self.actor, self.task_id)
            self.assertEqual(reads.call_count, 2)
            reads.reset_mock()
            self.assertEqual(read_retrieval_progress(self.repo, self.actor, self.task_id), expected)
            self.assertEqual(reads.call_count, 1)
            reads.reset_mock()
            rows = RetrievalCoordinator(self.repo, object())._progress(self.actor, self.f.approved)
            self.assertEqual(reads.call_count, 1)
        self.assertEqual(rows[0].committed_pages, 1)
        with closing(self.repo.tasks._connect()) as db:
            self.assertEqual(db.execute('SELECT payload FROM plan_set_ledger').fetchone()[0], before)

    def test_revocation_during_chain_validation_still_denies_final_snapshot(self):
        self.commit_first()
        original = plan_sets.validate_page
        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            self.f.fixture.denied.add('library:lib1')
            return result
        with patch.object(plan_sets, 'validate_page', side_effect=revoke):
            self.assert_code('ACCESS_DENIED', lambda: self.repo.get_progress_snapshot(self.actor, self.task_id))

    def test_concurrent_new_generation_does_not_mix_approval_and_page_snapshots(self):
        self.commit_first()
        # WAL permits the independent commit while this read snapshot is open.
        # This is isolated test configuration, not a production journal change.
        with closing(self.repo.tasks._connect()) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        original = self.repo._progress_from_state
        def advance(*args, **kwargs):
            with closing(self.repo.tasks._connect()) as db, db:
                memory = self.repo.tasks._load(db.execute('SELECT format,payload FROM local_ledger').fetchone())
                task = memory._tasks[self.task_id]
                memory._tasks[self.task_id] = replace(task, run_generation=task.run_generation + 1)
                db.execute('UPDATE local_ledger SET payload=?', (dump(memory),))
            return original(*args, **kwargs)
        with patch.object(self.repo, '_progress_from_state', side_effect=advance):
            approved, rows = self.repo.get_progress_snapshot(self.actor, self.task_id)
        self.assertEqual(approved, self.f.approved)
        self.assertEqual(rows[0].committed_pages, 1)
        self.assert_code('REVISION_CONFLICT', lambda: self.repo.get_progress_snapshot(self.actor, self.task_id))

    def test_dirty_projection_does_not_hide_broken_cursor_chain(self):
        operation = self.f.reserve(self.f.first, 'cursor-page')
        self.f.commit(operation, self.f.first, 'next-page')
        with closing(self.repo.tasks._connect()) as db, db:
            state = plan_sets.restore(db.execute('SELECT format,payload FROM plan_set_ledger').fetchone())
            state['cursors'].clear()
            db.execute('UPDATE plan_set_ledger SET payload=?', (plan_sets.serialize(state),))
        self.assert_code('STORAGE_CORRUPT', lambda: self.repo.get_progress_snapshot(self.actor, self.task_id))

    def test_downstream_browsing_preserves_the_strict_execution_boundary(self):
        self.commit_first()
        def advance(memory, state):
            task = memory._tasks[self.task_id]
            memory._tasks[self.task_id] = replace(task, task_version=task.task_version + 1)
        self.repo._transaction(True, advance)
        self.assert_code('REVISION_CONFLICT', lambda: self.repo.get_progress_snapshot(self.actor, self.task_id))
        expected = read_retrieval_progress(LegacyProgressPort(self.repo), self.actor, self.task_id, browsing=True)
        self.assertEqual(read_retrieval_progress(self.repo, self.actor, self.task_id, browsing=True), expected)
        approved, rows = self.repo.get_progress_snapshot(self.actor, self.task_id, allow_downstream=True)
        self.assertEqual(approved, self.f.approved)
        self.assertEqual(rows[0].committed_pages, 1)

    def test_current_plan_cannot_redirect_to_another_task(self):
        with closing(self.repo.tasks._connect()) as db, db:
            state = plan_sets.restore(db.execute('SELECT format,payload FROM plan_set_ledger').fetchone())
            state['current'][self.task_id] = replace(self.f.approved,
                scope=replace(self.f.approved.scope, task_id='other-task'))
            db.execute('UPDATE plan_set_ledger SET payload=?', (plan_sets.serialize(state),))
        self.assert_code('STORAGE_CORRUPT', lambda: self.repo.get_progress_snapshot(self.actor, self.task_id))
