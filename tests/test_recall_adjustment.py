from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.query_spec import digest
from dsh_retrieval.recall_adjustment import adjustment_scope, require_adjustable
from dsh_retrieval.expansion_models import preview_edited_groups
import test_retrieval_recall as fixtures


class AdjustmentPolicyTest(unittest.TestCase):
    def test_only_missing_groups_are_editable_and_ambiguous_gaps_fail_closed(self):
        groups = [{'group_id':'dog','primary_label':'狗','auxiliary_terms':['柯基'],'delivery_target':3},
                  {'group_id':'cat','primary_label':'猫','auxiliary_terms':['橘猫'],'delivery_target':2}]
        report = {'can_refine':True,'evidence_digest':'sha256:test','scope':{},
                  'diagnosis':{'next_step':{'action':'review_strategy'}},
                  'cleaning':{'status':'observed','group_gaps':[{'group_id':'dog','gap':1},{'group_id':'cat','gap':0}]}}
        policy = adjustment_scope(report, groups)
        self.assertEqual(policy['editable_groups'], ['狗']);self.assertEqual(policy['preserved_groups'], ['猫'])
        for gaps in ([], [{'group_id':'dog','gap':1}], [{'group_id':'dog','gap':0},{'group_id':'cat','gap':0}],
                     [{'group_id':'dog','gap':1},{'group_id':'dog','gap':0}],
                     [{'group_id':'dog','gap':True},{'group_id':'cat','gap':0}]):
            with self.subTest(gaps=gaps):
                changed=deepcopy(report);changed['cleaning']['group_gaps']=gaps
                with self.assertRaises(ServiceError):adjustment_scope(changed,groups)

    def test_diagnosis_gate_overrides_legacy_can_refine_flag(self):
        for action in ('review_cleaning','wait_for_result','continue_approved','owner_confirmation',
                       'target_reached','review_budget','reconcile_operation','inspect_failure','repair_mapping','check_continuation'):
            with self.subTest(action=action):
                with self.assertRaises(ServiceError):require_adjustable({'can_refine':True,'diagnosis':{'next_step':{'action':action}}})
        for action in ('review_strategy','review_candidates'):
            require_adjustable({'can_refine':True,'diagnosis':{'next_step':{'action':action}}})
        require_adjustable({'can_refine':True})  # older trusted provider remains compatible


class ScopedAdjustmentTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.RetrievalRecallTest();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def scoped_report(self):
        task,ref=self.f.ready();report=self.f.report(task)
        spec=json.loads(self.f.f.service.repository.get(self.f.f.actor,task['task_id']).confirmed_proposal_json)['query_spec']
        groups=spec['grouped_expansion']['groups']
        report['cleaning']={'status':'observed','accepted_count':4,'failed_count':1,'pending_review_count':0,
                            'duplicate_count':0,'gap':1,'group_gaps':[
                                {'group_id':g['group_id'],'gap':1 if g['primary_label']=='狗' else 0} for g in groups]}
        report['diagnosis']['next_step']['action']='review_strategy'
        report.pop('evidence_digest');report['evidence_digest']=digest(report)
        self.f.queue.recall_provider=lambda *args:deepcopy(report)
        return task,report,groups

    def test_scoped_model_proposal_preserves_met_group_and_only_owner_confirmation_executes(self):
        task,report,groups=self.scoped_report()
        calls=[]
        def model(*args,**kwargs):
            calls.append(kwargs);return preview_edited_groups(None,[{'primary_label':kwargs['label'],'auxiliary_terms':['金毛']}])
        self.f.models.expand_chunk=model
        before=self.f.f.service.get_task(self.f.f.actor,task['task_id'])
        request=self.f.request(task,report)
        job=self.f.queue.start_task(self.f.f.actor,task['task_id'],request)
        done=self.f.wait_terms(task,job)
        self.assertEqual(len(calls),1);self.assertEqual(calls[0]['label'],'狗')
        output={g['primary_label']:g for g in done['result']['plan']['groups']}
        self.assertEqual(output['猫']['auxiliary_terms'],next(g['auxiliary_terms'] for g in groups if g['primary_label']=='猫'))
        self.assertEqual([g['delivery_target'] for g in done['result']['plan']['groups']], [g['delivery_target'] for g in groups])
        receipt=done['recall_feedback']['adjustment']
        self.assertEqual(receipt['editable_groups'],['狗']);self.assertEqual(receipt['preserved_groups'],['猫'])
        self.assertTrue(receipt['requires_owner_confirmation']);self.assertFalse(receipt['execution_allowed'])
        self.assertEqual(receipt['group_changes'][1]['added_terms'],[])
        self.assertEqual(self.f.f.service.get_task(self.f.f.actor,task['task_id']),before)
        replay=self.f.queue.start_task(self.f.f.actor,task['task_id'],request)
        self.assertEqual(replay['job_id'],job['job_id']);self.assertEqual(len(calls),1)
        # Existing owner approval makes a new revision; generation was only provisional.
        draft,_=self.f.f.service.save_feedback(self.f.f.actor,task['task_id'],'adjustment-draft',{
            'text':done['feedback_text'],'scope':'all','expected_revision':1,'expected_task_version':task['task_version'],
            'target_count':5,'grouped_expansion':done['result']['plan']})
        updated,_=self.f.f.service.confirm_feedback(self.f.f.actor,task['task_id'],'adjustment-confirm',{
            'draft_id':draft['draft_id'],'expected_draft_version':draft['draft_version'],'proposal_digest':draft['proposal_digest'],
            'expected_task_version':task['task_version'],'action':'confirm'})
        self.assertEqual(updated['task']['task_id'],task['task_id']);self.assertEqual(updated['task']['confirmed_revision'],2)

    def test_blocked_diagnoses_make_no_job_or_model_call_even_with_can_refine(self):
        task,report,groups=self.scoped_report();before=self.f.queue.db.execute('SELECT COUNT(*) FROM expansion_jobs').fetchone()[0]
        for action in ('review_budget','review_cleaning','continue_approved','target_reached','reconcile_operation'):
            report['diagnosis']['next_step']['action']=action;report.pop('evidence_digest');report['evidence_digest']=digest(report)
            with self.subTest(action=action):
                with self.assertRaises(ServiceError) as caught:self.f.queue.start_task(self.f.f.actor,task['task_id'],self.f.request(task,report))
                self.assertEqual(caught.exception.code,'RECALL_NOT_READY')
        self.assertEqual(self.f.queue.db.execute('SELECT COUNT(*) FROM expansion_jobs').fetchone()[0],before)
        self.assertFalse(self.f.models.calls)

    def test_failed_scoped_batch_restarts_with_preserved_group_and_same_receipt(self):
        from dsh_retrieval.expansion_jobs import ExpansionJobs
        task,report,groups=self.scoped_report()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'terms.sqlite3'
            queue=ExpansionJobs(path,self.f.models,None,self.f.f.service.projects,self.f.f.service,
                                recall_provider=lambda *args:deepcopy(report))
            self.f.queue=queue
            self.f.models.expand_chunk=lambda *args,**kwargs:(_ for _ in ()).throw(ServiceError('MODEL_RATE_LIMIT','Fixture limited',429))
            job=queue.start_task(self.f.f.actor,task['task_id'],self.f.request(task,report))
            failed=self.f.wait_terms(task,job,'failed')
            self.assertEqual(failed['completed_terms'],1)
            self.assertEqual(failed['recall_feedback']['adjustment']['preserved_groups'],['猫'])
            queue.close()
            self.f.models.expand_chunk=lambda *args,**kwargs:preview_edited_groups(None,[{'primary_label':kwargs['label'],'auxiliary_terms':['金毛']}])
            reopened=ExpansionJobs(path,self.f.models,None,self.f.f.service.projects,self.f.f.service,
                                   recall_provider=lambda *args:deepcopy(report))
            self.addCleanup(reopened.close);self.f.queue=reopened
            reopened.retry(self.f.f.actor,'demo','task:'+task['task_id'],job['job_id'])
            done=self.f.wait_terms(task,job)
            self.assertEqual(done['recall_feedback']['adjustment']['preserved_groups'],['猫'])
            self.assertEqual(done['result']['plan']['groups'][1]['auxiliary_terms'],groups[1]['auxiliary_terms'])


