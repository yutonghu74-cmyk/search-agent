"""Feedback uses real published cleaning and existing durable refill execution."""
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import patch

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.query_branches import compile_query_branches
from dsh_retrieval.refill_runtime import build_refill_runtime
from dsh_retrieval.refill_service import ConfirmedRefillService
from dsh_retrieval.retrieval_feedback import cleaning_feedback, source_yield
import test_confirmed_terms_agent as fixtures


class RetrievalFeedbackTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ConfirmedTermsAgentTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.engine, self.actor = self.f.engine, self.f.actor
        source = json.loads(self.f.f.fixture_path.read_text())
        for record in source['records']:
            if not record['media']:
                record['media'] = deepcopy(source['records'][0]['media'])
        self.f.f.fixture_path.write_text(json.dumps(source))
        self.cleaning, self.dispatcher, self.detector = self.f.automatic_intake()

    def ready(self, *, automatic=False, max_rounds=3):
        body = self.f.auto_body()
        if automatic:
            body['query_spec']['refill_policy'].update(mode='maintain_target', progression='auto',
                approved_library_ids=['dogs', 'cats'])
            body['query_spec']['refill_limits'] = dict(max_rounds=max_rounds, max_vlm_calls=100, no_progress_rounds=2)
            body['node_modes']['refill'] = 'auto'
        draft = self.f.draft(body)
        status, task = self.f.confirm(draft); self.assertEqual(status, 202, task)
        tid, ref = task['task_id'], task['search_enqueue']['job_ref']
        self.f.wait_intake(tid, ref)
        # Deterministic fixture gateway with the production approved-position
        # adapter. This does not certify a real CLIP encoder or VLM's quality.
        self.engine.mode = 'LOCAL_CLIP'
        self.runtime = build_refill_runtime(self.f.f.ledger, self.f.service, self.engine, start=False)
        self.addCleanup(self.runtime.close)
        self.refill = ConfirmedRefillService(self.f.service.repository, self.cleaning.repository, self.runtime)
        self.runtime.enable_auto(self.cleaning.repository, self.refill, start=False)
        self.runtime.enable_auto_intake(self.f.service.repository, self.engine, self.cleaning,
            self.dispatcher, self.refill, start=False)
        self.dispatcher._drain()
        return tid, ref

    def test_cleaning_counts_reasons_and_read_only_scope_are_real(self):
        from dsh_retrieval.multi_rule_contracts import DetectionResult
        original = self.detector.detect
        def mixed(content, candidate, binding):
            value = original(content, candidate, binding)
            return DetectionResult({'text.present@1': False, 'watermark.present@1': 'dog' in content.decode()},
                value.evidence_ref, value.validator_ref, 'ok', '可见水印')
        self.detector.detect = mixed
        tid, _ = self.ready()
        result = self.cleaning.repository.get_current_result_set(self.actor, tid)
        task = self.f.service.repository.get(self.actor, tid)
        approved = self.engine.repository.get_browse_current(self.actor, tid)
        before = self.engine.search_budget.snapshot(approved.scope); calls = self.detector.calls
        report = self.engine.recall(self.actor, tid)
        self.assertEqual(report['cleaning']['status'], 'observed')
        self.assertIsNone(report['quality']['accepted_count'])
        self.assertEqual(report['cleaning']['accepted_count'], result['accepted_count'])
        self.assertEqual(report['cleaning']['failed_count'], result['failed_count'])
        self.assertGreater(report['cleaning']['failed_count'], 0)
        self.assertTrue(report['cleaning']['reason_counts'])
        self.assertTrue(any('失败规则' in row['reason'] for row in report['suggestions']))
        self.assertEqual(report['continuation']['action'], 'stopped')
        self.assertEqual(report['continuation']['stop_reason'], 'INPUT_SCOPE_COMPLETE')
        self.assertEqual(report['scope']['task_version'], task.task_version)
        self.assertEqual(self.engine.recall(self.actor, tid)['evidence_digest'], report['evidence_digest'])
        self.assertEqual(self.detector.calls, calls)
        self.assertEqual(self.engine.search_budget.snapshot(approved.scope), before)
        self.assertFalse(self.runtime.reservations.list_current(self.actor, tid))

    def test_automatic_loop_persists_feedback_and_does_not_expand_scope_or_duplicate(self):
        tid, _ = self.ready(automatic=True, max_rounds=2)
        task = self.f.service.repository.get(self.actor, tid)
        original = json.loads(task.confirmed_proposal_json)
        allowed = {b['query_text'] for b in compile_query_branches(original['query_spec'])['branches']}
        report = self.engine.recall(self.actor, tid)
        self.assertEqual(report['continuation']['action'], 'continue_approved')
        self.runtime.auto.drain()
        reservations = self.runtime.reservations.list_current(self.actor, tid)
        self.assertEqual(len(reservations), 1)
        self.runtime.auto.drain()
        self.assertEqual(len(self.runtime.reservations.list_current(self.actor, tid)), 1)
        with sqlite3.connect(self.f.f.ledger) as db:
            rows = db.execute("SELECT result_json FROM auto_refill_outbox WHERE status='delivered'").fetchall()
        receipt = json.loads(rows[0][0])['feedback']
        self.assertEqual(receipt['action'], 'continue_approved')
        self.assertEqual(receipt['cleaning']['result_set_id'], report['cleaning']['result_set_id'])
        self.assertEqual(receipt['next_position']['selection']['evidence_source'], 'published_cleaning_attribution')
        for _ in range(6):
            self.runtime.auto_intake.drain(); self.dispatcher._drain(); self.runtime.auto.drain()
        current = self.refill.preview(self.actor, tid)
        self.assertIn(current['stop_reason'], ('NO_PROGRESS', 'EXECUTION_LIMIT_REACHED', 'SEARCH_STRATEGIES_EXHAUSTED'))
        self.assertLessEqual(len(self.runtime.reservations.list_current(self.actor, tid)), 2)
        for item in self.runtime.reservations.list_current(self.actor, tid):
            binding = json.loads(item.position_binding_json)
            self.assertIn(binding['query'], allowed); self.assertIn(binding['library_id'], ('dogs', 'cats'))
        fresh = self.f.service.repository.get(self.actor, tid)
        self.assertEqual(json.loads(fresh.confirmed_proposal_json), original)
        self.assertEqual(fresh.confirmed_revision, task.confirmed_revision)
        self.assertEqual(fresh.run_generation, task.run_generation)
        # Opening a new service over the same persistent ledgers makes the same
        # bounded decision and does not dispatch anything just by reading it.
        restarted = ConfirmedRefillService(self.f.service.repository, self.cleaning.repository, self.runtime)
        self.assertEqual(restarted.preview(self.actor, tid)['feedback'], current['feedback'])

    def test_stale_feedback_or_foreign_actor_cannot_plan_continuation(self):
        from dsh_retrieval.models import Actor
        tid, _ = self.ready(automatic=True)
        report = self.engine.recall(self.actor, tid)
        forged = deepcopy(report); forged['scope']['generation'] += 1
        with self.assertRaises(ServiceError):self.refill.preview(self.actor, tid, recall_report=forged)
        with self.assertRaises(ServiceError):self.engine.recall(Actor('tenant-a', 'foreign'), tid)
        self.assertFalse(self.runtime.reservations.list_current(self.actor, tid))

    def test_unknown_cleaning_is_never_failed_or_effective_and_cannot_refine(self):
        from dsh_retrieval.multi_rule_contracts import DetectionResult
        def unknown(*args):
            self.detector.calls += 1
            return DetectionResult({}, None, self.detector.validator_ref, 'unknown', '证据不足')
        self.detector.detect = unknown
        tid, _ = self.ready()
        report = self.engine.recall(self.actor, tid)
        self.assertEqual(report['cleaning']['accepted_count'], 0)
        self.assertEqual(report['cleaning']['failed_count'], 0)
        self.assertGreater(report['cleaning']['pending_review_count'], 0)
        self.assertFalse(report['can_refine'])
        self.assertEqual(report['suggestions'][0]['action'], 'wait_or_reconcile')
        self.assertEqual(report['continuation']['action'], 'review_cleaning')

    def test_unknown_publication_waits_for_review_without_spending_or_stagnation_stop(self):
        from dsh_retrieval.multi_rule_contracts import DetectionResult
        self.detector.detect = lambda *args: DetectionResult({}, None, self.detector.validator_ref, 'unknown', '证据不足')
        tid, _ = self.ready(automatic=True)
        self.runtime.auto.drain(); self.runtime.auto.drain()
        self.assertFalse(self.runtime.reservations.list_current(self.actor, tid))
        self.assertIsNone(self.runtime.reservations.execution_evidence(self.actor, tid)['stop_reason'])
        with sqlite3.connect(self.f.f.ledger) as db:
            result = json.loads(db.execute("SELECT result_json FROM auto_refill_outbox WHERE status='delivered'").fetchone()[0])
        self.assertEqual(result['status'], 'waiting_review')
        self.assertEqual(result['feedback']['action'], 'review_cleaning')

    def test_inflight_cleaning_reports_wait_without_reusing_stale_counts(self):
        tid, _ = self.ready(automatic=True)
        with patch.object(self.cleaning.repository, 'get_current_result_set',
                          side_effect=ServiceError('REVISION_CONFLICT', 'new cleaning underway', 409)):
            report = self.engine.recall(self.actor, tid)
        self.assertEqual(report['cleaning']['status'], 'not_current')
        self.assertIsNone(report['quality']['accepted_count'])
        self.assertEqual(report['continuation']['action'], 'wait_for_result')
        self.assertFalse(report['can_refine'])
        self.assertFalse(self.runtime.reservations.list_current(self.actor, tid))

    def test_search_budget_stop_does_not_allocate_one_more_refill(self):
        tid, _ = self.ready(automatic=True)
        approved = self.engine.repository.get_browse_current(self.actor, tid)
        budget = self.engine.search_budget.snapshot(approved.scope)
        exhausted = {**budget, 'remaining_calls': 0, 'remaining_hit_slots': 0}
        with patch.object(self.engine.search_budget, 'snapshot', return_value=exhausted):
            view = self.refill.preview(self.actor, tid)
            self.assertEqual(view['stop_reason'], 'EXECUTION_LIMIT_REACHED')
            self.assertIsNone(view['next_position'])
            self.runtime.auto.drain()
        self.assertFalse(self.runtime.reservations.list_current(self.actor, tid))
        with sqlite3.connect(self.f.f.ledger) as db:
            receipt = json.loads(db.execute("SELECT result_json FROM auto_refill_outbox WHERE status='delivered'").fetchone()[0])
        self.assertEqual(receipt['feedback']['action'], 'stopped')
        self.assertEqual(receipt['feedback']['execution']['search_budget']['remaining_calls'], 0)

    def test_late_event_and_stop_are_fenced_by_the_original_task_version(self):
        tid, _ = self.ready(automatic=True)
        event = self.cleaning.repository.pending_auto_refill_events()[0]
        view = self.refill.preview(self.actor, tid)
        with patch.object(self.refill, 'preview', return_value={**view, 'task_version': event['task_version'] + 1}):
            self.assertEqual(self.refill.advance_auto_event(self.actor, event)['status'], 'superseded')
        with self.assertRaises(ServiceError):
            self.runtime.reservations.record_execution_stop(self.actor, tid, 'NO_PROGRESS',
                expected_revision=event['revision'], expected_generation=event['generation'],
                expected_task_version=event['task_version'] + 1)
        with self.assertRaises(ServiceError):
            self.cleaning.repository.finalize_refill_stop(self.actor, tid, 'NO_PROGRESS',
                expected_revision=event['revision'], expected_generation=event['generation'] + 1,
                expected_task_version=event['task_version'])
        self.assertFalse(self.runtime.reservations.list_current(self.actor, tid))
        self.assertIsNone(self.runtime.reservations.execution_evidence(self.actor, tid)['stop_reason'])

    def test_published_result_change_is_detected_before_using_feedback(self):
        tid, _ = self.ready(automatic=True)
        report = self.engine.recall(self.actor, tid)
        report['cleaning']['result_set_digest'] = 'sha256:' + '0' * 64
        with self.assertRaises(ServiceError):self.refill.preview(self.actor, tid, recall_report=report)
        self.assertFalse(self.runtime.reservations.list_current(self.actor, tid))


