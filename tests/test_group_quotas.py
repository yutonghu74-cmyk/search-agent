"""R14: a surplus in one group must never hide another group's shortfall."""
from copy import deepcopy
from dataclasses import replace
import json
import os
import re
import shutil
import subprocess
import unittest
from unittest import mock

from draft_helpers import BODY
from dsh_retrieval.adapters.cleaning.runner import GenericCleaningExecutor
from dsh_retrieval.adapters.task_storage.sqlite import decode, encode
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import (delivery_targets, normalize_grouped_expansion,
                                            preview_edited_groups, with_delivery_targets)
from dsh_retrieval.multi_rule_contracts import (
    ApprovedCleaningJob, CleaningBinding, CleaningCandidateRef, CleaningCandidateVerdict,
    CleaningJobResult, CleaningRecipe, CleaningRule, CleaningRuleVerdict, DetectionResult,
    DetectionStep, bytes_digest, candidate_manifest_digest, require_complete_cleaning_result)
from dsh_retrieval.proposals import GROUP_QUOTAS_ENV, normalize_proposal
from dsh_retrieval.query_spec import digest
from dsh_retrieval.result_contracts import (allocate_group_quotas, build_exact_result_set,
                                            ExactRepresentative, merge_exact_result_sets)

DOG, CAT = "group_dog", "group_cat"
TARGETS = ((DOG, 20), (CAT, 30))
RULES = {DOG: "rule-dog", CAT: "rule-cat"}
MAINTAIN = {"mode": "maintain_target", "target_count": 50,
            "approved_library_ids": ["library-a"], "progression": "manual"}


def recipe():
    return CleaningRecipe(
        "image", digest({"review": 1}), digest({"registry": 1}),
        (DetectionStep("step-dog", "vlm.group.dog@1", "vlm.boolean@1", "vlm.observation@1"),
         DetectionStep("step-cat", "vlm.group.cat@1", "vlm.boolean@1", "vlm.observation@1")),
        (CleaningRule(RULES[DOG], "step-dog", "image.group.dog@1", "eq", True, group_id=DOG),
         CleaningRule(RULES[CAT], "step-cat", "image.group.cat@1", "eq", True, group_id=CAT)))


def rule_verdict(group, value):
    if value == "skip":
        return CleaningRuleVerdict(RULES[group], "not_applicable", "GROUP_NOT_APPLICABLE",
                                   None, group_id=group)
    if value == "unknown":
        return CleaningRuleVerdict(RULES[group], "unknown", "FACT_UNAVAILABLE", None,
                                   group_id=group)
    return CleaningRuleVerdict(RULES[group], value, "MATCH" if value == "pass" else "MISMATCH",
                               "evidence", group_id=group)


def batch(rows, *, operation="operation", task_version=3, prefix="c"):
    """rows: (content, dog verdict, cat verdict); 'skip' means not an origin group."""
    refs = tuple(CleaningCandidateRef(
        f"{prefix}{index:03d}", "v1", bytes_digest(content),
        tuple(sorted(group for group, value in ((DOG, dog), (CAT, cat)) if value != "skip")))
        for index, (content, dog, cat) in enumerate(rows))
    built = recipe()
    binding = CleaningBinding("tenant", "owner", "project", "task", 1, 2, task_version, operation,
                              candidate_manifest_digest(refs), built.recipe_digest)
    job = ApprovedCleaningJob(binding, built, refs)
    verdicts = []
    for ref, (_, dog, cat) in zip(refs, rows):
        rules = (rule_verdict(DOG, dog), rule_verdict(CAT, cat))
        from dsh_retrieval.multi_rule_contracts import aggregate_rule_verdicts
        verdicts.append(CleaningCandidateVerdict(ref.candidate_id, "v1",
                                                 aggregate_rule_verdicts(rules), rules))
    return job, CleaningJobResult(binding, tuple(verdicts))


def dogs(count, tag=b"dog"):
    return [(tag + b"-%d" % index, "pass", "skip") for index in range(count)]


