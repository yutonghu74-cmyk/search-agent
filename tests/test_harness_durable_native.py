"""Actual rc.5 with same-ledger reservations and a deterministic localhost model."""
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import tempfile
import threading
import time
import unittest
from urllib.request import Request,urlopen

import test_harness_journal as fixtures
from dsh_retrieval.adapters.harness.dsh import DshAdapter
from dsh_retrieval.adapters.harness.dsh_releases import CANDIDATE_DSH
from dsh_retrieval.harness.contracts import AdapterRegistry, RuntimeRelease
from dsh_retrieval.harness.durable import DurableHarnessRunner


@unittest.skipUnless(os.environ.get('DSH_CANDIDATE_TEST_PYTHON'), 'actual rc.5 requires explicit isolated paths')
class DurableNativeTest(unittest.TestCase):
    def test_actual_rc5_admission_cap_and_restart_recovery(self):
        for allocation, expected_calls, status in ((2, 2, 'finished'), (1, 1, 'failed')):
            with self.subTest(allocation=allocation):
                self.run_case(allocation, expected_calls, status)

    def test_public_stage_api_runs_actual_rc5_and_survives_service_restart(self):
        self.run_case(2,2,'finished',public_api=True)

    def run_case(self, allocation, expected_calls, expected_status, *, public_api=False):
        case = fixtures.HarnessJournalTest(); case.setUp()
        self.addCleanup(case.doCleanups)
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(body)
                if len(calls) == 1:
                    name, arguments = 'get_stage_context', {}
                else:
                    name, arguments = 'submit_retrieval_strategy', case.proposal
                delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'call-{len(calls)}',
                    'type': 'function', 'function': {'name': 'mcp__business__' + name,
                                                   'arguments': json.dumps(arguments)}}]}
                rows = [{'id': 'test', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'test',
                    'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                    {'id': 'test', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'test',
                    'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}]
                payload = (''.join('data: ' + json.dumps(row) + '\n\n' for row in rows) + 'data: [DONE]\n\n').encode()
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(timeout=2)))
        with tempfile.TemporaryDirectory(prefix='dn-', dir='/tmp') as home:
            adapter = DshAdapter(python=os.environ['DSH_CANDIDATE_TEST_PYTHON'], home=home,
                dsh_bin=os.environ['DSH_CANDIDATE_TEST_DSH_BIN'], node=os.environ['DSH_CANDIDATE_TEST_NODE'],
                npm_lock=os.environ['DSH_CANDIDATE_TEST_NPM_LOCK'], release=CANDIDATE_DSH,
                adapter_id='rc5', model_environment=lambda _: {'RETRIEVAL_MODEL_KEY': 'test-placeholder',
                    'RETRIEVAL_MODEL_URL': f'http://127.0.0.1:{server.server_port}/v1',
                    'RETRIEVAL_MODEL_ID': 'test', 'RETRIEVAL_MAX_TOKENS': '12000'})
            registry = AdapterRegistry(); registry.register('rc5', adapter)
            release = RuntimeRelease('rc5-release', 'rc5', adapter.artifact_digest)
            def runner(): return DurableHarnessRunner(registry, {'retrieval': release}, {'retrieval': case.role},
                case.catalog, guard=case.guard, model_binding=lambda _: case.stage.model_binding_digest,
                journal=case.reopen(), actor=case.actor)
            if public_api:
                self.public_flow(case,runner,calls,adapter)
                return
            case.approve(); first = runner()
            handle = first.enqueue(case.stage, run_id='run', attempt_id='attempt', model_profile_ref='model',
                task_kind='plan', deadline=time.time() + 40, context_json=case.inv.context_json, model_calls=allocation)
            self.assertTrue(first.dispatch(handle))
            stop = time.monotonic() + 40
            while time.monotonic() < stop:
                row = first.poll(handle)
                if row['status'] in ('finished', 'failed'): break
                time.sleep(.05)
            else:
                first.cancel(handle); self.fail('durable SDK run did not finish')
            self.assertEqual(row['status'], expected_status, row['events'])
            self.assertEqual(len(calls), expected_calls)
            self.assertEqual((row['admitted'], row['budget_reserved']), (allocation, allocation))
            second = runner()
            self.assertFalse(second.dispatch(handle))
            if expected_status == 'finished':
                self.assertEqual(second.outputs(handle)[0]['arguments'], case.proposal)
            else:
                self.assertEqual(row['outputs'], ())
            self.assertEqual(len(calls), expected_calls)
            first.close(); second.close()


    def public_flow(self, case, factory, calls, adapter):
        from dsh_retrieval.api import create_handler
        from dsh_retrieval.adapters.harness.composition import build_agent_planning_service
        from dsh_retrieval.harness.stage_tools import StageAuthority
        config={'schema_version':'harness-routes@1','releases':{'rc5-api':{
            'adapter_id':'rc5-api','sdk_release':CANDIDATE_DSH.release_id,
            'python':os.environ['DSH_CANDIDATE_TEST_PYTHON'],'home':str(adapter.home),
            'dsh_bin':os.environ['DSH_CANDIDATE_TEST_DSH_BIN'],
            'node':os.environ['DSH_CANDIDATE_TEST_NODE'],
            'npm_lock':os.environ['DSH_CANDIDATE_TEST_NPM_LOCK']}},
            'roles':{'retrieval':'rc5-api','cleaning':'rc5-api'}}
        def authority(actor,inv):
            task=case.repo.get(actor,inv.stage.scope.task_id)
            return StageAuthority(actor,task,inv.stage,json.loads(task.confirmed_proposal_json)['query_spec'],
                                  10,(case.binding,),(),('facts-1',))
        def service():
            return build_agent_planning_service(case.service,config,authority=authority,
                context_provider=lambda actor,task,role:json.loads(case.inv.context_json),
                model_environment=lambda actor,ref:adapter.model_environment(ref),
                model_binding=lambda actor,ref:case.stage.model_binding_digest,lease_seconds=1)
        app=service(); self.addCleanup(app.close)
        handler=create_handler(case.service,agent_planning=app)
        handler.log_message=lambda *args:None
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join(timeout=2)))
        self.addCleanup(handler.task_commands.store.connection.close)
        base=f'http://127.0.0.1:{server.server_port}/v1/retrieval/tasks/{case.task.task_id}/agent-stages/'
        def request(path,body=None,key='native'):
            value=Request(base+path,data=json.dumps(body).encode() if body is not None else None,
                headers={'Content-Type':'application/json','X-Tenant-Id':'tenant','X-User-Id':'owner','Idempotency-Key':key})
            with urlopen(value,timeout=5) as response:return response.status,json.load(response)
        code,review=request('prepare',{'role':'retrieval','model_profile_ref':'model','model_calls':2,
            'expected_revision':case.task.confirmed_revision,'expected_generation':case.task.run_generation,
            'expected_task_version':case.task.task_version},'prepare-native')
        self.assertEqual(code,201)
        self.assertEqual(calls,[])
        draft=review['stage_draft_id']
        for action in ('confirm','start'):
            code,result=request(draft+'/'+action,{'action':action,'proposal_digest':review['proposal_digest'],
                'expected_task_version':review['expected_task_version']},action+'-native')
            self.assertEqual(code,202 if action=='start' else 200,result)
        stop=time.monotonic()+40
        while time.monotonic()<stop:
            code,result=request(draft)
            if result['status'] in ('finished','failed'):break
            time.sleep(.05)
        self.assertEqual(result['status'],'finished',result)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['outputs'][0]['arguments'],case.proposal)
        self.assertFalse(result['execution_allowed'])
        self.assertNotIn('context_json',result)
        restarted=service(); self.addCleanup(restarted.close)
        handler.agent_planning=restarted
        self.assertEqual(request(draft)[1]['outputs'],result['outputs'])
        self.assertEqual(len(calls),2)
        # Consume the actual native tool receipt through the SDK-neutral business
        # bridge, then use the existing owner-confirmation/page ledger unchanged.
        from dsh_retrieval.agent_retrieval import AgentRetrievalService
        from dsh_retrieval.adapters.task_storage.agent_retrieval import SQLiteAgentRetrievalHandoffs
        from dsh_retrieval.adapters.task_storage.retrieval import SQLiteRetrievalPlanRepository
        from dsh_retrieval.physical_query_preparation import TextQueryCapability
        from dsh_retrieval.integration_models import QueryVector, SearchPage
        class FixturePorts:
            def require_access(self, *args): pass
            def encode_query(self, context, query, binding):
                return QueryVector(binding.encoder_ref, (.2,) * binding.vector_dimension)
        ports=FixturePorts()
        retrieval=SQLiteRetrievalPlanRepository(case.repo.path,projects=case.service.projects,authorization=ports)
        handoffs=SQLiteAgentRetrievalHandoffs(journal=restarted.journal,retrieval=retrieval,
            authority=lambda inv:authority(case.actor,inv),
            context_provider=lambda actor,task,role:json.loads(case.inv.context_json))
        bridge=AgentRetrievalService(handoffs=handoffs,embeddings=ports,
            capabilities=(TextQueryCapability(case.binding,'image','filter@1',10),))
        row,review=restarted.store.get(case.actor,case.task.task_id,draft)
        handle=restarted.journal.handle(restarted.store.invocation(row,review))
        plan=bridge.prepare(case.actor,handle,epoch=1,batch_size=2)
        self.assertIsNone(retrieval.get_current(case.actor,case.task.task_id))
        approved,_=retrieval.confirm(case.actor,case.task.task_id,plan.request.plan_id,key='business-confirm',
            proposal_digest=plan.proposal_digest,expected_task_version=plan.scope.task_version,action='confirm')
        operation,_=retrieval.reserve_page(case.actor,approved,approved.request,'native-page')
        self.assertFalse(retrieval.commit_page(case.actor,operation,approved,approved.request,
            SearchPage((),None,'exhausted','snapshot','fixture-snapshot')))
        self.assertEqual(len(calls),2)


if __name__ == '__main__': unittest.main()
