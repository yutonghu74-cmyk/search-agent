from dataclasses import replace
import unittest

from dsh_retrieval.multi_rule_contracts import (
    ApprovedCleaningJob, CleaningBinding, CleaningCandidateRef, CleaningCandidateVerdict,
    CleaningJobResult, CleaningRecipe, CleaningRule, CleaningRuleVerdict, DetectionStep,
    bytes_digest, candidate_manifest_digest,
)
from dsh_retrieval.query_spec import digest
from dsh_retrieval.near_duplicates import ALGORITHM, PerceptualFingerprint
from dsh_retrieval.result_contracts import (FusedSourceEvidence,
                                            build_exact_result_set,
                                            merge_exact_result_sets)
from dsh_retrieval.adapters.task_storage.sqlite import decode, encode


class ExactResultContractTests(unittest.TestCase):
    def setUp(self):
        bodies = (b"duplicate", b"unique-two", b"duplicate", b"bad", b"unknown", b"unique-six")
        refs = tuple(CleaningCandidateRef(f"c{i}", "v1", bytes_digest(body))
                     for i, body in enumerate(bodies))
        step = DetectionStep("step", "fixture.detector", "fixture.validator", "fixture.eval")
        recipe = CleaningRecipe("image", digest({"review": 1}), digest({"registry": 1}),
                                (step,), (CleaningRule("r1", "step", "text.present@1", "eq", False),
                                          CleaningRule("r2", "step", "watermark.present@1", "eq", False)))
        binding = CleaningBinding("tenant", "owner", "project", "task", 1, 2, 3, "operation",
                                  candidate_manifest_digest(refs), recipe.recipe_digest)
        self.job = ApprovedCleaningJob(binding, recipe, refs)
        status = ("pass", "pass", "pass", "fail", "unknown", "pass")
        def rules(value):
            second = "unknown" if value == "unknown" else value
            return (CleaningRuleVerdict("r1", "pass", "MATCH", "evidence.1"),
                    CleaningRuleVerdict("r2", second,
                                        "FACT_UNAVAILABLE" if second == "unknown" else "MATCH",
                                        None if second == "unknown" else "evidence.2"))
        self.result = CleaningJobResult(binding, tuple(
            CleaningCandidateVerdict(ref.candidate_id, ref.asset_version, verdict, rules(verdict))
            for ref, verdict in zip(refs, status)))

    def test_exact_dedup_and_target_split(self):
        preview = build_exact_result_set(self.job, self.result, 2)
        self.assertEqual(tuple(ref.candidate_id for ref in preview.accepted), ("c0", "c1"))
        self.assertEqual(tuple(ref.candidate_id for ref in preview.reserve), ("c5",))
        self.assertEqual((preview.accepted_count, preview.duplicate_count,
                          preview.fail_count, preview.unknown_count, preview.gap), (2, 1, 1, 1, 0))
        self.assertEqual(preview.stop_reason, "TARGET_MET")
        self.assertEqual(preview.result_set_digest,
                         build_exact_result_set(self.job, self.result, 2).result_set_digest)

    def test_exact_duplicate_fuses_candidate_aliases_and_source_evidence(self):
        evidence = {
            "c0": (FusedSourceEvidence("c0", "binding-a", "library-a", "v1", "vec-a", 0.9),),
            "c2": (FusedSourceEvidence("c2", "binding-b", "library-b", "v2", 22, 0.8),),
        }
        preview = build_exact_result_set(
            self.job, self.result, 2, source_evidence_by_candidate=evidence)
        representative = preview.accepted[0]
        self.assertEqual(representative.candidate_id, "c0")
        self.assertEqual(representative.candidate_ids, ("c0", "c2"))
        self.assertEqual(tuple(source.library_id for source in representative.sources),
                         ("library-a", "library-b"))
        self.assertEqual(preview.duplicate_count, 1)
        self.assertNotEqual(preview.result_set_digest,
                            build_exact_result_set(self.job, self.result, 2).result_set_digest)

    def test_input_only_shortfall_is_explicit(self):
        preview = build_exact_result_set(self.job, self.result, 5)
        self.assertEqual(preview.accepted_count, 3)
        self.assertEqual(preview.reserve, ())
        self.assertEqual(preview.gap, 2)
        self.assertEqual(preview.stop_reason, "INPUT_SCOPE_COMPLETE")
        legacy = build_exact_result_set(self.job, self.result, 5, legacy_input_only=True)
        self.assertEqual(legacy.stop_reason, "SEARCH_STRATEGIES_EXHAUSTED")
        self.assertNotEqual(legacy.result_set_digest, preview.result_set_digest)

    def test_maintain_target_shortfall_waits_for_iteration_confirmation(self):
        preview = build_exact_result_set(self.job, self.result, 5,
            refill_policy={"mode": "maintain_target", "target_count": 5,
                           "approved_library_ids": ["library-a"],
                           "progression": "manual"}, node_mode="manual")
        self.assertEqual((preview.gap, preview.stop_reason), (2, "NEED_USER_INPUT"))

    def test_confirmed_auto_shortfall_is_scheduled_without_claiming_exhaustion(self):
        preview = build_exact_result_set(self.job, self.result, 5,
            refill_policy={"mode": "maintain_target", "target_count": 5,
                           "approved_library_ids": ["library-a"],
                           "progression": "auto"}, node_mode="auto")
        self.assertEqual((preview.gap, preview.stop_reason), (2, "REFILL_SCHEDULED"))

    def test_complete_result_and_binding_are_required(self):
        for changed in (replace(self.result, candidates=self.result.candidates[:-1]),
                        replace(self.result, binding=replace(self.result.binding, generation=3))):
            with self.assertRaises(ValueError):
                build_exact_result_set(self.job, changed, 2)
        with self.assertRaises(ValueError):
            build_exact_result_set(self.job, self.result, True)
        with self.assertRaises(ValueError):
            build_exact_result_set(self.job, self.result, 0)

    def test_fail_unknown_never_become_representatives(self):
        preview = build_exact_result_set(self.job, self.result, 10)
        chosen = {ref.candidate_id for ref in preview.accepted + preview.reserve}
        self.assertEqual(chosen, {"c0", "c1", "c5"})
        self.assertNotIn("c3", chosen)
        self.assertNotIn("c4", chosen)

    def test_result_change_changes_digest(self):
        original = build_exact_result_set(self.job, self.result, 2)
        changed = replace(self.result, candidates=(replace(self.result.candidates[0],
            rules=(replace(self.result.candidates[0].rules[0], reason_code="VERIFIED"),
                   self.result.candidates[0].rules[1])),) + self.result.candidates[1:])
        self.assertNotEqual(original.result_set_digest,
                            build_exact_result_set(self.job, changed, 2).result_set_digest)

    def test_deferred_perceptual_fingerprints_are_ignored_by_new_results(self):
        fingerprints = (
            PerceptualFingerprint("c0", ALGORITHM, 64, "0000000000000000"),
            PerceptualFingerprint("c1", ALGORITHM, 64, "0000000000000003"),
            PerceptualFingerprint("c5", ALGORITHM, 64, "ffffffffffffffff"),
        )
        enriched = replace(self.result, fingerprints=fingerprints)
        preview = build_exact_result_set(self.job, enriched, 2)
        self.assertEqual((preview.accepted_count, len(preview.reserve), preview.gap),
                         (2, 1, 0))
        self.assertEqual(preview.near_duplicate_pairs, ())
        self.assertTrue(all(item.fingerprint is None
                            for item in preview.accepted + preview.reserve))
        self.assertEqual(preview.cleaning_result_digest, enriched.result_digest)

    def test_legacy_cleaning_result_decoder_defaults_missing_fingerprints(self):
        encoded = encode(self.result)
        encoded['value'].pop('fingerprints')
        self.assertEqual(decode(encoded), self.result)

    def test_cumulative_merge_drops_deferred_perceptual_metadata(self):
        parent_result = replace(self.result, fingerprints=(
            PerceptualFingerprint("c0", ALGORITHM, 64, "0000000000000000"),))
        parent = build_exact_result_set(self.job, parent_result, 5)
        refs = (CleaningCandidateRef("c6", "v1", bytes_digest(b"new-near")),)
        binding = replace(self.job.binding, operation_id="operation-near",
                          task_version=4,
                          input_manifest_digest=candidate_manifest_digest(refs))
        job = ApprovedCleaningJob(binding, self.job.recipe, refs)
        verdict = CleaningCandidateVerdict(
            "c6", "v1", "pass",
            (CleaningRuleVerdict("r1", "pass", "MATCH", "evidence.1"),
             CleaningRuleVerdict("r2", "pass", "MATCH", "evidence.2")))
        current = build_exact_result_set(job, CleaningJobResult(
            binding, (verdict,), (PerceptualFingerprint(
                "c6", ALGORITHM, 64, "0000000000000003"),)), 5)
        merged = merge_exact_result_sets("parent-near", parent, current)
        self.assertEqual(merged.near_duplicate_pairs, ())
        self.assertTrue(all(item.fingerprint is None
                            for item in merged.accepted + merged.reserve))
        self.assertEqual((merged.accepted_count, merged.gap), (4, 1))

    def test_new_result_does_not_create_manual_near_duplicate_clusters(self):
        fingerprints = (
            PerceptualFingerprint("c0", ALGORITHM, 64, "0000000000000000",
                                  640, 480, 4.0),
            PerceptualFingerprint("c1", ALGORITHM, 64, "0000000000000003",
                                  1280, 720, 7.0),
        )
        preview = build_exact_result_set(
            self.job, replace(self.result, fingerprints=fingerprints), 3)
        self.assertEqual((preview.accepted_count, preview.duplicate_count, preview.gap),
                         (3, 1, 0))
        self.assertEqual(preview.near_duplicate_pairs, ())
        self.assertEqual(preview.near_duplicate_decisions, ())

    def test_cumulative_merge_deduplicates_content_and_recomputes_gap(self):
        policy = {"mode": "maintain_target", "target_count": 5,
                  "approved_library_ids": ["library-a"], "progression": "manual"}
        parent_evidence = {"c1": (FusedSourceEvidence(
            "c1", "binding-a", "library-a", "v1", "vec-1", 0.9),)}
        parent = build_exact_result_set(self.job, self.result, 5,
                                        refill_policy=policy, node_mode="manual",
                                        source_evidence_by_candidate=parent_evidence)
        refs = (
            CleaningCandidateRef("c7", "v1", self.job.candidates[1].content_digest),
            CleaningCandidateRef("c6", "v1", bytes_digest(b"new-across-batch")),
        )
        binding = replace(self.job.binding, operation_id="operation-2", task_version=4,
                          input_manifest_digest=candidate_manifest_digest(refs))
        job = ApprovedCleaningJob(binding, self.job.recipe, refs)
        passed = tuple(CleaningCandidateVerdict(
            ref.candidate_id, ref.asset_version, "pass",
            (CleaningRuleVerdict("r1", "pass", "MATCH", "evidence.1"),
             CleaningRuleVerdict("r2", "pass", "MATCH", "evidence.2"))) for ref in refs)
        current = build_exact_result_set(job, CleaningJobResult(binding, passed), 5,
                                         refill_policy=policy, node_mode="manual",
                                         source_evidence_by_candidate={
                                             "c7": (FusedSourceEvidence(
                                                 "c7", "binding-b", "library-b", "v2",
                                                 "vec-7", 0.8),)})
        merged = merge_exact_result_sets("resultset-parent", parent, current,
                                         refill_policy=policy, node_mode="manual")
        self.assertEqual(tuple(ref.candidate_id for ref in merged.accepted),
                         ("c0", "c1", "c5", "c6"))
        self.assertEqual((merged.gap, merged.duplicate_count, merged.fail_count,
                          merged.unknown_count, merged.stop_reason),
                         (1, 2, 1, 1, "NEED_USER_INPUT"))
        self.assertEqual(merged.parent_result_set_id, "resultset-parent")
        self.assertEqual(merged.parent_result_set_digest, parent.result_set_digest)
        self.assertEqual(merged.source_operation_ids, ("operation", "operation-2"))
        fused = next(item for item in merged.accepted if item.content_digest
                     == self.job.candidates[1].content_digest)
        self.assertEqual(fused.candidate_ids, ("c1", "c7"))
        self.assertEqual(tuple(source.library_id for source in fused.sources),
                         ("library-a", "library-b"))
        self.assertNotEqual(merged.result_set_digest, current.result_set_digest)

    def test_legacy_preview_decoder_preserves_v1_digest(self):
        preview = build_exact_result_set(self.job, self.result, 2, legacy_lineage=True)
        expected = preview.result_set_digest
        encoded = encode(preview)
        for field in ('parent_result_set_id', 'parent_result_set_digest',
                      'source_operation_ids', 'near_duplicate_pairs',
                      'near_duplicate_decisions'):
            encoded['value'].pop(field)
        restored = decode(encoded)
        self.assertEqual(restored, preview)
        self.assertEqual(restored.result_set_digest, expected)

    def test_legacy_representative_decoder_defaults_fusion_evidence(self):
        representative = build_exact_result_set(self.job, self.result, 2).accepted[0]
        encoded = encode(representative)
        encoded['value'].pop('candidate_ids')
        encoded['value'].pop('sources')
        encoded['value'].pop('fingerprint')
        restored = decode(encoded)
        self.assertEqual(restored, representative)

    def test_legacy_fingerprint_decoder_defaults_quality_signals(self):
        fingerprint = PerceptualFingerprint(
            "c0", ALGORITHM, 64, "0000000000000000")
        encoded = encode(fingerprint)
        for field in ("width", "height", "sharpness"):
            encoded["value"].pop(field)
        self.assertEqual(decode(encoded), fingerprint)


if __name__ == "__main__":
    unittest.main()
