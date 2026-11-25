from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from hashlib import sha256
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import threading
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import test_first_search_local as search_fixtures
import test_multi_rule_pipeline as pipeline_fixtures
from image_fixtures import image_bytes
from draft_helpers import BODY
from dsh_retrieval.adapters.platform_access import PlatformClient, PlatformIdentity, PlatformProjectAccess
from dsh_retrieval.adapters.task_storage.shared import SQLiteExternalBindings
from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.api import create_handler
from dsh_retrieval.cleaning_dispatch import CleaningDispatchCoordinator
from dsh_retrieval.confirmed_cleaning import ConfirmedCleaningService
from dsh_retrieval.detector_artifacts import DetectorArtifact, DetectorArtifactBinding, DetectorArtifactManifest
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.intent_compiler import DetectorBinding
from dsh_retrieval.intent_review import Capability
from dsh_retrieval.query_spec import digest
from dsh_retrieval.released_detectors import ReleasedDetectorRegistry
from dsh_retrieval.shared_access import CAPABILITIES, SharedPrincipal, SharedAccess
from dsh_retrieval.shared_sdk import AgentClient, OwnerClient, SharedTransport, SharedApiError
from dsh_retrieval.adapters.task_storage import ledger_rows
from dsh_retrieval.first_search_local import LocalFirstSearch
from dsh_retrieval.retrieval_contracts import RetrievalScope


class ProjectFixtureSearch(LocalFirstSearch):
    """Synthetic numeric-project backend; production LOCAL_EXACT stays demo-only."""
    def _preview(self, actor, task_id):
        task=self.service.repository.get(actor,task_id)
        self.service.projects.require_access(actor,task.project_id,task.dimension_id,'read')
        if task.project_id!='101':raise ServiceError('PROJECT_ACCESS_DENIED','Fixture project required',403)
        router,fixture=self._router();source_version=self._source_digest()
        preview=router.discover(actor,task_id,expected_scope=RetrievalScope.from_task(task),authorization_version=source_version)
        if self._source_digest()!=source_version:raise ServiceError('ROUTING_CHANGED','Fixture changed',409)
        return router,fixture,preview


