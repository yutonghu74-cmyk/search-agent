from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.search_budget import SearchBudget
from dsh_retrieval.search_scheduling import SearchLimits
from dsh_retrieval.retrieval_contracts import RetrievalScope


class SearchBudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger'
        self.scope = RetrievalScope('t', 'a', 'p', 'task', 1, 1, 2, 'sha256:' + 'a'*64)

    def test_restart_replan_and_retry_cannot_reset_budget_and_no_refunds(self):
        limits = SearchLimits(max_calls=2, max_hit_slots=10)
        budget = SearchBudget(self.path, limits)
        with self.assertRaises(RuntimeError):
            with budget.attempt(self.scope, 3):
                raise RuntimeError('ambiguous transport failure')
        reopened = SearchBudget(self.path, limits)
        with reopened.attempt(replace(self.scope, task_version=9), 3):
            pass
        with self.assertRaises(ServiceError) as caught:
            with reopened.attempt(self.scope, 1):
                self.fail('budget exceeded')
        self.assertEqual(caught.exception.code, 'SEARCH_BUDGET_EXHAUSTED')
        status = reopened.snapshot(self.scope)
        self.assertEqual((status['calls_reserved'], status['hit_slots_reserved']), (2, 6))
        self.assertEqual(status['stop_reason'], 'call_limit')
        # Raising server caps does not silently expand an already started scope.
        raised = SearchBudget(self.path, SearchLimits(max_calls=100))
        with self.assertRaises(ServiceError):
            with raised.attempt(self.scope, 1):
                self.fail('silent budget expansion')
        with raised.attempt(replace(self.scope, generation=2), 1):
            pass

    def test_atomic_cross_instance_hit_slot_reservations(self):
        limits = SearchLimits(max_calls=20, max_hit_slots=7)
        budgets = [SearchBudget(self.path, limits) for _ in range(2)]
        def reserve(index):
            try:
                budgets[index % 2]._charge(self.scope, 2)
                return True
            except ServiceError as error:
                self.assertEqual(error.code, 'SEARCH_BUDGET_EXHAUSTED')
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(12)))
        self.assertEqual(sum(results), 3)
        status = budgets[0].snapshot(self.scope)
        self.assertEqual((status['calls_reserved'], status['hit_slots_reserved']), (3, 6))
        self.assertEqual(status['stop_reason'], 'hit_slot_limit')

    def test_manual_refill_share_user_and_global_slots_without_spending_on_busy(self):
        budget = SearchBudget(self.path, SearchLimits(workers=2))
        with budget.attempt(self.scope, 2):
            with self.assertRaises(ServiceError) as caught:
                with budget.attempt(replace(self.scope, task_id='other'), 2):
                    self.fail('same user overlapped')
            self.assertEqual(caught.exception.code, 'SEARCH_BUSY')
            b = replace(self.scope, owner_user_id='b', task_id='b')
            with budget.attempt(b, 2):
                with self.assertRaises(ServiceError):
                    with budget.attempt(replace(b, owner_user_id='c', task_id='c'), 2):
                        self.fail('global slots exceeded')
        self.assertEqual(budget.snapshot(replace(self.scope, task_id='other'))['calls_reserved'], 0)
        self.assertEqual(budget.snapshot(self.scope)['calls_reserved'], 1)

    def test_owner_and_tenant_partitions_and_storage_cleanup(self):
        from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
        SQLiteTaskRepository(self.path)
        budget = SearchBudget(self.path, SearchLimits(max_calls=1))
        with budget.attempt(self.scope, 1):
            pass
        for scope in (replace(self.scope, tenant_id='other'), replace(self.scope, owner_user_id='other')):
            self.assertEqual(budget.snapshot(scope)['calls_reserved'], 0)
        # New table remains accepted by the task repository on restart.
        SQLiteTaskRepository(self.path)

    def test_corrupt_counters_fail_closed_and_lower_cap_cannot_refund_spent_work(self):
        budget = SearchBudget(self.path, SearchLimits(max_calls=3, max_hit_slots=20))
        with budget.attempt(self.scope, 4):
            pass
        lowered = SearchBudget(self.path, SearchLimits(max_calls=1, max_hit_slots=2))
        with self.assertRaises(ServiceError) as caught:
            with lowered.attempt(self.scope, 1):
                self.fail('lowered cap was ignored')
        self.assertEqual(caught.exception.code, 'SEARCH_BUDGET_EXHAUSTED')
        self.assertEqual(lowered.snapshot(self.scope)['hit_slots_reserved'], 4)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE retrieval_execution_budgets SET calls_reserved=-1')
        for operation in (lambda: lowered.snapshot(self.scope), lambda: lowered._charge(self.scope, 1)):
            with self.assertRaises(ServiceError) as corrupt:
                operation()
            self.assertEqual(corrupt.exception.code, 'STORAGE_CORRUPT')

    def test_separate_processes_cannot_overspend_shared_durable_caps(self):
        import os
        import subprocess
        import sys
        script = '''import sys
from dsh_retrieval.search_budget import SearchBudget
from dsh_retrieval.search_scheduling import SearchLimits
from dsh_retrieval.retrieval_contracts import RetrievalScope
from dsh_retrieval.errors import ServiceError
b=SearchBudget(sys.argv[1],SearchLimits(max_calls=5,max_hit_slots=10))
s=RetrievalScope('t','a','p','task',1,1,2,'sha256:'+'a'*64)
n=0
for _ in range(10):
 try:
  b._charge(s,1)
  n+=1
 except ServiceError as e:
  if e.code!='SEARCH_BUDGET_EXHAUSTED':raise
print(n)
'''
        processes = [subprocess.Popen([sys.executable, '-c', script, str(self.path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ.copy()) for _ in range(2)]
        total = 0
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            total += int(stdout)
        self.assertEqual(total, 5)
        view = SearchBudget(self.path, SearchLimits()).snapshot(self.scope)
        self.assertEqual(view['calls_reserved'], 5)