def cats(count, tag=b"cat"):
    return [(tag + b"-%d" % index, "skip", "pass") for index in range(count)]


def progress(preview):
    return {row.group_id: (row.accepted_count, row.gap) for row in preview.group_quotas}


class QuotaAcceptanceTests(unittest.TestCase):
    def test_total_of_fifty_with_ten_cats_still_reports_cat_gap_of_twenty(self):
        job, result = batch(dogs(40) + cats(10))
        preview = build_exact_result_set(job, result, 50, group_targets=TARGETS,
                                         refill_policy=MAINTAIN, node_mode="manual")
        self.assertEqual(len(preview.accepted) + len(preview.reserve), 50)
        self.assertEqual(progress(preview), {DOG: (20, 0), CAT: (10, 20)})
        self.assertEqual((preview.accepted_count, preview.gap), (30, 20))
        self.assertEqual(len(preview.reserve), 20)
        self.assertNotEqual(preview.stop_reason, "TARGET_MET")
        self.assertEqual(preview.stop_reason, "NEED_USER_INPUT")

    def test_fixed_input_shortfall_is_not_target_met(self):
        job, result = batch(dogs(40) + cats(10))
        preview = build_exact_result_set(job, result, 50, group_targets=TARGETS)
        self.assertEqual(preview.stop_reason, "INPUT_SCOPE_COMPLETE")

    def test_every_group_full_is_target_met(self):
        job, result = batch(dogs(20) + cats(30))
        preview = build_exact_result_set(job, result, 50, group_targets=TARGETS)
        self.assertEqual(progress(preview), {DOG: (20, 0), CAT: (30, 0)})
        self.assertEqual((preview.gap, preview.stop_reason), (0, "TARGET_MET"))

    def test_surplus_is_kept_as_reserve_and_never_assigned(self):
        job, result = batch(dogs(25) + cats(30))
        preview = build_exact_result_set(job, result, 50, group_targets=TARGETS)
        self.assertEqual(preview.gap, 0)
        self.assertEqual({item.assigned_group_id for item in preview.reserve}, {None})
        self.assertEqual(preview.group_quotas[0].eligible_reserve_count, 5)

    def test_failed_and_unknown_group_checks_never_count(self):
        rows = [(b"dog", "pass", "skip"), (b"not-dog", "fail", "skip"), (b"unsure", "unknown", "skip")]
        job, result = batch(rows)
        preview = build_exact_result_set(job, result, 50, group_targets=TARGETS)
        self.assertEqual(progress(preview)[DOG], (1, 19))
        self.assertEqual((preview.fail_count, preview.unknown_count), (1, 1))


class CrossGroupAttributionTests(unittest.TestCase):
    def test_duplicate_content_in_two_groups_counts_once(self):
        job, result = batch([(b"same", "pass", "skip"), (b"same", "skip", "pass")])
        preview = build_exact_result_set(job, result, 50, group_targets=TARGETS)
        self.assertEqual(preview.accepted_count, 1)
        self.assertEqual(preview.duplicate_count, 1)
        self.assertEqual(preview.accepted[0].group_ids, (CAT, DOG))
        self.assertEqual(sum(row.accepted_count for row in preview.group_quotas), 1)

    def test_shared_image_yields_when_only_it_can_fill_the_other_group(self):
        # First-come would give "both" to dog and leave cat short with dog-only spare.
        reps = (ExactRepresentative("a", "v1", "sha256:a", group_ids=(CAT, DOG)),
                ExactRepresentative("b", "v1", "sha256:b", group_ids=(DOG,)))
        accepted, reserve, rows = allocate_group_quotas(reps, ((DOG, 1), (CAT, 1)))
        self.assertEqual({item.candidate_id: item.assigned_group_id for item in accepted},
                         {"a": CAT, "b": DOG})
        self.assertEqual((reserve, [row.gap for row in rows]), ((), [0, 0]))

    def test_candidate_passing_one_of_two_origin_groups_counts_for_that_group(self):
        job, result = batch([(b"really-a-dog", "pass", "fail")])
        self.assertEqual(result.candidates[0].verdict, "pass")
        preview = build_exact_result_set(job, result, 50, group_targets=TARGETS)
        self.assertEqual(preview.accepted[0].assigned_group_id, DOG)

    def test_allocation_is_deterministic_regardless_of_input_order(self):
        reps = [ExactRepresentative(f"c{i}", "v1", f"sha256:{i}",
                                    group_ids=(CAT, DOG) if i % 2 else (DOG,))
                for i in range(8)]
        first = allocate_group_quotas(reps, ((DOG, 3), (CAT, 3)))
        self.assertEqual(first, allocate_group_quotas(list(reversed(reps)), ((DOG, 3), (CAT, 3))))

    def test_no_reserve_image_could_still_fill_a_short_group(self):
        reps = [ExactRepresentative(f"c{i}", "v1", f"sha256:{i}",
                                    group_ids=(CAT, DOG) if i < 2 else (DOG,))
                for i in range(6)]
        _, reserve, rows = allocate_group_quotas(reps, ((DOG, 3), (CAT, 3)))
        short = {row.group_id for row in rows if row.gap}
        self.assertFalse(any(short & set(item.group_ids) for item in reserve))