class SharedCapabilitiesTest(unittest.TestCase):
    def setUp(self):
        self.f=search_fixtures.FirstSearchLocalTest();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.member=True;self.task_id=None
        class Reply(BytesIO):status=200
        def upstream(request,timeout):
            token=request.get_header('Authorization','').removeprefix('Bearer ')
            uid='other' if token=='fixture-outsider' else 'user-a'
            if token not in ('fixture-owner','fixture-agent-a','fixture-agent-b','fixture-outsider'):
                raise HTTPError(request.full_url,401,'fixture unauthorized',{},None)
            if request.full_url.endswith('/user/info'):data={'id':uid}
            else:
                if not self.member:raise HTTPError(request.full_url,403,'fixture denied',{},None)
                data={'id':int(request.get_header('X-app-id')),'status':'normal'}
            return Reply(json.dumps({'code':200,'data':data}).encode())
        client=PlatformClient('https://fixture.invalid/api',opener=upstream)
        self.identity=PlatformIdentity(client,'tenant-a',extract_user_id=lambda value:value['id'])
        self.projects=PlatformProjectAccess(client,'tenant-a',credential_for_actor=lambda actor:
            'fixture-owner' if actor==self.f.actor else None,
            allowed_actions={'read','create','approve','write'},extract_user_id=lambda value:value['id'])
        self.f.tasks.projects=self.projects
        self.f.local.close()
        source=json.loads(self.f.source.read_text())
        source['grants'] += [{**grant,'project_id':'101'} for grant in source['grants'] if grant['project_id']=='demo']
        self.f.source.write_text(json.dumps(source))
        self.f.local=ProjectFixtureSearch(self.f.tasks,self.f.local.configs,ledger_path=self.f.ledger,
            source_path=self.f.source,capabilities_path=search_fixtures.CONFIG/'local-first-search-capabilities.example.json',
            fixture_path=self.f.fixture_path,cursor_secret=b'test-only-shared-project-cursor-key',start=True)
        self.addCleanup(self.f.local.close)
        self.bindings=SQLiteExternalBindings(self.f.tasks.repository,self.projects)
        self.grants=CAPABILITIES
        def resolve(actor,headers):
            token=headers.get('Authorization','').removeprefix('Bearer ')
            if token=='fixture-owner':return SharedPrincipal(actor,'101','human')
            integration='team-b-agent' if token=='fixture-agent-b' else 'team-a-agent'
            tasks=frozenset({'ungranted-task'}) if token=='fixture-agent-b' else frozenset({self.task_id})
            return SharedPrincipal(actor,'101','integration',integration,self.grants,tasks)
        self.shared=SharedAccess(self.bindings,resolve_principal=resolve)
        content=image_bytes('PNG');self.content=content
        fixture=json.loads(self.f.fixture_path.read_text())
        refs=set()
        for record in fixture['records']:
            for media in record['media']:
                media['content_digest']='sha256:'+sha256(content).hexdigest()
                refs.add(media['authorized_media_ref'])
        self.f.fixture_path.write_text(json.dumps(fixture))
        root=self.f.root
        artifact_path=root/'released-detector.py';artifact_path.write_bytes(b'synthetic fixture implementation')
        class Detector(pipeline_fixtures.Detector):
            def detect(self,content,candidate,binding):
                from PIL import Image
                with Image.open(BytesIO(content)) as decoded:decoded.verify()
                return super().detect(content,candidate,binding)
        self.detector=Detector()
        artifact=DetectorArtifact('implementation',artifact_path.name,'sha256:'+sha256(artifact_path.read_bytes()).hexdigest())
        artifact_binding=DetectorArtifactBinding(self.detector.backend_ref,self.detector.validator_ref,self.detector.evidence_ref,(artifact,))
        self.detector.artifact_binding_digest=artifact_binding.binding_digest
        registry=ReleasedDetectorRegistry(tuple(Capability(p,'constraint','image',('eq',),'boolean','verified','fixture.validator','fixture.eval')
            for p in ('text.present@1','watermark.present@1')),
            tuple(DetectorBinding(p,'image','fixture.validator','fixture.detector','fixture.eval') for p in ('text.present@1','watermark.present@1')),
            {self.detector.backend_ref:self.detector},DetectorArtifactManifest(root,(artifact_binding,)))
        class Gateway:
            @contextmanager
            def open_read(inner,context,ref,*,max_bytes):
                self.projects.require_access(context.actor,'101','default','read')
                if ref not in refs:raise ServiceError('ACCESS_DENIED','fixture media not authorized',403)
                with BytesIO(content) as stream:yield stream
        self.clean_repo=SQLiteMultiRuleApprovalRepository(self.f.ledger,projects=self.projects,authorization=self.f.local._authority())
        self.cleaning=ConfirmedCleaningService(self.clean_repo,registry=registry,gateway=Gateway())
        self.dispatch=CleaningDispatchCoordinator(self.clean_repo,self.cleaning,poll_seconds=.02)
        self.addCleanup(self.dispatch.close)
        self.handler=create_handler(self.f.tasks,first_search=self.f.local,plan_set_repository=self.f.local.repository,
            identity_mode='platform',identity_provider=self.identity,shared_access=self.shared,
            cleaning_service=self.cleaning,cleaning_reader=self.clean_repo,cleaning_dispatch=self.dispatch)
        self.handler.log_message=lambda *args:None
        self.server=ThreadingHTTPServer(('127.0.0.1',0),self.handler)
        thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(self.server.shutdown(),self.server.server_close(),thread.join(timeout=2)))
        self.addCleanup(self.handler.task_commands.store.connection.close)
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.owner=OwnerClient(self.transport('fixture-owner'))
        self.agent=AgentClient(self.transport('fixture-agent-a'),external_task_ref='external-training:round-1')
        body=json.loads(json.dumps(BODY));body['query_spec']['raw_request']='找动物图片'
        body['query_spec']['rules']=[{'rule_id':name,'predicate_ref':p,'operator':'eq','value':False,'hardness':'hard','origin':'user_explicit'}
            for name,p in (('text','text.present@1'),('mark','watermark.present@1'))]
        review=self.owner.save_draft(body,key='owner-draft')
        self.task=self.owner.confirm_task(review,key='owner-confirm')
        self.task_id=self.task['task_id']

    def transport(self,token):
        return SharedTransport(self.base,project_id='101',credential=lambda:token,allow_local_http=True,timeout=5)

    def bind(self,agent=None,key='bind'):
        return (agent or self.agent).bind(self.task_id,revision=self.task['confirmed_revision'],
            generation=self.task['run_generation'],task_version=self.task['task_version'],key=key)

    def prepare_search(self):
        self.bind();preview=self.agent.retrieval_preview(self.task_id)
        choices=[{'branch_id':route['branch_id'],'binding_id':option['binding_id'],'batch_size':2}
                 for route in preview['routes'] for option in route['options']]
        return self.agent.prepare_retrieval(self.task_id,routing_digest=preview['routing_digest'],choices=choices,key='prepare-search')

    def search(self):
        proposal=self.prepare_search()
        self.owner.confirm(self.task_id,proposal,stage='retrieval',key='approve-search')
        self.f.wait_stopped(self.f.local,self.task_id)
        return proposal

    def test_shared_terms_sdk_uses_explicit_grants_and_never_confirms_or_searches(self):
        from dsh_retrieval.expansion_jobs import ExpansionJobs
        from test_retrieval_terms_agent import Models
        models = Models()
        jobs = ExpansionJobs(None, models, None, self.projects, self.f.tasks)
        self.addCleanup(jobs.close)
        self.handler.retrieval_terms = self.handler.expansion_jobs = jobs
        self.bind()
        before = self.agent.task(self.task_id)
        body = {'expected_task_version': before['task_version'],
                'expected_revision': before['confirmed_revision'],
                'raw_request': '狗3张、猫2张', 'target_count': 5, 'dimension_id': 'default',
                'requested_terms_per_group': 1,
                'model_selection': {'llm': 'chosen-llm', 'vlm': 'chosen-vlm'}}
        start = self.agent.prepare_terms(self.task_id, body, key='draft-terms')
        for _ in range(100):
            done = self.agent.terms(self.task_id, start['job_id'])
            if done['status'] == 'completed': break
            time.sleep(.01)
        self.assertEqual(done['status'], 'completed', done)
        self.assertEqual(done['agent']['workflow_ref'], 'retrieval-terms@1')
        self.assertEqual([g['delivery_target'] for g in done['result']['plan']['groups']], [3, 2])
        self.assertEqual(self.agent.prepare_terms(self.task_id, body, key='draft-terms')['job_id'], start['job_id'])
        self.assertEqual(len(models.calls), 1)
        self.assertEqual(self.agent.task(self.task_id)['task_version'], before['task_version'])
        self.assertIsNone(self.f.local.repository.get_current(self.f.actor, self.task_id))
        self.assertTrue(self.agent.capabilities()['retrieval_terms_configured'])
        self.grants = frozenset({'agent.retrieval.terms.read'})
        self.assertEqual(self.agent.terms(self.task_id)['job_id'], start['job_id'])
        for suffix in ('/retrieval-agent/terms', '/expansion-jobs'):
            with self.assertRaises(SharedApiError) as error:
                self.agent._post(self.task_id, suffix, body, 'not-granted')
            self.assertEqual(error.exception.code, 'SHARED_CAPABILITY_DENIED')
        with self.assertRaises(SharedApiError):
            self.agent.retry_terms(self.task_id, start['job_id'], key='retry-not-granted')
        other = AgentClient(self.transport('fixture-agent-b'), external_task_ref='external-training:round-1')
        with self.assertRaises(SharedApiError): other.terms(self.task_id, start['job_id'])
        self.grants = frozenset({'agent.retrieval.prepare', 'agent.retrieval.read'})
        with self.assertRaises(SharedApiError): self.agent.terms(self.task_id, start['job_id'])
        self.assertEqual(len(models.calls), 1)

    def test_shared_recall_sdk_has_explicit_read_scope_and_no_search_side_effect(self):
        self.search()
        current = self.f.local.repository.get_current(self.f.actor, self.task_id)
        before = self.f.local.search_budget.snapshot(current.scope)
        self.grants = frozenset({'agent.retrieval.recall.read'})
        report = self.agent.recall(self.task_id)
        self.assertEqual(report['schema_version'], 'retrieval-recall@1')
        self.assertIsNone(report['quality']['accepted_count'])
        self.assertEqual(self.f.local.search_budget.snapshot(current.scope), before)
        self.grants = frozenset({'retrieval.read', 'agent.retrieval.terms.read'})
        with self.assertRaises(SharedApiError) as error: self.agent.recall(self.task_id)
        self.assertEqual(error.exception.code, 'SHARED_CAPABILITY_DENIED')

    def test_sdk_owner_confirmed_retrieval_cleaning_and_results(self):
        proposal=self.prepare_search()
        self.assertEqual(self.agent.retrieval_status(self.task_id)['status'],'unapproved')
        with self.assertRaises(SharedApiError) as error:
            OwnerClient(self.transport('fixture-agent-a')).confirm(self.task_id,proposal,stage='retrieval',key='forged-confirm')
        self.assertEqual(error.exception.code,'SHARED_CAPABILITY_DENIED')
        self.owner.confirm(self.task_id,proposal,stage='retrieval',key='approve-search')
        self.f.wait_stopped(self.f.local,self.task_id)
        self.assertGreater(self.agent.candidates(self.task_id)['raw_hit_count'],0)
        manifest_review=self.agent.candidate_manifest(self.task_id)
        manifest=self.owner.confirm(self.task_id,manifest_review,stage='manifest',key='approve-manifest')
        review=self.agent.prepare_cleaning(self.task_id,manifest['manifest_ref'],key='prepare-clean')
        self.assertEqual(self.detector.calls,0)
        approval=self.owner.confirm(self.task_id,review,stage='cleaning',key='approve-clean')
        self.agent.execute_cleaning(self.task_id,approval['operation_id'],key='execute-clean')
        stop=time.monotonic()+5
        while time.monotonic()<stop:
            status=self.agent.cleaning_status(self.task_id,approval['operation_id'])
            if status['status'] in ('completed','succeeded'):break
            time.sleep(.02)
        else:self.fail(status)
        results=self.agent.results(self.task_id)
        self.assertTrue(results)
        accepted=self.clean_repo.get_current_result_set(self.f.actor,self.task_id)
        self.assertEqual(accepted['accepted_count'],1)
        calls=self.detector.calls
        self.agent.execute_cleaning(self.task_id,approval['operation_id'],key='execute-clean')
        self.assertEqual(self.detector.calls,calls)

    def test_unbound_scope_and_integration_cannot_bypass_approval_or_raw_routes(self):
        with self.assertRaises(SharedApiError):self.agent.retrieval_preview(self.task_id)
        self.bind()
        for path,body in (('/v1/retrieval/tasks',{}),
            (f'/v1/retrieval/tasks/{self.task_id}/first-search/tick',{'max_requests':1}),
            (f'/v1/retrieval/tasks/{self.task_id}/cleaning/confirm',{})):
            with self.assertRaises(SharedApiError) as error:self.agent.transport.request(path,body=body,key='deny',external_ref=self.agent.external_task_ref)
            self.assertEqual(error.exception.status,403)
        other=AgentClient(self.transport('fixture-agent-b'),external_task_ref=self.agent.external_task_ref)
        with self.assertRaises(SharedApiError):self.bind(other)
        outsider=AgentClient(self.transport('fixture-outsider'),external_task_ref='foreign')
        with self.assertRaises(SharedApiError):self.bind(outsider)

    def test_prepare_receipt_and_binding_survive_store_restart_without_reencoding(self):
        proposal=self.prepare_search()
        self.shared.bindings=SQLiteExternalBindings(SQLiteTaskRepository(self.f.ledger),self.projects)
        original=self.f.local.prepare
        self.f.local.prepare=lambda *a,**k:self.fail('replay must not encode or prepare again')
        try:
            receipt=self.agent.receipt(self.task_id,operation='retrieval.prepare',key='prepare-search')
            self.assertEqual(receipt['result'],{k:v for k,v in proposal.items() if k!='trace_id'})
            preview=self.agent.retrieval_preview(self.task_id)
            choices=[{'branch_id':route['branch_id'],'binding_id':option['binding_id'],'batch_size':2}
                     for route in preview['routes'] for option in route['options']]
            replay=self.agent.prepare_retrieval(self.task_id,routing_digest=preview['routing_digest'],choices=choices,key='prepare-search')
            self.assertEqual({k:v for k,v in replay.items() if k!='trace_id'},receipt['result'])
            args={'routing_digest':preview['routing_digest'],'choices':[]}
            with self.assertRaises(SharedApiError) as error:self.agent._post(self.task_id,'/first-search/prepare',args,'prepare-search')
            self.assertEqual(error.exception.code,'IDEMPOTENCY_CONFLICT')
        finally:self.f.local.prepare=original
        self.assertFalse(self.agent.binding(self.task_id)['stale'])

    def test_cancel_fences_generation_replays_and_blocks_old_commit(self):
        self.search();before=self.agent.task(self.task_id)
        current=self.f.local.repository.get_current(self.f.actor,self.task_id)
        reply=self.agent.cancel(self.task_id,task_version=before['task_version'],key='cancel')
        self.assertTrue(reply['publication_fenced']);self.assertEqual(reply['termination'],'unknown')
        self.assertEqual({k:v for k,v in self.agent.cancel(self.task_id,task_version=before['task_version'],key='cancel').items() if k!='trace_id'}, {k:v for k,v in reply.items() if k!='trace_id'})
        task=self.f.tasks.repository.get(self.f.actor,self.task_id)
        self.assertEqual(task.run_status.value,'CANCELLED')
        self.assertEqual(task.run_generation,before['run_generation']+1)
        with self.assertRaises(ServiceError):self.f.local.repository.reserve_page(self.f.actor,current,
            plan_id=current.members[0].plan.request.plan_id,request=current.members[0].plan.request,key='old-write')
        self.assertTrue(self.agent.binding(self.task_id)['stale'])
        with self.assertRaises(SharedApiError):self.agent.retrieval_preview(self.task_id)

    def test_permission_revocation_capability_limits_and_forged_identity(self):
        self.bind();self.grants=frozenset({'retrieval.read'})
        with self.assertRaises(SharedApiError):self.agent.prepare_cleaning(self.task_id,'manifest',key='deny')
        headers={'Authorization':'Bearer fixture-agent-a','x-app-id':'101','X-External-Task-Ref':self.agent.external_task_ref,
                 'X-User-Id':'admin','X-Tenant-Id':'other','X-Principal-Kind':'human'}
        req=Request(self.base+f'/v1/retrieval/tasks/{self.task_id}/first-search/confirm',headers=headers,data=b'{}')
        with self.assertRaises(HTTPError) as error:urlopen(req,timeout=5)
        error.exception.close();self.assertEqual(error.exception.code,403)
        self.member=False
        with self.assertRaises(SharedApiError) as error:self.agent.binding(self.task_id)
        self.assertEqual(error.exception.status,403)

    def test_pending_unknown_preparation_does_not_retry_side_effects(self):
        self.bind()
        principal=SharedPrincipal(self.f.actor,'101','integration','team-a-agent',CAPABILITIES,frozenset({self.task_id}))
        def interrupted():raise RuntimeError('synthetic process interruption')
        with self.assertRaises(RuntimeError):self.bindings.invoke_once(principal,self.task_id,self.agent.external_task_ref,
            'interrupted','retrieval.prepare',{},interrupted)
        self.assertEqual(self.agent.receipt(self.task_id,operation='retrieval.prepare',key='interrupted')['status'],'pending_or_unknown')
        with self.assertRaises(ServiceError) as error:self.bindings.invoke_once(principal,self.task_id,self.agent.external_task_ref,
            'interrupted','retrieval.prepare',{},lambda:self.fail('must not retry'))
        self.assertEqual(error.exception.code,'SHARED_OUTCOME_UNKNOWN')

    def test_concurrent_bind_capacity_and_hard_delete_cascade(self):
        with ThreadPoolExecutor(max_workers=4) as pool:values=list(pool.map(lambda _:self.bind(),range(4)))
        self.assertTrue(all({k:x for k,x in v.items() if k!='trace_id'}=={k:x for k,x in values[0].items() if k!='trace_id'} for v in values))
        self.shared.bindings=SQLiteExternalBindings(self.f.tasks.repository,self.projects,max_bindings=1)
        with self.assertRaises(SharedApiError) as error:self.bind(AgentClient(self.agent.transport,external_task_ref='another-root'),key='other')
        self.assertEqual(error.exception.code,'SHARED_BINDING_LIMIT')
        task=self.f.tasks.repository.get(self.f.actor,self.task_id)
        self.f.tasks.repository.hard_delete_conversation(self.f.actor,task.conversation_id)
        with closing(self.f.tasks.repository._connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM external_task_bindings').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM external_task_actions').fetchone()[0],0)
        SQLiteTaskRepository(self.f.ledger)

    def test_inflight_generation_change_does_not_publish_a_receipt(self):
        self.bind()
        principal=SharedPrincipal(self.f.actor,'101','integration','team-a-agent',CAPABILITIES,frozenset({self.task_id}))
        def superseded():
            with closing(self.f.tasks.repository._connect()) as db,db:
                db.execute('BEGIN IMMEDIATE')
                memory=self.f.tasks.repository._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
                memory._tasks[self.task_id].run_generation+=1
                ledger_rows.write_memory(db,memory)
                self.f.tasks.repository._sync_task_read_summaries(db,memory)
            return {'proposal_digest':digest({'stale':True})}
        with self.assertRaises(ServiceError):self.bindings.invoke_once(principal,self.task_id,self.agent.external_task_ref,
            'stale','retrieval.prepare',{},superseded)
        self.assertEqual(self.agent.receipt(self.task_id,operation='retrieval.prepare',key='stale')['status'],'pending_or_unknown')

    def test_development_headers_cannot_enable_shared_access(self):
        with self.assertRaises(ValueError):create_handler(self.f.tasks,shared_access=self.shared)


