from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import ThreadingHTTPServer
import json
import threading
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import test_harness_journal as fixtures
from dsh_retrieval.adapters.harness.local import LocalHarnessAdapter
from dsh_retrieval.api import create_handler
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.harness.contracts import AdapterRegistry
from dsh_retrieval.harness.durable import DurableHarnessRunner
from dsh_retrieval.harness.roles import DEFAULT_ROLES
from dsh_retrieval.harness.stage_service import AgentPlanningService
from dsh_retrieval.adapters.task_storage.agent_stages import AgentStageStore
from dsh_retrieval.models import Actor
from dsh_retrieval.multi_rule_contracts import DetectionStep
from dsh_retrieval.agent_contracts import ProcessingCapability
from dsh_retrieval.harness.stage_tools import StageAuthority, stage_catalog


class AgentStageApiTest(unittest.TestCase):
    def setUp(self):
        self.domain = fixtures.HarnessJournalTest(); self.domain.setUp()
        self.addCleanup(self.domain.doCleanups)
        self.actor, self.task = self.domain.actor, self.domain.task
        self.drives, self.context_version = [], 1
        class Adapter(LocalHarnessAdapter):
            def describe(self):
                value = super().describe()
                return replace(value, capabilities=value.capabilities | {'model_call_admission'})
        def drive(inv, tools, stopped):
            self.drives.append(inv.role.role)
            tools.admit_model_call(1)
            if inv.role.role == 'retrieval':
                tools.call('submit_retrieval_strategy', self.domain.proposal)
            else:
                tools.call('submit_processing_plan', {'proposal': {'steps': [
                    {'requirement_id': 'watermark-tag', 'step_id': 'tag'}]}})
        self.adapter = Adapter('fixture', drive, artifact_digest=self.domain.release.artifact_digest)
        self.registry = AdapterRegistry(); self.registry.register('fixture', self.adapter)
        def factory(actor):
            cap = ProcessingCapability(DetectionStep('tag','watermark@1','validator@1','evidence@1'),
                                       'image',frozenset({'tag'}),frozenset({'boolean'}))
            def authority(inv):
                task = self.domain.repo.get(actor,inv.stage.scope.task_id)
                return StageAuthority(actor,task,inv.stage,json.loads(task.confirmed_proposal_json)['query_spec'],
                                      10,(self.domain.binding,),(cap,),('facts-1',))
            catalog, guard = stage_catalog(authority)
            def binding(ref):
                if ref != 'model': raise ServiceError('MODEL_NOT_CONFIGURED','Model profile unavailable',422)
                return self.domain.stage.model_binding_digest
            return DurableHarnessRunner(self.registry, dict.fromkeys(('retrieval','cleaning'), self.domain.release),
                DEFAULT_ROLES, catalog, guard=guard, model_binding=binding,
                journal=self.domain.reopen(), actor=actor)
        self.factory = factory
        self.app = self.build_app()
        self.addCleanup(self.app.close)
        self.body = {'role': 'retrieval', 'model_profile_ref': 'model', 'model_calls': 2,
            'expected_revision': self.task.confirmed_revision, 'expected_generation': self.task.run_generation,
            'expected_task_version': self.task.task_version}

    def build_app(self):
        journal=self.domain.reopen()
        return AgentPlanningService(self.domain.service,journal=journal,review_store=AgentStageStore(journal),runner_factory=self.factory,
            context_provider=lambda actor,task,role: {'role': role, 'snapshot_version': self.context_version},
            lease_seconds=1)

    def prepare(self, key='prepare'):
        return self.app.prepare(self.actor, self.task.task_id, key, self.body)[0]

    @staticmethod
    def action_body(review, action):
        return {'action': action, 'proposal_digest': review['proposal_digest'],
                'expected_task_version': review['expected_task_version']}

    def act(self, review, action, key=None, app=None):
        return (app or self.app).act(self.actor, self.task.task_id, review['stage_draft_id'], action,
                                    key or action, self.action_body(review, action))

    def completed(self, review):
        for _ in range(300):
            result = self.app.get(self.actor, self.task.task_id, review['stage_draft_id'])
            if result['status'] in ('finished','failed'): return result
            time.sleep(.01)
        self.fail('stage did not finish')

    def test_prepare_is_draft_only_and_idempotent_across_restart(self):
        view = self.prepare()
        second = self.build_app(); self.addCleanup(second.close)
        duplicate, replayed = second.prepare(self.actor,self.task.task_id,'prepare',self.body)
        self.assertTrue(replayed); self.assertEqual(view, duplicate)
        self.assertEqual(self.drives, [])
        with self.domain.journal._tx() as db:
            for table in ('harness_attempts','harness_stages','harness_model_budgets'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
        changed = dict(self.body, model_calls=3)
        with self.assertRaises(ServiceError): self.app.prepare(self.actor,self.task.task_id,'prepare',changed)
        self.assertFalse(view['execution_allowed'])
        self.assertNotIn('context_json',view); self.assertNotIn('system_prompt',view)

    def test_confirm_start_query_complete_and_retry_do_not_duplicate_work(self):
        view = self.prepare()
        with self.assertRaises(ServiceError): self.act(view,'start')
        self.assertEqual(self.drives, [])
        self.act(view,'confirm')
        with self.domain.journal._tx() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM harness_attempts').fetchone()[0],0)
        self.act(view,'start')
        result = self.completed(view)
        self.assertEqual(result['status'],'finished',result)
        self.assertEqual(self.drives,['retrieval'])
        self.act(view,'start','different-start-key')
        restarted = self.build_app(); self.addCleanup(restarted.close)
        current = restarted.get(self.actor,self.task.task_id,view['stage_draft_id'])
        self.assertEqual(current['outputs'][0]['arguments'],self.domain.proposal)
        self.assertEqual(self.drives,['retrieval'])
        self.assertEqual(current['model_calls_reserved'],2)
        self.assertEqual(current['model_calls_admitted'],1)

    def test_confirmation_digest_identity_version_and_source_injection_are_rejected(self):
        view = self.prepare()
        for extra in ('context_json','tenant_id','runtime_release_ref','backend_ref','base_url'):
            with self.subTest(extra=extra), self.assertRaises(ServiceError):
                self.app.prepare(self.actor,self.task.task_id,extra,dict(self.body,**{extra:'injected'}))
        wrong = dict(self.action_body(view,'confirm'),proposal_digest='wrong')
        with self.assertRaises(ServiceError): self.app.act(self.actor,self.task.task_id,view['stage_draft_id'],'confirm','wrong',wrong)
        with self.assertRaises(ServiceError): self.app.get(Actor('tenant','other'),self.task.task_id,view['stage_draft_id'])
        self.domain.mutate_task(lambda task:setattr(task,'run_generation',task.run_generation+1))
        with self.assertRaises(ServiceError): self.act(view,'confirm')
        self.assertEqual(self.drives,[])

    def test_changed_input_and_configuration_require_new_review(self):
        view = self.prepare()
        self.context_version += 1
        with self.assertRaises(ServiceError) as caught: self.act(view,'confirm')
        self.assertEqual(caught.exception.code,'AGENT_INPUT_CHANGED')
        self.context_version -= 1
        runner = self.app._runner(self.actor)
        runner.roles['retrieval'] = replace(runner.roles['retrieval'],system_prompt='changed')
        with self.assertRaises(ServiceError) as caught: self.act(view,'confirm')
        self.assertEqual(caught.exception.code,'AGENT_STAGE_CONFIGURATION_CHANGED')

    def test_concurrent_confirmation_and_start_create_one_attempt(self):
        view = self.prepare()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i:self.act(view,'confirm',f'confirm-{i}'), range(4)))
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i:self.act(view,'start',f'start-{i}'), range(4)))
        self.assertEqual(self.completed(view)['status'],'finished')
        self.assertEqual(self.drives,['retrieval'])
        with self.domain.journal._tx() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM harness_attempts').fetchone()[0],1)

    def test_cancel_draft_or_queued_phase_does_not_run_or_refund(self):
        view = self.prepare(); self.act(view,'confirm'); self.act(view,'cancel')
        with self.assertRaises(ServiceError): self.act(view,'start')
        self.assertEqual(self.drives,[])
        self.assertEqual(self.app.get(self.actor,self.task.task_id,view['stage_draft_id'])['status'],'cancelled')

    def test_hard_delete_purges_preparation_and_action_receipts(self):
        view = self.prepare(); self.act(view,'confirm')
        self.domain.repo.hard_delete_conversation(self.actor,self.task.conversation_id)
        with self.domain.journal._tx() as db:
            for table in ('harness_stage_drafts','harness_stage_actions'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)

    def test_cleaning_role_uses_its_own_review_and_tools(self):
        self.body['role']='cleaning'
        view=self.prepare(); self.act(view,'confirm'); self.act(view,'start')
        result=self.completed(view)
        self.assertEqual(result['status'],'finished',result)
        self.assertEqual(self.drives,['cleaning'])
        self.assertEqual(result['outputs'][0]['tool_id'],'submit_processing_plan')

    def test_stage_action_keys_cannot_be_reused_for_other_reviews(self):
        first=self.prepare('first'); second=self.prepare('second')
        self.act(first,'confirm','same-key')
        with self.assertRaises(ServiceError) as caught:self.act(second,'confirm','same-key')
        self.assertEqual(caught.exception.code,'IDEMPOTENCY_CONFLICT')
        with self.assertRaises(ServiceError):
            self.app.prepare(self.actor,self.task.task_id,'missing-model',dict(self.body,model_profile_ref='missing'))

    def test_list_recovers_current_stage_ids_without_running_or_leaking_context(self):
        view=self.prepare()
        restarted=self.build_app(); self.addCleanup(restarted.close)
        result=restarted.list(self.actor,self.task.task_id)
        self.assertEqual(result['items'][0]['stage_draft_id'],view['stage_draft_id'])
        self.assertEqual(self.drives,[])
        self.assertNotIn('context_json',json.dumps(result))
        with self.assertRaises(ServiceError):restarted.list(Actor('tenant','other'),self.task.task_id)
        self.domain.mutate_task(lambda task:setattr(task,'run_generation',task.run_generation+1))
        self.assertEqual(restarted.list(self.actor,self.task.task_id)['items'],[])

    def test_list_rechecks_version_inside_its_read_transaction(self):
        self.prepare()
        old=self.domain.repo.get(self.actor,self.task.task_id)
        self.domain.mutate_task(lambda task:setattr(task,'run_generation',task.run_generation+1))
        self.assertEqual(self.app.store.list(self.actor,old,32),[])

    def test_corrupt_review_digest_is_not_approved_or_rebuilt(self):
        view=self.prepare()
        with self.domain.journal._tx() as db:
            db.execute("UPDATE harness_stage_drafts SET proposal_digest=? WHERE draft_id=?",('sha256:'+'0'*64,view['stage_draft_id']))
        with self.assertRaises(ServiceError) as caught:self.act(view,'confirm')
        self.assertEqual(caught.exception.code,'STORAGE_CORRUPT')
        self.assertEqual(self.drives,[])

    def test_http_stage_lifecycle_and_query_validation(self):
        handler = create_handler(self.domain.service,agent_planning=self.app)
        server = ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join(timeout=2)))
        self.addCleanup(handler.task_commands.store.connection.close)
        base=f'http://127.0.0.1:{server.server_port}/v1/retrieval/tasks/{self.task.task_id}/agent-stages/'
        def call(path,body=None,key='http',user='owner'):
            request=Request(base.rstrip('/') if path=='LIST' else base+path,data=json.dumps(body).encode() if body is not None else None,
                headers={'Content-Type':'application/json','X-Tenant-Id':'tenant','X-User-Id':user,'Idempotency-Key':key})
            try:
                with urlopen(request,timeout=3) as response:return response.status,json.load(response)
            except HTTPError as error:return error.code,json.load(error)
        code,view=call('prepare',self.body,'prepare-http')
        self.assertEqual(code,201,view)
        draft=view['stage_draft_id']
        self.assertEqual(call(draft)[0],200)
        self.assertEqual(call('LIST')[1]['items'][0]['stage_draft_id'],draft)
        self.assertEqual(call(draft+'?after_cursor=-1')[0],422)
        self.assertEqual(call(draft+'?after_cursor=1&after_cursor=2')[0],422)
        self.assertEqual(call(draft,user='other')[0],404)
        code,body=call(draft+'/confirm',self.action_body(view,'confirm'),'confirm-http')
        self.assertEqual(code,200,body)
        code,body=call(draft+'/start',self.action_body(view,'start'),'start-http')
        self.assertEqual(code,202,body)
        self.assertEqual(self.completed(view)['status'],'finished')