class CumulativeMergeTests(unittest.TestCase):
    def test_refill_fills_only_short_group_and_recomputes_gap(self):
        parent_job, parent_result = batch(dogs(40) + cats(10))
        parent = build_exact_result_set(parent_job, parent_result, 50, group_targets=TARGETS)
        job, result = batch(cats(20, b"new-cat"), operation="operation-2", task_version=4,
                            prefix="d")
        current = build_exact_result_set(job, result, 50, group_targets=TARGETS)
        merged = merge_exact_result_sets("parent", parent, current)
        self.assertEqual(progress(merged), {DOG: (20, 0), CAT: (30, 0)})
        self.assertEqual((merged.gap, merged.stop_reason), (0, "TARGET_MET"))

    def test_cross_batch_duplicate_does_not_close_the_gap(self):
        parent_job, parent_result = batch(dogs(40) + cats(10))
        parent = build_exact_result_set(parent_job, parent_result, 50, group_targets=TARGETS)
        job, result = batch(cats(10), operation="operation-2", task_version=4, prefix="d")
        current = build_exact_result_set(job, result, 50, group_targets=TARGETS)
        merged = merge_exact_result_sets("parent", parent, current)
        self.assertEqual(progress(merged)[CAT], (10, 20))
        self.assertEqual(merged.duplicate_count, 10)

    def test_changed_quota_cannot_merge_into_old_lineage(self):
        parent_job, parent_result = batch(dogs(2))
        parent = build_exact_result_set(parent_job, parent_result, 50, group_targets=TARGETS)
        job, result = batch(cats(2), operation="operation-2", task_version=4, prefix="d")
        current = build_exact_result_set(job, result, 50, group_targets=((DOG, 30), (CAT, 20)))
        with self.assertRaises(ValueError):
            merge_exact_result_sets("parent", parent, current)


