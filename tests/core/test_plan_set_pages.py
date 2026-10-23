from dataclasses import replace
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch

from dsh_retrieval.adapters.task_storage.plan_sets import restore, serialize
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import SearchPage
from tests.core import test_plan_set_storage as fixtures


class PlanSetPagesTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PlanSetStorageTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.repo, self.actor = f.repo, f.actor
        self.repo.put_proposal(self.actor, f.proposal)
        self.approved, _ = f.confirm()
        self.first, self.second = [m.plan.request for m in self.approved.members]

    def reserve(self, request=None, key='page'):
        request = request or self.first
        return self.repo.reserve_page(self.actor, self.approved, plan_id=request.plan_id, request=request, key=key)

    def commit(self, operation, request=None, cursor=None, snapshot='snapshot'):
        request = request or self.first
        page = SearchPage((), cursor, 'more_available' if cursor else 'exhausted', 'snapshot', snapshot)
        return self.repo.commit_page(self.actor, self.approved, operation_id=operation,
                                     plan_id=request.plan_id, request=request, page=page)

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_replay_restart_and_no_task_change(self):
        before = self.fixture.service.repository.get(self.actor, self.fixture.task_id)
        operation, replay = self.reserve()
        self.assertFalse(replay)
        self.assertEqual(self.reserve(), (operation, True))
        self.assertEqual(self.reserve(key='other'), (operation, True))
        self.assertFalse(self.commit(operation, cursor='next'))
        self.repo = self.fixture.reopen()
        self.assertTrue(self.commit(operation, cursor='next'))
        next_request = replace(self.first, cursor='next')
        next_operation, _ = self.reserve(next_request, 'next')
        self.assertFalse(self.commit(next_operation, next_request))
        self.assertEqual(self.fixture.service.repository.get(self.actor, self.fixture.task_id), before)
        events = self.repo._transaction(False, lambda memory, state: state['events'])
        self.assertEqual(len(events), 3)  # One approval and two pages.

    def test_cursor_chains_are_member_specific(self):
        operation, _ = self.reserve()
        self.commit(operation, cursor='shared-token')
        self.assert_code('CURSOR_INVALID', lambda: self.reserve(replace(self.second, cursor='shared-token'), 'cross'))
        second_operation, _ = self.reserve(self.second, 'second')
        self.commit(second_operation, self.second, 'shared-token')
        self.reserve(replace(self.second, cursor='shared-token'), 'second-next')
        self.reserve(replace(self.first, cursor='shared-token'), 'first-next')

    def test_conflicts_wrong_operation_and_snapshot(self):
        operation, _ = self.reserve()
        self.assert_code('IDEMPOTENCY_CONFLICT', lambda: self.reserve(self.second))
        self.assert_code('OPERATION_MISMATCH', lambda: self.commit(operation, self.second))
        self.commit(operation, cursor='next')
        self.assert_code('RESULT_CONFLICT', lambda: self.commit(operation))
        request = replace(self.first, cursor='next')
        following, _ = self.reserve(request, 'next')
        self.assert_code('INVALID_SEARCH_PAGE', lambda: self.commit(following, request, snapshot='changed'))
        self.assert_code('INVALID_SEARCH_PAGE', lambda: self.commit(following, request, cursor='next'))

    def test_old_set_cannot_reserve_or_commit(self):
        operation, _ = self.reserve()
        proposal = self.fixture.make_proposal(2)
        self.repo.put_proposal(self.actor, proposal)
        self.fixture.confirm(proposal, key='replacement')
        self.assert_code('REVISION_CONFLICT', lambda: self.commit(operation))
        self.assert_code('REVISION_CONFLICT', lambda: self.reserve())

    def test_revoke_and_serialization_failure_leave_page_uncommitted(self):
        operation, _ = self.reserve()
        self.fixture.denied.add('library:lib1')
        self.assert_code('ACCESS_DENIED', lambda: self.commit(operation))
        self.fixture.denied.clear()
        with patch('dsh_retrieval.adapters.task_storage.plan_sets.serialize', side_effect=ValueError('failure')):
            with self.assertRaises(ValueError):
                self.commit(operation)
        self.assertFalse(self.commit(operation))

    def test_legacy_format_migration_and_corrupt_partial_state(self):
        with closing(sqlite3.connect(self.fixture.path)) as db, db:
            state = restore(db.execute('SELECT format,payload FROM plan_set_ledger WHERE id=1').fetchone())
            for key in ('reserve_keys', 'operations', 'requests', 'cursors'):
                del state[key]
            db.execute('UPDATE plan_set_ledger SET format=1,payload=?', (serialize(state),))
        self.repo = self.fixture.reopen()
        self.assertEqual(self.repo.get_current(self.actor, self.fixture.task_id), self.approved)
        self.reserve()
        with closing(sqlite3.connect(self.fixture.path)) as db, db:
            row = db.execute('SELECT format,payload FROM plan_set_ledger').fetchone()
        self.assertEqual(row[0], 2)
        state = restore(row)
        del state['cursors']
        self.assert_code('STORAGE_CORRUPT', lambda: restore((2, serialize(state))))

    def test_pending_limit_is_atomic_and_replay_remains_available(self):
        operation, _ = self.reserve()
        # Inject a full pending ledger to exercise the configured admission bound;
        # normal sequential cursor chains have fewer concurrent pages.
        def fill(memory, state):
            record = state['operations'][operation]
            for index in range(99):
                state['operations']['fixture-' + str(index)] = record
        self.repo._transaction(True, fill)
        self.assertEqual(self.reserve(), (operation, True))
        self.assert_code('INPUT_LIMIT', lambda: self.reserve(self.second, 'blocked'))
        self.commit(operation)
        self.assertFalse(self.reserve(self.second, 'blocked')[1])

    def test_generation_change_blocks_late_commit(self):
        operation, _ = self.reserve()
        def advance(memory, state):
            task = memory._tasks[self.fixture.task_id]
            memory._tasks[task.task_id] = replace(task, run_generation=task.run_generation + 1)
        self.repo._transaction(True, advance)
        self.assert_code('REVISION_CONFLICT', lambda: self.commit(operation))

    def test_concurrent_reservations_and_commits_have_single_effect(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda key: self.reserve(key=key), ['one', 'two']))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(replay for _, replay in results), [False, True])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.commit(results[0][0]), range(2)))
        self.assertEqual(sorted(results), [False, True])


if __name__ == '__main__':
    unittest.main()
