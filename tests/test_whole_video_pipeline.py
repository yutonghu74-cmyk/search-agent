"""Synthetic whole-video integration; no real model or internal platform access."""
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import unittest

from core import test_retrieval_storage as storage_fixture
from test_video_query import video_body
from dsh_retrieval.adapters.cleaning.video import WholeVideoConditionDetector
from dsh_retrieval.adapters.task_storage.candidates import SQLiteCandidateManifestRepository
from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
from dsh_retrieval.candidate_contracts import CandidateManifestProposal, ManifestItem
from dsh_retrieval.cleaning_models import Candidate
from dsh_retrieval.confirmed_cleaning import ConfirmedCleaningService
from dsh_retrieval.detector_artifacts import DetectorArtifact, DetectorArtifactBinding, DetectorArtifactManifest
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import MediaRecord
from dsh_retrieval.intent_compiler import DetectorBinding
from dsh_retrieval.intent_review import Capability
from dsh_retrieval.materialization_models import MaterializedCandidate, MaterializedPage
from dsh_retrieval.multi_rule_contracts import CleaningRuleVerdict, bytes_digest
from dsh_retrieval.multi_rule_pipeline import run_approved_multi_rule
from dsh_retrieval.proposals import normalize_proposal
from dsh_retrieval.query_spec import digest
from dsh_retrieval.recipe_bridge import compile_confirmed_recipe
from dsh_retrieval.released_detectors import ReleasedDetectorRegistry
from dsh_retrieval.whole_video import VIDEO_PREDICATE, canonical_whole_video_record