class ContractIntegrityTests(unittest.TestCase):
    def test_ungrouped_digests_are_unchanged_by_quota_fields(self):
        ref = CleaningCandidateRef("c1", "v1", bytes_digest(b"x"))
        self.assertEqual(candidate_manifest_digest((ref,)), digest({"candidates": [
            {"candidate_id": "c1", "asset_version": "v1", "content_digest": ref.content_digest}]}))
        grouped = replace(ref, group_ids=(DOG,))
        self.assertNotEqual(candidate_manifest_digest((ref,)), candidate_manifest_digest((grouped,)))

    def test_quota_changes_result_digest(self):
        job, result = batch(dogs(1))
        self.assertNotEqual(
            build_exact_result_set(job, result, 50, group_targets=TARGETS).result_set_digest,
            build_exact_result_set(job, result, 50,
                                   group_targets=((DOG, 25), (CAT, 25))).result_set_digest)

    def test_targets_must_sum_to_total(self):
        job, result = batch(dogs(1))
        for targets in (((DOG, 20), (CAT, 20)), ((DOG, 50), (DOG, 0)), ()):
            with self.assertRaises(ValueError):
                build_exact_result_set(job, result, 50, group_targets=targets)

    def test_task_wide_pass_without_group_verdict_is_rejected_for_quota(self):
        ref = CleaningCandidateRef("c1", "v1", bytes_digest(b"x"))
        built = CleaningRecipe("image", digest({"r": 1}), digest({"g": 1}),
                               (DetectionStep("s", "b", "v", "e"),),
                               (CleaningRule("r", "s", "p", "eq", True),))
        binding = CleaningBinding("t", "o", "p", "task", 1, 1, 1, "op",
                                  candidate_manifest_digest((ref,)), built.recipe_digest)
        verdict = CleaningCandidateVerdict("c1", "v1", "pass", (
            CleaningRuleVerdict("r", "pass", "MATCH", "e"),))
        with self.assertRaises(ValueError):
            build_exact_result_set(ApprovedCleaningJob(binding, built, (ref,)),
                                   CleaningJobResult(binding, (verdict,)), 50,
                                   group_targets=TARGETS)

    def test_no_applicable_group_check_stays_unknown(self):
        rules = (rule_verdict(DOG, "skip"), rule_verdict(CAT, "skip"))
        verdict = CleaningCandidateVerdict("c1", "v1", "unknown", rules)
        self.assertEqual(verdict.passed_group_ids, ())
        with self.assertRaises(ValueError):
            CleaningCandidateVerdict("c1", "v1", "pass", rules)

    def test_skipping_an_origin_group_check_is_incomplete(self):
        job, result = batch([(b"x", "pass", "fail")])
        skipped = (rule_verdict(DOG, "pass"), rule_verdict(CAT, "skip"))
        forged = replace(result, candidates=(CleaningCandidateVerdict("c000", "v1", "pass", skipped),))
        with self.assertRaises(ValueError):
            require_complete_cleaning_result(job, forged)

    def test_step_cannot_mix_group_scopes(self):
        with self.assertRaises(ValueError):
            CleaningRecipe("image", digest({"r": 1}), digest({"g": 1}),
                           (DetectionStep("s", "b", "v", "e"),),
                           (CleaningRule("a", "s", "p", "eq", True, group_id=DOG),
                            CleaningRule("b", "s", "q", "eq", True, group_id=CAT)))

    def test_preview_survives_ledger_round_trip(self):
        job, result = batch(dogs(40) + cats(10))
        preview = build_exact_result_set(job, result, 50, group_targets=TARGETS)
        restored = decode(encode(preview))
        self.assertEqual(restored, preview)
        self.assertEqual(restored.result_set_digest, preview.result_set_digest)

    def test_legacy_records_decode_without_quota_fields(self):
        ref = CleaningCandidateRef("c1", "v1", bytes_digest(b"x"))
        encoded = encode(ref)
        encoded["value"].pop("group_ids")
        self.assertEqual(decode(encoded), ref)
        verdict = CleaningRuleVerdict("r", "pass", "MATCH", "e")
        encoded = encode(verdict)
        for field in ("group_id", "reason_summary", "reason_truncated"):
            encoded["value"].pop(field)
        self.assertEqual(decode(encoded), verdict)
        rule = CleaningRule("r", "s", "p", "eq", True)
        encoded = encode(rule)
        encoded["value"].pop("group_id")
        self.assertEqual(decode(encoded), rule)


