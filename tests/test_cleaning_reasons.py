import os
import shutil
import subprocess
import unittest
from dataclasses import replace
from unittest.mock import patch

from tests import test_multi_rule_contracts as contracts
from tests import test_vlm_detector as vlm
from tests.cleaning import test_generic_runner as runner
from core import test_multi_rule_approval as approval
from dsh_retrieval.adapters.task_storage.sqlite import encode, decode
from dsh_retrieval.chat_ui import CHAT_JS
from dsh_retrieval.multi_rule_contracts import (
    CleaningJobResult, CleaningCandidateVerdict, CleaningRuleVerdict,
    DetectionResult, bounded_reason,
)


class CleaningReasonsTest(unittest.TestCase):
    def test_unicode_limit_and_no_raw_response_retained(self):
        text = '图中只有猫🐈' * 20
        summary, truncated = bounded_reason(text)
        self.assertEqual(summary, text[:50])
        self.assertTrue(truncated)
        self.assertEqual(bounded_reason('  主体\n 被\x00遮挡\u202e  '), ('主体 被 遮挡', False))
        self.assertEqual(bounded_reason(' '), (None, False))
        self.assertEqual(bounded_reason('猫' * 50), ('猫' * 50, False))

    def test_persisted_contract_rejects_unbounded_or_invalid_summary(self):
        rule = CleaningRuleVerdict('rule', 'unknown', 'FACT_UNAVAILABLE', None)
        for changes in ({'reason_summary': '猫' * 51}, {'reason_summary': ''},
                        {'reason_summary': '\ud800'}, {'reason_summary': ' x'},
                        {'reason_summary': 'x\n'}, {'reason_truncated': True},
                        {'reason_truncated': 1}, {'reason_summary': 123}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(rule, **changes)

    def test_legacy_decode_and_digest_are_unchanged_new_summary_is_bound(self):
        f = contracts.MultiRuleContractTest()
        f.setUp()
        result = f.result()
        self.assertEqual(result.result_digest,
                         'sha256:184b99858ea0779e760d96b164969e340b6c6439d5304fa1e7b8c4ed63c826c0')
        old = encode(result)
        def legacy(value):
            if isinstance(value, dict):
                if value.get('type') == 'CleaningRuleVerdict':
                    value['value'].pop('reason_summary')
                    value['value'].pop('reason_truncated')
                for child in value.values():
                    legacy(child)
            elif isinstance(value, list):
                for child in value:
                    legacy(child)
        legacy(old)
        self.assertEqual(decode(old), result)
        candidate = result.candidates[1]
        rule = replace(candidate.rules[1], reason_summary='主体被遮挡')
        changed = replace(result, candidates=(result.candidates[0], replace(
            candidate, rules=(candidate.rules[0], rule))))
        self.assertNotEqual(changed.result_digest, result.result_digest)
        self.assertEqual(decode(encode(changed)), changed)

    def test_vlm_summaries_are_short_and_pass_omits_them(self):
        f = vlm.VLMDetectorTests()
        f.setUp()
        for verdict in ('pass', 'fail', 'unknown'):
            with patch.object(f.runtime, 'assess', return_value={
                    'verdict': verdict, 'reason': '遮挡🐈' * 30}):
                result = f.detector().detect(b'\xff\xd8\xffimage', None, None)
            self.assertEqual(result.reason_summary, None if verdict == 'pass' else ('遮挡🐈' * 30)[:50])
            self.assertEqual(result.reason_truncated, verdict != 'pass')

    def test_executor_keeps_unknown_reason_but_never_leaks_exception(self):
        f = runner.GenericRunnerTests()
        f.setUp()
        f.detector.outputs['asset.0'] = DetectionResult(
            {}, None, 'fixture.validator', 'unknown', '主体被遮挡')
        f.detector.outputs['asset.1'] = RuntimeError('sensitive exception payload')
        f.detector.outputs['asset.2'] = DetectionResult(
            {}, None, 'wrong.validator', 'unknown', 'untrusted wrong validator')
        result = f.runner().run(f.job)
        self.assertTrue(all(row.verdict == 'unknown' for row in result.candidates))
        self.assertEqual(result.candidates[0].rules[0].reason_summary, '主体被遮挡')
        self.assertEqual(result.candidates[1].rules[0].reason_code, 'DETECTOR_ERROR')
        self.assertEqual(result.candidates[2].rules[0].reason_code, 'DETECTOR_OUTPUT_INVALID')
        self.assertNotIn('sensitive', repr(result))
        self.assertNotIn('untrusted', repr(result))

    def test_reason_survives_sqlite_reopen_and_step_projection(self):
        f = approval.MultiRuleApprovalTest()
        f.setUp()
        self.addCleanup(f.doCleanups)
        approved, _ = f.confirm(f.prepare())
        job = approved.job
        candidate = job.candidates[0]
        summary, truncated = bounded_reason('图像存在明显水印' * 20)
        result = CleaningJobResult(job.binding, (CleaningCandidateVerdict(
            candidate.candidate_id, candidate.asset_version, 'fail', (
                CleaningRuleVerdict('text', 'pass', 'MATCH', 'fixture_evidence'),
                CleaningRuleVerdict('mark', 'fail', 'MISMATCH', 'fixture_evidence', summary, truncated),
            )),))
        f.repo.publish_result(f.f.actor, f.f.tid, approved.operation_id, result)
        view = f.reopen().step_results(f.f.actor, f.f.tid)
        reason = view['runs'][0]['steps'][0]['items'][0]['reasons'][1]
        self.assertEqual((reason['reason_summary'], reason['reason_truncated']), (summary, True))
        self.assertEqual(view['runs'][0]['output_version'], result.result_digest)

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_ui_shows_reason_truncation_and_legacy_absence_as_text(self):
        source = CHAT_JS[CHAT_JS.index('function cleaningReasonSummary('):
                         CHAT_JS.index('async function loadCleaningSteps(')]
        script = '''
const assert=require('node:assert/strict');
const mod=new Function(process.env.SOURCE+';return {summary:cleaningReasonSummary,label:cleaningReasonLabel}')();
const base={rule_id:'r',verdict:'fail',reason_code:'MISMATCH'};
assert(mod.summary(base).includes('未保存具体原因摘要'));
assert(mod.summary({...base,reason_summary:'只有猫',reason_truncated:true}).includes('只有猫（摘要已截断）'));
assert(mod.label('DETECTOR_ERROR').includes('未作业务判定'));
assert(!mod.summary({...base,verdict:'pass',reason_code:'MATCH'}).includes('未保存'));
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True,
                                env={**os.environ, 'SOURCE': source})
        self.assertEqual(result.returncode, 0, result.stderr)
        # Structured per-rule rendering: each reason renders a verdict badge,
        # a labelled reason code, and a summary line built by the helpers above.
        self.assertIn("rcode.textContent=cleaningReasonLabel(reason.reason_code)", CHAT_JS)
        self.assertIn("cleaningReasonSummary(reason)", CHAT_JS)
