"""Recovery snapshots from the real SQLite plan-set ledger."""
from dataclasses import replace
import subprocess
import sys
import unittest
from unittest.mock import patch

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import SearchPage
from dsh_retrieval.retrieval_coordinator import RetrievalCoordinator
from tests.core import test_plan_set_storage as fixtures


class PlanSetProgressTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PlanSetStorageTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.repo, self.actor = f.repo, f.actor
        self.repo.put_proposal(self.actor, f.proposal)
        self.approved, _ = f.confirm()
        self.first, self.second = [m.plan.request for m in self.approved.members]

    def progress(self):
        return self.repo.get_progress(self.actor, self.approved)

    def reserve(self, request, key):
        return self.repo.reserve_page(self.actor, self.approved,
                                      plan_id=request.plan_id, request=request, key=key)[0]

    def commit(self, operation, request, cursor=None):
        page = SearchPage((), cursor, 'more_available' if cursor else 'exhausted',
                          'snapshot', 'snap')
        self.repo.commit_page(self.actor, self.approved, operation_id=operation,
                              plan_id=request.plan_id, request=request, page=page)
        return page

    def assert_code(self, code, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_empty_pending_and_reopened_reader_are_read_only(self):
        before = self.repo._transaction(False, lambda memory, state: state.copy())
        rows = self.progress()
        self.assertEqual(tuple(r.plan_id for r in rows), (self.first.plan_id, self.second.plan_id))
        self.assertEqual([r.committed_pages for r in rows], [0, 0])
        self.assertTrue(all(r.operation_id is None and r.page is None for r in rows))
        operation = self.reserve(self.first, 'first')
        self.repo = self.fixture.reopen()
        rows = self.progress()
        self.assertEqual((rows[0].operation_id, rows[0].request, rows[0].page,
                          rows[0].committed_pages), (operation, self.first, None, 0))
        after = self.repo._transaction(False, lambda memory, state: state.copy())
        self.assertEqual(after['operations'][operation][3], None)
        self.assertEqual(before['events'], after['events'])

    def test_recover_two_member_pages_and_terminal_head(self):
        first_op = self.reserve(self.first, 'first')
        first_page = self.commit(first_op, self.first, 'shared')
        second_op = self.reserve(self.second, 'second')
        self.commit(second_op, self.second, 'shared')
        next_first = replace(self.first, cursor='shared')
        pending = self.reserve(next_first, 'next-first')
        self.repo = self.fixture.reopen()
        rows = self.progress()
        self.assertEqual((rows[0].operation_id, rows[0].request, rows[0].committed_pages),
                         (pending, next_first, 1))
        self.assertIsNone(rows[0].page)
        self.assertEqual((rows[1].operation_id, rows[1].page.next_cursor,
                          rows[1].committed_pages), (second_op, 'shared', 1))
        self.assertEqual(first_page.next_cursor, 'shared')
        last_page = self.commit(pending, next_first)
        self.repo = self.fixture.reopen()
        self.assertEqual((self.progress()[0].page, self.progress()[0].committed_pages),
                         (last_page, 2))

    def test_fresh_process_reads_same_pending_checkpoint(self):
        operation = self.reserve(self.first, 'first')
        script = '''
import sys
from dsh_retrieval.adapters.task_storage.plan_sets import SQLitePlanSetApprovalRepository
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.models import Actor
class Auth:
    def require_access(self, *args): pass
actor = Actor('tenant', 'owner')
repo = SQLitePlanSetApprovalRepository(sys.argv[1],
    projects=LocalProjectAccess([('tenant', 'owner', 'demo', 'default')]), authorization=Auth())
approved = repo.get_current(actor, sys.argv[2])
print(repo.get_progress(actor, approved)[0].operation_id)
'''
        result = subprocess.run([sys.executable, '-c', script, str(self.fixture.path),
                                 self.fixture.task_id], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), operation)

    def test_progress_decodes_only_this_task_and_rechecks_grants(self):
        operation = self.reserve(self.first, 'first')
        self.commit(operation, self.first)
        def history(memory, state):
            saved, plan_id, request, page = state['operations'][operation]
            scope = replace(saved.scope, task_id='unrelated-task')
            historical = replace(saved, scope=scope, members=tuple(
                replace(member, plan=replace(member.plan, scope=scope))
                for member in saved.members))
            state['operations']['unrelated-operation'] = (historical, plan_id, request, page)
        self.repo._transaction(True, history)
        from dsh_retrieval.adapters.task_storage import sqlite
        original = sqlite.decode
        def decode(value):
            if (isinstance(value, dict) and value.get('type') == 'RetrievalScope'
                    and value['value']['task_id'] == 'unrelated-task'):
                raise AssertionError('decoded another task history')
            return original(value)
        with patch.object(sqlite, 'decode', side_effect=decode), patch.object(
                self.repo.tasks, '_load', side_effect=AssertionError('decoded full task ledger')):
            self.assertEqual(self.repo.get_current(self.actor, self.fixture.task_id), self.approved)
            self.assertEqual(self.progress()[0].committed_pages, 1)
            self.fixture.denied.add('library:lib1')
            self.assert_code('ACCESS_DENIED', self.progress)

    def test_dirty_task_projection_falls_back_without_stale_version(self):
        with self.repo.tasks._connect() as db:
            db.execute('UPDATE task_read_summaries_meta SET dirty=1 WHERE id=1')
        with patch.object(self.repo.tasks, '_load', wraps=self.repo.tasks._load) as loader:
            self.assertEqual(self.progress()[0].committed_pages, 0)
            self.assertEqual(loader.call_count, 1)
        # Wrong ownership must still fail, including with a dirty projection.
        from dsh_retrieval.models import Actor
        self.assert_code('NOT_FOUND', lambda: self.repo.get_progress(
            Actor(self.actor.tenant_id, 'another-owner'), self.approved))

    def test_revoked_library_denies_whole_snapshot(self):
        self.reserve(self.first, 'first')
        self.fixture.denied.add('library:lib1')
        self.assert_code('ACCESS_DENIED', self.progress)

    def test_replacement_and_generation_change_reject_old_checkpoint(self):
        self.reserve(self.first, 'first')
        second = self.fixture.make_proposal(2)
        self.repo.put_proposal(self.actor, second)
        self.fixture.confirm(second, key='replacement')
        self.assert_code('PLAN_NOT_CURRENT', self.progress)
        new_approved = self.repo.get_current(self.actor, self.fixture.task_id)
        def advance(memory, state):
            task = memory._tasks[self.fixture.task_id]
            memory._tasks[task.task_id] = replace(task, run_generation=task.run_generation + 1)
        self.repo._transaction(True, advance)
        self.assert_code('REVISION_CONFLICT', lambda: self.repo.get_progress(self.actor, new_approved))

    def test_orphan_operation_or_missing_cursor_index_fails_closed(self):
        operation = self.reserve(self.first, 'first')
        self.commit(operation, self.first, 'next')
        def corrupt_index(memory, state):
            state['cursors'].clear()
        self.repo._transaction(True, corrupt_index)
        self.assert_code('STORAGE_CORRUPT', self.progress)
        def orphan(memory, state):
            state['cursors'][(self.approved.proposal_digest, self.first.plan_id, 'next')] = operation
            state['operations']['orphan'] = state['operations'][operation]
        self.repo._transaction(True, orphan)
        self.assert_code('STORAGE_CORRUPT', self.progress)

    def test_coordinator_restarts_and_does_not_redispatch_committed_pages(self):
        calls = []
        class Gateway:
            def search(self, context, request):
                calls.append((request.plan_id, request.cursor))
                cursor = 'same-token' if request.cursor is None else None
                return SearchPage((), cursor, 'more_available' if cursor else 'exhausted',
                                  'snapshot', 'snap')
        gateway = Gateway()
        first = RetrievalCoordinator(self.repo, gateway).tick(self.actor, self.fixture.task_id, max_requests=2)
        self.assertEqual(first['network_attempts'], 2)
        self.repo = self.fixture.reopen()
        second = RetrievalCoordinator(self.repo, gateway).tick(self.actor, self.fixture.task_id, max_requests=2)
        self.assertTrue(second['pagination_stopped'])
        self.assertEqual(second['network_attempts'], 2)
        self.assertEqual([r.committed_pages for r in self.progress()], [2, 2])
        self.assertEqual(calls, [(self.first.plan_id, None), (self.second.plan_id, None),
                                 (self.first.plan_id, 'same-token'),
                                 (self.second.plan_id, 'same-token')])
        self.assertEqual(RetrievalCoordinator(self.fixture.reopen(), gateway).tick(
            self.actor, self.fixture.task_id)['network_attempts'], 0)

    def test_coordinator_retries_pending_operation_after_reopen(self):
        calls = []
        class Gateway:
            def search(self, context, request):
                calls.append(request)
                if len(calls) == 1:
                    raise ServiceError('SEARCH_TIMEOUT', 'fixture timeout', 504, retryable=True)
                return SearchPage((), None, 'exhausted', 'snapshot', 'snap')
        gateway = Gateway()
        first = RetrievalCoordinator(self.repo, gateway).tick(self.actor, self.fixture.task_id)
        self.assertEqual(first['outcomes'][0]['status'], 'retry_pending')
        operation = first['outcomes'][0]['operation_id']
        self.repo = self.fixture.reopen()
        second = RetrievalCoordinator(self.repo, gateway).tick(self.actor, self.fixture.task_id)
        self.assertEqual(second['outcomes'][0]['operation_id'], operation)
        self.assertEqual(calls, [self.first, self.first])
        self.assertEqual(self.progress()[0].committed_pages, 1)

    def test_revocation_between_gateway_and_commit_blocks_publication(self):
        class Gateway:
            def search(inner, context, request):
                self.fixture.denied.add('library:lib1')
                return SearchPage((), None, 'exhausted', 'snapshot', 'snap')
        self.assert_code('ACCESS_DENIED', lambda: RetrievalCoordinator(self.repo, Gateway()).tick(
            self.actor, self.fixture.task_id))
        self.fixture.denied.clear()
        row = self.progress()[0]
        self.assertIsNotNone(row.operation_id)
        self.assertIsNone(row.page)
        self.assertEqual(row.committed_pages, 0)


if __name__ == '__main__':
    unittest.main()
