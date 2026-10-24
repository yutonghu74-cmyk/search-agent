from dataclasses import replace
import unittest

from tests.core import test_plan_set_storage as fixtures
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import SearchPage
from dsh_retrieval.multi_branch_contracts import PlanSetMemberProgress
from dsh_retrieval.retrieval_coordinator import RetrievalCoordinator


class FixtureLedger:
    """Test-only recovery port; actual SQLite recovery is a separate core test."""
    def __init__(self, approved):
        self.current = approved
        self.rows = {m.plan.request.plan_id: PlanSetMemberProgress(m.plan.request.plan_id,
            m.plan.request, None, None, 0) for m in approved.members}
        self.reservations = 0

    def get_current(self, actor, task_id):
        return self.current

    def get_progress(self, actor, approved):
        if approved != self.current:
            raise ServiceError('PLAN_NOT_CURRENT', 'changed', 409)
        return tuple(self.rows.values())

    def reserve_page(self, actor, approved, *, plan_id, request, key):
        row = self.rows[plan_id]
        if row.request == request and row.operation_id:
            return row.operation_id, True
        self.reservations += 1
        operation = 'operation-' + str(self.reservations)
        self.rows[plan_id] = PlanSetMemberProgress(plan_id, request, operation, None, row.committed_pages)
        return operation, False

    def commit_page(self, actor, approved, *, plan_id, request, operation_id, page):
        self.get_progress(actor, approved)
        row = self.rows[plan_id]
        self.rows[plan_id] = replace(row, page=page, committed_pages=row.committed_pages + 1)
        return False


class CoordinatorTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PlanSetStorageTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.repo.put_proposal(f.actor, f.proposal)
        self.approved, _ = f.confirm()
        self.repo = FixtureLedger(self.approved)
        self.coordinator = RetrievalCoordinator(self.repo, self)
        self.calls, self.fail, self.hook = [], False, lambda: None

    def search(self, context, request):
        self.calls.append(request)
        self.hook()
        if self.fail:
            raise ServiceError('SEARCH_TIMEOUT', 'fixture failure', 504, retryable=True)
        return SearchPage((), 'next' if request.cursor is None else None,
            'more_available' if request.cursor is None else 'exhausted', 'snapshot', 'snapshot')

    def tick(self, **kwargs):
        return self.coordinator.tick(self.fixture.actor, self.fixture.task_id, **kwargs)

    def test_two_members_isolated_and_terminal_not_dispatched(self):
        result = self.tick(max_requests=2)
        self.assertFalse(result['pagination_stopped'])
        self.assertEqual([m['committed_pages'] for m in result['members']], [1, 1])
        result = self.tick(max_requests=2)
        self.assertTrue(result['pagination_stopped'])
        self.assertEqual([r.cursor for r in self.calls], [None, None, 'next', 'next'])
        self.assertEqual(self.tick(max_requests=2)['network_attempts'], 0)

    def test_retry_uses_same_reservation_and_new_instance(self):
        self.fail = True
        result = self.tick()
        operation = result['outcomes'][0]['operation_id']
        self.assertEqual(result['outcomes'][0]['status'], 'retry_pending')
        self.fail = False
        self.coordinator = RetrievalCoordinator(self.repo, self)
        result = self.tick()
        self.assertEqual(result['outcomes'][0]['operation_id'], operation)
        self.assertEqual(self.repo.reservations, 1)
        self.assertEqual(self.calls[0], self.calls[1])

    def test_transient_failure_can_leave_other_member_progress(self):
        original = self.search
        def search(context, request):
            self.fail = request.plan_id == self.approved.members[0].plan.request.plan_id
            return original(context, request)
        self.coordinator.gateway = type('Gateway', (), {'search': staticmethod(search)})()
        result = self.tick(max_requests=2)
        self.assertEqual([r['status'] for r in result['outcomes']], ['retry_pending', 'committed'])

    def test_plan_change_after_network_rejects_callback(self):
        self.hook = lambda: setattr(self.repo, 'current', None)
        with self.assertRaises(ServiceError):
            self.tick()
        self.assertTrue(all(row.page is None for row in self.repo.rows.values()))

    def test_missing_approval_or_recovery_never_dispatches(self):
        with self.assertRaises(ServiceError):
            RetrievalCoordinator(object(), self)
        self.repo.current = None
        with self.assertRaises(ServiceError):
            self.tick()
        self.assertEqual(self.calls, [])

    def test_corrupt_progress_or_limits_fail_before_network(self):
        for maximum in (0, 65, True):
            with self.assertRaises(ServiceError):
                self.tick(max_requests=maximum)
        plan_id = next(iter(self.repo.rows))
        self.repo.rows[plan_id] = replace(self.repo.rows[plan_id], committed_pages=-1)
        with self.assertRaises(ServiceError):
            self.tick()
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
