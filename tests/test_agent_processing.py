from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
import unittest

from core import test_retrieval_storage as retrieval_fixtures
import test_agent_contracts as agent_fixtures
import test_multi_rule_pipeline as pipeline_fixtures
from dsh_retrieval.adapters.task_storage.candidates import SQLiteCandidateManifestRepository
from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository, load_state
from dsh_retrieval.adapters.task_storage.harness import SQLiteHarnessJournal
from dsh_retrieval.adapters.task_storage.agent_processing import SQLiteAgentProcessingHandoffs
from dsh_retrieval.agent_contracts import AgentStageTask, ProcessingCapability
from dsh_retrieval.agent_processing import AgentProcessingService
from dsh_retrieval.candidate_contracts import CandidateManifestProposal, ManifestItem
from dsh_retrieval.cleaning_models import Candidate
from dsh_retrieval.detector_artifacts import DetectorArtifact, DetectorArtifactBinding, DetectorArtifactManifest
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.harness.contracts import HarnessInvocation, RoleDefinition, RuntimeRelease
from dsh_retrieval.harness.durable import DurableToolBroker
from dsh_retrieval.harness.stage_tools import StageAuthority, stage_catalog
from dsh_retrieval.integration_models import MediaRecord
from dsh_retrieval.intent_compiler import DetectorBinding
from dsh_retrieval.intent_review import Capability
from dsh_retrieval.materialization_models import MaterializedCandidate, MaterializedPage
from dsh_retrieval.models import Actor
from dsh_retrieval.multi_rule_pipeline import run_approved_multi_rule
from dsh_retrieval.proposals import normalize_proposal
from dsh_retrieval.query_spec import digest
from dsh_retrieval.recipe_bridge import compile_confirmed_recipe
from dsh_retrieval.released_detectors import ReleasedDetectorRegistry
from dsh_retrieval.retrieval_contracts import RetrievalScope


