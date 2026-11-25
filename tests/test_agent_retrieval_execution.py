from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import subprocess
import sys
import unittest

import test_agent_retrieval as fixtures
from dsh_retrieval.adapters.task_storage.retrieval import SQLiteRetrievalPlanRepository
from dsh_retrieval.agent_retrieval import AgentRetrievalExecutor
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import SearchHit, SearchPage
from dsh_retrieval.models import Actor
from dsh_retrieval.search_budget import SearchBudget
from dsh_retrieval.search_scheduling import SearchLimits


class AgentRetrievalExecutionTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AgentRetrievalTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.finish()
        self.proposal = self.f.prepare()
        self.actor, self.task = self.f.actor, self.f.task
        self.requests, self.on_search = [], lambda: None
        self.budget = SearchBudget(self.f.f.repo.path, SearchLimits(max_calls=2, max_hit_slots=4))
        self.page = SearchPage((SearchHit(self.f.f.binding, 'hit', .8),), 'page-2', 'more_available', 'snapshot', 'snapshot')
        self.executor = self.build()

    def build(self):
        return AgentRetrievalExecutor(retrieval=self.f.retrieval, gateway=self, budget=self.budget)

    def search(self, context, request):
        self.requests.append((context, request))
        self.on_search()
        return self.page

    def approve(self):
        self.approved, _ = self.f.confirm(self.proposal)
        return self.approved

    def execute(self, **overrides):
        body = {'plan_id': self.approved.request.plan_id, 'proposal_digest': self.approved.proposal_digest}
        body.update(overrides)
        return self.executor.execute_page(self.actor, self.task.task_id, **body)

    def test_explicit_confirmation_required_and_unapproved_calls_do_no_network_work(self):
        with self.assertRaises(ServiceError) as error:
            self.executor.execute_page(self.actor, self.task.task_id,
                plan_id=self.proposal.request.plan_id, proposal_digest=self.proposal.proposal_digest)
        self.assertEqual(error.exception.code, 'PLAN_NOT_APPROVED')
        self.assertEqual(self.requests, [])
        self.assertEqual(self.budget.snapshot(self.proposal.scope)['calls_reserved'], 0)

    def test_confirmed_strategy_search_pages_and_restart_use_existing_immutable_ledger(self):
        self.approve()
        first = self.execute()
        self.assertEqual(first['status'], 'committed')
        self.assertEqual(first['network_attempts'], 1)
        self.assertEqual(first['operation'].page, self.page)
        operation_id = first['operation'].operation_id
        self.f.retrieval = SQLiteRetrievalPlanRepository(self.f.f.repo.path,
            projects=self.f.f.service.projects, authorization=self.f)
        self.executor = self.build()
        replay = self.execute()
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['operation'].operation_id, operation_id)
        self.assertEqual(replay['network_attempts'], 0)
        self.assertEqual(self.executor.read(self.actor, self.task.task_id, operation_id)['status'], 'committed')
        self.page = SearchPage((), None, 'exhausted', 'snapshot', 'snapshot')
        second = self.execute(cursor='page-2')
        self.assertEqual(second['operation'].page.hits, ())
        self.assertEqual(second['operation'].page.stop_reason, 'exhausted')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.budget.snapshot(self.approved.scope)['calls_reserved'], 2)
        current = self.f.f.repo.get(self.actor, self.task.task_id)
        self.assertEqual(current.stage, 'retrieving')
        self.assertIsNone(current.active_cleaning_operation_id)

    def test_concurrent_executors_dispatch_same_page_once(self):
        self.approve()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.execute(), range(6)))
        self.assertEqual(sum(item['network_attempts'] for item in results), 1)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len({item['operation'].operation_id for item in results}), 1)
        self.assertEqual(self.budget.snapshot(self.approved.scope)['calls_reserved'], 1)

    def test_separate_processes_share_the_page_dispatch_fence(self):
        self.approve()
        script = '''import json, sys
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.task_storage.retrieval import SQLiteRetrievalPlanRepository
from dsh_retrieval.agent_retrieval import AgentRetrievalExecutor
from dsh_retrieval.integration_models import SearchPage
from dsh_retrieval.models import Actor
from dsh_retrieval.search_budget import SearchBudget
from dsh_retrieval.search_scheduling import SearchLimits
class Authority:
 def require_access(self, *args): pass
class Gateway:
 def search(self, context, request):
  return SearchPage((), None, 'exhausted', 'snapshot', 'snapshot')
actor=Actor('tenant','owner')
repository=SQLiteRetrievalPlanRepository(sys.argv[1],
 projects=LocalProjectAccess([('tenant','owner','demo','default')]), authorization=Authority())
executor=AgentRetrievalExecutor(retrieval=repository, gateway=Gateway(),
 budget=SearchBudget(sys.argv[1],SearchLimits(max_calls=2,max_hit_slots=4)))
result=executor.execute_page(actor,sys.argv[2],plan_id=sys.argv[3],proposal_digest=sys.argv[4])
print(json.dumps({'network_attempts':result['network_attempts'],'operation_id':result['operation'].operation_id}))
'''
        args = [sys.executable, '-c', script, str(self.f.f.repo.path), self.task.task_id,
                self.approved.request.plan_id, self.approved.proposal_digest]
        processes = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        values = []
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, stderr)
                values.append(json.loads(stdout))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill(); process.communicate()
        self.assertEqual(sum(value['network_attempts'] for value in values), 1)
        self.assertEqual(len({value['operation_id'] for value in values}), 1)
        self.assertEqual(self.budget.snapshot(self.approved.scope)['calls_reserved'], 1)

    def test_interrupted_reservation_is_unknown_and_never_automatically_sent(self):
        self.approve()
        operation_id, _ = self.f.retrieval.reserve_page(self.actor, self.approved, self.approved.request, 'crash-before-send')
        result = self.execute()
        self.assertEqual(result['operation'].operation_id, operation_id)
        self.assertEqual(result['status'], 'pending_or_unknown')
        self.assertEqual(self.requests, [])
        self.assertEqual(self.budget.snapshot(self.approved.scope)['calls_reserved'], 0)

    def test_transport_failure_remains_unknown_across_restart_without_second_charge(self):
        self.approve()
        def timeout(): raise ServiceError('SEARCH_TIMEOUT', 'ambiguous connector completion', 504, True)
        self.on_search = timeout
        with self.assertRaises(ServiceError): self.execute()
        self.executor = self.build()
        result = self.execute()
        self.assertEqual(result['status'], 'pending_or_unknown')
        self.assertEqual(result['network_attempts'], 0)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.budget.snapshot(self.approved.scope)['calls_reserved'], 1)
        # Trusted connector-specific reconciliation can publish the original page.
        operation = result['operation']
        self.f.retrieval.commit_page(self.actor, operation.operation_id, operation.plan, operation.request, self.page)
        self.assertEqual(self.execute()['status'], 'committed')
        self.assertEqual(len(self.requests), 1)

    def test_invalid_cursor_identity_and_cross_owner_do_not_dispatch(self):
        self.approve()
        for body in ({'cursor': 'invented'}, {'cursor': ''}, {'cursor': True},
                     {'proposal_digest': self.proposal.proposal_digest}, {'plan_id': 'another-plan'}):
            with self.assertRaises(ServiceError): self.execute(**body)
        with self.assertRaises(ServiceError):
            self.executor.execute_page(Actor('tenant', 'other'), self.task.task_id,
                plan_id=self.approved.request.plan_id, proposal_digest=self.approved.proposal_digest)
        self.assertEqual(self.requests, [])

    def test_revocation_before_or_during_search_prevents_publication(self):
        self.approve(); self.f.denied = True
        with self.assertRaises(ServiceError): self.execute()
        self.assertEqual(self.requests, [])
        self.f.denied = False
        self.on_search = lambda: setattr(self.f, 'denied', True)
        with self.assertRaises(ServiceError): self.execute()
        self.f.denied = False
        self.assertEqual(self.execute()['status'], 'pending_or_unknown')

    def test_old_generation_callback_cannot_publish(self):
        self.approve()
        self.on_search = lambda: self.f.f.mutate_task(lambda task: setattr(task, 'run_generation', task.run_generation + 1))
        with self.assertRaises(ServiceError): self.execute()
        self.assertEqual(len(self.requests), 1)

    def test_budget_exhaustion_blocks_next_page_and_keeps_truthful_unknown_intent(self):
        self.approve()
        self.budget = SearchBudget(self.f.f.repo.path, SearchLimits(max_calls=1, max_hit_slots=2))
        self.executor = self.build()
        self.execute()
        with self.assertRaises(ServiceError) as error: self.execute(cursor='page-2')
        self.assertEqual(error.exception.code, 'SEARCH_BUDGET_EXHAUSTED')
        self.assertEqual(self.execute(cursor='page-2')['status'], 'pending_or_unknown')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.budget.snapshot(self.approved.scope)['calls_reserved'], 1)

    def test_invalid_page_cannot_be_mistaken_for_empty_success(self):
        self.approve()
        self.page = replace(self.page, next_cursor=None)
        with self.assertRaises(ServiceError) as error: self.execute()
        self.assertEqual(error.exception.code, 'INVALID_SEARCH_PAGE')
        self.assertEqual(self.execute()['status'], 'pending_or_unknown')
        self.assertEqual(len(self.requests), 1)

    def test_budget_port_is_mandatory(self):
        with self.assertRaises(ValueError):
            AgentRetrievalExecutor(retrieval=self.f.retrieval, gateway=self, budget=None)
