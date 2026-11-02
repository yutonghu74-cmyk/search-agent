from contextlib import contextmanager
from io import BytesIO
import json
import time
import unittest

from dsh_retrieval.adapters.cleaning.approved_input import ApprovedManifestAccess
from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.multi_rule_contracts import (
    CleaningCandidateVerdict, CleaningJobResult, CleaningRecipe, CleaningRule,
    CleaningRuleVerdict, DetectionStep,
)
from dsh_retrieval.candidate_contracts import CandidateManifestProposal
from dsh_retrieval.query_spec import digest
from draft_helpers import BODY, confirmation
from test_first_search_local import FirstSearchLocalTest


class BytesGateway:
    def __init__(self, manifest):
        self.by_ref = {item.media.authorized_media_ref:
                       item.media.media_unit_id.encode() for item in manifest.proposal.items}

    @contextmanager
    def open_read(self, context, ref, *, max_bytes):
        if context.purpose != 'cleaning_input' or ref not in self.by_ref:
            raise ServiceError('ACCESS_DENIED', 'Unknown test media', 403)
        yield BytesIO(self.by_ref[ref])


class PlanSetCleaningBridgeTest(unittest.TestCase):
    def setUp(self):
        self.fixture = FirstSearchLocalTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_confirmed_plan_set_candidates_prepare_approve_and_read(self):
        f = self.fixture
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = '找动物图片'
        body['query_spec']['rules'] = [{
            'rule_id': 'person', 'predicate_ref': 'person.present@1',
            'operator': 'eq', 'value': False, 'hardness': 'hard',
            'origin': 'user_explicit'}]
        task, _ = f.tasks.create_task(f.actor, 'bridge-task',
            confirmation(f.tasks, f.actor, key='bridge-draft', body=body))
        task_id = task['task_id']
        queued = f.local.auto_start(f.actor, task_id, key='bridge-search')
        self.assertEqual(queued['status'], 'queued')
        f.wait_stopped(f.local, task_id)
        preview = f.local.preview_manifest(f.actor, task_id)
        published, _ = f.local.confirm_manifest(f.actor, task_id,
            proposal_digest=preview['proposal_digest'],
            expected_task_version=preview['expected_task_version'],
            key='bridge-manifest', action='confirm')
        manifest_ref = published['manifest_ref']
        step = DetectionStep('vlm', 'fixture.detector', 'fixture.validator', 'fixture.evidence')
        recipe = CleaningRecipe('image', digest({'review': 'fixture'}),
            digest({'registry': 'fixture'}), (step,),
            (CleaningRule('person', 'vlm', 'person.present@1', 'eq', False),))
        repo = SQLiteMultiRuleApprovalRepository(f.ledger, projects=f.projects,
                                                 authorization=f.local._authority())
        proposal = repo.prepare(f.actor, task_id, manifest_ref, recipe,
            detector_release_digest=digest({'release': 'fixture'}))
        self.assertEqual(len(proposal.candidates), published['candidate_count'])
        approval, replayed = repo.confirm(f.actor, task_id, proposal.proposal_digest,
            key='bridge-cleaning', expected_task_version=proposal.expected_task_version,
            action='confirm')
        self.assertFalse(replayed)
        self.assertEqual(repo.confirm(f.actor, task_id, proposal.proposal_digest,
            key='bridge-cleaning', expected_task_version=proposal.expected_task_version,
            action='confirm'), (approval, True))
        restored = SQLiteMultiRuleApprovalRepository(f.ledger, projects=f.projects,
                                                     authorization=f.local._authority())
        manifest = restored.get_manifest(f.actor, task_id, manifest_ref)
        reader = ApprovedManifestAccess(restored, f.actor, task_id, BytesGateway(manifest))
        first = approval.job.candidates[0]
        self.assertEqual(reader.read(first, approval.job.binding),
                         manifest.proposal.items[0].media.media_unit_id.encode())
        self.assertEqual(restored.get_current(f.actor, task_id, approval.operation_id), approval)
        with self.assertRaises(ServiceError):
            restored.get_manifest(Actor(f.actor.tenant_id, 'other'), task_id, manifest_ref)

    def test_incremental_cleaning_publishes_cumulative_result_lineage(self):
        f = self.fixture
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = '找动物图片'
        body['query_spec']['target_count'] = 2
        body['query_spec']['refill_policy'] = {
            'mode': 'maintain_target', 'target_count': 2,
            'approved_library_ids': ['dogs', 'cats'], 'progression': 'auto'}
        body['node_modes']['refill'] = 'auto'
        body['query_spec']['rules'] = [{
            'rule_id': 'person', 'predicate_ref': 'person.present@1',
            'operator': 'eq', 'value': False, 'hardness': 'hard',
            'origin': 'user_explicit'}]
        task, _ = f.tasks.create_task(f.actor, 'cumulative-bridge-task',
            confirmation(f.tasks, f.actor, key='cumulative-bridge-draft', body=body))
        task_id = task['task_id']
        f.local.auto_start(f.actor, task_id, key='cumulative-search')
        for _ in range(300):
            if f.local.progress(f.actor, task_id)['initial_window_complete']:
                break
            time.sleep(.01)
        else:
            self.fail('automatic initial search window did not finish')
        complete = f.local._candidate_manifest_proposal(f.actor, task_id)
        self.assertGreater(len(complete.items), 1)
        first_batch = CandidateManifestProposal(
            complete.scope, complete.operation_id, complete.plan_digest, complete.items[:1])
        f.local.repository.put_candidate_proposal(f.actor, first_batch)
        first_manifest, _ = f.local.repository.confirm_candidate_manifest(
            f.actor, first_batch, key='cumulative-first-manifest',
            expected_task_version=first_batch.scope.task_version, action='confirm')

        step = DetectionStep('vlm', 'fixture.detector', 'fixture.validator', 'fixture.evidence')
        recipe = CleaningRecipe('image', digest({'review': 'fixture'}),
            digest({'registry': 'fixture'}), (step,),
            (CleaningRule('person', 'vlm', 'person.present@1', 'eq', False),))
        repo = SQLiteMultiRuleApprovalRepository(f.ledger, projects=f.projects,
                                                 authorization=f.local._authority())

        def clean(manifest_ref, key):
            proposal = repo.prepare(f.actor, task_id, manifest_ref, recipe,
                detector_release_digest=digest({'release': 'fixture'}))
            approval, _ = repo.confirm(f.actor, task_id, proposal.proposal_digest,
                key=key, expected_task_version=proposal.expected_task_version,
                action='confirm')
            verdicts = tuple(CleaningCandidateVerdict(
                candidate.candidate_id, candidate.asset_version, 'pass',
                (CleaningRuleVerdict('person', 'pass', 'MATCH', 'evidence.1'),))
                for candidate in approval.job.candidates)
            repo.publish_result(f.actor, task_id, approval.operation_id,
                                CleaningJobResult(approval.job.binding, verdicts))
            return approval, repo.publish_preview(
                f.actor, task_id, approval.operation_id)[0]

        first_approval, first_view = clean(first_manifest.manifest_ref,
                                           'cumulative-first-cleaning')
        self.assertEqual((first_view['accepted_count'], first_view['gap']), (1, 1))
        pending_auto = repo.pending_auto_refill_events()
        self.assertEqual(len(pending_auto), 1)
        self.assertEqual(pending_auto[0]['result_set_id'], first_view['result_set_id'])
        self.assertEqual(SQLiteMultiRuleApprovalRepository(
            f.ledger, projects=f.projects,
            authorization=f.local._authority()).pending_auto_refill_events(), pending_auto)
        incremental = f.local.preview_manifest(f.actor, task_id)
        self.assertGreater(incremental['candidate_count'], 0)
        second_manifest, _ = f.local.confirm_manifest(
            f.actor, task_id, proposal_digest=incremental['proposal_digest'],
            expected_task_version=incremental['expected_task_version'],
            key='cumulative-second-manifest', action='confirm')
        second_approval, second_view = clean(second_manifest['manifest_ref'],
                                             'cumulative-second-cleaning')

        self.assertEqual((second_view['accepted_count'], second_view['gap']), (2, 0))
        self.assertEqual(second_view['parent_result_set_id'], first_view['result_set_id'])
        self.assertEqual(second_view['source_operation_ids'],
                         (first_approval.operation_id, second_approval.operation_id))
        self.assertEqual(repo.get_published_candidate(
            f.actor, task_id, second_approval.operation_id,
            first_approval.job.candidates[0].candidate_id)[0],
            first_approval.job.candidates[0])
        self.assertEqual(len(repo.step_results(f.actor, task_id)['runs']), 2)
        self.assertEqual(SQLiteMultiRuleApprovalRepository(
            f.ledger, projects=f.projects,
            authorization=f.local._authority()).get_current_result_set(f.actor, task_id),
            second_view)
