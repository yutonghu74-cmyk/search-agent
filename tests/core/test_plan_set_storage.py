from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository, dump
from dsh_retrieval.adapters.task_storage.plan_sets import SQLitePlanSetApprovalRepository
from dsh_retrieval.candidate_contracts import CandidateManifestProposal, ManifestItem
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import (IndexBinding, MediaRecord, QueryVector,
                                              SearchRequest, SearchPage, SearchHit)
from dsh_retrieval.models import Actor, RunStatus
from dsh_retrieval.multi_branch_contracts import RetrievalPlanSet
from dsh_retrieval.physical_query_preparation import PreparedQuery
from dsh_retrieval.query_branches import compile_query_branches
from dsh_retrieval.retrieval_contracts import RetrievalPlan, RetrievalScope
from dsh_retrieval.retrieval_coordinator import RetrievalCoordinator
from draft_helpers import make_service, confirmation


class PlanSetStorageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'ledger.sqlite3'
        self.actor = Actor('tenant', 'owner')
        self.service = make_service(SQLiteTaskRepository(self.path))
        task, _ = self.service.create_task(self.actor, 'create', confirmation(self.service, self.actor))
        self.task_id = task['task_id']
        self.denied = set()
        self.calls = 0
        self.fail_at = None
        self.repo = self.reopen()
        self.proposal = self.make_proposal()

    def require_access(self, context, resource_ref, action):
        self.calls += 1
        if resource_ref in self.denied or self.calls == self.fail_at:
            raise ServiceError('ACCESS_DENIED', 'Denied', 403)

    def reopen(self):
        return SQLitePlanSetApprovalRepository(self.path, projects=self.service.projects, authorization=self)

    def make_proposal(self, epoch=1):
        task = self.service.repository.get(self.actor, self.task_id)
        scope = RetrievalScope.from_task(task)
        preview = compile_query_branches(json.loads(task.confirmed_proposal_json)['query_spec'])
        branch = preview['branches'][0]
        members = []
        for index in range(2):
            binding = IndexBinding(f'b{index}', f'lib{index}', 'v1', 'encoder', 2, 'COSINE')
            request = SearchRequest(f'p-{epoch}-{index}', epoch, binding, QueryVector('encoder', (0.1, 0.2)), 'filter', 10)
            members.append(PreparedQuery(RetrievalPlan(scope, epoch, request), branch['branch_id'],
                                         branch['query_fingerprint'], preview['preview_digest']))
        return RetrievalPlanSet(scope, epoch, tuple(members))

    def confirm(self, proposal=None, key='confirm', **kwargs):
        proposal = proposal or self.proposal
        args = dict(proposal_digest=proposal.proposal_digest,
                    expected_task_version=proposal.scope.task_version, action='confirm', key=key)
        return self.repo.confirm(self.actor, self.task_id, **(args | kwargs))

    def test_atomic_approval_restart_and_multi_key_replay(self):
        self.repo.put_proposal(self.actor, self.proposal)
        approved, replay = self.confirm()
        self.assertFalse(replay)
        self.assertEqual(approved.scope.task_version, 2)
        self.assertTrue(all(m.plan.scope == approved.scope for m in approved.members))
        self.assertEqual(self.reopen().get_current(self.actor, self.task_id), approved)
        self.assertEqual(self.confirm(), (approved, True))
        self.assertEqual(self.confirm(key='another'), (approved, True))
        self.assertEqual(SQLiteTaskRepository(self.path).get(self.actor, self.task_id).task_version, 2)
        events = self.repo._transaction(False, lambda memory, state: state['events'])
        self.assertEqual(len(events), 1)

    def test_replacement_does_not_reactivate_old_set(self):
        self.repo.put_proposal(self.actor, self.proposal)
        first, _ = self.confirm()
        second = self.make_proposal(2)
        self.repo.put_proposal(self.actor, second)
        approved, _ = self.confirm(second, key='second')
        self.assertEqual(self.confirm(key='late'), (first, True))
        self.assertEqual(self.repo.get_current(self.actor, self.task_id), approved)
        self.assertEqual(approved.scope.task_version, 3)

    def test_denial_mid_confirmation_rolls_back_everything(self):
        self.repo.put_proposal(self.actor, self.proposal)
        self.calls = 0
        self.fail_at = 4  # Last library of the final authorization pass.
        with self.assertRaises(ServiceError):
            self.confirm()
        self.fail_at = None
        self.assertIsNone(self.repo.get_current(self.actor, self.task_id))
        self.assertEqual(self.service.repository.get(self.actor, self.task_id).task_version, 1)
        self.assertFalse(self.confirm()[1])

    def test_serialization_failure_rolls_back_task_and_set(self):
        self.repo.put_proposal(self.actor, self.proposal)
        with patch('dsh_retrieval.adapters.task_storage.plan_sets.serialize', side_effect=ValueError('failure')):
            with self.assertRaises(ValueError):
                self.confirm()
        self.assertEqual(self.service.repository.get(self.actor, self.task_id).task_version, 1)
        self.assertIsNone(self.reopen().get_current(self.actor, self.task_id))

    def test_owner_revoke_conflicting_key_and_stale_epoch(self):
        self.repo.put_proposal(self.actor, self.proposal)
        self.confirm()
        with self.assertRaises(ServiceError) as caught:
            self.confirm(action='reject')
        self.assertEqual(caught.exception.code, 'IDEMPOTENCY_CONFLICT')
        self.denied.add('library:lib1')
        for call in (lambda: self.confirm(), lambda: self.repo.get_current(self.actor, self.task_id)):
            with self.assertRaises(ServiceError) as caught:
                call()
            self.assertEqual(caught.exception.code, 'ACCESS_DENIED')
        self.denied.clear()
        with self.assertRaises(ServiceError) as caught:
            self.repo.get_current(Actor('tenant', 'other'), self.task_id)
        self.assertEqual(caught.exception.code, 'NOT_FOUND')
        with self.assertRaises(ServiceError) as caught:
            self.repo.put_proposal(self.actor, self.make_proposal(1))
        self.assertEqual(caught.exception.code, 'PLAN_EPOCH_CONFLICT')

    def test_concurrent_different_keys_have_one_effect(self):
        self.repo.put_proposal(self.actor, self.proposal)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda key: self.confirm(key=key), ['one', 'two']))
        self.assertEqual(sorted(replay for _, replay in results), [False, True])
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(self.service.repository.get(self.actor, self.task_id).task_version, 2)

    def test_new_process_recovers_approved_set(self):
        import subprocess
        import sys
        self.repo.put_proposal(self.actor, self.proposal)
        approved, _ = self.confirm()
        script = '''
import sys
from dsh_retrieval.adapters.task_storage.plan_sets import SQLitePlanSetApprovalRepository
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.models import Actor
class Auth:
    def require_access(self, *args): pass
repo = SQLitePlanSetApprovalRepository(sys.argv[1],
    projects=LocalProjectAccess([('tenant', 'owner', 'demo', 'default')]), authorization=Auth())
current = repo.get_current(Actor('tenant', 'owner'), sys.argv[2])
print(current.proposal_digest)
'''
        result = subprocess.run([sys.executable, '-c', script, str(self.path), self.task_id],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), approved.proposal_digest)

    def test_refill_position_can_commit_after_downstream_task_version_advance(self):
        self.repo.put_proposal(self.actor, self.proposal)
        approved, _ = self.confirm()
        with self.repo.tasks._connect() as db, db:
            db.execute('BEGIN IMMEDIATE')
            memory = self.repo.tasks._load(db.execute(
                'SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            current = memory._tasks[self.task_id]
            memory._tasks[self.task_id] = replace(
                current, task_version=current.task_version + 1,
                run_status=RunStatus.RUNNING, stage='deciding')
            db.execute('UPDATE local_ledger SET payload=? WHERE id=1', (dump(memory),))

        class Gateway:
            def search(inner, context, request):
                hit = SearchHit(request.binding, 'candidate-1', 0.9)
                return SearchPage((hit,), None, 'exhausted', 'snapshot', 'snap-1')

        result = RetrievalCoordinator(self.repo, Gateway()).tick_position(
            self.actor, approved, plan_id=approved.members[0].plan.request.plan_id,
            expected_cursor=None)
        self.assertEqual(result['status'], 'committed')
        heads = self.repo.get_progress(self.actor, approved, allow_downstream=True)
        self.assertEqual(heads[0].committed_pages, 1)
        with self.assertRaises(ServiceError) as error:
            self.repo.get_progress(self.actor, approved)
        self.assertEqual(error.exception.code, 'REVISION_CONFLICT')

    def test_recovery_skips_refill_task_waiting_for_owner_decision(self):
        self.repo.put_proposal(self.actor, self.proposal)
        self.confirm()
        with self.repo.tasks._connect() as db, db:
            db.execute('BEGIN IMMEDIATE')
            memory = self.repo.tasks._load(db.execute(
                'SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            current = memory._tasks[self.task_id]
            memory._tasks[self.task_id] = replace(
                current, run_status=RunStatus.AWAITING_INPUT, stage='refill')
            db.execute('UPDATE local_ledger SET payload=? WHERE id=1', (dump(memory),))
        self.assertEqual(self.reopen().recoverable(), ())

    def test_delivery_read_revalidates_candidate_after_task_enters_finalizing(self):
        self.repo.put_proposal(self.actor, self.proposal)
        approved, _ = self.confirm()
        class Gateway:
            def search(inner, context, request):
                hit = SearchHit(request.binding, 'candidate-1', 0.9)
                return SearchPage((hit,), None, 'exhausted', 'snapshot',
                                  'snap-' + request.plan_id)
        coordinator = RetrievalCoordinator(self.repo, Gateway())
        hits = []
        for member in approved.members:
            coordinator.tick_position(
                self.actor, approved, plan_id=member.plan.request.plan_id,
                expected_cursor=None)
            hits.append(SearchHit(member.plan.request.binding, 'candidate-1', 0.9))
        task = self.service.repository.get(self.actor, self.task_id)
        media = MediaRecord('unit-1', 'v1', 'source', 'asset-1', 'trusted:asset-1',
                            'sha256:' + sha256(b'asset-1').hexdigest())
        proposal = CandidateManifestProposal(
            RetrievalScope.from_task(task), self.repo.candidate_source_id(approved),
            approved.proposal_digest, (ManifestItem(media, tuple(hits)),))
        self.repo.put_candidate_proposal(self.actor, proposal)
        self.repo.confirm_candidate_manifest(
            self.actor, proposal, key='confirm-for-delivery',
            expected_task_version=proposal.scope.task_version, action='confirm')
        with self.repo.tasks._connect() as db, db:
            db.execute('BEGIN IMMEDIATE')
            memory = self.repo.tasks._load(db.execute(
                'SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            current = memory._tasks[self.task_id]
            memory._tasks[self.task_id] = replace(
                current, task_version=current.task_version + 1,
                run_status=RunStatus.PARTIAL, stage='FINALIZING')
            db.execute('UPDATE local_ledger SET payload=? WHERE id=1', (dump(memory),))

        def validate(memory, state, delivery_read):
            return self.repo._validate_candidate_manifest(
                memory, state, self.actor, proposal, reading=True,
                delivery_read=delivery_read)

        self.repo._transaction(False, lambda memory, state: validate(memory, state, True))
        with self.assertRaises(ServiceError) as error:
            self.repo._transaction(False, lambda memory, state: validate(memory, state, False))
        self.assertEqual(error.exception.code, 'REVISION_CONFLICT')

    def test_downstream_candidate_batch_accepts_committed_refill_prefix(self):
        self.repo.put_proposal(self.actor, self.proposal)
        approved, _ = self.confirm()
        with self.repo.tasks._connect() as db, db:
            db.execute('BEGIN IMMEDIATE')
            memory = self.repo.tasks._load(db.execute(
                'SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            current = memory._tasks[self.task_id]
            memory._tasks[self.task_id] = replace(
                current, task_version=current.task_version + 1,
                run_status=RunStatus.RUNNING, stage='deciding')
            db.execute('UPDATE local_ledger SET payload=? WHERE id=1', (dump(memory),))

        hit = SearchHit(approved.members[0].plan.request.binding, 'candidate-1', 0.9)

        class Gateway:
            def search(inner, context, request):
                return SearchPage((hit,), 'next-page', 'more_available',
                                  'snapshot', 'snap-1')

        result = RetrievalCoordinator(self.repo, Gateway()).tick_position(
            self.actor, approved, plan_id=approved.members[0].plan.request.plan_id,
            expected_cursor=None)
        self.assertEqual(result['status'], 'committed')
        task = self.service.repository.get(self.actor, self.task_id)
        media = MediaRecord('unit-1', 'v1', 'source', 'asset-1', 'trusted:asset-1',
                            'sha256:' + sha256(b'asset-1').hexdigest())
        proposal = CandidateManifestProposal(
            RetrievalScope.from_task(task), self.repo.candidate_source_id(approved),
            approved.proposal_digest, (ManifestItem(media, (hit,)),))
        self.repo.put_candidate_proposal(self.actor, proposal)
        manifest, replayed = self.repo.confirm_candidate_manifest(
            self.actor, proposal, key='confirm-refill-prefix',
            expected_task_version=proposal.scope.task_version, action='confirm')
        self.assertFalse(replayed)
        self.assertEqual(manifest.proposal, proposal)
        self.assertEqual(self.repo.candidate_manifest_candidate_ids(
            self.actor, self.task_id, approved), frozenset({proposal.items[0].candidate_id}))


if __name__ == '__main__':
    unittest.main()
