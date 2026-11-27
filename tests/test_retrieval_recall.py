from copy import deepcopy
from dataclasses import replace
import json
import os
import subprocess
import threading
import time
import unittest

import test_confirmed_terms_agent as fixtures
from test_retrieval_terms_agent import Models
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_jobs import ExpansionJobs
from dsh_retrieval.expansion_models import preview_edited_groups
from dsh_retrieval.integration_models import MediaResolution
from dsh_retrieval.model_runtime import ModelRuntime


class RetrievalRecallTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ConfirmedTermsAgentTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        # Fully mapped synthetic source for the complete feedback path. The
        # explicit mapping_missing case below independently verifies fail-closed behavior.
        source = json.loads(self.f.f.fixture_path.read_text())
        representative = source['records'][0]['media']
        for record in source['records']:
            if not record['media']: record['media'] = deepcopy(representative)
        self.f.f.fixture_path.write_text(json.dumps(source))
        self.models = Models()
        original = self.models.expand_chunk
        def revised(*args, **kwargs):
            original(*args, **kwargs)
            return preview_edited_groups(None, [
                {'primary_label': '狗', 'auxiliary_terms': ['金毛']},
                {'primary_label': '猫', 'auxiliary_terms': ['橘猫']}])
        self.models.expand_chunk = revised
        self.queue = ExpansionJobs(None, self.models, None, self.f.service.projects, self.f.service,
                                   recall_provider=self.f.engine.recall)
        self.addCleanup(self.queue.close)
        self.f.server.RequestHandlerClass.retrieval_terms = self.queue

    def ready(self):
        _, task, ref = self.f.create()
        self.f.wait_review(task['task_id'], ref)
        task = self.f.service.get_task(self.f.actor, task['task_id'])
        return task, ref

    def report(self, task):
        status, report = self.f.call(f"/v1/retrieval/tasks/{task['task_id']}/retrieval-agent/recall")
        self.assertEqual(status, 200, report)
        return report

    def request(self, task, report):
        return {'expected_revision': task['confirmed_revision'], 'expected_task_version': task['task_version'],
                'raw_request': '狗3张、猫2张', 'target_count': 5, 'dimension_id': 'default',
                'model_selection': {'llm': 'chosen-llm', 'vlm': 'chosen-vlm'},
                'recall_evidence_digest': report['evidence_digest']}

    def wait_terms(self, task, start, status='completed'):
        for _ in range(200):
            job = self.queue.get(self.f.actor, 'demo', 'task:' + task['task_id'], start['job_id'])
            if job['status'] == status: return job
            time.sleep(.01)
        self.fail(str(job))

    def test_read_only_counts_are_attributed_and_feed_original_confirmation_loop(self):
        task, old_ref = self.ready(); tid = task['task_id']
        approved = self.f.engine.repository.get_current(self.f.actor, tid)
        before = self.f.engine.search_budget.snapshot(approved.scope)
        report = self.report(task)
        recorded = self.f.agent.get(self.f.actor, tid, old_ref)['recall_analysis']
        self.assertEqual(recorded['status'], 'recorded')
        self.assertEqual(recorded['evidence_digest'], report['evidence_digest'])
        self.assertTrue(report['complete']); self.assertTrue(report['can_refine'])
        self.assertEqual(len(report['branches']), 5)
        self.assertEqual(report['totals']['raw_hits'], sum(r['raw_hits'] for r in report['branches']))
        self.assertEqual(report['totals']['resolved_hits'], report['totals']['unique_candidates'] + report['totals']['repeated_hits'])
        self.assertEqual(report['totals']['assignable_candidates'],
                         len(self.f.engine._candidate_manifest_proposal(self.f.actor, tid).items))
        self.assertIsNone(report['quality']['accepted_count'])
        self.assertEqual(self.report(task)['evidence_digest'], report['evidence_digest'])
        self.assertEqual(self.f.engine.search_budget.snapshot(approved.scope), before)
        self.assertFalse(self.models.calls)
        path = f'/v1/retrieval/tasks/{tid}/retrieval-agent/terms'
        status, start = self.f.call(path, self.request(task, report))
        self.assertEqual(status, 202, start)
        done = self.wait_terms(task, start)
        self.assertEqual(done['recall_feedback']['evidence_digest'], report['evidence_digest'])
        self.assertEqual([g['delivery_target'] for g in done['result']['plan']['groups']], [3, 2])
        self.assertEqual(self.models.calls[0][3]['recall_context']['evidence_digest'], report['evidence_digest'])
        self.assertEqual(self.f.service.get_task(self.f.actor, tid)['task_version'], task['task_version'])
        self.assertEqual(self.f.engine.search_budget.snapshot(approved.scope), before)
        status, replay = self.f.call(path, self.request(task, report))
        self.assertEqual(replay['job_id'], start['job_id']); self.assertEqual(len(self.models.calls), 1)
        # Provisional feedback returns through the original owner-only revision gate.
        draft, _ = self.f.service.save_feedback(self.f.actor, tid, 'recall-feedback', {
            'text': done['feedback_text'], 'scope': 'all', 'expected_revision': 1,
            'expected_task_version': task['task_version'], 'grouped_expansion': done['result']['plan'], 'target_count': 5})
        self.f.service.confirm_feedback(self.f.actor, tid, 'recall-confirm', {
            'draft_id': draft['draft_id'], 'expected_draft_version': draft['draft_version'],
            'proposal_digest': draft['proposal_digest'], 'expected_task_version': task['task_version'], 'action': 'confirm'})
        dispatched = self.f.dispatch.dispatch_task(tid)
        self.assertNotEqual(dispatched['job_ref'], old_ref)
        self.assertEqual(self.f.agent.get(self.f.actor, tid, old_ref)['status'], 'cancelled')
        job = self.f.wait_review(tid, dispatched['job_ref'])
        self.assertTrue(any('金毛' in text for text in job['query_terms']))
        self.assertEqual(len(self.models.calls), 1)

    def test_unknown_search_is_not_empty_and_cannot_trigger_model_feedback(self):
        original = self.f.engine._adapter_for_plan
        def ambiguous(*args):
            adapter = original(*args)
            def fail(*args): raise ServiceError('NETWORK_UNAVAILABLE', 'Fixture ambiguous outcome', 503)
            adapter.search = fail
            return adapter
        self.f.engine._adapter_for_plan = ambiguous
        _, task, ref = self.f.create(); tid = task['task_id']
        for _ in range(100):
            self.f.worker.drain()
            if self.f.jobs.get(self.f.actor, tid, ref)['status'] == 'unknown': break
            time.sleep(.01)
        task = self.f.service.get_task(self.f.actor, tid)
        report = self.report(task)
        self.assertFalse(report['complete']); self.assertFalse(report['can_refine'])
        self.assertEqual(report['suggestions'][0]['action'], 'wait_or_reconcile')
        status, error = self.f.call(f'/v1/retrieval/tasks/{tid}/retrieval-agent/terms', self.request(task, report))
        self.assertEqual(status, 409, error); self.assertEqual(error['code'], 'RECALL_NOT_READY')
        self.assertFalse(self.models.calls)

    def test_mapping_unknown_and_stale_digest_cannot_be_used_for_refinement(self):
        task, _ = self.ready(); report = self.report(task)
        body = self.request(task, report); body['recall_evidence_digest'] = 'sha256:' + '0' * 64
        status, error = self.f.call(f"/v1/retrieval/tasks/{task['task_id']}/retrieval-agent/terms", body)
        self.assertEqual((status, error['code']), (409, 'RECALL_EVIDENCE_CHANGED'))
        original = self.f.engine._adapter_for_plan
        def unmapped(*args):
            adapter = original(*args)
            adapter.resolve_hits = lambda context, hits: tuple(MediaResolution(hit, None, 'mapping_missing') for hit in hits)
            return adapter
        self.f.engine._adapter_for_plan = unmapped
        unknown = self.report(task)
        self.assertTrue(unknown['complete']); self.assertFalse(unknown['mapping_complete'])
        self.assertFalse(unknown['can_refine']); self.assertGreater(unknown['totals']['unresolved_hits'], 0)
        self.assertEqual(unknown['suggestions'][0]['action'], 'repair_mapping')
        self.assertNotEqual(unknown['evidence_digest'], report['evidence_digest'])
        status, error = self.f.call(f"/v1/retrieval/tasks/{task['task_id']}/retrieval-agent/terms", self.request(task, unknown))
        self.assertEqual((status, error['code']), (409, 'RECALL_NOT_READY'))
        self.assertFalse(self.models.calls)

    def test_known_zero_committed_pages_can_propose_new_terms_without_fake_artifacts(self):
        original = self.f.engine._adapter_for_plan
        def empty(*args):
            adapter = original(*args); search = adapter.search
            adapter.search = lambda context, request: replace(search(context, request), hits=(), next_cursor=None, stop_reason='exhausted')
            return adapter
        self.f.engine._adapter_for_plan = empty
        _, task, ref = self.f.create(); tid = task['task_id']
        for _ in range(150):
            self.f.worker.drain()
            if self.f.jobs.get(self.f.actor, tid, ref)['status'] == 'completed': break
            time.sleep(.01)
        task = self.f.service.get_task(self.f.actor, tid); report = self.report(task)
        self.assertTrue(report['complete']); self.assertTrue(report['can_refine'])
        self.assertEqual(report['totals']['raw_hits'], 0)
        self.assertEqual(report['suggestions'][0]['action'], 'broaden_terms')
        self.assertFalse(self.f.jobs.artifacts(self.f.actor, tid, ref)['items'])

    def test_feedback_retains_confirmed_parent_and_quotas_not_in_raw_text(self):
        original = self.f.body
        def body():
            value = original(); value['query_spec']['raw_request'] = '找动物图片'
            value['query_spec']['grouped_expansion'] = preview_edited_groups('动物', [
                {'primary_label': '狗', 'auxiliary_terms': ['柯基'], 'delivery_target': 3},
                {'primary_label': '猫', 'auxiliary_terms': ['橘猫'], 'delivery_target': 2}])['plan']
            return value
        self.f.body = body
        task, _ = self.ready(); report = self.report(task)
        request = self.request(task, report); request['raw_request'] = '找动物图片'
        start = self.queue.start_task(self.f.actor, task['task_id'], request)
        plan = self.wait_terms(task, start)['result']['plan']
        self.assertEqual(plan['parent_label'], '动物')
        self.assertEqual([g['delivery_target'] for g in plan['groups']], [3, 2])

    def test_feedback_evidence_change_while_model_running_discards_response(self):
        task, _ = self.ready(); report = self.report(task)
        entered, release = threading.Event(), threading.Event(); self.addCleanup(release.set)
        original = self.models.expand_chunk
        def blocked(*args, **kwargs):
            entered.set(); release.wait(3)
            return original(*args, **kwargs)
        self.models.expand_chunk = blocked
        start = self.queue.start_task(self.f.actor, task['task_id'], self.request(task, report))
        self.assertTrue(entered.wait(2))
        changed = deepcopy(report); changed['evidence_digest'] = 'sha256:' + '1' * 64
        self.queue.recall_provider = lambda actor, tid: changed
        release.set()
        failed = self.wait_terms(task, start, 'failed')
        self.assertEqual(failed['error']['code'], 'RECALL_EVIDENCE_CHANGED')
        self.assertIsNone(failed['result']); self.assertEqual(failed['completed_terms'], 0)

    def test_report_uses_owner_scope_and_rejects_source_revocation(self):
        task, _ = self.ready(); tid = task['task_id']
        status, _ = self.f.call(f'/v1/retrieval/tasks/{tid}/retrieval-agent/recall', user='other')
        self.assertIn(status, (403, 404))
        def denied(*args): raise ServiceError('ACCESS_DENIED', 'Fixture revoked source', 403)
        self.f.engine._adapter_for_plan = denied
        status, error = self.f.call(f'/v1/retrieval/tasks/{tid}/retrieval-agent/recall')
        self.assertEqual(status, 403, error)

    def test_client_cannot_supply_a_report_or_silently_change_counts(self):
        task, _ = self.ready(); report = self.report(task)
        path = f"/v1/retrieval/tasks/{task['task_id']}/retrieval-agent/terms"
        for change in ({'recall_context': report}, {'target_count': 6}, {'requested_terms_per_group': 10},
                       {'current_groups': [{'primary_label': '其他', 'auxiliary_terms': ['其他词']}]},
                       {'raw_request': '换成其他主题'}):
            with self.subTest(change=change):
                status, error = self.f.call(path, self.request(task, report) | change)
                self.assertEqual(status, 422, error)
        self.assertFalse(self.models.calls)

    def test_original_model_port_receives_structured_feedback_not_a_replaced_user_request(self):
        model = ModelRuntime(); model.select = lambda *args: object()
        captured = []
        def respond(profile, prompt, content, **kwargs):
            captured.append((prompt, json.loads(content)))
            return {'parent_label': None, 'groups': [{'primary_label': '狗', 'auxiliary_terms': ['金毛']}]}
        model._request = respond
        evidence = {'schema_version': 'retrieval-recall@1', 'totals': {'raw_hits': 10, 'unique_candidates': 2}}
        model.expand_chunk('狗3张，不要人', 'llm', 'vlm', limit=1, recall_context=evidence)
        self.assertEqual(captured[0][1]['raw_request'], '狗3张，不要人')
        self.assertEqual(captured[0][1]['recall_context'], evidence)
        self.assertIn('不是全库召回率或人工语义质量评测', captured[0][0])

    def test_actual_chat_analysis_action_uses_read_then_provisional_terms_api(self):
        from dsh_retrieval.chat_ui import CHAT_JS
        task, _ = self.ready()
        source = CHAT_JS[CHAT_JS.index('async function showRecallAnalysis('):CHAT_JS.index('async function decideFeedback(')]
        script = r"""
const assert=require('node:assert/strict');
const state={task:JSON.parse(process.env.TASK)},actions=[],calls=[],watched=[],bubbles=[];
const fields={'llm-model':{value:'chosen-llm'},'vlm-model':{value:'chosen-vlm'},status:{textContent:''}};
const message=(role,title,text)=>{const p={textContent:text},b={dataset:{},isConnected:true,querySelector(){return p},remove(){this.isConnected=false}};bubbles.push(b);return b};
async function api(path,options={}){
 calls.push([path,options.method||'GET']);
 const r=await fetch(process.env.API+path,{method:options.method||'GET',headers:{'X-Tenant-Id':'tenant-a','X-User-Id':'user-a','Content-Type':'application/json','Idempotency-Key':'node-recall'},body:options.body?JSON.stringify(options.body):undefined});
 const data=await r.json();if(!r.ok)throw Error(JSON.stringify(data));return data;
}
const analyze=new Function('state','message','api','reviewDetails','guidedAction','$','setFollowupBusy','taskExpansionPath','watchTaskExpansionJob',process.env.SOURCE+';return showRecallAnalysis')(
 state,message,api,()=>{},(b,label,action)=>actions.push(action),id=>fields[id],()=>{},
 id=>'/v1/retrieval/tasks/'+id+'/retrieval-agent/terms',job=>watched.push(job));
(async()=>{
 await analyze();assert.equal(actions.length,1);assert.equal(watched.length,0);
 assert(calls.every(row=>row[1]==='GET'));
 await actions[0]();assert.equal(watched.length,1);assert(watched[0].recall_feedback);
 assert.equal(calls.filter(row=>row[1]==='POST').length,1);
 assert(!calls.some(([path])=>/first-search|confirmations|agent-jobs/.test(path)));
 assert.equal(fields.status.textContent,'');
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source,
            'TASK': json.dumps(task), 'API': self.f.base}, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