class SourceYieldSemanticsTest(unittest.TestCase):
    def test_reserve_duplicates_and_unknown_are_not_target_counted_yield(self):
        rows = {'a': {'disposition': 'accepted', 'cleaning_status': 'pass'},
                'b': {'disposition': 'reserve', 'cleaning_status': 'pass'},
                'c': {'disposition': 'duplicate', 'cleaning_status': 'pass'},
                'd': {'disposition': 'review', 'cleaning_status': 'unknown'}}
        measured = source_yield(set(rows) | {'missing'}, rows)
        self.assertEqual(measured['effective_unique'], 1)
        self.assertEqual(measured['reserve'], 1)
        self.assertEqual(measured['duplicates'], 1)
        self.assertEqual(measured['unknown'], 1)
        self.assertEqual(measured['unassessed'], 1)
        self.assertEqual(measured['failed'], 0)


class FeedbackUiTest(unittest.TestCase):
    def test_chat_displays_published_counts_and_confirmation_boundary(self):
        import os, shutil, subprocess
        if not shutil.which('node'):self.skipTest('Node required')
        from dsh_retrieval.chat_ui import CHAT_JS
        source = CHAT_JS[CHAT_JS.index('async function showRecallAnalysis('):CHAT_JS.index('async function decideFeedback(')]
        script = r"""
const assert=require('node:assert/strict');
const state={task:{task_id:'t',task_version:4}},p={textContent:''},actions=[];
const bubble={dataset:{},querySelector:()=>p,remove(){},isConnected:false};
const report={scope:{task_version:4},complete:true,can_refine:true,
 totals:{raw_hits:12,unique_candidates:4,repeated_hits:8,assignable_candidates:4},branches:[],
 suggestions:[{reason:'保留原动作与硬条件'}],cleaning:{status:'observed',accepted_count:1,failed_count:2,pending_review_count:1,duplicate_count:0,gap:9},
 continuation:{reason:'新词表和扩大范围仍需确认。'}};
const analyze=new Function('state','message','api','reviewDetails','guidedAction',process.env.SOURCE+';return showRecallAnalysis')(
 state,()=>bubble,async()=>report,()=>{},(b,label,cb)=>actions.push([label,cb]));
(async()=>{await analyze();assert(p.textContent.includes('清洗后有效 1 张'));
 assert(p.textContent.includes('淘汰 2 张，待复核 1 张'));assert(p.textContent.includes('有效缺口 9 张'));
 assert(p.textContent.includes('新词表和扩大范围仍需确认'));assert.equal(actions.length,1);
 report.cleaning={status:'not_published'};report.can_refine=false;actions.length=0;
 await analyze();assert(p.textContent.includes('尚无当前已发布清洗结果'));assert.equal(actions.length,0);
})().catch(e=>{console.error(e);process.exit(1)});
"""
        run = subprocess.run(['node','-e',script], env={**os.environ,'SOURCE':source},capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
