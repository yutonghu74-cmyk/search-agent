"""Coordinator authorization, durable handoff, restart and original-window integration."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import ThreadingHTTPServer
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

import test_harness_journal as fixtures
from dsh_retrieval.adapters.harness.composition import build_planning_runner
from dsh_retrieval.adapters.harness.local import LocalHarnessAdapter
from dsh_retrieval.adapters.task_storage.harness import SQLiteHarnessJournal
from dsh_retrieval.agent_jobs import AgentJobRegistry, AgentJobService, AgentJobDispatcher
from dsh_retrieval.api import create_handler
from dsh_retrieval.chat_ui import CHAT_JS
from dsh_retrieval.coordinator_jobs import PlatformCoordinatorJobs
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.platform_coordination import (
    CoordinationRegistry, CoordinationCapability, CoordinationPolicy, WORKFLOW, validate_plan)
from dsh_retrieval.query_spec import digest


class CoordinatorTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.HarnessJournalTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.service, self.actor, self.task = self.f.service, self.f.actor, self.f.task
        self.model_calls, self.deliveries = [], []
        self.binding, self.model_binding = digest({'input': 1}), digest({'model': 1})
        self.plan = {'reply': '已核对；候选仍需发起人在原入口确认。', 'steps': [
            {'step_id': 'search', 'capability': 'retrieval', 'action': 'ensure', 'depends_on': []},
            {'step_id': 'clean', 'capability': 'cleaning', 'action': 'inspect', 'depends_on': ['search']}]}
        self.registry = CoordinationRegistry()
        def describe(actor, task):
            return {'actions': ['ensure', 'inspect'], 'binding_digest': self.binding}
        def invoke(actor, task, action, key, fresh):
            self.deliveries.append((action, key))
            return {'status': 'waiting_confirmation', 'job_ref': 'original-business-job'}
        for name in ('retrieval', 'cleaning'):
            self.registry.register(CoordinationCapability(name, name+'@1', 'agent', describe, invoke))
        class Adapter(LocalHarnessAdapter):
            def describe(self):
                value = super().describe()
                return replace(value, capabilities=value.capabilities | {'model_call_admission'})
        def driver(inv, tools, stop):
            tools.admit_model_call(1); self.model_calls.append(1)
            tools.call('get_platform_context', {})
            tools.admit_model_call(2); self.model_calls.append(2)
            tools.call('submit_coordination_plan', self.plan)
        self.adapter = Adapter('coordinator-fixture', driver, artifact_digest=digest({'code': 1}))
        self.config = {'schema_version': 'harness-routes@2', 'releases': {'coordinator-local@1': {
            'provider': 'registered', 'adapter_id': self.adapter.adapter_id,
            'artifact_digest': self.adapter.artifact_digest}}, 'roles': {'coordinator': 'coordinator-local@1'}}
        self.app = self.build()
        registry = AgentJobRegistry(); registry.register(self.app)
        self.jobs = AgentJobService(registry=registry, links=self.app.links)
        self.worker = AgentJobDispatcher(self.jobs, start=False); self.addCleanup(self.worker.close)

    def build(self, policy=None):
        def runner(actor, domain, journal):
            return build_planning_runner(self.config, authority=None, domain=domain,
                model_environment=lambda _: {}, model_binding=lambda _: self.model_binding,
                journal=journal, actor=actor, installed_adapters={self.adapter.adapter_id: self.adapter})
        app = PlatformCoordinatorJobs(self.service, self.registry, runner_factory=runner,
            model_binding=lambda actor, ref: self.model_binding, policy=policy)
        self.addCleanup(app.close)
        return app

    def body(self):
        task = self.service.repository.get(self.actor, self.task.task_id)
        return {'capability': 'coordination', 'workflow_ref': WORKFLOW, 'planning': {
            'message': '安排下一步', 'model_profile_ref': 'model',
            'expected_revision': task.confirmed_revision, 'expected_generation': task.run_generation,
            'expected_task_version': task.task_version}}

    def prepare(self, key='coordinate'):
        view, _ = self.jobs.prepare(self.actor, self.task.task_id, key, self.body())
        return view

    def start(self, view, key='start'):
        return self.jobs.act(self.actor, self.task.task_id, view['job_ref'], 'start', key,
            {'proposal_digest': view['proposal_digest'], 'expected_task_version': view['task_version']})[0]

    def finish(self, view):
        for _ in range(200):
            self.worker.drain()
            value = self.jobs.get(self.actor, self.task.task_id, view['job_ref'])
            if value['status'] in ('completed', 'unknown', 'cancelled'): return value
            time.sleep(.01)
        self.fail('Coordinator did not finish')

    def test_explicit_start_uses_same_task_and_dispatches_only_after_validated_output(self):
        before = self.service.repository.get(self.actor, self.task.task_id)
        view = self.prepare()
        for _ in range(3): self.jobs.get(self.actor, self.task.task_id, view['job_ref'])
        self.assertEqual((self.model_calls, self.deliveries), ([], []))
        self.assertEqual(view['status'], 'ready')
        self.start(view); done = self.finish(view)
        self.assertEqual(done['status'], 'completed', done)
        self.assertEqual(self.model_calls, [1, 2])
        self.assertEqual([row[0] for row in self.deliveries], ['ensure', 'inspect'])
        self.assertEqual([r['result']['status'] for r in done['coordination']['steps']], ['waiting_confirmation']*2)
        self.assertEqual(self.service.repository.get(self.actor, self.task.task_id), before)
        self.assertIsNone(done['accepted_count'])
        serialized = json.dumps(done)
        for secret in ('context_json', 'api_key', 'credential_binding', 'invocation', 'runtime'):
            self.assertNotIn(secret, serialized)
        self.assertEqual(self.jobs.artifacts(self.actor, self.task.task_id, view['job_ref'])['items'], [])

    def test_concurrent_start_and_restart_do_not_repeat_calls_or_delivery(self):
        view = self.prepare()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.start(view), range(8)))
        done = self.finish(view); self.assertEqual(done['status'], 'completed', done)
        restarted = self.build()
        self.jobs.registry._workflows[WORKFLOW] = restarted; self.jobs.links = restarted.links
        self.assertEqual(restarted.get(self.actor, self.task.task_id, view['job_ref']), done)
        self.assertTrue(restarted.prepare(self.actor, self.task.task_id, 'coordinate', self.body()['planning'])[1])
        self.start(view); self.worker.drain()
        self.assertEqual((len(self.model_calls), len(self.deliveries)), (2, 2))
        self.assertEqual(SQLiteHarnessJournal(self.service.repository, self.service.projects).pending(self.actor, 'demo'), ())

    def test_idempotency_and_owner_capability_boundaries(self):
        view = self.prepare(); changed = self.body(); changed['planning']['message'] = 'changed'
        with self.assertRaises(ServiceError) as caught:
            self.jobs.prepare(self.actor, self.task.task_id, 'coordinate', changed)
        self.assertEqual(caught.exception.code, 'IDEMPOTENCY_CONFLICT')
        for operation in ('confirm', 'cancel'):
            with self.assertRaises(ServiceError):
                self.jobs.act(self.actor, self.task.task_id, view['job_ref'], operation, 'x', {})
        with self.assertRaises(ServiceError): self.jobs.get(Actor('tenant', 'other'), self.task.task_id, view['job_ref'])
        with self.assertRaises(ServiceError):
            self.jobs.prepare(self.actor, self.task.task_id, 'external', self.body(), allowed=frozenset({'retrieval'}))
        self.assertEqual((self.model_calls, self.deliveries), ([], []))
        with self.assertRaises(ServiceError):
            self.registry.register(CoordinationCapability('training', 'training@1', 'agent', lambda *a: {}, lambda *a: {}))

    def test_model_or_task_change_blocks_start(self):
        view = self.prepare(); self.model_binding = digest({'model': 2})
        with self.assertRaises(ServiceError) as caught: self.start(view)
        self.assertEqual(caught.exception.code, 'HARNESS_MODEL_BINDING_CONFLICT')
        self.model_binding = digest({'model': 1})
        self.f.mutate_task(lambda task: setattr(task, 'task_version', task.task_version+1))
        with self.assertRaises(ServiceError): self.start(view)
        self.assertEqual(self.jobs.get(self.actor, self.task.task_id, view['job_ref'])['status'], 'unknown')
        self.assertEqual((self.model_calls, self.deliveries), ([], []))

    def test_active_turn_cannot_be_replaced_by_a_new_request_key(self):
        first = self.prepare(); self.start(first)
        with self.assertRaises(ServiceError) as caught: self.prepare('different-turn')
        self.assertEqual(caught.exception.code, 'COORDINATOR_BUSY')
        self.assertEqual(self.finish(first)['status'], 'completed')

    def test_stale_queued_turn_does_not_block_current_task_version(self):
        first = self.prepare(); self.start(first)
        self.f.mutate_task(lambda task: setattr(task, 'task_version', task.task_version+1))
        second = self.prepare('current-version'); self.start(second)
        self.assertEqual(self.finish(second)['status'], 'completed')
        self.assertEqual(self.jobs.get(self.actor, self.task.task_id, first['job_ref'])['status'], 'unknown')
        self.assertEqual(self.model_calls, [1, 2])

    def test_revocation_blocks_model_spend(self):
        view = self.prepare(); self.service.projects.grants.clear()
        with self.assertRaises(ServiceError): self.start(view)
        self.assertEqual(self.model_calls, [])

    def test_changed_input_and_unknown_delivery_never_resend(self):
        view = self.prepare(); self.start(view)
        original = self.registry._ports['retrieval']
        def lost(*args):
            self.deliveries.append(('unknown', args[3])); raise TimeoutError('lost delivery')
        self.registry._ports['retrieval'] = replace(original, invoke=lost)
        done = self.finish(view)
        self.assertEqual(done['status'], 'unknown')
        self.assertEqual(done['coordination']['steps'][0]['state'], 'pending_or_unknown')
        for _ in range(4): self.start(view); self.worker.drain()
        self.assertEqual(len(self.deliveries), 1)
        # A new owner request with changed provider input may inspect but cannot
        # deliver a plan bound to an old artifact.
        self.registry._ports['retrieval'] = original
        next_view = self.prepare('next'); self.binding = digest({'input': 2})
        self.start(next_view); next_done = self.finish(next_view)
        self.assertEqual(next_done['code'], 'COORDINATOR_INPUT_CHANGED')
        self.assertEqual(len(self.deliveries), 1)

    def test_crash_after_intent_does_not_invoke_again(self):
        view = self.prepare()
        self.app.links.update(self.actor, self.task.task_id, view['job_ref'], status='dispatching', plan=self.plan)
        self.assertTrue(self.app.links.claim_step(self.actor, self.task.task_id, view['job_ref'], self.plan['steps'][0]))
        self.app.advance(self.actor, self.task.task_id, view['job_ref'])
        self.assertEqual(self.app.get(self.actor, self.task.task_id, view['job_ref'])['status'], 'unknown')
        self.assertEqual(self.deliveries, [])

    def test_completed_receipt_recovery_skips_delivered_step_and_new_generation_is_fenced(self):
        view = self.prepare()
        self.app.links.update(self.actor, self.task.task_id, view['job_ref'], status='dispatching', plan=self.plan,
            steps=[{**self.plan['steps'][0], 'state': 'delivered', 'result': {'status': 'queued'}}])
        self.app.advance(self.actor, self.task.task_id, view['job_ref'])
        self.assertEqual([d[0] for d in self.deliveries], ['inspect'])
        self.f.mutate_task(lambda task: setattr(task, 'run_generation', task.run_generation+1))
        done = self.app.get(self.actor, self.task.task_id, view['job_ref'])
        self.assertEqual(done['status'], 'cancelled'); self.assertEqual(done['coordination']['steps'], [])

    def test_bounded_generation_budget_is_shared_by_distinct_requests(self):
        self.app = self.build(CoordinationPolicy(model_calls=2, generation_model_calls=2))
        self.jobs.registry._workflows[WORKFLOW] = self.app; self.jobs.registry._capabilities['coordination'] = self.app
        self.jobs.links = self.app.links
        first = self.prepare(); self.start(first)
        self.assertEqual(self.finish(first)['status'], 'completed')
        second = self.prepare('second'); self.start(second)
        denied = self.finish(second)
        self.assertEqual(denied['status'], 'unknown')
        self.assertEqual(self.model_calls, [1, 2])

    def test_unregistered_actions_and_invalid_dependency_graph_are_rejected(self):
        view = self.prepare(); context = self.app.links.get(self.actor, self.task.task_id, view['job_ref'])['links']['context']
        for steps in ([{'step_id': 's', 'capability': 'training', 'action': 'ensure', 'depends_on': []}],
                      [{'step_id': 's', 'capability': 'cleaning', 'action': 'confirm', 'depends_on': []}],
                      [{'step_id': 's', 'capability': 'retrieval', 'action': 'ensure', 'depends_on': ['s']}]):
            with self.assertRaises(ServiceError): validate_plan({'reply': 'test', 'steps': steps}, context, self.app.policy)
        self.plan['steps'][0]['action'] = 'confirm'
        self.start(view); done = self.finish(view)
        self.assertEqual(done['status'], 'unknown'); self.assertEqual(self.deliveries, [])

    def test_task_deletion_purges_original_coordinator_and_harness_records(self):
        view = self.prepare(); self.start(view)
        self.assertEqual(self.finish(view)['status'], 'completed')
        self.service.repository.hard_delete_conversation(self.actor, self.task.conversation_id)
        with self.app.journal._tx() as db:
            for table in ('agent_job_links', 'harness_attempts', 'harness_stages', 'harness_model_budgets', 'harness_tool_receipts'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0], 0, table)

    def http_server(self):
        handler = create_handler(self.service, agent_jobs=self.jobs)
        handler.log_message = lambda *args: None
        self.addCleanup(handler.task_commands.close)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        return server, handler

    def test_original_chat_controller_calls_common_http_job_api_and_waits_for_owner(self):
        server, _ = self.http_server()
        self.worker.close(); self.worker = AgentJobDispatcher(self.jobs, poll_seconds=.05)
        self.addCleanup(self.worker.close)
        source = CHAT_JS[CHAT_JS.index('async function startPlatformCoordination('):CHAT_JS.index('async function showRecallAnalysis(')]
        script = r"""
