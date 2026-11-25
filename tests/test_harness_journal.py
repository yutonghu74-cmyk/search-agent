from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import subprocess
import sys
from dataclasses import asdict
import threading
import time
import unittest

from draft_helpers import make_service, confirmation
import test_agent_contracts as contract_fixtures
import test_harness_port as port_fixtures
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.adapters.task_storage import ledger_rows
from dsh_retrieval.adapters.task_storage.harness import SQLiteHarnessJournal
from dsh_retrieval.agent_contracts import AgentStageTask
from dsh_retrieval.harness.contracts import HarnessInvocation, RoleDefinition, RuntimeRelease, AdapterRegistry
from dsh_retrieval.harness.durable import DurableHarnessRunner, DurableToolBroker
from dsh_retrieval.harness.stage_tools import stage_catalog, StageAuthority
from dsh_retrieval.adapters.harness.local import LocalHarnessAdapter
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.query_spec import digest
from dsh_retrieval.retrieval_contracts import RetrievalScope


class HarnessJournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.repo = SQLiteTaskRepository(Path(self.tmp.name) / 'ledger.db')
        self.service = make_service(self.repo); self.actor = Actor('tenant', 'owner')
        request = confirmation(self.service, self.actor, body=contract_fixtures.body())
        # Trusted repository fixture: public TaskService execution gate remains untouched.
        self.task, _ = self.repo.confirm_draft(self.actor, 'fixture', request['draft_id'],
                                             request['expected_draft_version'], request['proposal_digest'])
        self.now = time.time(); self.clock = lambda: self.now
        self.journal = self.reopen()
        self.context = {'input': 'authorized-snapshot'}
        self.spec = json.loads(self.task.confirmed_proposal_json)['query_spec']
        self.stage = AgentStageTask(RetrievalScope.from_task(self.task), 'operation', 'retrieval',
            digest(self.context), digest(self.spec['agent_requirements']), digest({'approval': 1}),
            'policy@1', digest({'model': 1}), 'budget')
        self.role = RoleDefinition('role@1', 'retrieval', 'Plan.',
            ('get_stage_context', 'submit_retrieval_strategy', 'submit_stage_report'), 'strategy@1')
        self.release = RuntimeRelease('fixture-release', 'fixture', digest({'artifact': 1}))
        self.inv = HarnessInvocation(self.stage, 'run', 'attempt', self.role, self.release,
                                    'model', 'plan', self.now + 100, json.dumps(self.context))
        port = port_fixtures.HarnessPortTest(); port.setUp()
        self.proposal, self.binding = port.proposal, port.binding
        def authority(inv):
            task = self.repo.get(self.actor, inv.stage.scope.task_id)
            return StageAuthority(self.actor, task, inv.stage,
                json.loads(task.confirmed_proposal_json)['query_spec'], 10, (self.binding,), (), ('facts-1',))
        self.catalog, self.guard = stage_catalog(authority)

    def reopen(self):
        return SQLiteHarnessJournal(self.repo, self.service.projects, clock=self.clock)

    def approve(self, stage=None):
        stage = stage or self.stage
        self.journal.approve(self.actor, stage, action='confirm', proposal_digest=stage.stage_digest)

    def start(self, invocation=None, allocation=3):
        invocation = invocation or self.inv
        self.approve(invocation.stage)
        handle = self.journal.enqueue(self.actor, invocation, model_calls=allocation)
        token = self.journal.claim(self.actor, handle, lease_seconds=10)
        return handle, token

    def broker(self, handle, token, inv=None):
        return DurableToolBroker(inv or self.inv, self.catalog, guard=self.guard,
                                journal=self.journal, actor=self.actor, handle=handle, token=token)

    def mutate_task(self, operation):
        with closing(self.repo._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            memory = self.repo._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            operation(memory._tasks[self.task.task_id])
            ledger_rows.write_memory(db, memory)
            self.repo._sync_task_read_summaries(db, memory)

    def test_explicit_stage_confirmation_and_idempotent_enqueue(self):
        with self.assertRaises(ServiceError): self.journal.enqueue(self.actor, self.inv, model_calls=3)
        with self.assertRaises(ServiceError): self.journal.approve(self.actor, self.stage, action='auto', proposal_digest=self.stage.stage_digest)
        handle, token = self.start()
        self.assertEqual(self.journal.enqueue(self.actor, self.inv, model_calls=3), handle)
        self.assertIsNone(self.journal.claim(self.actor, handle))
        with self.assertRaises(ServiceError): self.journal.enqueue(self.actor, self.inv, model_calls=4)
        self.assertEqual(self.journal.read(self.actor, handle)['budget_reserved'], 3)

    def test_concurrent_connections_claim_once_and_reserve_once(self):
        self.approve(); handle = self.journal.enqueue(self.actor, self.inv, model_calls=3)
        def claim(_): return self.reopen().claim(self.actor, handle)
        with ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(claim, range(16)))
        self.assertEqual(sum(v is not None for v in results), 1)
        self.assertEqual(self.journal.read(self.actor, handle)['budget_reserved'], 3)

    def test_restarted_worker_can_discover_queued_work_with_owner_isolation(self):
        self.approve()
        handle = self.journal.enqueue(self.actor, self.inv, model_calls=3)
        self.assertEqual(self.reopen().pending(self.actor, 'demo'), (handle,))
        self.assertEqual(self.reopen().pending(Actor('tenant', 'other'), 'demo'), ())
        self.assertEqual(self.reopen().pending(self.actor, 'different-project'), ())
        token = self.reopen().claim(self.actor, handle)
        self.assertIsNotNone(token)
        self.assertEqual(self.reopen().pending(self.actor, 'demo'), ())

    def test_model_admission_precedes_forward_and_is_not_replayable(self):
        handle, token = self.start(allocation=2)
        self.journal.model_admit(self.actor, handle, token, 1)
        with self.assertRaises(ServiceError): self.journal.model_admit(self.actor, handle, token, 1)
        with self.assertRaises(ServiceError): self.journal.model_admit(self.actor, handle, token, True)
        self.journal.model_admit(self.actor, handle, token, 2)
        with self.assertRaises(ServiceError): self.journal.model_admit(self.actor, handle, token, 3)
        row = self.journal.read(self.actor, handle)
        self.assertEqual((row['admitted'], row['budget_reserved']), (2, 2))

    def test_expired_unknown_is_not_requeued_or_refunded(self):
        handle, token = self.start(allocation=7)
        self.journal.model_admit(self.actor, handle, token, 1)
        self.now += 11
        self.assertEqual(self.reopen().recover(self.actor, handle), 'unknown')
        with self.assertRaises(ServiceError): self.journal.model_admit(self.actor, handle, token, 2)
        self.assertIsNone(self.journal.claim(self.actor, handle))
        next_inv = replace(self.inv, attempt_id='next')
        next_handle = self.journal.enqueue(self.actor, next_inv, model_calls=4)
        with self.assertRaises(ServiceError): self.journal.claim(self.actor, next_handle)
        self.assertEqual(self.journal.read(self.actor, handle)['budget_reserved'], 7)

    def test_persisted_proposal_recovers_after_receipt_before_finish_crash(self):
        handle, token = self.start()
        result = self.broker(handle, token).call('submit_retrieval_strategy', self.proposal)
        self.now += 11
        journal = self.reopen()
        self.assertEqual(journal.recover(self.actor, handle), 'finished')
        row = journal.read(self.actor, handle)
        self.assertEqual(row['outputs'][0]['arguments'], self.proposal)
        self.assertEqual(row['outputs'][0]['receipt'], result)
        self.assertEqual(row['budget_reserved'], 3)
        with self.assertRaises(ServiceError): self.broker(handle, token).call('get_stage_context', {})

    def test_durable_receipt_idempotency_rechecks_generation_and_permission(self):
        handle, token = self.start(); broker = self.broker(handle, token)
        first = broker.call('submit_retrieval_strategy', self.proposal)
        self.assertEqual(self.broker(handle, token).call('submit_retrieval_strategy', self.proposal), first)
        altered = json.loads(json.dumps(self.proposal)); altered['proposal']['query'] = 'another'
        with self.assertRaises(ServiceError): broker.call('submit_retrieval_strategy', altered)
        self.mutate_task(lambda task: setattr(task, 'run_generation', task.run_generation + 1))
        with self.assertRaises(ServiceError): broker.call('submit_retrieval_strategy', self.proposal)
        with self.assertRaises(ServiceError): self.journal.model_admit(self.actor, handle, token, 1)
        self.journal.cancel(self.actor, handle)  # Cleanup remains possible after generation invalidation.

    def test_cancel_fences_budget_and_tools_without_refund(self):
        handle, token = self.start(); self.journal.cancel(self.actor, handle)
        with self.assertRaises(ServiceError): self.journal.model_admit(self.actor, handle, token, 1)
        with self.assertRaises(ServiceError): self.broker(handle, token).call('get_stage_context', {})
        self.assertEqual(self.journal.read(self.actor, handle)['budget_reserved'], 3)
        with self.assertRaises(ServiceError): self.journal.read(Actor('tenant', 'other'), handle)

    def test_roles_and_new_budget_refs_cannot_reset_generation_budget(self):
        handle, token = self.start(allocation=8)
        self.journal.cancel(self.actor, handle)
        stage = replace(self.stage, role='cleaning', operation_id='clean', budget_ref='different')
        self.approve(stage)
        inv = replace(self.inv, stage=stage, role=replace(self.role, role='cleaning'), attempt_id='clean')
        handle2 = self.journal.enqueue(self.actor, inv, model_calls=3)
        with self.assertRaises(ServiceError): self.journal.claim(self.actor, handle2)

    def test_completed_outputs_and_events_survive_new_python_process(self):
        handle, token = self.start()
        self.broker(handle, token).call('submit_retrieval_strategy', self.proposal)
        self.journal.finish(self.actor, handle, token)
        script = """
import sys,json
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.adapters.task_storage.harness import SQLiteHarnessJournal
from dsh_retrieval.harness.contracts import RuntimeHandle
from dsh_retrieval.models import Actor
from draft_helpers import make_service
repo=SQLiteTaskRepository(sys.argv[1]); service=make_service(repo)
row=SQLiteHarnessJournal(repo,service.projects).read(Actor('tenant','owner'),RuntimeHandle(**json.loads(sys.argv[2])))
print(json.dumps({'status':row['status'],'outputs':row['outputs'],'reserved':row['budget_reserved']}))
"""
        result = subprocess.run([sys.executable, '-c', script, str(self.repo.path), json.dumps(asdict(handle))],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row['status'], 'finished')
        self.assertEqual(row['outputs'][0]['arguments'], self.proposal)
        self.assertEqual(row['reserved'], 3)
        events = self.journal.read(self.actor, handle)['events']
        self.assertEqual(self.reopen().read(self.actor, handle, after_cursor=events[-1].cursor)['events'], ())

    def test_revocation_during_handler_rolls_back_receipt(self):
        handle, token = self.start()
        broker = self.broker(handle, token)
        definition, handler, args, _, fingerprint = broker.prepare_call('submit_retrieval_strategy', self.proposal)
        def revoked(inv, arguments):
            result = handler(inv, arguments)
            self.service.projects.grants.clear()
            return result
        with self.assertRaises(ServiceError):
            self.journal.tool_call(self.actor, handle, token, definition, args, fingerprint, revoked)
        with self.journal._tx() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM harness_tool_receipts').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT reserved FROM harness_model_budgets').fetchone()[0], 3)

    def test_old_confirmation_replay_does_not_restore_superseded_stage(self):
        handle, token = self.start()
        next_stage = replace(self.stage, operation_id='new-input', input_digest=digest({'next': 1}))
        self.approve(next_stage)
        with self.assertRaises(ServiceError): self.approve()
        with self.assertRaises(ServiceError): self.journal.model_admit(self.actor, handle, token, 1)

    def test_only_one_live_attempt_per_stage_even_across_run_ids(self):
        handle, token = self.start()
        other = self.journal.enqueue(self.actor, replace(self.inv, run_id='other-run'), model_calls=3)
        self.assertIsNone(self.journal.claim(self.actor, other))
        self.journal.cancel(self.actor, handle)
        self.assertIsNotNone(self.journal.claim(self.actor, other))
        self.assertEqual(self.journal.read(self.actor, other)['budget_reserved'], 6)

    def test_storage_unavailable_never_falls_back_to_memory(self):
        handle, token = self.start()
        with self.journal._tx() as db:
            db.execute('UPDATE harness_journal_meta SET format=99 WHERE id=1')
        with self.assertRaises(ServiceError) as caught: self.reopen()
        self.assertEqual(caught.exception.code, 'STORAGE_FORMAT_UNSUPPORTED')

    def test_hard_delete_cascades_journal_and_fences_existing_lease(self):
        handle, token = self.start()
        self.broker(handle, token).call('submit_retrieval_strategy', self.proposal)
        self.repo.hard_delete_conversation(self.actor, self.task.conversation_id)
        with self.journal._tx() as db:
            for table in ('harness_stages', 'harness_stage_heads', 'harness_model_budgets',
                          'harness_attempts', 'harness_tool_receipts', 'harness_attempt_tools', 'harness_run_events'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0, table)
        with self.assertRaises(ServiceError): self.journal.model_admit(self.actor, handle, token, 1)
        # The original repository can still reopen after the new journal tables exist.
        SQLiteTaskRepository(self.repo.path)

    def test_legacy_task_delete_also_purges_its_journal_only(self):
        original = self.task
        legacy = replace(original, task_id='legacy-task', conversation_id=None)
        self.repo.create(self.actor, 'legacy-create', digest({'legacy': 1}), legacy)
        self.task = legacy
        self.stage = replace(self.stage, scope=RetrievalScope.from_task(legacy))
        self.inv = replace(self.inv, stage=self.stage)
        handle, token = self.start()
        self.broker(handle, token).call('submit_retrieval_strategy', self.proposal)
        self.repo.hard_delete_task(self.actor, legacy.task_id)
        with self.journal._tx() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM harness_attempts').fetchone()[0], 0)
        self.assertEqual(self.repo.get(self.actor, original.task_id).task_id, original.task_id)

    def test_local_durable_runner_recovers_outputs_without_running_model_again(self):
        calls = []
        class Adapter(LocalHarnessAdapter):
            def describe(self):
                row = super().describe()
                return replace(row, capabilities=row.capabilities | {'model_call_admission'})
        def drive(inv, tools, stop):
            calls.append(1); tools.admit_model_call(1)
            tools.call('submit_retrieval_strategy', self.proposal)
        adapter = Adapter('fixture', drive, artifact_digest=self.release.artifact_digest)
        registry = AdapterRegistry(); registry.register('fixture', adapter)
        def runner():
            return DurableHarnessRunner(registry, {'retrieval': self.release}, {'retrieval': self.role},
                self.catalog, guard=self.guard, model_binding=lambda _: self.stage.model_binding_digest,
                journal=self.reopen(), actor=self.actor)
        self.approve(); first = runner()
        handle = first.enqueue(self.stage, run_id='run', attempt_id='attempt', model_profile_ref='model',
            task_kind='plan', deadline=self.inv.deadline, context_json=self.inv.context_json, model_calls=3)
        self.assertTrue(first.dispatch(handle, lease_seconds=1))
        for _ in range(300):
            # No Runner/UI polling: the dispatch heartbeat must persist completion itself.
            if self.journal.read(self.actor, handle)['status'] == 'finished': break
            time.sleep(.01)
        else: self.fail('durable run did not finish')
        second = runner()
        self.assertEqual(second.outputs(handle)[0]['arguments'], self.proposal)
        self.assertFalse(second.dispatch(handle))
        self.assertEqual(calls, [1])
        first.close(); second.close()