class SharedSdkSafetyTest(unittest.TestCase):
    def test_secure_transport_and_unknown_writes_are_not_automatically_retried(self):
        for url in ('http://remote.invalid','https://user:password@example.invalid','https://example.invalid/?token=value'):
            with self.assertRaises(ValueError):SharedTransport(url,project_id='101',credential=lambda:'fixture-token')
        transport=SharedTransport('https://example.invalid',project_id='101',credential=lambda:'fixture-token')
        calls=[]
        def timeout(*args,**kwargs):calls.append(1);raise TimeoutError('private transport detail')
        transport._open=timeout
        with self.assertRaises(SharedApiError) as error:transport.request('/v1/retrieval/tasks',body={},key='once')
        self.assertEqual(calls,[1]);self.assertEqual(error.exception.code,'TRANSPORT_OUTCOME_UNKNOWN')
        self.assertNotIn('fixture-token',str(error.exception));self.assertNotIn('private',str(error.exception))

    def test_redirect_does_not_forward_credentials(self):
        seen=[]
        from http.server import BaseHTTPRequestHandler
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                seen.append(self.path)
                self.send_response(302);self.send_header('Location','/v1/sink');self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=SharedTransport(f'http://127.0.0.1:{server.server_port}',project_id='101',credential=lambda:'fixture-token',allow_local_http=True)
            with self.assertRaises(SharedApiError):client.request('/v1/redirect')
            self.assertEqual(seen,['/v1/redirect'])
        finally:server.shutdown();server.server_close();thread.join(timeout=2)
