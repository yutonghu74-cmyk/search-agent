from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
import unittest

import test_harness_journal as journal_fixtures
from dsh_retrieval.agent_retrieval import AgentRetrievalService
from dsh_retrieval.adapters.task_storage.agent_retrieval import SQLiteAgentRetrievalHandoffs
from dsh_retrieval.adapters.task_storage.retrieval import SQLiteRetrievalPlanRepository, load_state
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.harness.stage_tools import StageAuthority
from dsh_retrieval.integration_models import QueryVector, SearchPage, SearchHit
from dsh_retrieval.models import Actor
from dsh_retrieval.physical_query_preparation import TextQueryCapability


class AgentRetrievalTest(unittest.TestCase):
    def setUp(self):
        self.f = journal_fixtures.HarnessJournalTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.actor, self.task = self.f.actor, self.f.task
        self.context = dict(self.f.context)
        self.bindings = (self.f.binding,)
        self.denied, self.encodings, self.on_encode = False, [], lambda: None
        self.retrieval = SQLiteRetrievalPlanRepository(self.f.repo.path,
            projects=self.f.service.projects, authorization=self)
        self.handoffs = SQLiteAgentRetrievalHandoffs(journal=self.f.journal,
            retrieval=self.retrieval, authority=self.authority,
            context_provider=lambda actor, task, role: self.context)
        self.service = AgentRetrievalService(handoffs=self.handoffs, embeddings=self,
            capabilities=(TextQueryCapability(self.f.binding, 'image', 'filter@1', 10),))
        self.handle, self.token = self.f.start()

    def require_access(self, *args):
        if self.denied: raise ServiceError('ACCESS_DENIED', 'revoked', 403)

    def authority(self, invocation):
        task = self.f.repo.get(self.actor, self.task.task_id)
        return StageAuthority(self.actor, task, invocation.stage,
            json.loads(task.confirmed_proposal_json)['query_spec'], 0,
            self.bindings, (), ('facts-1',))

    def encode_query(self, context, text, binding):
        self.encodings.append((context, text, binding))
        self.on_encode()
        return QueryVector(binding.encoder_ref, (0.1,) * binding.vector_dimension)

    def finish(self):
        self.f.broker(self.handle, self.token).call('submit_retrieval_strategy', self.f.proposal)
        self.f.journal.finish(self.actor, self.handle, self.token)

    def prepare(self, **kwargs):
        return self.service.prepare(self.actor, self.handle, epoch=kwargs.get('epoch', 1), batch_size=kwargs.get('batch_size', 2))

    def confirm(self, plan, key='confirm'):
        return self.retrieval.confirm(self.actor, self.task.task_id, plan.request.plan_id,
            key=key, proposal_digest=plan.proposal_digest,
            expected_task_version=plan.scope.task_version, action='confirm')

    def plans(self):
        with closing(self.f.repo._connect()) as db:
            return load_state(db.execute('SELECT format,payload FROM retrieval_ledger WHERE id=1').fetchone())['proposals']

    def test_durable_strategy_to_existing_confirm_reserve_and_commit(self):
        self.finish()
        plan = self.prepare()
        self.assertIsNone(self.retrieval.get_current(self.actor, self.task.task_id))
        self.assertEqual(self.encodings[0][1], self.f.proposal['proposal']['query'])
        self.assertEqual(self.prepare(), plan)
        self.assertEqual(len(self.plans()), 1)
        # Reopen both ledgers before the normal business confirmation.
        self.retrieval = SQLiteRetrievalPlanRepository(self.f.repo.path,
            projects=self.f.service.projects, authorization=self)
        approved, replayed = self.confirm(plan)
        self.assertFalse(replayed)
        self.assertTrue(self.confirm(plan)[1])
        operation, _ = self.retrieval.reserve_page(self.actor, approved, approved.request, 'page')
        page = SearchPage((SearchHit(self.f.binding, 'hit', .8),), None, 'exhausted', 'snapshot', 'snapshot')
        self.assertFalse(self.retrieval.commit_page(self.actor, operation, approved, approved.request, page))
        self.assertTrue(self.retrieval.commit_page(self.actor, operation, approved, approved.request, page))
        current = self.f.repo.get(self.actor, self.task.task_id)
        self.assertIsNone(current.active_cleaning_operation_id)
        self.assertEqual(current.stage, 'retrieving')

    def test_unfinished_failed_and_report_only_do_not_encode(self):
        with self.assertRaises(ServiceError): self.prepare()
        self.f.broker(self.handle, self.token).call('submit_stage_report', {'proposal': {
            'outcome': 'stopped', 'summary': '预算耗尽', 'recommendation': 'stop', 'evidence_refs': ['facts-1']}})
        self.f.journal.finish(self.actor, self.handle, self.token)
        with self.assertRaises(ServiceError): self.prepare()
        self.assertEqual(self.encodings, [])
        self.assertEqual(self.plans(), {})

    def test_revocation_and_binding_input_changes_fail_before_encoding(self):
        self.finish()
        for change, reset in ((lambda: setattr(self, 'denied', True), lambda: setattr(self, 'denied', False)),
                (lambda: setattr(self, 'bindings', (replace(self.f.binding, index_version='new'),)),
                 lambda: setattr(self, 'bindings', (self.f.binding,))),
                (lambda: self.context.update(changed=True), lambda: self.context.pop('changed'))):
            change()
            with self.assertRaises(ServiceError): self.prepare()
            reset()
        self.assertEqual(self.encodings, [])

    def test_encoding_race_rolls_back_business_proposal(self):
        self.finish()
        self.on_encode = lambda: self.f.mutate_task(lambda task: setattr(task, 'run_generation', task.run_generation + 1))
        with self.assertRaises(ServiceError): self.prepare()
        self.assertEqual(self.plans(), {})

    def test_permission_revoked_during_encoding_does_not_publish(self):
        self.finish(); self.on_encode = lambda: setattr(self, 'denied', True)
        with self.assertRaises(ServiceError): self.prepare()
        self.assertEqual(self.plans(), {})

    def test_cancel_after_compilation_blocks_business_confirmation(self):
        self.finish(); plan = self.prepare()
        with self.f.journal._tx() as db:
            db.execute('DELETE FROM harness_stage_heads WHERE stage_digest=?', (self.f.stage.stage_digest,))
        with self.assertRaises(ServiceError) as error: self.confirm(plan)
        self.assertEqual(error.exception.code, 'AGENT_CONFIRMATION_REQUIRED')
        self.assertIsNone(self.retrieval.get_current(self.actor, self.task.task_id))

    def test_round_batch_limits_and_actor_isolation(self):
        self.finish()
        for args in ({'epoch': 4}, {'epoch': True}, {'batch_size': 11}, {'batch_size': False}):
            with self.assertRaises(ServiceError): self.prepare(**args)
        with self.assertRaises(ServiceError):
            self.service.prepare(Actor('tenant', 'other'), self.handle, epoch=1, batch_size=2)
        self.assertEqual(self.encodings, [])

    def test_concurrent_prepare_has_one_immutable_proposal(self):
        self.finish()
        with ThreadPoolExecutor(max_workers=4) as pool:
            plans = list(pool.map(lambda _: self.prepare(), range(4)))
        self.assertTrue(all(p == plans[0] for p in plans))
        self.assertEqual(len(self.plans()), 1)

    def test_corrupt_receipt_refuses_handoff(self):
        self.finish()
        with self.f.journal._tx() as db:
            db.execute("UPDATE harness_tool_receipts SET result='{}' WHERE kind='proposal'")
        with self.assertRaises(ServiceError) as error: self.prepare()
        self.assertEqual(error.exception.code, 'STORAGE_CORRUPT')
        self.assertEqual(self.encodings, [])

    def test_nondeterministic_encoder_cannot_mint_second_plan(self):
        self.finish(); self.prepare()
        self.service.embeddings = type('ChangedEncoder', (), {'encode_query': lambda _, context, text, binding:
            QueryVector(binding.encoder_ref, (.9,) * binding.vector_dimension)})()
        with self.assertRaises(ServiceError) as error: self.prepare()
        self.assertEqual(error.exception.code, 'PLAN_ID_CONFLICT')
        self.assertEqual(len(self.plans()), 1)

    def test_superseded_stage_cannot_confirm_prepared_plan(self):
        self.finish(); plan = self.prepare()
        self.f.approve(replace(self.f.stage, operation_id='next-strategy'))
        with self.assertRaises(ServiceError): self.confirm(plan)
        self.assertIsNone(self.retrieval.get_current(self.actor, self.task.task_id))

    def test_invalid_encoder_and_changed_plan_id_are_rejected(self):
        self.finish()
        self.service.embeddings = type('BadEncoder', (), {'encode_query': lambda *args: QueryVector('wrong', (.1,))})()
        with self.assertRaises(ServiceError): self.prepare()
        self.service.embeddings = self
        plan = self.prepare()
        snapshot = self.handoffs.load(self.actor, self.handle)
        with self.assertRaises(ServiceError):
            self.handoffs.save(self.actor, self.handle, snapshot,
                replace(plan, request=replace(plan.request, plan_id='bypass-origin')))

    def test_hard_delete_cascades_handoff_and_preserves_database_format(self):
        self.finish(); self.prepare()
        self.f.repo.hard_delete_conversation(self.actor, self.task.conversation_id)
        with closing(self.f.repo._connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_retrieval_handoffs').fetchone()[0], 0)
        from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
        SQLiteTaskRepository(self.f.repo.path)

    def test_legacy_task_delete_cascades_handoff(self):
        # Trusted repository fixture, retaining the public new-mode gate.
        self.f.journal.cancel(self.actor, self.handle)
        legacy = replace(self.task, task_id='legacy-task', conversation_id=None)
        self.f.repo.create(self.actor, 'legacy', 'sha256:' + '1' * 64, legacy)
        self.task = self.f.task = legacy
        from dsh_retrieval.retrieval_contracts import RetrievalScope
        self.f.stage = replace(self.f.stage, scope=RetrievalScope.from_task(legacy))
        self.f.inv = replace(self.f.inv, stage=self.f.stage, run_id='legacy-run')
        self.handle, self.token = self.f.start()
        self.finish(); self.prepare()
        self.f.repo.hard_delete_task(self.actor, legacy.task_id)
        with closing(self.f.repo._connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_retrieval_handoffs').fetchone()[0], 0)

    def test_retry_strategy_is_not_compiled_as_a_new_query(self):
        self.f.proposal['proposal']['action'] = 'retry'
        self.finish()
        with self.assertRaises(ServiceError) as error: self.prepare()
        self.assertEqual(error.exception.code, 'AGENT_RETRIEVAL_UNSUPPORTED')
        self.assertEqual(self.encodings, [])

    def test_receipt_changed_after_prepare_cannot_be_confirmed(self):
        self.finish(); plan = self.prepare()
        with self.f.journal._tx() as db:
            db.execute("UPDATE harness_tool_receipts SET result='{}' WHERE kind='proposal'")
        with self.assertRaises(ServiceError): self.confirm(plan)
        self.assertIsNone(self.retrieval.get_current(self.actor, self.task.task_id))