class AgentProcessingTest(unittest.TestCase):
    def setUp(self):
        self.f = retrieval_fixtures.RetrievalStorageTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.actor = self.f.actor
        task = self.f.tasks.get(self.actor, self.f.tid)
        proposal = json.loads(task.confirmed_proposal_json)
        spec = proposal['query_spec']
        spec['rules'] = [{'rule_id': name, 'predicate_ref': predicate, 'operator': 'eq',
            'value': False, 'hardness': 'hard', 'origin': 'user_explicit'}
            for name, predicate in (('text', 'text.present@1'), ('mark', 'watermark.present@1'))]
        spec['refill_policy'].update(mode='maintain_target', approved_library_ids=['library'])
        requirements = agent_fixtures.requirements()
        requirements['autonomy']['library_ids'] = ['library']
        requirements['processing'] = [{'requirement_id': 'remove-mark', 'purpose': 'filter',
            'instruction': '不要水印', 'source_text': '客厅图片', 'group_id': None,
            'rule_ref': 'mark', 'output_field': None, 'output_schema': None,
            'unknown_policy': 'hold_for_review'}]
        spec['agent_requirements'] = requirements
        proposal = normalize_proposal(proposal)
        # Trusted repository fixture; production TaskService new-mode gate stays closed.
        self.f.change_task(confirmed_proposal_json=json.dumps(proposal, sort_keys=True),
            request_digest=digest({'project_id': task.project_id, 'proposal': proposal}))
        plan, _ = self.f.confirm(self.f.propose(plan_id='agent-source'))
        operation, _ = self.f.repo.reserve_page(self.actor, plan, plan.request, 'page')
        self.f.repo.commit_page(self.actor, operation, plan, plan.request, self.f.first)
        media = MediaRecord('unit', 'v1', 'source', 'asset', 'trusted:ref',
            'sha256:6331bddb5157e37150aa291caba5c6396b443f04e3cefb5aee9a3df4c966ff73')
        # Match the bytes returned by the deterministic media gateway.
        import hashlib
        media = replace(media, content_digest='sha256:' + hashlib.sha256(b'fixture').hexdigest())
        item = ManifestItem(media, (self.f.hit,))
        materialized = MaterializedPage(self.f.repo.get_operation(self.actor, self.f.tid, operation),
            (MaterializedCandidate(Candidate(item.candidate_id, 'v1', '', media.content_digest), media, item.hits),))
        manifest_proposal = CandidateManifestProposal.from_materialized(materialized)
        manifests = SQLiteCandidateManifestRepository(self.f.path, projects=self.f.projects, authorization=self.f.auth)
        manifests.put_proposal(self.actor, manifest_proposal)
        self.manifest, _ = manifests.confirm(self.actor, manifest_proposal, key='manifest',
            expected_task_version=manifest_proposal.scope.task_version, action='confirm')
        self.cleaning = SQLiteMultiRuleApprovalRepository(self.f.path, projects=self.f.projects, authorization=self.f.auth)
        self.detector = pipeline_fixtures.Detector()
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = self.artifact_root = Path(temp.name); (root / 'detector.py').write_bytes(b'synthetic implementation')
        artifact = DetectorArtifact('implementation', 'detector.py',
            'sha256:' + hashlib.sha256((root / 'detector.py').read_bytes()).hexdigest())
        binding = DetectorArtifactBinding(self.detector.backend_ref, self.detector.validator_ref,
            self.detector.evidence_ref, (artifact,))
        self.detector.artifact_binding_digest = binding.binding_digest
        self.registry = ReleasedDetectorRegistry(tuple(Capability(p, 'constraint', 'image', ('eq',),
            'boolean', 'verified', 'fixture.validator', 'fixture.eval') for p in ('text.present@1', 'watermark.present@1')),
            tuple(DetectorBinding(p, 'image', 'fixture.validator', 'fixture.detector', 'fixture.eval')
                for p in ('text.present@1', 'watermark.present@1')),
            {self.detector.backend_ref: self.detector}, DetectorArtifactManifest(root, (binding,)))
        self.task = self.f.tasks.get(self.actor, self.f.tid)
        recipe = compile_confirmed_recipe(self.task, self.registry)
        self.capabilities = tuple(ProcessingCapability(s, 'image', frozenset({'filter'}), frozenset()) for s in recipe.steps)
        self.context = {'manifest_ref': self.manifest.manifest_ref}
        self.journal = SQLiteHarnessJournal(self.f.tasks, self.f.projects)
        self.stage = AgentStageTask(RetrievalScope.from_task(self.task), 'clean-plan', 'cleaning',
            digest(self.context), digest(requirements), digest({'approval': 1}), 'policy@1', digest({'model': 1}), 'budget')
        self.invocation = HarnessInvocation(self.stage, 'run', 'attempt',
            RoleDefinition('clean@1', 'cleaning', 'Plan.', ('submit_processing_plan',), 'processing@1'),
            RuntimeRelease('fixture', 'fixture', digest({'artifact': 1})), 'model', 'plan',
            time.time() + 60, json.dumps(self.context))
        catalog, guard = stage_catalog(self.authority)
        self.journal.approve(self.actor, self.stage, action='confirm', proposal_digest=self.stage.stage_digest)
        self.handle = self.journal.enqueue(self.actor, self.invocation, model_calls=1)
        self.token = self.journal.claim(self.actor, self.handle)
        self.broker = DurableToolBroker(self.invocation, catalog, guard=guard,
            journal=self.journal, actor=self.actor, handle=self.handle, token=self.token)
        self.handoffs = SQLiteAgentProcessingHandoffs(journal=self.journal, cleaning=self.cleaning,
            authority=self.authority, context_provider=lambda *args: self.context)
        self.provider_calls = 0
        self.service = AgentProcessingService(handoffs=self.handoffs, registry_provider=self.provider)
        self.arguments = {'proposal': {'steps': [{'requirement_id': 'remove-mark', 'step_id': recipe.steps[0].step_id}]}}

    def authority(self, invocation):
        task = self.f.tasks.get(self.actor, self.f.tid)
        return StageAuthority(self.actor, task, invocation.stage,
            json.loads(task.confirmed_proposal_json)['query_spec'], 10, (), self.capabilities)

    def provider(self, *args):
        self.provider_calls += 1
        return self.registry

    def finish(self):
        self.broker.call('submit_processing_plan', self.arguments)
        self.journal.finish(self.actor, self.handle, self.token)

    def prepare(self, **changes):
        return self.service.prepare(self.actor, self.handle,
            manifest_ref=changes.get('manifest_ref', self.manifest.manifest_ref))

    def confirm(self, proposal):
        return self.cleaning.confirm(self.actor, self.f.tid, proposal.proposal_digest,
            key='clean-confirm', expected_task_version=proposal.expected_task_version, action='confirm')

    def state(self):
        with closing(self.f.tasks._connect()) as db:
            return load_state(db.execute('SELECT format,payload FROM multi_rule_cleaning_ledger WHERE id=1').fetchone())

    def test_receipt_compiles_all_hard_rules_and_runs_existing_executor_once(self):
        self.finish(); proposal = self.prepare()
        self.assertEqual({r.rule_id for r in proposal.recipe.rules}, {'text', 'mark'})
        self.assertEqual(self.detector.calls, 0)
        self.assertEqual(self.prepare(), proposal)
        approval, replayed = self.confirm(proposal); self.assertFalse(replayed)
        self.assertTrue(self.confirm(proposal)[1])
        gateway = pipeline_fixtures.Gateway(self.f.auth)
        result, replayed = run_approved_multi_rule(self.cleaning, self.actor, self.f.tid,
            approval.operation_id, gateway=gateway, registry=self.registry)
        self.assertFalse(replayed)
        self.assertEqual(self.detector.calls, 1)
        again, replayed = run_approved_multi_rule(self.cleaning, self.actor, self.f.tid,
            approval.operation_id, gateway=gateway, registry=self.registry)
        self.assertTrue(replayed); self.assertEqual(again, result)
        self.assertEqual(self.detector.calls, 1)

    def test_unfinished_and_other_manifest_do_not_resolve_registry(self):
        with self.assertRaises(ServiceError): self.prepare()
        self.finish()
        with self.assertRaises(ServiceError): self.prepare(manifest_ref='other-manifest')
        self.assertEqual(self.provider_calls, 0)

    def test_receipt_revocation_and_candidate_input_change_fail_closed(self):
        self.finish(); proposal = self.prepare()
        self.context['changed'] = True
        with self.assertRaises(ServiceError): self.prepare()
        with self.journal._tx() as db:
            db.execute('DELETE FROM harness_stage_heads WHERE stage_digest=?', (self.stage.stage_digest,))
        with self.assertRaises(ServiceError): self.confirm(proposal)
        self.assertEqual(self.state()['approvals'], {})

    def test_released_capability_disappears_after_receipt(self):
        self.finish(); self.capabilities = ()
        with self.assertRaises(ServiceError): self.prepare()
        self.assertEqual(self.provider_calls, 0)

    def test_revoked_library_or_owner_cannot_prepare(self):
        self.finish(); self.f.auth.denied = True
        with self.assertRaises(ServiceError): self.prepare()
        with self.assertRaises(ServiceError):
            self.service.prepare(Actor('tenant', 'other'), self.handle, manifest_ref=self.manifest.manifest_ref)
        self.assertEqual(self.state()['proposals'], {})

    def test_registry_change_during_resolution_cannot_publish(self):
        self.finish()
        def changed(actor, task):
            self.f.change_task(run_generation=task.run_generation + 1)
            return self.registry
        self.service.registry_provider = changed
        with self.assertRaises(ServiceError): self.prepare()
        self.assertEqual(self.state()['proposals'], {})

    def test_direct_legacy_prepare_cannot_bypass_agent_origin_confirmation(self):
        recipe = compile_confirmed_recipe(self.task, self.registry)
        proposal = self.cleaning.prepare(self.actor, self.f.tid, self.manifest.manifest_ref,
            recipe, detector_release_digest=self.registry.release_digest)
        with self.assertRaises(ServiceError) as error: self.confirm(proposal)
        self.assertEqual(error.exception.code, 'AGENT_CONFIRMATION_REQUIRED')

    def test_artifact_change_refuses_prepare(self):
        self.finish()
        (self.artifact_root / 'detector.py').write_bytes(b'changed')
        with self.assertRaises(ServiceError): self.prepare()
        self.assertEqual(self.state()['proposals'], {})

    def test_wrong_rule_detector_cannot_replace_confirmed_binding(self):
        from dsh_retrieval.multi_rule_contracts import DetectionStep
        other = DetectionStep('other-step', 'other-detector', 'other-validator', 'other-evidence')
        self.capabilities += (ProcessingCapability(other, 'image', frozenset({'filter'}), frozenset()),)
        self.arguments['proposal']['steps'][0]['step_id'] = other.step_id
        self.finish()
        with self.assertRaises(ServiceError) as error: self.prepare()
        self.assertEqual(error.exception.code, 'AGENT_RECIPE_MISMATCH')
        self.assertEqual(self.detector.calls, 0)

    def test_tag_purpose_is_rejected_before_registry_resolution(self):
        # Unit check of the purpose guard; no fabricated snapshot is persisted.
        self.finish(); snapshot = self.handoffs.load(self.actor, self.handle)
        proposal = json.loads(snapshot.task.confirmed_proposal_json)
        request = proposal['query_spec']['agent_requirements']['processing'][0]
        request.update(purpose='tag', rule_ref=None, output_field='watermark',
            output_schema={'version':'tag@1','type':'boolean','values':[],'required':False})
        changed = replace(snapshot, task=replace(snapshot.task, confirmed_proposal_json=json.dumps(proposal)))
        from unittest.mock import Mock
        service = AgentProcessingService(handoffs=Mock(load=Mock(return_value=changed)), registry_provider=self.provider)
        with self.assertRaises(ServiceError) as error:
            service.prepare(self.actor, self.handle, manifest_ref=self.manifest.manifest_ref)
        self.assertEqual(error.exception.code, 'AGENT_PROCESSING_OUTPUT_UNSUPPORTED')
        self.assertEqual(self.provider_calls, 0)

    def test_concurrent_preparation_saves_one_proposal_and_one_origin(self):
        self.finish()
        with ThreadPoolExecutor(max_workers=4) as pool:
            proposals = list(pool.map(lambda _: self.prepare(), range(4)))
        self.assertTrue(all(p == proposals[0] for p in proposals))
        self.assertEqual(len(self.state()['proposals']), 1)
        with closing(self.f.tasks._connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_processing_handoffs').fetchone()[0], 1)

    def test_unknown_detector_output_remains_unknown(self):
        from dsh_retrieval.multi_rule_contracts import DetectionResult
        self.finish(); approval, _ = self.confirm(self.prepare())
        self.detector.detect = lambda *args: DetectionResult({}, None, 'fixture.validator', 'unknown')
        result, _ = run_approved_multi_rule(self.cleaning, self.actor, self.f.tid,
            approval.operation_id, gateway=pipeline_fixtures.Gateway(self.f.auth), registry=self.registry)
        self.assertEqual(result['accepted_count'], 0)
        self.assertEqual(result['pending_review_count'], 1)

    def test_deletion_purges_processing_origins(self):
        self.finish(); self.prepare()
        if self.task.conversation_id:
            self.f.tasks.hard_delete_conversation(self.actor, self.task.conversation_id)
        else:
            self.f.tasks.hard_delete_task(self.actor, self.task.task_id)
        with closing(self.f.tasks._connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_processing_handoffs').fetchone()[0], 0)
