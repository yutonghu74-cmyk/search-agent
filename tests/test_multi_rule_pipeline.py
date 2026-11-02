from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from core import test_multi_rule_approval as fixture
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.confirmed_cleaning import ConfirmedCleaningService
from dsh_retrieval.detector_artifacts import (
    DetectorArtifact, DetectorArtifactBinding, DetectorArtifactManifest,
)
from dsh_retrieval.intent_compiler import DetectorBinding
from dsh_retrieval.intent_review import Capability
from dsh_retrieval.multi_rule_contracts import (
    CleaningCandidateVerdict, CleaningJobResult, CleaningRuleVerdict, DetectionResult,
)
from dsh_retrieval.near_duplicates import (ALGORITHM, NearDuplicatePair,
                                           PerceptualFingerprint)
from dsh_retrieval.result_contracts import ExactRepresentative, near_duplicate_clusters
from dsh_retrieval.multi_rule_pipeline import prepare_released_multi_rule, run_approved_multi_rule
from dsh_retrieval.recipe_bridge import compile_confirmed_recipe, confirmed_rules_digest
from dsh_retrieval.released_detectors import ReleasedDetectorRegistry


class Gateway:
    def __init__(self, authority):
        self.authority = authority
        self.reads = 0

    @contextmanager
    def open_read(self, context, ref, *, max_bytes):
        self.authority.require_access(context, ref, "read")
        self.reads += 1
        if ref != "trusted:ref":
            raise AssertionError("unapproved media reference")
        with BytesIO(b"fixture") as stream:
            yield stream


class Detector:
    backend_ref = "fixture.detector"
    validator_ref = "fixture.validator"
    evidence_ref = "fixture.eval"

    def __init__(self):
        self.calls = 0
        self.after_detect = None
        self.artifact_binding_digest = None

    def detect(self, content, candidate, binding):
        self.calls += 1
        if self.after_detect is not None:
            self.after_detect()
        return DetectionResult({"text.present@1": False,
                                "watermark.present@1": False},
                               "fixture.evidence", self.validator_ref, "ok")


class MultiRulePipelineTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.MultiRuleApprovalTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.actor = self.f.f.actor
        self.task_id = self.f.f.tid
        self.gateway = Gateway(self.f.f.auth)
        self.detector = Detector()
        self.artifact_temp = TemporaryDirectory()
        self.addCleanup(self.artifact_temp.cleanup)
        self.artifact_root = Path(self.artifact_temp.name)
        self.artifact_path = self.artifact_root / "fixture_detector.py"
        self.artifact_path.write_bytes(b"synthetic detector implementation")
        self.model_path = self.artifact_root / "fixture_model.bin"
        self.model_path.write_bytes(b"synthetic model weights")
        artifacts = (
            DetectorArtifact("implementation", self.artifact_path.name,
                "sha256:" + sha256(self.artifact_path.read_bytes()).hexdigest()),
            DetectorArtifact("model", self.model_path.name,
                "sha256:" + sha256(self.model_path.read_bytes()).hexdigest()),
        )
        self.artifact_binding = DetectorArtifactBinding(
            self.detector.backend_ref, self.detector.validator_ref,
            self.detector.evidence_ref, artifacts)
        self.detector.artifact_binding_digest = self.artifact_binding.binding_digest
        self.artifact_manifest = DetectorArtifactManifest(
            self.artifact_root, (self.artifact_binding,))
        self.capabilities = tuple(Capability(ref, "constraint", "image", ("eq",),
            "boolean", "verified", "fixture.validator", "fixture.eval")
            for ref in ("text.present@1", "watermark.present@1"))
        self.bindings = tuple(DetectorBinding(ref, "image", "fixture.validator",
            "fixture.detector", "fixture.eval")
            for ref in ("text.present@1", "watermark.present@1"))
        self.registry = ReleasedDetectorRegistry(self.capabilities, self.bindings,
            {self.detector.backend_ref: self.detector}, self.artifact_manifest)
        self.f.recipe = replace(self.f.recipe, registry_digest=self.registry.registry_digest)
        self.f.proposal = prepare_released_multi_rule(
            self.f.repo, self.actor, self.task_id, self.f.manifest.manifest_ref,
            self.f.recipe, registry=self.registry)
        self.approval, _ = self.f.confirm()
        self.repo = self.f.reopen()
        self.operation_id = self.approval.operation_id

    def make_result(self):
        candidate = self.approval.job.candidates[0]
        rules = tuple(CleaningRuleVerdict(rule.rule_id, "pass", "MATCH", "evidence.1")
                      for rule in self.approval.job.recipe.rules)
        return CleaningJobResult(self.approval.job.binding,
            (CleaningCandidateVerdict(candidate.candidate_id, candidate.asset_version,
                                      "pass", rules),))

    def run_pipeline(self, registry=None):
        return run_approved_multi_rule(self.repo, self.actor, self.task_id,
            self.operation_id, gateway=self.gateway,
            registry=registry or self.registry)

    def changed_registry(self):
        changed_model = replace(self.artifact_binding.artifacts[1],
                                content_digest="sha256:" + "0" * 64)
        binding = replace(self.artifact_binding,
                          artifacts=(self.artifact_binding.artifacts[0], changed_model))
        manifest = DetectorArtifactManifest(self.artifact_root, (binding,))
        return ReleasedDetectorRegistry(self.capabilities, self.bindings,
            {self.detector.backend_ref: self.detector}, manifest)

    def assert_code(self, code, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_prepare_entry_uses_only_registry_release_identity(self):
        self.assertEqual(self.approval.proposal.detector_release_digest,
                         self.registry.release_digest)
        with self.assertRaises(TypeError):
            prepare_released_multi_rule(
                self.f.repo, self.actor, self.task_id, self.f.manifest.manifest_ref,
                self.f.recipe, registry=self.registry,
                detector_release_digest="sha256:" + "0" * 64)

    def test_confirmed_task_compiles_only_server_released_recipe(self):
        task = self.repo.tasks.get(self.actor, self.task_id)
        recipe = compile_confirmed_recipe(task, self.registry)
        self.assertEqual(recipe.source_review_digest, confirmed_rules_digest(task))
        self.assertEqual(recipe.registry_digest, self.registry.registry_digest)
        self.assertEqual(
            [(rule.rule_id, rule.predicate_ref, rule.value) for rule in recipe.rules],
            [(rule.rule_id, rule.predicate_ref, rule.value) for rule in self.f.recipe.rules],
        )
        self.assertEqual({step.backend_ref for step in recipe.steps},
                         {self.detector.backend_ref})

    def test_confirmed_recipe_rejects_unreleased_rule_before_approval(self):
        task = self.repo.tasks.get(self.actor, self.task_id)
        registry = ReleasedDetectorRegistry(self.capabilities[:1], self.bindings[:1],
            {self.detector.backend_ref: self.detector}, self.artifact_manifest)
        with patch.object(self.repo, "prepare") as prepare:
            self.assert_code("DETECTOR_NOT_RELEASED",
                             lambda: compile_confirmed_recipe(task, registry))
            prepare.assert_not_called()

    def test_prepare_entry_rejects_untrusted_registry_before_ledger(self):
        with patch.object(self.f.repo, "prepare") as prepare:
            with self.assertRaisesRegex(ValueError, "trusted released"):
                prepare_released_multi_rule(
                    self.f.repo, self.actor, self.task_id, self.f.manifest.manifest_ref,
                    self.f.recipe, registry=object())
            prepare.assert_not_called()

    def test_prepare_entry_revalidates_release_before_ledger(self):
        self.model_path.write_bytes(b"changed model weights")
        with patch.object(self.f.repo, "prepare") as prepare:
            self.assert_code("DETECTOR_NOT_RELEASED", lambda: prepare_released_multi_rule(
                self.f.repo, self.actor, self.task_id, self.f.manifest.manifest_ref,
                self.f.recipe, registry=self.registry))
            prepare.assert_not_called()

    def test_prepare_entry_rejects_recipe_capability_mismatch_before_ledger(self):
        registry = ReleasedDetectorRegistry(self.capabilities[:1], self.bindings,
            {self.detector.backend_ref: self.detector}, self.artifact_manifest)
        with patch.object(self.f.repo, "prepare") as prepare:
            self.assert_code("DETECTOR_NOT_RELEASED", lambda: prepare_released_multi_rule(
                self.f.repo, self.actor, self.task_id, self.f.manifest.manifest_ref,
                self.f.recipe, registry=registry))
            prepare.assert_not_called()

    def test_approved_job_executes_persists_and_replays_without_rerun(self):
        view, replayed = self.run_pipeline()
        self.assertFalse(replayed)
        self.assertEqual((view["accepted_count"], view["gap"], view["kind"]),
                         (1, 9, "preview"))
        representative = view["accepted"][0]
        self.assertEqual(representative.candidate_ids,
                         (representative.candidate_id,))
        self.assertEqual(len(representative.sources), 1)
        self.assertEqual(representative.sources[0].candidate_id,
                         representative.candidate_id)
        self.assertEqual((self.gateway.reads, self.detector.calls), (1, 1))
        self.assertEqual(self.run_pipeline(), (view, True))
        self.assertEqual((self.gateway.reads, self.detector.calls), (1, 1))
        self.assertEqual(len(self.f.state()["result_sets"]), 1)

    def test_owner_near_duplicate_merge_is_versioned_idempotent_and_restartable(self):
        view, _ = self.run_pipeline()
        def seed(memory, retrieval, candidates, state):
            result_set_id = state["current_result_sets"][self.task_id]
            preview, published_version = state["result_sets"][result_set_id]
            original = preview.accepted[0]
            second_id = original.candidate_id + "z"
            first = replace(original, fingerprint=PerceptualFingerprint(
                original.candidate_id, ALGORITHM, 64, "0000000000000000",
                640, 480, 4.0))
            second = ExactRepresentative(
                second_id, "v1", "sha256:" + "2" * 64,
                fingerprint=PerceptualFingerprint(
                    second_id, ALGORITHM, 64, "0000000000000003",
                    1280, 720, 8.0))
            pair = NearDuplicatePair(original.candidate_id, second_id, 2, 6)
            seeded = replace(preview, accepted=(first, second), gap=8,
                             near_duplicate_pairs=(pair,))
            state["result_sets"][result_set_id] = (seeded, published_version)
            event = next(item for item in state["events"]
                         if item.get("result_set_id") == result_set_id)
            event.update(accepted_count=2, gap=8)
            return result_set_id, seeded, published_version
        old_id, seeded, version = self.repo._transaction(True, seed)
        cluster = near_duplicate_clusters(seeded)[0]
        reviewed, replayed = self.repo.review_near_duplicate_cluster(
            self.actor, self.task_id, key="near-review",
            expected_task_version=version,
            result_set_digest=seeded.result_set_digest,
            cluster_id=cluster.cluster_id, action="merge",
            representative_candidate_id=cluster.recommended_candidate_id)
        self.assertFalse(replayed)
        self.assertNotEqual(reviewed["result_set_id"], old_id)
        self.assertEqual((reviewed["counts"]["accepted"],
                          reviewed["counts"]["duplicate"],
                          reviewed["counts"]["gap"]), (1, 1, 9))
        replay, replayed = self.repo.review_near_duplicate_cluster(
            self.actor, self.task_id, key="near-review",
            expected_task_version=version,
            result_set_digest=seeded.result_set_digest,
            cluster_id=cluster.cluster_id, action="merge",
            representative_candidate_id=cluster.recommended_candidate_id)
        self.assertTrue(replayed)
        self.assertEqual(replay, reviewed)
        reopened = self.f.reopen()
        state = self.f.state()
        current_id = state["current_result_sets"][self.task_id]
        current, current_version = state["result_sets"][current_id]
        self.assertEqual((len(current.near_duplicate_decisions), current_version),
                         (1, version + 1))

    def test_confirmed_cleaning_service_executes_without_client_recipe(self):
        service = ConfirmedCleaningService(
            self.repo, registry=self.registry, gateway=self.gateway)
        view, replayed = service.execute(self.actor, self.task_id, self.operation_id)
        self.assertFalse(replayed)
        self.assertEqual((view["kind"], view["accepted_count"]),
                         ("cleaning_preview", 1))
        self.assertNotIn("authorized_media_ref", str(view))
        self.assertTrue(service.steps(self.actor, self.task_id)["available"])

    def test_prepare_exposes_frozen_execution_without_running_detector(self):
        from types import SimpleNamespace
        execution = {"model": {"id": "vision", "provider": "fixture", "name": "v1"},
                     "instruction": "完整的已确认条件", "prompt_version": "v1",
                     "config_digest": "sha256:" + "1" * 64}
        provider = SimpleNamespace(build=lambda actor, task: self.registry,
                                   describe=lambda task: execution)
        service = ConfirmedCleaningService(self.repo, registry_provider=provider,
                                           gateway=self.gateway)
        with patch('dsh_retrieval.confirmed_cleaning.compile_confirmed_recipe',
                   return_value=self.approval.proposal.recipe), patch(
                       'dsh_retrieval.confirmed_cleaning.prepare_released_multi_rule',
                       return_value=self.approval.proposal):
            view = service.prepare(self.actor, self.task_id,
                                   self.approval.proposal.manifest_ref)
        self.assertEqual(view['execution'], execution)
        self.assertEqual(view['detector_release_digest'], self.registry.release_digest)
        self.assertEqual(view['unknown_policy'], 'hold_for_review')
        self.assertEqual(view['steps'][0]['backend_ref'], self.detector.backend_ref)
        self.assertEqual((self.gateway.reads, self.detector.calls), (0, 0))

    def test_prepare_model_drift_does_not_persist_an_approval_card(self):
        from types import SimpleNamespace
        def changed(task):
            raise ServiceError('MODEL_BINDING_CHANGED', 'reconfirm required', 409)
        provider = SimpleNamespace(build=lambda actor, task: self.registry, describe=changed)
        service = ConfirmedCleaningService(self.repo, registry_provider=provider,
                                           gateway=self.gateway)
        with patch('dsh_retrieval.confirmed_cleaning.compile_confirmed_recipe',
                   return_value=self.approval.proposal.recipe), patch(
                       'dsh_retrieval.confirmed_cleaning.prepare_released_multi_rule') as prepare:
            self.assert_code('MODEL_BINDING_CHANGED', lambda: service.prepare(
                self.actor, self.task_id, self.approval.proposal.manifest_ref))
        prepare.assert_not_called()

    def test_result_already_persisted_skips_media_and_detector(self):
        result = self.make_result()
        self.repo.publish_result(self.actor, self.task_id, self.operation_id, result)
        view, replayed = self.run_pipeline()
        self.assertFalse(replayed)
        self.assertEqual(view["accepted_count"], 1)
        self.assertEqual((self.gateway.reads, self.detector.calls), (0, 0))

    def test_failure_after_result_write_resumes_without_rerun(self):
        with patch.object(self.repo, "publish_preview", side_effect=RuntimeError("interrupt")):
            with self.assertRaises(RuntimeError):
                self.run_pipeline()
        self.assertEqual(len(self.f.state()["results"]), 1)
        self.assertEqual(len(self.f.state()["result_sets"]), 0)
        view, replayed = self.run_pipeline()
        self.assertFalse(replayed)
        self.assertEqual(view["accepted_count"], 1)
        self.assertEqual((self.gateway.reads, self.detector.calls), (1, 1))

    def test_artifact_change_blocks_preview_retry_after_result_write(self):
        self.repo.publish_result(self.actor, self.task_id,
                                 self.operation_id, self.make_result())
        self.model_path.write_bytes(b"changed model weights")
        self.assert_code("DETECTOR_NOT_RELEASED", self.run_pipeline)
        self.assertEqual((self.gateway.reads, self.detector.calls), (0, 0))
        self.assertEqual(self.f.state()["result_sets"], {})

    def test_release_change_blocks_saved_result_before_preview(self):
        self.repo.publish_result(self.actor, self.task_id,
                                 self.operation_id, self.make_result())
        self.assert_code("DETECTOR_NOT_RELEASED",
                         lambda: self.run_pipeline(self.changed_registry()))
        self.assertEqual((self.gateway.reads, self.detector.calls), (0, 0))
        self.assertEqual(self.f.state()["result_sets"], {})

    def test_legacy_approval_without_release_binding_never_runs(self):
        legacy = replace(self.approval, proposal=replace(
            self.approval.proposal, detector_release_digest=None))
        with patch.object(self.repo, "get_current", return_value=legacy):
            self.assert_code("DETECTOR_NOT_RELEASED", self.run_pipeline)
        self.assertEqual((self.gateway.reads, self.detector.calls), (0, 0))
        self.assertEqual(self.f.state()["results"], {})

    def test_published_preview_replay_does_not_rewrite_release_history(self):
        view, _ = self.run_pipeline()
        self.assertEqual(self.run_pipeline(self.changed_registry()), (view, True))
        self.assertEqual((self.gateway.reads, self.detector.calls), (1, 1))

    def test_revocation_or_stale_generation_never_runs(self):
        self.f.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", self.run_pipeline)
        self.f.f.auth.denied = False
        self.f.f.change_task(run_generation=self.approval.proposal.generation + 1)
        self.assert_code("REVISION_CONFLICT", self.run_pipeline)
        self.assertEqual((self.gateway.reads, self.detector.calls), (0, 0))
        self.assertEqual(self.f.state()["results"], {})

    def test_revoked_replay_does_not_expose_preview(self):
        self.run_pipeline()
        self.f.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", self.run_pipeline)

    def test_revocation_during_detection_writes_nothing(self):
        self.detector.after_detect = lambda: setattr(self.f.f.auth, "denied", True)
        self.assert_code("ACCESS_DENIED", self.run_pipeline)
        state = self.f.state()
        self.assertEqual(state["results"], {})
        self.assertEqual(state["result_sets"], {})

    def test_missing_detector_fails_before_media_read(self):
        missing = ReleasedDetectorRegistry(self.capabilities, self.bindings, {},
                                           self.artifact_manifest)
        self.assert_code("DETECTOR_NOT_RELEASED", lambda: run_approved_multi_rule(
            self.repo, self.actor, self.task_id, self.operation_id,
            gateway=self.gateway, registry=missing))
        self.assertEqual(self.gateway.reads, 0)


if __name__ == "__main__":
    unittest.main()