class WholeVideoPipelineTest(unittest.TestCase):
    def setUp(self):
        self.f = f = storage_fixture.RetrievalStorageTest()
        f.setUp(); self.addCleanup(f.doCleanups)
        self.actor, self.task_id = f.actor, f.tid
        # Trusted synthetic confirmed snapshot. Public video confirmation stays closed.
        body = normalize_proposal(video_body())
        if getattr(self, 'fixture_target_count', None) is not None:
            target = self.fixture_target_count
            body['query_spec']['target_count'] = target
            body['query_spec']['refill_policy']['target_count'] = target
            body['query_spec']['raw_request'] = f'找{target}个喝水的原视频'
        if self._testMethodName == 'test_any_confirmed_relation_preserves_partial_pass':
            body['query_spec']['video_conditions']['relation']['operator'] = 'any'
        old = f.tasks.get(f.actor, f.tid)
        f.change_task(media_type='video', raw_request=body['query_spec']['raw_request'],
            target_count=body['query_spec']['target_count'],
            confirmed_proposal_json=json.dumps(body),
            request_digest=digest({'project_id': old.project_id, 'proposal': body}))
        f.request = replace(f.request, batch_size=10)
        plan, _ = f.confirm(f.propose(plan_id='video-plan'))
        oid, _ = f.repo.reserve_page(f.actor, plan, plan.request, 'video-page')
        hits = tuple(replace(f.hit, vector_id=i+1) for i in range(4))
        f.repo.commit_page(f.actor, oid, plan, plan.request, replace(f.first, hits=hits))
        operation = f.repo.get_operation(f.actor, f.tid, oid)
        self.content = {'trusted:v0': b'pass', 'trusted:v1': b'pass',
                        'trusted:v2': b'fail', 'trusted:v3': b'unknown'}
        materialized = []
        for i, hit in enumerate(hits):
            ref = f'trusted:v{i}'
            media = canonical_whole_video_record(MediaRecord('source-unit', 'v1', 'source',
                f'original-{i}', ref, bytes_digest(self.content[ref])), source_unit_type='whole_video')
            item = ManifestItem(media, (hit,))
            materialized.append(MaterializedCandidate(Candidate(item.candidate_id, 'v1', '', media.content_digest), media, (hit,)))
        manifests = SQLiteCandidateManifestRepository(f.path, projects=f.projects, authorization=f.auth)
        proposal = CandidateManifestProposal.from_materialized(MaterializedPage(operation, tuple(materialized)))
        manifests.put_proposal(f.actor, proposal)
        self.manifest, _ = manifests.confirm(f.actor, proposal, key='video-manifest',
            expected_task_version=proposal.scope.task_version, action='confirm')
        self.calls = self.reads = 0
        task = f.tasks.get(f.actor, f.tid)
        self.detector = WholeVideoConditionDetector(task, self.evaluate)
        root = Path(f.temp.name) / 'artifacts'; root.mkdir()
        artifact = root / 'synthetic-video-evaluator.py'; artifact.write_bytes(b'synthetic video evaluator')
        binding = DetectorArtifactBinding(self.detector.backend_ref, self.detector.validator_ref,
            self.detector.evidence_ref, (DetectorArtifact('implementation', artifact.name, bytes_digest(artifact.read_bytes())),))
        self.detector.artifact_binding_digest = binding.binding_digest
        self.registry = ReleasedDetectorRegistry(
            (Capability(VIDEO_PREDICATE, 'constraint', 'video', ('eq',), 'boolean', 'verified',
                        self.detector.validator_ref, self.detector.evidence_ref),),
            (DetectorBinding(VIDEO_PREDICATE, 'video', self.detector.validator_ref,
                             self.detector.backend_ref, self.detector.evidence_ref),),
            {self.detector.backend_ref: self.detector}, DetectorArtifactManifest(root, (binding,)))
        self.recipe = compile_confirmed_recipe(task, self.registry)
        self.repo = self.reopen()
        service = ConfirmedCleaningService(self.repo, registry=self.registry, gateway=self)
        proposal = service.prepare(f.actor, f.tid, self.manifest.manifest_ref)
        self.approval, _ = self.repo.confirm(f.actor, f.tid, proposal['proposal_digest'],
            key='approve-video', expected_task_version=proposal['expected_task_version'], action='confirm')

    def reopen(self):
        return SQLiteMultiRuleApprovalRepository(self.f.path, projects=self.f.projects, authorization=self.f.auth)

    def evaluate(self, content, candidate, binding, conditions):
        self.calls += 1
        value = content.decode()
        rows = tuple(CleaningRuleVerdict(c['id'], value,
            'UNAVAILABLE' if value == 'unknown' else 'OBSERVED',
            None if value == 'unknown' else 'video.evidence') for c in conditions['clauses'])
        return rows, None if value == 'unknown' else 'video.evidence'

    @contextmanager
    def open_read(self, context, ref, *, max_bytes):
        self.f.auth.require_access(context, ref, 'read')
        self.reads += 1
        with BytesIO(self.content[ref]) as stream:
            yield stream

    def execute_video(self):
        return run_approved_multi_rule(self.repo, self.actor, self.task_id,
            self.approval.operation_id, gateway=self, registry=self.registry)

    def test_persisted_verification_counting_and_restart_replay(self):
        view, replay = self.execute_video()
        self.assertFalse(replay)
        self.assertEqual(view['accepted_count'], 1)
        self.assertEqual((view['duplicate_count'], view['failed_count'], view['pending_review_count']), (1, 1, 1))
        count = self.calls
        self.repo = self.reopen()
        restored, replay = self.execute_video()
        self.assertTrue(replay)
        self.assertEqual(restored['result_set_digest'], view['result_set_digest'])
        self.assertEqual(restored['accepted_count'], 1)
        self.assertEqual(self.calls, count)

    def test_fixed_input_stop_conflict_rolls_back_preview_and_task_transition(self):
        from dsh_retrieval.adapters.task_storage.refill import SQLiteRefillReservationRepository
        stops=SQLiteRefillReservationRepository(self.f.path,projects=self.f.projects,authorization=self.f.auth)
        stops.record_execution_stop(self.actor,self.task_id,'NO_PROGRESS')
        before=self.repo.tasks.get(self.actor,self.task_id).to_summary()
        with self.assertRaises(ServiceError) as error:self.execute_video()
        self.assertEqual(error.exception.code,'RESULT_CONFLICT')
        self.assertEqual(self.repo.tasks.get(self.actor,self.task_id).to_summary(),before)
        with self.assertRaises(ServiceError) as missing:self.repo.get_current_result_set(self.actor,self.task_id)
        self.assertEqual(missing.exception.code,'NOT_FOUND')

    def test_failed_or_malformed_evaluator_output_stays_unknown(self):
        def invalid(*args):
            return 0.99
        self.detector._evaluate = invalid
        view, _ = self.execute_video()
        self.assertEqual(view['accepted_count'], 0)
        self.assertEqual(view['pending_review_count'], 4)
        self.assertEqual(view['gap'], 10)

    def test_all_relation_does_not_hide_an_unknown_clause(self):
        # The aggregation truth table is tested separately. A task-scoped
        # adapter preserves unknown as unknown; it never treats a score as pass.
        def mixed(content, candidate, binding, conditions):
            conditions['relation']['operator'] = 'any'  # evaluator cannot rewrite confirmed ALL
            return (CleaningRuleVerdict(conditions['clauses'][0]['id'], 'pass', 'MATCH', 'video.e1'),
                    CleaningRuleVerdict(conditions['clauses'][1]['id'], 'unknown', 'MISSING', None)), 'video.set'
        self.detector._evaluate = mixed
        view, _ = self.execute_video()
        self.assertEqual(view['accepted_count'], 0)  # the confirmed task uses ALL
        self.assertEqual(view['pending_review_count'], 4)

    def test_any_confirmed_relation_preserves_partial_pass(self):
        def mixed(content, candidate, binding, conditions):
            return (CleaningRuleVerdict(conditions['clauses'][0]['id'], 'pass', 'MATCH', 'video.e1'),
                    CleaningRuleVerdict(conditions['clauses'][1]['id'], 'unknown', 'MISSING', None)), 'video.set'
        self.detector._evaluate = mixed
        view, _ = self.execute_video()
        self.assertEqual(view['accepted_count'], 3)
        self.assertEqual(view['duplicate_count'], 1)
        self.assertEqual(view['pending_review_count'], 0)

    def test_changed_original_version_cannot_extend_a_published_lineage(self):
        self.execute_video()
        item = self.manifest.proposal.items[0]
        changed_media = canonical_whole_video_record(replace(item.media, asset_version='v2'),
                                                     source_unit_type='whole_video')
        changed = replace(self.manifest, proposal=replace(self.manifest.proposal,
                          items=(replace(item, media=changed_media),)))
        def verify(memory, retrieval, candidates, state):
            task = memory.get(self.actor, self.task_id)
            parent = state['result_sets'][state['current_result_sets'][self.task_id]][0]
            self.repo._require_video_lineage(memory, retrieval, candidates, state, self.actor,
                                            task, parent, changed)
        with self.assertRaises(ServiceError) as error:
            self.repo._transaction(False, verify)
        self.assertEqual(error.exception.code, 'INPUT_SNAPSHOT_CHANGED')

    def test_revocation_blocks_before_read(self):
        self.f.auth.denied = True
        with self.assertRaises(ServiceError):
            self.execute_video()
        self.assertEqual(self.reads, 0)

    def test_old_generation_rejected_before_read(self):
        task = self.f.tasks.get(self.actor, self.task_id)
        self.f.change_task(run_generation=task.run_generation+1)
        with self.assertRaises(ServiceError):
            self.execute_video()
        self.assertEqual(self.reads, 0)

    def test_evaluator_rebound_to_changed_conditions_is_not_reused(self):
        task = self.f.tasks.get(self.actor, self.task_id)
        proposal = json.loads(task.confirmed_proposal_json)
        proposal['query_spec']['video_conditions']['relation']['operator'] = 'any'
        changed = replace(task, confirmed_proposal_json=json.dumps(proposal),
            request_digest=digest({'project_id': task.project_id, 'proposal': proposal}))
        with self.assertRaises(ServiceError) as error:
            compile_confirmed_recipe(changed, self.registry)
        self.assertEqual(error.exception.code, 'DETECTOR_NOT_RELEASED')