const assert=require('node:assert/strict');
const state={task:JSON.parse(process.env.TASK),historyOpenToken:1};const calls=[],fields={status:{textContent:''},'llm-model':{value:'model'}};
const bubbles=[];const message=()=>{const p={textContent:''};const b={querySelector:()=>p};bubbles.push(b);return b};
async function api(path,opts={}){calls.push([path,opts.method||'GET',opts.body]);const r=await fetch(process.env.API+path,{method:opts.method||'GET',headers:{'X-Tenant-Id':'tenant','X-User-Id':'owner','Content-Type':'application/json','Idempotency-Key':opts.keyValue||'read'},body:opts.body?JSON.stringify(opts.body):undefined});const data=await r.json();if(!r.ok)throw Error(JSON.stringify(data));return data;}
const run=new Function('state','$','message','key','api','reviewDetails','guidedAction','refreshTaskSidebar','setTimeout',process.env.SOURCE+';return startPlatformCoordination')(
 state,id=>fields[id],message,p=>p+'-once',api,()=>{},()=>{},async()=>{},fn=>setTimeout(fn,25));
(async()=>{await run();await run();for(let i=0;i<200&&state.coordinatorBusy;i++)await new Promise(r=>setTimeout(r,25));assert.equal(state.coordinatorJob.status,'completed');assert(bubbles[0].querySelector('p').textContent.includes('需要你在原节点确认'));const posts=calls.filter(c=>c[1]==='POST');assert.equal(posts.length,2);assert.equal(posts[0][2].capability,'coordination');assert(!calls.some(c=>/confirmations|first-search|\/confirm$/.test(c[0])));})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source,
            'TASK': json.dumps({'task_id': self.task.task_id, 'confirmed_revision': self.task.confirmed_revision,
                'run_generation': self.task.run_generation, 'task_version': self.task.task_version}),
            'API': f'http://127.0.0.1:{server.server_port}'}, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual((len(self.model_calls), len(self.deliveries)), (2, 2))


    def test_old_chat_network_error_does_not_clear_new_chat_activity(self):
        source = CHAT_JS[CHAT_JS.index('async function startPlatformCoordination('):CHAT_JS.index('async function showRecallAnalysis(')]
        script = r"""
const assert=require('node:assert/strict');let reject;
const state={task:{task_id:'old',task_version:1,confirmed_revision:1,run_generation:1},historyOpenToken:1};
const run=new Function('state','$','message','key','api','reviewDetails','guidedAction','refreshTaskSidebar',process.env.SOURCE+';return startPlatformCoordination')(
state,()=>({value:'model'}),()=>({querySelector:()=>({textContent:''})}),()=> 'key',()=>new Promise((resolve,no)=>{reject=no}),()=>{},()=>{},async()=>{});
(async()=>{const old=run();state.task={task_id:'new'};state.historyOpenToken++;state.coordinatorBusy=true;reject(Error('late old request'));await old;assert.equal(state.coordinatorBusy,true);})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


@unittest.skipUnless(os.environ.get('DSH_CANDIDATE_TEST_PYTHON'), 'native rc.5 requires explicit isolated paths')
class NativeCoordinatorTest(unittest.TestCase):
    def test_actual_rc5_submits_plan_dispatches_ports_and_recovers_without_model_repeat(self):
        from http.server import BaseHTTPRequestHandler
        from dsh_retrieval.adapters.harness.dsh_releases import CANDIDATE_DSH
        f = CoordinatorTest(); f.setUp(); self.addCleanup(f.doCleanups)
        requests = []
        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                name, args = ('get_platform_context', {}) if len(requests) == 1 else ('submit_coordination_plan', f.plan)
                delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call-'+str(len(requests)),
                    'type': 'function', 'function': {'name': 'mcp__business__'+name, 'arguments': json.dumps(args)}}]}
                rows = [{'id': 'local', 'object': 'chat.completion.chunk', 'model': 'test', 'created': 0,
                    'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                    {'id': 'local', 'object': 'chat.completion.chunk', 'model': 'test', 'created': 0,
                     'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}],
                     'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}]
                payload = (''.join('data: '+json.dumps(row)+'\n\n' for row in rows)+'data: [DONE]\n\n').encode()
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        with tempfile.TemporaryDirectory(prefix='pc-', dir='/tmp') as home:
            item = {'provider': 'dsh', 'adapter_id': 'coordinator-native', 'sdk_release': CANDIDATE_DSH.release_id,
                'python': os.environ['DSH_CANDIDATE_TEST_PYTHON'], 'home': home,
                'dsh_bin': os.environ['DSH_CANDIDATE_TEST_DSH_BIN'], 'node': os.environ['DSH_CANDIDATE_TEST_NODE'],
                'npm_lock': os.environ['DSH_CANDIDATE_TEST_NPM_LOCK']}
            config = {'schema_version': 'harness-routes@2', 'releases': {'native-rc5': item},
                      'roles': {'coordinator': 'native-rc5'}}
            def factory(actor, domain, journal):
                return build_planning_runner(config, authority=None, domain=domain, journal=journal, actor=actor,
                    model_binding=lambda _: f.model_binding, model_environment=lambda _: {
                        'RETRIEVAL_MODEL_KEY': 'synthetic', 'RETRIEVAL_MODEL_ID': 'test',
                        'RETRIEVAL_MODEL_URL': f'http://127.0.0.1:{server.server_port}/v1', 'RETRIEVAL_MAX_TOKENS': '12000'})
            app = PlatformCoordinatorJobs(f.service, f.registry, runner_factory=factory,
                                          model_binding=lambda *args: f.model_binding)
            self.addCleanup(app.close)
            f.app = app; f.jobs.registry._workflows[WORKFLOW] = app; f.jobs.links = app.links
            view = f.prepare(); f.start(view)
            deadline = time.monotonic()+50
            while time.monotonic()<deadline:
                f.worker.drain(); result = f.jobs.get(f.actor, f.task.task_id, view['job_ref'])
                if result['status'] in ('completed', 'unknown'): break
                time.sleep(.05)
            self.assertEqual(result['status'], 'completed', result)
            self.assertEqual((len(requests), len(f.deliveries)), (2, 2))
            invocation = app.invocation(app.links.get(f.actor, f.task.task_id, view['job_ref'])['links'])
            journal = app.journal.read(f.actor, app.handle(invocation))
            self.assertEqual((journal['admitted'], journal['budget_reserved']), (2, 3))
            restarted = PlatformCoordinatorJobs(f.service, f.registry, runner_factory=factory,
                                               model_binding=lambda *args: f.model_binding)
            self.addCleanup(restarted.close)
            restarted.advance(f.actor, f.task.task_id, view['job_ref'])
            self.assertEqual(restarted.get(f.actor, f.task.task_id, view['job_ref']), result)
            self.assertEqual((len(requests), len(f.deliveries)), (2, 2))


class InstalledBusinessPortsTest(unittest.TestCase):
    def test_reuses_original_retrieval_and_cleaning_proposal_without_owner_approval(self):
        import test_confirmed_terms_agent
        import test_multi_rule_pipeline
        from dsh_retrieval.adapters.task_storage.agent_jobs import SQLiteAgentJobLinks
        from dsh_retrieval.adapters.task_storage.agent_stages import AgentStageStore
        from dsh_retrieval.adapters.task_storage.agent_retrieval import SQLiteAgentRetrievalHandoffs
        from dsh_retrieval.adapters.task_storage.candidates import SQLiteCandidateManifestRepository
        from dsh_retrieval.adapters.task_storage.retrieval import SQLiteRetrievalPlanRepository
        from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
        from dsh_retrieval.confirmed_cleaning import ConfirmedCleaningService
        from dsh_retrieval.confirmed_terms_agent import RoutedJobLinks
        from dsh_retrieval.local_coordination import install_local_coordinator
        f = test_confirmed_terms_agent.ConfirmedTermsAgentTest(); f.setUp(); self.addCleanup(f.doCleanups)
        detector = test_multi_rule_pipeline.MultiRulePipelineTests(); detector.setUp(); self.addCleanup(detector.doCleanups)
        driver = CoordinatorTest(); driver.setUp(); self.addCleanup(driver.doCleanups)
        repo = f.service.repository
        retrieval = SQLiteRetrievalPlanRepository(repo.path, projects=f.service.projects, authorization=f.engine._authority())
        manifests = SQLiteCandidateManifestRepository(repo.path, projects=f.service.projects, authorization=f.engine._authority())
        stages = AgentStageStore(f.journal)
        SQLiteAgentRetrievalHandoffs(journal=f.journal, retrieval=retrieval,
            authority=lambda inv: None, context_provider=lambda *args: {})
        original = SQLiteAgentJobLinks(stages=stages, retrieval=retrieval, manifests=manifests)
        f.jobs.links = RoutedJobLinks(original, f.links)
        cleaning_repo = SQLiteMultiRuleApprovalRepository(repo.path, projects=f.service.projects, authorization=f.engine._authority())
        cleaning = ConfirmedCleaningService(cleaning_repo, registry=detector.registry, gateway=detector.gateway)
        profile = SimpleNamespace(profile_id='model', config=SimpleNamespace(model='synthetic',
            api_key='synthetic-secret', base_url='https://models.example.test/v1'))
        config = {'harness': driver.config, 'coordinator': {'enabled': True, 'release_ref': 'coordinator-local@1',
            'model_calls': 3, 'generation_model_calls': 30, 'max_seconds': 60, 'max_steps': 4}}
        deployment = SimpleNamespace(configuration=config, service=f.service, search=f.engine,
            runtime=SimpleNamespace(settings=None, select=lambda ref,kind: profile), jobs=f.jobs,
            worker=f.worker, confirmed_terms=f.agent, window=f.window)
        coordinator = install_local_coordinator(deployment, cleaning=cleaning,
            adapters_provider=lambda actor: {driver.adapter.adapter_id: driver.adapter})
        self.addCleanup(coordinator.close)
        body = f.body()
        # This static detector supports ordinary boolean rules, not the
        # semantic per-group quota checks exercised by the other fixture.
        from dsh_retrieval.expansion_models import preview_edited_groups
        body['query_spec']['grouped_expansion'] = preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': ['柯基']},
            {'primary_label': '猫', 'auxiliary_terms': ['橘猫']}])['plan']
        body['query_spec']['raw_request'] = '狗和猫的图片'
        body['query_spec']['rules'] = [{'rule_id': 'watermark',
            'predicate_ref': 'watermark.present@1', 'operator': 'eq', 'value': False,
            'hardness': 'hard', 'origin': 'user_explicit'}]
        draft = f.draft(body); code, task = f.confirm(draft); self.assertEqual(code, 202, task)
        tid, ref = task['task_id'], task['search_enqueue']['job_ref']
        review = f.wait_review(tid, ref)
        approved = f.engine.repository.get_current(f.actor, tid)
        before = f.engine.search_budget.snapshot(approved.scope)
        def coordinate(key):
            task = repo.get(f.actor, tid)
            view, _ = coordinator.prepare(f.actor, tid, key, {'message': '安排下一步',
                'model_profile_ref': 'model', 'expected_revision': task.confirmed_revision,
                'expected_generation': task.run_generation, 'expected_task_version': task.task_version})
            coordinator.act(f.actor, tid, view['job_ref'], 'start', key+'-start',
                {'proposal_digest': view['proposal_digest'], 'expected_task_version': task.task_version})
            for _ in range(200):
                f.worker.drain(); result = coordinator.get(f.actor, tid, view['job_ref'])
                if result['status'] in ('completed', 'unknown'): return result
                time.sleep(.01)
            self.fail('Installed coordinator did not finish')
        result = coordinate('before-candidates')
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['coordination']['steps'][0]['result']['job_ref'], ref)
        self.assertTrue(result['coordination']['steps'][0]['result']['replayed'])
        self.assertEqual(result['coordination']['steps'][1]['result']['status'], 'waiting_input')
        self.assertEqual(f.engine.search_budget.snapshot(approved.scope), before)
        self.assertIsNone(f.engine.repository.get_current_candidate_manifest(f.actor, tid))
        c = review['confirmation']
        f.agent.act(f.actor, tid, ref, 'confirm', 'explicit-owner-candidates',
            {'action': 'confirm', **{k:c[k] for k in ('checkpoint','proposal_digest','expected_task_version')}})
        driver.plan = {'reply': '准备清洗方案，等待发起人确认。', 'steps': [
            {'step_id': 'clean', 'capability': 'cleaning', 'action': 'prepare', 'depends_on': []}]}
        result = coordinate('after-candidates')
        self.assertEqual(result['status'], 'completed', result)
        receipt = result['coordination']['steps'][0]['result']
        self.assertEqual(receipt['status'], 'waiting_confirmation')
        self.assertIn('proposal_digest', receipt)
        self.assertIsNone(repo.get(f.actor, tid).active_cleaning_operation_id)
        self.assertEqual(detector.detector.calls, 0)
        self.assertEqual(cleaning_repo.pending_approved_operations(), ())
        # A provider that needs another model is routed to the original
        # owner entry until separately budgeted child planning is admitted.
        cleaning.registry_provider = SimpleNamespace(generate=lambda *args: self.fail('unbudgeted model call'))
        capabilities = coordinator.registry.context(f.actor, repo.get(f.actor, tid))
        self.assertEqual(capabilities['cleaning']['actions'], ['inspect'])
        self.assertTrue(capabilities['cleaning']['planning_model_requires_owner_entry'])


if __name__ == '__main__': unittest.main()
