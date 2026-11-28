from copy import deepcopy
import json
import os
import shutil
import subprocess
import unittest

from dsh_retrieval.gap_diagnosis import diagnose_gap


def report():
    return {'scope': {'task_id': 'rt_test', 'revision': 1, 'generation': 1, 'task_version': 4},
        'complete': True, 'mapping_complete': True, 'target_count': 20,
        'totals': {'assignable_candidates': 8, 'unresolved_hits': 0,
                   'content_unknown_candidates': 0, 'repeated_hit_ratio': .2},
        'execution_observation': {'queue_state': 'not_queued', 'pending_operations': 0,
                                  'not_started': 0, 'more_available': 2},
        'execution_budget': {}, 'cleaning': {'status': 'observed', 'accepted_count': 3,
            'failed_count': 4, 'duplicate_count': 1, 'pending_review_count': 0,
            'gap': 17, 'group_gaps': [], 'reason_counts': []}}


class GapDiagnosisTest(unittest.TestCase):
    def test_explanations_use_observed_counts_and_do_not_mutate_input_or_authorize(self):
        value = report(); before = deepcopy(value); result = diagnose_gap(value)
        self.assertEqual(value, before)
        self.assertEqual(result, diagnose_gap(value))
        self.assertEqual(result['effective_gap'], 17)
        self.assertEqual(result['corpus_sufficiency'], 'unknown')
        self.assertFalse(result['next_step']['execution_allowed'])
        self.assertEqual(result['next_step']['action'], 'check_continuation')
        self.assertTrue({'CLEANING_REJECTIONS', 'CONTENT_DUPLICATES', 'WINDOW_CANDIDATE_SHORTFALL'} <= {f['code'] for f in result['facts']})

    def test_pending_failed_and_missing_mapping_never_suggest_repeat_or_expand(self):
        value = report(); value['complete'] = False
        value['execution_observation'].update(queue_state='failed', queue_error='NETWORK_UNAVAILABLE', pending_operations=1)
        value['continuation'] = {'action': 'continue_approved'}
        self.assertEqual(diagnose_gap(value)['next_step']['action'], 'reconcile_operation')
        value['execution_observation']['pending_operations'] = 0
        self.assertEqual(diagnose_gap(value)['next_step']['action'], 'inspect_failure')
        value['execution_observation']['queue_state'] = 'not_queued'
        value['mapping_complete'] = False
        self.assertEqual(diagnose_gap(value)['next_step']['action'], 'repair_mapping')
        value['mapping_complete'] = True
        self.assertEqual(diagnose_gap(value)['next_step']['action'], 'wait_for_result')

    def test_unpublished_and_stale_counts_are_unknown_instead_of_target_minus_candidates(self):
        for status in ('not_current', 'not_published', 'not_configured'):
            with self.subTest(status=status):
                value = report(); value['cleaning'] = {'status': status, 'gap': 0, 'accepted_count': 20}
                result = diagnose_gap(value)
                self.assertIsNone(result['effective_gap'])
                self.assertEqual(result['next_step']['action'], 'review_candidates')
                self.assertIn('EFFECTIVE_COUNT_UNKNOWN', {f['code'] for f in result['facts']})

    def test_pending_review_blocks_continuation_and_cannot_be_called_rejection(self):
        value = report(); value['cleaning'].update(pending_review_count=2, failed_count=0)
        value['continuation'] = {'action': 'continue_approved'}
        result = diagnose_gap(value)
        self.assertEqual(result['next_step']['action'], 'review_cleaning')
        self.assertNotIn('CLEANING_REJECTIONS', {f['code'] for f in result['facts']})

    def test_total_met_does_not_hide_group_gap(self):
        value = report(); value['cleaning']['gap'] = 0
        self.assertEqual(diagnose_gap(value)['next_step']['action'], 'target_reached')
        value['cleaning']['group_gaps'] = [{'group_id': 'dogs', 'gap': 2}]
        result = diagnose_gap(value)
        self.assertNotEqual(result['next_step']['action'], 'target_reached')
        self.assertEqual(result['group_gaps'][0]['gap'], 2)

    def test_planner_controls_authority_and_stops_do_not_prove_corpus_exhaustion(self):
        cases = [('continue_approved', None, 'continue_approved'),
                 ('owner_confirmation', None, 'owner_confirmation'),
                 ('stopped', 'EXECUTION_LIMIT_REACHED', 'review_budget'),
                 ('stopped', 'NO_PROGRESS', 'review_strategy'),
                 ('stopped', 'SEARCH_STRATEGIES_EXHAUSTED', 'review_strategy'),
                 ('stopped', 'INPUT_SCOPE_COMPLETE', 'review_strategy'),
                 ('wait_for_result', None, 'wait_for_result')]
        for action, stop, expected in cases:
            with self.subTest(action=action, stop=stop):
                value = report(); value['continuation'] = {'action': action, 'stop_reason': stop}
                result = diagnose_gap(value)
                self.assertEqual(result['next_step']['action'], expected)
                self.assertEqual(result['corpus_sufficiency'], 'unknown')
                self.assertFalse(result['next_step']['execution_allowed'])
        value = report(); value['complete'] = False; value['execution_budget']['stop_reason'] = 'MAX_CALLS'
        self.assertEqual(diagnose_gap(value)['next_step']['action'], 'review_budget')

    def test_ui_refresh_is_read_only_and_stale_navigation_cannot_open_review(self):
        if not shutil.which('node'): self.skipTest('Node required')
        from dsh_retrieval.chat_ui import CHAT_JS
        source = CHAT_JS[CHAT_JS.index('async function showRecallAnalysis('):CHAT_JS.index('async function decideFeedback(')]
        value = report(); value.update(can_refine=False, suggestions=[], branches=[])
        value['totals'].update(raw_hits=8, unique_candidates=8, repeated_hits=0)
        value['diagnosis'] = diagnose_gap(value)
        script = r'''
const assert=require('node:assert/strict');
const report=JSON.parse(process.env.REPORT),state={task:{task_id:'rt_test',task_version:4}},actions=[],calls=[];
const b={dataset:{},isConnected:true,querySelector(){return this.p},p:{textContent:''},remove(){this.isConnected=false}};
let opened=0,clicked=0,resolve;
const fields={'review-tab':{click(){clicked++}}};
const api=async(path,opts)=>{calls.push([path,opts]);return structuredClone(report)};
const fn=new Function('state','message','api','reviewDetails','guidedAction','$','openCandidateBrowser',process.env.SOURCE+';return showRecallAnalysis')(
 state,()=>b,api,()=>{},(b,label,action)=>actions.push({label,action}),id=>fields[id],()=>{opened++;return new Promise(r=>resolve=r)});
(async()=>{
 report.diagnosis.next_step.action='reconcile_operation';await fn();
 assert.equal(actions[0].label,'重新读取诊断');await actions[0].action();
 assert.equal(calls.length,2);assert(calls.every(c=>c[1]===undefined));
 actions.length=0;report.diagnosis.next_step.action='review_cleaning';await fn();
 const done=actions[0].action();assert.equal(opened,1);state.task={task_id:'other',task_version:4};resolve();await done;
 assert.equal(clicked,0);await actions[0].action();assert.equal(opened,1);
})().catch(e=>{console.error(e);process.exit(1)});
'''
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source, 'REPORT': json.dumps(value)}, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