class AdjustmentReviewUiTest(unittest.TestCase):
    def test_diff_is_visible_and_changed_evidence_does_not_save_or_confirm(self):
        if not shutil.which('node'):self.skipTest('Node required')
        from dsh_retrieval.chat_ui import CHAT_JS
        source=CHAT_JS[CHAT_JS.index('function taskExpansionPath('):CHAT_JS.index('async function restoreTaskExpansionJob(')]
        script=r"""
const assert=require('node:assert/strict');
const state={task:{task_id:'t',task_version:2,confirmed_revision:1}},actions=[],details=[],calls=[],status={textContent:''};
const bubble=()=>({isConnected:true,querySelector(){return {textContent:''}},remove(){}});
let evidence='changed';
const api=async(path,options)=>{calls.push([path,options]);return {evidence_digest:evidence}};
const watch=new Function('state','$','message','reviewDetails','guidedAction','key','setBusy','api','decideFeedback',process.env.SOURCE+';return watchTaskExpansionJob')(
 state,id=>status,bubble,(b,l,t)=>details.push([l,t]),(b,l,a)=>actions.push([l,a]),()=> 'review-token',()=>{},api,()=>{throw Error('must not confirm')});
const job={task_id:'t',task_version:2,revision:1,job_id:'j',target_count:5,status:'completed',feedback_text:'保持原条件',
 recall_feedback:{evidence_digest:'original',adjustment:{editable_groups:['狗'],preserved_groups:['猫'],group_changes:[
 {group_label:'狗',editable:true,added_terms:['金毛'],removed_terms:['柯基']},{group_label:'猫',editable:false,added_terms:[],removed_terms:[]}]}},
 result:{plan:{groups:[{primary_label:'狗',auxiliary_terms:['金毛'],delivery_target:3},{primary_label:'猫',auxiliary_terms:['橘猫'],delivery_target:2}]}}};
(async()=>{
 watch(job);const diff=details.find(([l])=>l==='查看调整范围与逐组差异')[1];
 assert.match(diff,/保持原词表：猫/);assert.match(diff,/金毛/);assert.match(diff,/柯基/);
 await actions.find(([l])=>l==='确认新版词表')[1]();
 assert.equal(calls.length,1);assert(calls[0][0].endsWith('/recall'));assert.equal(calls[0][1],undefined);
 assert.match(status.textContent,/证据已变化/);
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result=subprocess.run(['node','-e',script],env={**os.environ,'SOURCE':source},capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