class ExecutorTests(unittest.TestCase):
    class Guard:
        def require_current(self, binding):
            return None

    class Reader:
        def __init__(self, contents):
            self.contents = contents

        def read(self, candidate, binding):
            return self.contents[candidate.candidate_id]

    class Detector:
        validator_ref, evidence_ref = "vlm.boolean@1", "vlm.observation@1"

        def __init__(self, backend_ref, predicate, answer):
            self.backend_ref, self.predicate, self.answer = backend_ref, predicate, answer
            self.calls = []

        def detect(self, content, candidate, binding):
            self.calls.append(candidate.candidate_id)
            return DetectionResult({self.predicate: self.answer}, "evidence", self.validator_ref, "ok")

    def run_job(self, rows, *, dog=True, cat=True):
        job, _ = batch(rows)
        contents = {ref.candidate_id: rows[index][0] for index, ref in enumerate(job.candidates)}
        detectors = {"vlm.group.dog@1": self.Detector("vlm.group.dog@1", "image.group.dog@1", dog),
                     "vlm.group.cat@1": self.Detector("vlm.group.cat@1", "image.group.cat@1", cat)}
        executor = GenericCleaningExecutor(guard=self.Guard(), reader=self.Reader(contents),
                                           detectors=detectors)
        return job, executor, detectors

    def test_only_origin_group_checks_call_the_model(self):
        job, executor, detectors = self.run_job(dogs(2) + cats(1))
        result = executor.run(job)
        self.assertEqual(detectors["vlm.group.dog@1"].calls, ["c000", "c001"])
        self.assertEqual(detectors["vlm.group.cat@1"].calls, ["c002"])
        self.assertEqual([item.verdict for item in result.candidates], ["pass"] * 3)
        self.assertEqual(result.candidates[0].rules[1].verdict, "not_applicable")
        self.assertEqual(result.candidates[0].passed_group_ids, (DOG,))

    def test_dog_query_hit_that_is_a_cat_counts_for_no_group(self):
        job, executor, _ = self.run_job(dogs(1), dog=False)
        result = executor.run(job)
        self.assertEqual(result.candidates[0].verdict, "fail")
        self.assertEqual(build_exact_result_set(job, result, 50, group_targets=TARGETS).accepted, ())

    def test_resume_does_not_repeat_saved_step_or_skip(self):
        rows = [(b"both", "pass", "pass")]
        job, executor, detectors = self.run_job(rows)
        saved = []
        executor.run(job, step_checkpoint=lambda c, s, v: saved.append(v))
        resumed = self.run_job(rows)
        resumed[1].run(resumed[0], partial=("c000", (saved[0],)))
        self.assertEqual(resumed[2]["vlm.group.dog@1"].calls, [])
        self.assertEqual(resumed[2]["vlm.group.cat@1"].calls, ["c000"])


class UserVisibleQuotaTests(unittest.TestCase):
    """The shortfall has to reach the person; correct counts behind a total don't help."""

    def node(self, script, **env):
        if not shutil.which("node"):
            self.skipTest("Node is required for JavaScript behavior verification")
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                                env={**os.environ, **env})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_board_summary_shows_the_short_group(self):
        from dsh_retrieval.production_experience_ui import EXPERIENCE_JS
        start = EXPERIENCE_JS.index("function goalSummary")
        body = EXPERIENCE_JS[start:EXPERIENCE_JS.index("\n", start)]
        self.node(r"""
const assert=require('node:assert/strict');
const knownCount=value=>value==null?'待计算':String(value);
""" + body + r"""
const text=goalSummary({progress:{counts:{target:50,accepted:30,gap:20,groups:[
  {label:'狗',accepted_count:20,target_count:20,gap:0},
  {label:'猫',accepted_count:10,target_count:30,gap:20}]}}});
assert.match(text,/狗 20\/20/);assert.match(text,/猫 10\/30（缺 20）/);assert.doesNotMatch(text,/狗 20\/20（缺/);
assert.doesNotMatch(goalSummary({progress:{counts:{target:5,accepted:5,gap:0}}}),/undefined/);
""")

    def term_review_text(self, plan):
        from dsh_retrieval.chat_ui import CHAT_JS
        start = CHAT_JS.index("function showTermReview")
        end = re.compile(r"\n(?:async )?function ").search(CHAT_JS, start).start()
        body = CHAT_JS[start:end]
        script = r"""
let shown='';const $=()=>({textContent:''});const refillFormValue=()=>({}),refillSummary=()=>'';
const message=(a,b,text)=>{shown=text;return {}},guidedAction=()=>{};const state={};
""" + body + r"""
showTermReview({lines:'狗 | 金毛',plan:JSON.parse(process.env.PLAN)});
const assert=require('node:assert/strict');
const expected=JSON.parse(process.env.EXPECTED);
for(const part of expected)assert.ok(shown.includes(part),shown);
"""
        return script

    def test_confirmation_shows_targets_before_the_owner_commits(self):
        self.node(self.term_review_text(None), PLAN=json.dumps({"groups": [
            {"primary_label": "狗", "delivery_target": 20},
            {"primary_label": "猫", "delivery_target": 30}]}),
            EXPECTED=json.dumps(["狗 20 张、猫 30 张", "任一组不足都不算完成"]))

    def test_confirmation_says_when_groups_only_count_toward_the_total(self):
        self.node(self.term_review_text(None), PLAN=json.dumps({"groups": [
            {"primary_label": "狗", "delivery_target": None},
            {"primary_label": "猫", "delivery_target": None}]}),
            EXPECTED=json.dumps(["未单独分配各组数量"]))


