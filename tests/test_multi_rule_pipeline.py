from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sqlite3
import json
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
from dsh_retrieval.result_views import ResultCursorError, build_result_page
from dsh_retrieval.cleaning_step_outputs import (
    StepOutputCursorError, build_step_output_page)
from dsh_retrieval.multi_rule_pipeline import prepare_released_multi_rule, run_approved_multi_rule
from dsh_retrieval.recipe_bridge import compile_confirmed_recipe, confirmed_rules_digest
from dsh_retrieval.released_detectors import ReleasedDetectorRegistry
from draft_helpers import make_service


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

    def test_unpublished_result_read_skips_large_candidate_ledgers(self):
        with patch.object(self.repo, "_transaction", side_effect=AssertionError(
                "unpublished result should not load candidate ledgers")):
            self.assert_code("NOT_FOUND", lambda:
                self.repo.get_current_result_set(self.actor, self.task_id))
        other_actor = replace(self.actor, user_id="another-user")
        self.assert_code("NOT_FOUND", lambda:
            self.repo.get_current_result_set(other_actor, self.task_id))

    def test_progress_inputs_match_separate_reads_and_recheck_revocation(self):
        self.run_pipeline()
        expected_result = self.repo.get_current_result_set(self.actor, self.task_id)
        expected_steps = self.repo.step_results(self.actor, self.task_id)
        with patch.object(self.repo, '_transaction', wraps=self.repo._transaction) as read:
            self.assertEqual(self.repo.progress_inputs(self.actor, self.task_id),
                             (expected_result, expected_steps))
            self.assertEqual(read.call_count, 1)
        self.f.f.auth.denied = True
        self.assert_code('ACCESS_DENIED', lambda:
            self.repo.progress_inputs(self.actor, self.task_id))

    def test_progress_before_cleaning_skips_page_history_with_owner_check(self):
        f = fixture.MultiRuleApprovalTest()
        f.setUp()
        self.addCleanup(f.doCleanups)
        with patch.object(f.repo, '_transaction', side_effect=AssertionError('page history')):
            result, steps = f.repo.progress_inputs(f.f.actor, f.f.tid)
            self.assertIsNone(result)
            self.assertFalse(steps['available'])
            self.assertEqual(steps['runs'], [])
            self.assert_code('NOT_FOUND', lambda: f.repo.progress_inputs(
                replace(f.f.actor, user_id='another-user'), f.f.tid))

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
        self.assertTrue(view["candidate_preview_available"])
        self.assertEqual(view["candidate_assessments"][
            view["accepted"][0].candidate_id]["cleaning_status"], "pass")
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

    def test_indexed_result_page_preserves_current_refill_stop(self):
        from dsh_retrieval.result_views import build_result_page
        from contextlib import closing
        self.run_pipeline()
        task = self.repo.tasks.get(self.actor, self.task_id)
        with closing(self.repo.tasks._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS refill_execution_stops (
                task_id TEXT, revision INTEGER, generation INTEGER,
                stop_reason TEXT, created_at TEXT,
                PRIMARY KEY(task_id,revision,generation))""")
            db.execute("INSERT OR REPLACE INTO refill_execution_stops VALUES (?,?,?,?,?)",
                       (self.task_id,task.confirmed_revision,task.run_generation,'NO_PROGRESS','now'))
            db.execute("INSERT OR REPLACE INTO refill_execution_stops VALUES (?,?,?,?,?)",
                       (self.task_id,task.confirmed_revision,task.run_generation+1,'OTHER_GENERATION','now'))
        for category in ('accepted','reserve','failed','review','user_excluded'):
            expected = build_result_page(task.to_summary(),json.loads(task.confirmed_proposal_json),
                self.repo.get_current_result_set(self.actor,self.task_id),view_type=category,limit=1)
            with patch.object(self.repo.tasks,'_load',side_effect=AssertionError('full ledger read')):
                actual = self.repo.get_result_read_page(self.actor,self.task_id,view_type=category,limit=1)
            self.assertEqual(actual,expected)
            self.assertEqual(actual['stop_reason'],'NO_PROGRESS')

    def test_single_preview_binding_uses_index_and_rechecks_access(self):
        self.run_pipeline()
        candidate = self.approval.job.candidates[0]
        with patch.object(self.repo, '_transaction', side_effect=AssertionError(
                'result page must not rebuild result lineage')), patch.object(
                self.repo.tasks, '_load', side_effect=AssertionError('full task ledger read')):
            snapshot = self.repo.get_current_result_read_snapshot(
                self.actor, self.task_id)
        self.assertEqual(snapshot['accepted_count'], 1)
        from dsh_retrieval.progress_models import build_task_progress
        task = self.repo.tasks.get(self.actor, self.task_id)
        saved = self.repo.tasks.list_task_read_page(
            self.actor, task.project_id, limit=20)[0]['progress']
        self.assertEqual(saved, build_task_progress(task.to_summary(),
            json.loads(task.confirmed_proposal_json), snapshot).to_view())
        full_steps = self.repo.step_results(self.actor, self.task_id)
        with patch.object(self.repo, '_transaction', side_effect=AssertionError(
                'step summary must not load candidate ledgers')), patch.object(
                self.repo.tasks, '_load', side_effect=AssertionError('full task ledger read')):
            summary = self.repo.step_results_summary(self.actor, self.task_id)
        expected_steps = {**full_steps, 'runs': [
            {**run, 'steps': [{key: value for key, value in step.items()
                              if key != 'items'} for step in run['steps']]}
            for run in full_steps['runs']]}
        self.assertEqual(summary, expected_steps)
        run = full_steps['runs'][0]
        step = run['steps'][0]
        for view_type in ('input', 'retained', 'rejected', 'review', 'pending', 'blocked'):
            expected_page = build_step_output_page(full_steps,
                operation_id=run['operation_id'],
                step_execution_id=step['step_execution_id'],
                output_version=run['output_version'], view_type=view_type,
                limit=1)
            with patch.object(self.repo, '_transaction', side_effect=AssertionError(
                    'step page must not rebuild result lineage')):
                actual_page = self.repo.step_output_read_page(self.actor, self.task_id,
                    operation_id=run['operation_id'],
                    step_execution_id=step['step_execution_id'],
                    output_version=run['output_version'], view_type=view_type,
                    limit=1)
            self.assertEqual(actual_page, expected_page)
        with self.assertRaises(StepOutputCursorError):
            self.repo.step_output_read_page(self.actor, self.task_id,
                operation_id=run['operation_id'],
                step_execution_id=step['step_execution_id'],
                output_version=run['output_version'], cursor='tampered')
        reason = next((reason['reason_code'] for item in step['items']
                       for reason in item['reasons'] if reason.get('reason_code')),
                      'reason-that-does-not-exist')
        reason_kwargs = {'operation_id': run['operation_id'],
                         'step_execution_id': step['step_execution_id'],
                         'output_version': run['output_version'],
                         'reason_code': reason, 'limit': 1}
        self.assertEqual(self.repo.step_output_read_page(
            self.actor, self.task_id, **reason_kwargs),
            build_step_output_page(full_steps, **reason_kwargs))
        for filters in ({'query': '符合条件'}, {'query': '不存在'},
                        {'rule_id': step['rules'][0]['rule_id']},
                        {'rule_id': 'missing'}, {'query': '%_'},
                        {'rule_id': step['rules'][0]['rule_id'], 'query': '符合条件'}):
            kwargs = {**reason_kwargs, **filters}
            with patch.object(self.repo, '_transaction', side_effect=AssertionError('full read')):
                self.assertEqual(self.repo.step_output_read_page(
                    self.actor, self.task_id, **kwargs),
                    build_step_output_page(full_steps, **kwargs))
        task = self.repo.tasks.get(self.actor, self.task_id)
        proposal = json.loads(task.confirmed_proposal_json)
        for view_type in ('accepted', 'reserve', 'failed', 'review', 'user_excluded'):
            expected = build_result_page(task.to_summary(), proposal,
                self.repo.get_current_result_set(self.actor, self.task_id),
                view_type=view_type, limit=1)
            with patch.object(self.repo, '_transaction', side_effect=AssertionError(
                    'indexed page must not rebuild result lineage')), patch.object(
                    self.repo.tasks, '_load', side_effect=AssertionError('full task ledger read')):
                actual = self.repo.get_result_read_page(self.actor, self.task_id,
                    view_type=view_type, limit=1)
            self.assertEqual(actual, expected)
        with self.assertRaises(ResultCursorError):
            self.repo.get_result_read_page(self.actor, self.task_id,
                                           cursor='tampered')
        with patch.object(self.repo, '_transaction', side_effect=AssertionError(
                'single preview must not rebuild result lineage')), patch.object(
                self.repo.tasks, '_load', side_effect=AssertionError('full task ledger read')):
            self.assertEqual(self.repo.get_current_preview_candidate(
                self.actor, self.task_id, candidate.candidate_id),
                (candidate, 'trusted:ref'))
        reopened = self.f.reopen()
        self.assertEqual(reopened.get_current_preview_candidate(
            self.actor, self.task_id, candidate.candidate_id),
            (candidate, 'trusted:ref'))
        with reopened.tasks._connect() as db:
            db.execute('DELETE FROM task_asset_read_bindings WHERE task_id=?',
                       (self.task_id,))
            db.execute('DELETE FROM task_asset_read_heads WHERE task_id=?',
                       (self.task_id,))
        self.assert_code('READ_PROJECTION_MISSING', lambda:
            reopened.get_current_preview_candidate(
                self.actor, self.task_id, candidate.candidate_id))
        self.assertEqual(reopened.rebuild_asset_read_bindings(), 1)
        self.assertEqual(reopened.get_current_preview_candidate(
            self.actor, self.task_id, candidate.candidate_id),
            (candidate, 'trusted:ref'))
        other_actor = replace(self.actor, user_id='another-user')
        self.assert_code('NOT_FOUND', lambda:
            reopened.get_current_preview_candidate(
                other_actor, self.task_id, candidate.candidate_id))
        self.assert_code('NOT_FOUND', lambda:
            reopened.get_result_read_page(other_actor, self.task_id))
        self.assert_code('NOT_FOUND', lambda:
            reopened.step_output_read_page(other_actor, self.task_id,
                operation_id=run['operation_id'],
                step_execution_id=step['step_execution_id'],
                output_version=run['output_version']))

    def assert_migration_parity(self):
        from tools.migrate_task_asset_bindings import migrate
        with TemporaryDirectory() as directory:
            self.assertEqual(migrate(self.repo.tasks.path, Path(directory) / 'copy.sqlite'), 1)

    def test_preview_binding_migration_uses_isolated_copy(self):
        from tools.migrate_task_asset_bindings import migrate
        self.run_pipeline()
        with TemporaryDirectory() as directory:
            copy = Path(directory) / 'migrated.sqlite'
            self.assertEqual(migrate(self.repo.tasks.path, copy), 1)
            self.assertTrue(copy.is_file())
            with sqlite3.connect(copy) as db:
                self.assertEqual(db.execute(
                    'SELECT COUNT(*) FROM task_asset_read_bindings').fetchone()[0], 1)
                self.assertEqual(db.execute(
                    'SELECT COUNT(*) FROM task_read_summaries').fetchone()[0], 1)
                self.assertEqual(db.execute(
                    'SELECT format FROM task_read_summaries_meta WHERE id=1').fetchone(),
                    (1,))
            with self.assertRaises(ValueError):
                migrate(self.repo.tasks.path, copy)

    def test_migration_resumes_after_verification_interrupt_and_rejects_corruption(self):
        from tools.migrate_task_asset_bindings import migrate, source_digest
        from tools.verify_task_read_projections import verify
        self.run_pipeline()
        original = source_digest(self.repo.tasks.path)
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'copy.sqlite'
            with patch('tools.verify_task_read_projections.verify', side_effect=RuntimeError('interrupted')):
                with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                    migrate(self.repo.tasks.path, target)
            self.assertFalse(target.exists())
            self.assertEqual(migrate(self.repo.tasks.path, target, resume=True), 1)
            self.assertEqual(source_digest(self.repo.tasks.path), original)
            copied = type(self.repo)(target, projects=None, authorization=None)
            with sqlite3.connect(target) as db:
                db.execute("UPDATE task_result_read_items SET item_json='{}' WHERE view_type='accepted'")
            with self.assertRaisesRegex(ValueError, 'semantic mismatch'):
                verify(copied)

    def test_preview_binding_failure_rolls_back_publication(self):
        with patch.object(self.repo, '_sync_asset_read_bindings',
                          side_effect=ValueError('projection write failed')):
            with self.assertRaisesRegex(ValueError, 'projection write failed'):
                self.run_pipeline()
        self.assertEqual(self.f.state()['result_sets'], {})

    def test_result_page_missing_row_fails_closed(self):
        self.run_pipeline()
        with self.repo.tasks._connect() as db:
            db.execute("DELETE FROM task_result_read_items WHERE task_id=? "
                       "AND view_type='accepted'", (self.task_id,))
        self.assert_code('STORAGE_CORRUPT', lambda:
            self.repo.get_result_read_page(self.actor, self.task_id,
                                           view_type='accepted'))

    def test_cleaning_feedback_can_rebind_confirmed_candidates_before_first_cleaning(self):
        task = self.repo.tasks.get(self.actor, self.task_id)
        service = make_service(self.repo.tasks)
        draft, _ = service.save_feedback(self.actor, self.task_id, 'early-feedback', {
            'text': '不要闭眼的图片', 'scope': 'all', 'cleaning_feedback': True,
            'expected_revision': task.confirmed_revision, 'expected_task_version': task.task_version})
        self.assertIn('追加检测条件：不要闭眼的图片', draft['proposal']['query_spec']['raw_request'])
        service.confirm_feedback(self.actor, self.task_id, 'early-confirm', {
            'draft_id': draft['draft_id'], 'expected_draft_version': draft['draft_version'],
            'proposal_digest': draft['proposal_digest'], 'expected_task_version': draft['base_task_version'],
            'action': 'confirm'})
        current = self.repo.tasks.get(self.actor, self.task_id)
        recipe = compile_confirmed_recipe(current, self.registry)
        proposal = self.repo.prepare_incremental_recompute(self.actor, self.task_id, recipe,
            detector_release_digest=self.registry.release_digest)
        self.assertEqual(proposal.revision, current.confirmed_revision)
        self.assertEqual(len(proposal.candidates), 1)
        self.assertNotEqual(proposal.manifest_ref, self.f.manifest.manifest_ref)
        self.assertEqual(self.detector.calls, 0)
        self.assertEqual(self.gateway.reads, 0)
        self.assert_code('NOT_FOUND', lambda: self.repo.get_current_result_set(self.actor, self.task_id))
        self.f.f.auth.denied = True
        self.assert_code('ACCESS_DENIED', lambda: self.repo.prepare_incremental_recompute(
            self.actor, self.task_id, recipe, detector_release_digest=self.registry.release_digest))

    def test_feedback_recompute_republishes_current_revision_without_model_rerun(self):
        old_view, _ = self.run_pipeline()
        task = self.repo.tasks.get(self.actor, self.task_id)
        service = make_service(self.repo.tasks)
        draft, _ = service.save_feedback(self.actor, self.task_id, 'r16-feedback', {
            'text': '目标数量调整后按同一清洗规则重新计算', 'scope': 'all',
            'expected_revision': task.confirmed_revision,
            'expected_task_version': task.task_version,
        })
        service.confirm_feedback(self.actor, self.task_id, 'r16-confirm', {
            'draft_id': draft['draft_id'],
            'expected_draft_version': draft['draft_version'],
            'proposal_digest': draft['proposal_digest'],
            'expected_task_version': draft['base_task_version'],
            'action': 'confirm',
        })
        repo = self.f.reopen()
        proposal = repo.prepare_incremental_recompute(
            self.actor, self.task_id, self.f.recipe,
            detector_release_digest=self.registry.release_digest)
        self.assertEqual(proposal, repo.prepare_incremental_recompute(
            self.actor, self.task_id, self.f.recipe,
            detector_release_digest=self.registry.release_digest))
        plan = repo.incremental_recompute(
            self.actor, self.task_id, self.f.recipe,
            detector_release_digest=self.registry.release_digest)
        self.assertEqual((plan['candidate_source'], plan['reused_candidate_count']),
                         ('historical_input_manifest', 1))
        approval, replayed = repo.confirm(
            self.actor, self.task_id, proposal.proposal_digest,
            key='r16-cleaning-confirm',
            expected_task_version=proposal.expected_task_version, action='confirm')
        self.assertFalse(replayed)
        view, replayed = run_approved_multi_rule(
            repo, self.actor, self.task_id, approval.operation_id,
            gateway=self.gateway, registry=self.registry)
        self.assertFalse(replayed)
        self.assertEqual(view['accepted_count'], old_view['accepted_count'])
        self.assertEqual(self.detector.calls, 1)
        self.assertEqual(self.gateway.reads, 2)
        current = repo.tasks.get(self.actor, self.task_id)
        self.assertEqual((view['generation'], current.run_generation),
                         (current.run_generation, current.run_generation))
        self.assert_migration_parity()

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

    def test_owner_single_result_exclusion_is_versioned_idempotent_and_restartable(self):
        view, _ = self.run_pipeline()
        candidate_id = view["accepted"][0].candidate_id
        changed, replayed = self.repo.exclude_result_candidates(
            self.actor, self.task_id, key="exclude-one",
            expected_task_version=view["as_of_task_version"],
            result_set_digest=view["result_set_digest"],
            candidate_id=candidate_id, scope="candidate",
            reason="这张图构图不适合", cluster_id=None)
        self.assertFalse(replayed)
        self.assertEqual((changed["accepted_count"], changed["gap"],
                          changed["user_excluded_count"]), (0, 10, 1))
        self.assertEqual(changed["failed_count"], 0)
        replay, replayed = self.repo.exclude_result_candidates(
            self.actor, self.task_id, key="exclude-one",
            expected_task_version=view["as_of_task_version"],
            result_set_digest=view["result_set_digest"],
            candidate_id=candidate_id, scope="candidate",
            reason="这张图构图不适合", cluster_id=None)
        self.assertTrue(replayed)
        self.assertEqual(replay, changed)
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda:
            self.repo.exclude_result_candidates(
                self.actor, self.task_id, key="exclude-one",
                expected_task_version=view["as_of_task_version"],
                result_set_digest=view["result_set_digest"],
                candidate_id=candidate_id, scope="candidate",
                reason="换一个原因", cluster_id=None))
        self.assert_code("REVISION_CONFLICT", lambda:
            self.repo.exclude_result_candidates(
                self.actor, self.task_id, key="exclude-stale",
                expected_task_version=view["as_of_task_version"],
                result_set_digest=view["result_set_digest"],
                candidate_id=candidate_id, scope="candidate",
                reason="这张图构图不适合", cluster_id=None))
        reopened = self.f.reopen()
        current = reopened.get_current_result_set(self.actor, self.task_id)
        self.assertEqual((current["result_set_digest"], current["user_excluded_count"]),
                         (changed["result_set_digest"], 1))
        self.assertEqual((len(current["user_excluded_items"]),
                          current["user_excluded_items"][0]["user_exclusion"]["reason"]),
                         (1, "这张图构图不适合"))
        self.assert_migration_parity()

    def test_owner_manual_review_accepts_unknown_versioned_and_restartable(self):
        candidate = self.approval.job.candidates[0]
        rules = tuple(CleaningRuleVerdict(
            rule.rule_id, "unknown" if index == 0 else "pass",
            "FACT_UNAVAILABLE" if index == 0 else "MATCH",
            None if index == 0 else "evidence.1", group_id=rule.group_id)
            for index, rule in enumerate(self.approval.job.recipe.rules))
        result = CleaningJobResult(
            self.approval.job.binding,
            (CleaningCandidateVerdict(candidate.candidate_id,
                                      candidate.asset_version,
                                      "unknown", rules),))
        self.repo.publish_result(self.actor, self.task_id,
                                 self.operation_id, result)
        view, _ = self.repo.publish_preview(
            self.actor, self.task_id, self.operation_id)
        self.assertEqual((view["accepted_count"],
                          view["pending_review_count"]), (0, 1))
        changed, replayed = self.repo.review_unknown_candidate(
            self.actor, self.task_id, key="manual-accept",
            expected_task_version=view["as_of_task_version"],
            result_set_digest=view["result_set_digest"],
            candidate_id=candidate.candidate_id,
            asset_version=candidate.asset_version,
            action="accept", reason="人工确认主体符合需求")
        self.assertFalse(replayed)
        self.assertEqual((changed["accepted_count"],
                          changed["pending_review_count"], changed["gap"]),
                         (1, 0, 9))
        replay, replayed = self.repo.review_unknown_candidate(
            self.actor, self.task_id, key="manual-accept",
            expected_task_version=view["as_of_task_version"],
            result_set_digest=view["result_set_digest"],
            candidate_id=candidate.candidate_id,
            asset_version=candidate.asset_version,
            action="accept", reason="人工确认主体符合需求")
        self.assertTrue(replayed)
        self.assertEqual(replay, changed)
        reopened = self.f.reopen()
        current = reopened.get_current_result_set(self.actor, self.task_id)
        self.assertEqual((current["accepted_count"],
                          current["pending_review_count"]), (1, 0))
        self.assertEqual(current["candidate_assessments"][candidate.candidate_id]
                         ["manual_review"]["reason"], "人工确认主体符合需求")
        self.assert_migration_parity()

    def test_owner_can_exclude_whole_pending_near_duplicate_cluster(self):
        view, _ = self.run_pipeline()
        def seed(memory, retrieval, candidates, state):
            result_set_id = state["current_result_sets"][self.task_id]
            preview, published_version = state["result_sets"][result_set_id]
            original = preview.accepted[0]
            second_id = original.candidate_id + "z"
            first = replace(original, fingerprint=PerceptualFingerprint(
                original.candidate_id, ALGORITHM, 64, "0000000000000000"))
            second = ExactRepresentative(
                second_id, "v1", "sha256:" + "2" * 64,
                fingerprint=PerceptualFingerprint(
                    second_id, ALGORITHM, 64, "0000000000000003"))
            pair = NearDuplicatePair(original.candidate_id, second_id, 2, 6)
            seeded = replace(preview, accepted=(first, second), gap=8,
                             near_duplicate_pairs=(pair,))
            state["result_sets"][result_set_id] = (seeded, published_version)
            event = next(item for item in state["events"]
                         if item.get("result_set_id") == result_set_id)
            event.update(accepted_count=2, gap=8)
            return seeded, published_version
        seeded, version = self.repo._transaction(True, seed)
        cluster = near_duplicate_clusters(seeded)[0]
        changed, replayed = self.repo.exclude_result_candidates(
            self.actor, self.task_id, key="exclude-cluster",
            expected_task_version=version,
            result_set_digest=seeded.result_set_digest,
            candidate_id=cluster.candidate_ids[0],
            scope="near_duplicate_cluster", reason="都是同一错误画面的变体")
        self.assertFalse(replayed)
        self.assertEqual((changed["accepted_count"], changed["user_excluded_count"],
                          changed["near_duplicate_cluster_count"], changed["gap"]),
                         (0, 2, 0, 10))
        # This synthetic second candidate has no cleaning source. The owner
        # action remains recorded, while its incomplete audit page fails closed.
        self.assert_code('STORAGE_CORRUPT', lambda:
            self.repo.get_result_read_page(self.actor, self.task_id,
                                           view_type='user_excluded'))

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
