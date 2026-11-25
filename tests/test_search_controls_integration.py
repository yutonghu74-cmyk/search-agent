from dataclasses import replace
import sqlite3
import unittest
from unittest.mock import patch

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.search_budget import SearchBudget
from dsh_retrieval.search_scheduling import SearchLimits
from dsh_retrieval.retrieval_coordinator import RetrievalCoordinator
from dsh_retrieval.workflow_state import project_workflow


class SearchControlsIntegrationTest(unittest.TestCase):
    def local_fixture(self):
        from test_first_search_local import FirstSearchLocalTest
        fixture = FirstSearchLocalTest('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.local.close()
        fixture.select_and_approve()
        return fixture

    def test_manual_initial_refill_and_restart_share_one_durable_budget(self):
        f = self.local_fixture()
        search = f.local
        search.search_budget = SearchBudget(f.ledger, SearchLimits(max_calls=1, max_hit_slots=4))
        result = search.tick(f.actor, f.task_id, max_requests=1)
        self.assertEqual(result['network_attempts'], 1)
        approved = search.repository.get_current(f.actor, f.task_id)
        gateway = search._adapter_for_plan(f.actor, f.task_id, approved)
        head = next(row for row in search.repository.get_progress(f.actor, approved) if row.page is None)
        with patch.object(gateway, 'search', wraps=gateway.search) as network:
            with self.assertRaises(ServiceError) as caught:
                RetrievalCoordinator(search.repository, gateway, budget=search.search_budget).tick_position(
                    f.actor, approved, plan_id=head.plan_id, expected_cursor=None)
            self.assertEqual(caught.exception.code, 'SEARCH_BUDGET_EXHAUSTED')
            network.assert_not_called()
        reopened = f.make_local(start=False)
        with self.assertRaises(ServiceError):
            reopened.tick(f.actor, f.task_id, max_requests=1)
        status = reopened.progress(f.actor, f.task_id)
        self.assertFalse(status['pagination_stopped'])
        self.assertEqual(status['execution_budget']['calls_reserved'], 1)
        self.assertEqual(status['execution_budget']['stop_reason'], 'call_limit')
        self.assertEqual(reopened.candidates(f.actor, f.task_id)['raw_hit_count'], 2)
        # Task/Chat deletion removes the per-task budget in the same business delete transaction.
        task = f.tasks.get_task(f.actor, f.task_id)
        with self.assertRaises(ServiceError) as denied:
            f.tasks.delete_conversation(f.actor, task['conversation_id'])
        self.assertEqual(denied.exception.code, 'CONVERSATION_DELETE_FORBIDDEN')
        f.tasks.hard_delete_conversation(f.actor, task['conversation_id'])
        with sqlite3.connect(f.ledger) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM retrieval_execution_budgets').fetchone()[0], 0)

    def test_retry_consumes_budget_and_successful_page_replay_does_not(self):
        f = self.local_fixture()
        approved = f.local.repository.get_current(f.actor, f.task_id)
        budget = SearchBudget(f.ledger, SearchLimits(max_calls=2, max_hit_slots=8))
        gateway = f.local._adapter_for_plan(f.actor, f.task_id, approved)
        head = f.local.repository.get_progress(f.actor, approved)[0]
        coordinator = RetrievalCoordinator(f.local.repository, gateway, budget=budget)
        with patch.object(gateway, 'search', side_effect=ServiceError('TIMEOUT', 'timeout', 504, True)):
            first = coordinator.tick_position(f.actor, approved, plan_id=head.plan_id, expected_cursor=None)
        self.assertEqual(first['status'], 'retry_pending')
        coordinator.tick_position(f.actor, approved, plan_id=head.plan_id, expected_cursor=None)
        current = f.local.repository.get_progress(f.actor, approved)[0]
        self.assertIsNotNone(current.page)
        self.assertEqual(budget.snapshot(approved.scope)['calls_reserved'], 2)
        # Reading or rejecting the old position cannot issue a third external request.
        with patch.object(gateway, 'search', wraps=gateway.search) as network:
            try:
                coordinator.tick_position(f.actor, approved, plan_id=head.plan_id, expected_cursor=None)
            except ServiceError as error:
                self.assertEqual(error.code, 'REFILL_POSITION_CHANGED')
            network.assert_not_called()
        self.assertEqual(budget.snapshot(approved.scope)['calls_reserved'], 2)

    def test_budget_stop_is_neither_success_nor_automatic_candidate_confirmation(self):
        from test_workflow_state import WorkflowStateTest
        fixture = WorkflowStateTest('runTest');fixture.setUp()
        search = {**fixture.search, 'execution_budget': {'stop_reason': 'hit_slot_limit'}}
        value = project_workflow(fixture.task, fixture.progress, search=search)
        self.assertEqual(value['state'], 'retrieval_budget_exhausted')
        self.assertEqual(value['nodes'][1]['display_status'], '预算已用尽')
        self.assertNotEqual(value['nodes'][1]['node_status'], 'succeeded')
        self.assertFalse(any(row['id'] == 'confirm_candidates' for row in value['available_actions']))
        self.assertIsNone(value['counts']['accepted'])

    def test_combined_clip_uses_same_budget_for_initial_and_downstream_retry(self):
        from test_clip_search_jobs import CLIPSearchJobsTest
        from dsh_retrieval.clip_search_jobs import CLIPSearchExecutor
        fixture = CLIPSearchJobsTest('runTest');fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        budget = SearchBudget(fixture.path, SearchLimits(max_calls=1))
        executor = CLIPSearchExecutor(fixture.tasks, fixture.jobs, fixture.client, budget=budget)
        job_id, _ = fixture.create()
        result = executor.tick(job_id)
        self.assertEqual(result['network_attempts'], 1)
        with self.assertRaises(ServiceError) as caught:
            executor.tick_refill(job_id, plan_id=fixture.request.plan_id, expected_cursor='page-2')
        self.assertEqual(caught.exception.code, 'SEARCH_BUDGET_EXHAUSTED')
        self.assertEqual(len(fixture.client.calls), 1)

    def test_budget_stop_event_is_durable_and_repeated_denials_do_not_duplicate_it(self):
        from dsh_retrieval.task_events import TaskEventFeed
        f = self.local_fixture()
        approved = f.local.repository.get_current(f.actor, f.task_id)
        feed = TaskEventFeed(f.ledger)
        before = feed.read(f.actor, f.task_id, after_sequence=0, limit=100)['as_of_sequence']
        budget = SearchBudget(f.ledger, SearchLimits(max_calls=1))
        with budget.attempt(approved.scope, 1):
            pass
        for _ in range(2):
            with self.assertRaises(ServiceError):
                with budget.attempt(approved.scope, 1):
                    self.fail('budget exceeded')
        events = feed.read(f.actor, f.task_id, after_sequence=before, limit=100)['events']
        stops = [e for e in events if e['payload'].get('code') == 'SEARCH_BUDGET_EXHAUSTED']
        self.assertEqual(len(stops), 1)
        self.assertTrue(feed.can_replay(f.actor, f.tasks.get_task(f.actor, f.task_id),
                                       feed.task_mutation_sequence(f.task_id)))
        # An obsolete generation cannot send a stop into this generation's stream.
        old = replace(approved.scope, generation=approved.scope.generation + 1)
        with budget.attempt(old, 1):
            pass
        with self.assertRaises(ServiceError):
            with budget.attempt(old, 1):
                self.fail('budget exceeded')
        later = feed.read(f.actor, f.task_id, after_sequence=stops[0]['sequence'], limit=100)['events']
        self.assertFalse(any(e['payload'].get('code') == 'SEARCH_BUDGET_EXHAUSTED' for e in later))

    def test_refill_budget_stop_finishes_outbox_without_false_intake_or_retries(self):
        from test_refill_runtime import RefillRuntimeTest
        from dsh_retrieval.refill_runtime import build_refill_runtime
        f = RefillRuntimeTest('runTest');f.setUp();self.addCleanup(f.doCleanups)
        search = f.search()
        runtime = build_refill_runtime(f.path, f.service, search, start=False)
        self.addCleanup(runtime.close)
        position = runtime.adapter.list_refill_positions(f.actor, f.task_id, revision=1, generation=1)[0]
        task = f.tasks.get(f.actor, f.task_id)
        reservation, _ = runtime.reservations.reserve(f.actor, f.task_id, position,
            expected_revision=task.confirmed_revision, expected_generation=task.run_generation,
            expected_task_version=task.task_version)
        error = ServiceError('SEARCH_BUDGET_EXHAUSTED', 'budget exhausted', 409)
        with patch.object(search.executor, 'tick_refill', side_effect=error) as call:
            result = runtime.dispatch_reservation(reservation.reservation_id)
            self.assertEqual(result['status'], 'budget_exhausted')
            runtime.dispatch_reservation(reservation.reservation_id)
            self.assertEqual(call.call_count, 1)
        self.assertEqual(runtime.reservations.pending_auto_intake_events(), ())
        self.assertEqual(runtime.reservations.execution_evidence(f.actor, f.task_id)['stop_reason'], 'EXECUTION_LIMIT_REACHED')
        with self.assertRaises(ServiceError) as caught:
            runtime.reservations.record_execution_stop(f.actor, f.task_id, 'EXECUTION_LIMIT_REACHED',
                expected_revision=1, expected_generation=2)
        self.assertEqual(caught.exception.code, 'REVISION_CONFLICT')

    def test_concurrent_manual_tick_cannot_reissue_page_while_commit_is_pending(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        f = self.local_fixture()
        budget = SearchBudget(f.ledger, SearchLimits(max_calls=4))
        f.local.search_budget = budget
        approved = f.local.repository.get_current(f.actor, f.task_id)
        committing, release = Event(), Event()
        original = f.local.repository.commit_page
        def delayed(*args, **kwargs):
            committing.set()
            self.assertTrue(release.wait(3))
            return original(*args, **kwargs)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(f.local.repository, 'commit_page', side_effect=delayed):
                future = pool.submit(f.local.tick, f.actor, f.task_id, max_requests=1)
                try:
                    self.assertTrue(committing.wait(3))
                    with self.assertRaises(ServiceError) as caught:
                        f.local.tick(f.actor, f.task_id, max_requests=1)
                    self.assertEqual(caught.exception.code, 'SEARCH_BUSY')
                    self.assertEqual(budget.snapshot(approved.scope)['calls_reserved'], 1)
                finally:
                    release.set()
                self.assertEqual(future.result()['network_attempts'], 1)