class ProposalTests(unittest.TestCase):
    def plan(self, dog=20, cat=30):
        return with_delivery_targets(preview_edited_groups("动物", [
            {"primary_label": "狗", "auxiliary_terms": ["金毛"]},
            {"primary_label": "猫", "auxiliary_terms": ["橘猫"]}])["plan"], {"狗": dog, "猫": cat})

    def body(self, plan, target=50):
        body = deepcopy(BODY)
        body["query_spec"].update(target_count=target, grouped_expansion=plan)
        body["query_spec"]["refill_policy"]["target_count"] = target
        return body

    def test_confirmed_targets_are_sealed_in_the_plan(self):
        plan = self.plan()
        self.assertEqual([target for _, target in delivery_targets(plan)], [20, 30])
        self.assertEqual(normalize_grouped_expansion(plan), plan)
        forged = deepcopy(plan)
        forged["groups"][1]["delivery_target"] = 10
        with self.assertRaises(ValueError):
            normalize_grouped_expansion(forged)

    def test_proposal_requires_targets_to_sum_to_total(self):
        normalize_proposal(self.body(self.plan()))
        with self.assertRaises(ServiceError) as raised:
            normalize_proposal(self.body(self.plan(), target=60))
        self.assertEqual(raised.exception.code, "INVALID_SPEC")

    def test_partial_allocation_is_rejected(self):
        plan = self.plan()
        payload = {key: value for key, value in plan.items() if key != "plan_digest"}
        payload["groups"] = deepcopy(payload["groups"])
        payload["groups"][1]["delivery_target"] = None
        with self.assertRaises(ValueError):
            normalize_grouped_expansion({**payload, "plan_digest": digest(payload)})

    def test_targets_must_name_every_group(self):
        base = preview_edited_groups("动物", [
            {"primary_label": "狗", "auxiliary_terms": []},
            {"primary_label": "猫", "auxiliary_terms": []}])["plan"]
        with self.assertRaises(ValueError):
            with_delivery_targets(base, {"狗": 50})

    def test_unallocated_plan_is_not_a_quota(self):
        base = preview_edited_groups("动物", [{"primary_label": "狗", "auxiliary_terms": []}])["plan"]
        self.assertEqual(delivery_targets(base), ())
        self.assertEqual(delivery_targets(None), ())

    def test_disabled_capability_refuses_instead_of_dropping_quota(self):
        with mock.patch.dict(os.environ, {GROUP_QUOTAS_ENV: "off"}):
            with self.assertRaises(ServiceError) as raised:
                normalize_proposal(self.body(self.plan()))
        self.assertEqual(raised.exception.code, "QUOTA_NOT_SUPPORTED")


if __name__ == "__main__":
    unittest.main()
