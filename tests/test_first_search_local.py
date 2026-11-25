from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository, dump
from dsh_retrieval.candidate_contracts import CandidateManifestProposal
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import preview_edited_groups
from dsh_retrieval.first_search_local import LocalFirstSearch
from dsh_retrieval.integration_models import AccessContext, QueryVector
from dsh_retrieval.local_catalog import build_local_catalog
from dsh_retrieval.model_runtime import ModelRuntime
from dsh_retrieval.models import Actor, RunStatus
from dsh_retrieval.query_branches import compile_query_branches
from dsh_retrieval.service import TaskService
from draft_helpers import BODY, confirmation


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config'


class FirstSearchLocalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'catalog.json'
        shutil.copyfile(CONFIG / 'local-first-search-catalog.example.json', self.source)
        self.fixture_path = self.root / 'fixture.json'
        shutil.copyfile(CONFIG / 'local-first-search-guided.example.json', self.fixture_path)
        fixture = json.loads(self.fixture_path.read_text())
        for record in fixture['records']:
            for media in record['media']:
                if media['content_digest'] is None:
                    media['content_digest'] = 'sha256:' + hashlib.sha256(
                        media['media_unit_id'].encode()).hexdigest()
        self.fixture_path.write_text(json.dumps(fixture, ensure_ascii=False))
        self.ledger = self.root / 'ledger.sqlite3'
        self.config_ledger = self.root / 'config.sqlite3'
        self.actor = Actor('tenant-a', 'user-a')
        self.projects = LocalProjectAccess([('tenant-a', 'user-a', 'demo', 'default')])
        self.tasks = TaskService(SQLiteTaskRepository(self.ledger), self.projects)
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = '找动物图片'
        created, _ = self.tasks.create_task(self.actor, 'task',
            confirmation(self.tasks, self.actor, body=body))
        self.task_id = created['task_id']
        self.local = self.make_local()

    def make_local(self, *, start=True):
        configs = build_local_catalog(self.source, self.config_ledger, ModelRuntime())
        local = LocalFirstSearch(self.tasks, configs, ledger_path=self.ledger,
            source_path=self.source,
            capabilities_path=CONFIG / 'local-first-search-capabilities.example.json',
            fixture_path=self.fixture_path,
            cursor_secret=b'test-only-local-cursor-secret-12345', start=start)
        self.addCleanup(local.close)
        return local

    def test_auto_window_routes_one_index_and_keeps_alternative_for_refill(self):
        from dsh_retrieval.refill_clip import LocalCLIPRefillAdapter
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = '找动物图片'
        body['query_spec']['target_count'] = 1
        body['query_spec']['refill_policy'] = dict(mode='maintain_target', target_count=1,
            approved_library_ids=['dogs', 'cats'], progression='auto')
        body['node_modes']['refill'] = 'auto'
        created, _ = self.tasks.create_task(self.actor, 'routed-auto',
            confirmation(self.tasks, self.actor, key='routed-draft', body=body))
        task_id = created['task_id']
        self.local.auto_start(self.actor, task_id, key='routed-start')
        deadline = time.monotonic() + 3
        while not self.local.progress(self.actor, task_id)['initial_window_complete']:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)
        progress = self.local.progress(self.actor, task_id)
        self.assertEqual(sum(m['committed_pages'] for m in progress['members']), 1)
        self.assertEqual(sum(m['initial_deferred'] for m in progress['members']), 1)
        self.assertFalse(progress['pagination_stopped'])
        # Fixing the first window must not wait on intentionally deferred indexes.
        self.assertGreater(self.local._candidate_manifest_proposal(self.actor, task_id).view()['candidate_count'], 0)
        # Both approved sources remain in the durable plan; unqueried alternative
        # precedes deeper pagination, without constructing a new plan/approval.
        original_mode = self.local.mode
        self.local.mode = 'LOCAL_CLIP'
        positions = LocalCLIPRefillAdapter(self.local).list_refill_positions(
            self.actor, task_id, revision=1, generation=1)
        self.local.mode = original_mode
        self.assertEqual(positions[0].library_id, 'cats')
        self.local.close()
        restarted = self.make_local()
        self.assertTrue(restarted._first_window_committed(self.actor, task_id))
        self.assertEqual(sum(m['committed_pages'] for m in restarted.progress(
            self.actor, task_id)['members']), 1)

    def test_browse_only_adapter_does_not_recover_or_start_worker(self):
        from unittest.mock import patch
        with patch('dsh_retrieval.first_search_local.SQLitePlanSetApprovalRepository.recoverable',
                   side_effect=AssertionError('browse-only recovered work')):
            local = self.make_local(start=False)
        self.assertFalse(local._worker.is_alive())
        local.close()

    def test_refill_authority_only_allows_library_search(self):
        authority = self.local._authority()
        context = AccessContext(self.actor, self.task_id, 'refill', 'current')
        authority.require_access(context, 'library:dogs', 'search')
        for resource, action in (('library:dogs', 'discover'),
                                 ('media:asset-1', 'read')):
            with self.assertRaises(ServiceError) as error:
                authority.require_access(context, resource, action)
            self.assertEqual(error.exception.code, 'ACCESS_DENIED')

    def test_fixture_accepts_search_metadata_without_inventing_missing_captions(self):
        fixture = json.loads(self.fixture_path.read_text())
        fixture['records'][0]['search_text'] = '动物 dog SKU-Ab12'
        self.fixture_path.write_text(json.dumps(fixture))
        records = self.local._fixture()[2]
        self.assertEqual(records[0].search_text, '动物 dog SKU-Ab12')
        self.assertTrue(all(r.search_text == '' for r in records[1:]))

    def test_clip_mode_uses_dynamic_text_encoder_not_fixed_word_rules(self):
        self.local.close()
        source = json.loads(self.source.read_text())
        for library in source['libraries']:
            for binding in library['bindings']:
                binding['encoder_ref'] = 'local-clip:test'
        self.source.write_text(json.dumps(source))
        capabilities_path = self.root / 'clip-capabilities.json'
        capabilities = json.loads((CONFIG / 'local-first-search-capabilities.example.json').read_text())
        for item in capabilities['items']:
            item['binding']['encoder_ref'] = 'local-clip:test'
        capabilities_path.write_text(json.dumps(capabilities))
        fixture = json.loads(self.fixture_path.read_text())
        fixture['query_vectors'] = []
        fixture.pop('query_rules', None)
        self.fixture_path.write_text(json.dumps(fixture))

        class Encoder:
            encoder_ref = 'local-clip:test'
            dimension = 2

            def __init__(self, model_path):
                pass

            def encode_query(self, text, binding):
                return QueryVector(self.encoder_ref, (1.0, 0.0))

        configs = build_local_catalog(self.source, self.config_ledger, ModelRuntime())
        with patch('dsh_retrieval.local_clip.LocalCLIPEncoder', Encoder):
            local = LocalFirstSearch(self.tasks, configs, ledger_path=self.ledger,
                source_path=self.source, capabilities_path=capabilities_path,
                fixture_path=self.fixture_path,
                cursor_secret=b'test-only-local-cursor-secret-12345',
                clip_model_path=self.root / 'offline-model')
        self.addCleanup(local.close)
        preview = local.preview(self.actor, self.task_id)
        self.assertEqual(preview['mode'], 'LOCAL_CLIP')
        self.assertTrue(all(route['availability'] == 'available' for route in preview['routes']))
        self.assertEqual(local.auto_start(self.actor, self.task_id, key='clip-auto')['status'], 'queued')
        self.wait_stopped(local, self.task_id)
        candidates = local.candidates(self.actor, self.task_id)
        self.assertEqual(candidates['mode'], 'LOCAL_CLIP')
        self.assertGreater(candidates['resolved_count'], 0)
        self.assertIsNone(candidates['accepted_count'])

    def wait_stopped(self, local, task_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            progress = local.progress(self.actor, task_id)
            if progress.get('pagination_stopped'):
                return progress
            time.sleep(.01)
        self.fail('background search did not finish')

    def advance_task_for_refill(self):
        with closing(self.local.repository.tasks._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            memory = self.local.repository.tasks._load(db.execute(
                'SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            current = memory._tasks[self.task_id]
            memory._tasks[self.task_id] = replace(
                current, task_version=current.task_version + 1,
                run_status=RunStatus.RUNNING, stage='deciding')
            db.execute('UPDATE local_ledger SET payload=? WHERE id=1', (dump(memory),))

    def select_and_approve(self):
        preview = self.local.preview(self.actor, self.task_id)
        self.assertEqual(preview['mode'], 'LOCAL_EXACT')
        self.assertEqual(len(preview['routes']), 1)
        route = preview['routes'][0]
        self.assertEqual({option['binding_id'] for option in route['options']}, {'dog-v1', 'cat-v1'})
        choices = [{'branch_id': route['branch_id'], 'binding_id': option['binding_id'], 'batch_size': 2}
                   for option in route['options']]
        proposed = self.local.prepare(self.actor, self.task_id,
            routing_digest=preview['routing_digest'], choices=choices)
        confirmed, replay = self.local.confirm(self.actor, self.task_id,
            proposal_digest=proposed['proposal_digest'],
            expected_task_version=proposed['expected_task_version'],
            action='confirm', key='approve')
        self.assertFalse(replay)
        self.assertEqual(confirmed['status'], 'approved')
        return proposed

    def test_two_index_first_batch_restart_and_candidate_mapping(self):
        self.assertEqual(self.local.progress(self.actor, self.task_id)['status'], 'unapproved')
        proposed = self.select_and_approve()
        self.assertEqual(len(proposed['members']), 2)
        self.assertFalse(self.local.progress(self.actor, self.task_id)['pagination_stopped'])
        first = self.local.tick(self.actor, self.task_id, max_requests=2)
        self.assertEqual(first['network_attempts'], 2)
        self.assertEqual({row['status'] for row in first['outcomes']}, {'committed'})
        candidates = self.local.candidates(self.actor, self.task_id)
        self.assertEqual(candidates['raw_hit_count'], 4)
        self.assertEqual(candidates['resolved_count'], 4)
        self.assertIsNone(candidates['accepted_count'])
        self.assertEqual({(item['binding_id'], item['vector_id']) for item in candidates['items']},
                         {('dog-v1', 1), ('dog-v1', 2), ('cat-v1', 1), ('cat-v1', 2)})
        self.assertTrue(all(item['query_text'] == '找动物图片' and item['query_role'] == 'original'
                            and item['source_term'] == '找动物图片' and item['branch_id'].startswith('branch_')
                            and item['plan_id'] for item in candidates['items']))
        self.assertNotEqual(next(item for item in candidates['items'] if item['binding_id'] == 'dog-v1')['media_unit_id'],
                            next(item for item in candidates['items'] if item['binding_id'] == 'cat-v1')['media_unit_id'])
        self.assertTrue(all('authorized_media_ref' not in item for item in candidates['items']))
        self.assertTrue(all(item['candidate_id'].startswith('candidate_')
                            and (item['content_digest'] is None
                                 or item['content_digest'].startswith('sha256:'))
                            for item in candidates['items']))
        self.assertEqual(candidates['verified_count'], candidates['resolved_count'])
        restarted = self.make_local()
        self.wait_stopped(restarted, self.task_id)
        last = restarted.tick(self.actor, self.task_id, max_requests=2)
        self.assertEqual(last['network_attempts'], 0)
        self.assertTrue(last['pagination_stopped'])
        self.assertTrue(restarted.progress(self.actor, self.task_id)['pagination_stopped'])
        self.assertEqual(restarted.progress(self.actor, self.task_id)['task_id'], self.task_id)
        all_candidates = restarted.candidates(self.actor, self.task_id)
        self.assertEqual(all_candidates['raw_hit_count'], 5)
        self.assertEqual([item['status'] for item in all_candidates['items']].count('mapping_missing'), 1)
        self.assertEqual(restarted.tick(self.actor, self.task_id, max_requests=2)['network_attempts'], 0)

    def test_legacy_fixture_keeps_approved_candidate_pages_readable(self):
        self.select_and_approve()
        self.local.tick(self.actor, self.task_id, max_requests=2)
        before = self.local.candidates(self.actor, self.task_id)
        legacy_path = self.root / 'legacy-fixture.json'
        shutil.copyfile(self.fixture_path, legacy_path)
        updated = json.loads(self.fixture_path.read_text())
        updated['filter_templates']['new-public'] = ['public']
        self.fixture_path.write_text(json.dumps(updated))
        self.local.close()
        with self.assertRaises(ServiceError) as expired:
            self.local.candidates(self.actor, self.task_id)
        self.assertEqual(expired.exception.code, 'INDEX_SNAPSHOT_EXPIRED')
        configs = build_local_catalog(self.source, self.config_ledger, ModelRuntime())
        recovered = LocalFirstSearch(self.tasks, configs, ledger_path=self.ledger,
            source_path=self.source,
            capabilities_path=CONFIG / 'local-first-search-capabilities.example.json',
            fixture_path=self.fixture_path,
            legacy_fixture_path=legacy_path,
            legacy_capabilities_path=CONFIG / 'local-first-search-capabilities.example.json',
            cursor_secret=b'test-only-local-cursor-secret-12345')
        self.addCleanup(recovered.close)
        after = recovered.candidates(self.actor, self.task_id)
        self.assertEqual(after['raw_hit_count'], before['raw_hit_count'])

    def test_unambiguous_confirmation_queues_background_search_once(self):
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = '找狗图片\n3张'
        body['query_spec']['grouped_expansion'] = preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': ['柯基']}])['plan']
        created, _ = self.tasks.create_task(self.actor, 'auto-task',
            confirmation(self.tasks, self.actor, key='auto-draft', body=body))
        task_id = created['task_id']
        queued = self.local.auto_start(self.actor, task_id, key='terms:auto:1')
        self.assertEqual(queued['status'], 'queued')
        self.wait_stopped(self.local, task_id)
        self.assertGreater(self.local.candidates(self.actor, task_id)['raw_hit_count'], 0)
        replay = self.local.auto_start(self.actor, task_id, key='terms:auto:1')
        self.assertEqual((replay['status'], replay['replayed']), ('queued', True))

    def test_auto_refill_auto_start_bounds_page_size_to_target(self):
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = '找动物图片'
        body['query_spec']['target_count'] = 1
        body['query_spec']['refill_policy'] = {
            'mode': 'maintain_target', 'target_count': 1,
            'approved_library_ids': ['dogs'], 'progression': 'auto'}
        body['node_modes']['refill'] = 'auto'
        created, _ = self.tasks.create_task(self.actor, 'bounded-auto-task',
            confirmation(self.tasks, self.actor, key='bounded-auto-draft', body=body))
        task_id = created['task_id']
        self.local.auto_start(self.actor, task_id, key='bounded-auto-start')
        approved = self.local.repository.get_current(self.actor, task_id)
        self.assertTrue(approved.members)
        self.assertEqual({member.plan.request.batch_size for member in approved.members}, {2})

    def test_restart_does_not_page_an_auto_task_past_its_first_window(self):
        # A real task restarted after its first window reserved page 2: the
        # extra hits break the one-manifest budget and refill owns those pages.
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = '找动物图片'
        body['query_spec']['target_count'] = 1
        body['query_spec']['refill_policy'] = {
            'mode': 'maintain_target', 'target_count': 1,
            'approved_library_ids': ['dogs'], 'progression': 'auto'}
        body['node_modes']['refill'] = 'auto'
        created, _ = self.tasks.create_task(self.actor, 'restart-auto-task',
            confirmation(self.tasks, self.actor, key='restart-auto-draft', body=body))
        task_id = created['task_id']
        self.local.auto_start(self.actor, task_id, key='restart-auto-start')
        deadline = time.monotonic() + 3
        while not self.local.progress(self.actor, task_id)['initial_window_complete']:
            self.assertLess(time.monotonic(), deadline, 'first window did not finish')
            time.sleep(.01)
        before = [(m['committed_pages'], m['status'])
                  for m in self.local.progress(self.actor, task_id)['members']]
        restarted = self.make_local()  # recovery re-enqueues persisted plans
        restarted.enqueue(self.actor, task_id)
        time.sleep(.5)
        after = restarted.progress(self.actor, task_id)
        self.assertEqual([(m['committed_pages'], m['status']) for m in after['members']], before)
        self.assertTrue(after['initial_window_complete'])

    def test_auto_first_window_fits_one_candidate_manifest(self):
        # A real 狗10/兔子5 run had 19 branches x 8 hits and never fixed a manifest.
        terms = [f'狗场景{i}' for i in range(15)]
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = '找狗图片'
        body['query_spec']['target_count'] = 15
        body['query_spec']['refill_policy'] = {
            'mode': 'maintain_target', 'target_count': 15,
            'approved_library_ids': ['dogs'], 'progression': 'auto'}
        body['node_modes']['refill'] = 'auto'
        body['query_spec']['grouped_expansion'] = preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': terms}])['plan']
        created, _ = self.tasks.create_task(self.actor, 'wide-auto-task',
            confirmation(self.tasks, self.actor, key='wide-auto-draft', body=body))
        self.local.auto_start(self.actor, created['task_id'], key='wide-auto-start')
        approved = self.local.repository.get_current(self.actor, created['task_id'])
        self.assertGreater(len(approved.members), 12)
        self.assertLessEqual(sum(m.plan.request.batch_size for m in approved.members), 100)

    def test_background_search_requeues_after_bounded_activation(self):
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = '找动物图片'
        body['query_spec']['target_count'] = 100
        body['query_spec']['refill_policy']['target_count'] = 100
        created, _ = self.tasks.create_task(self.actor, 'long-search-task',
            confirmation(self.tasks, self.actor, key='long-search-draft', body=body))
        task_id = created['task_id']
        fixture = json.loads(self.fixture_path.read_text())
        fixture['max_depth'] = 65
        fixture['records'] = [{
            'binding_id': 'dog-v1', 'vector_id': index, 'values': [1.0, 0.0],
            'scope': 'public', 'media': []} for index in range(1, 66)]
        self.fixture_path.write_text(json.dumps(fixture, ensure_ascii=False))
        preview = self.local.preview(self.actor, task_id)
        route = preview['routes'][0]
        dog = next(option for option in route['options'] if option['binding_id'] == 'dog-v1')
        proposed = self.local.prepare(self.actor, task_id,
            routing_digest=preview['routing_digest'], choices=[{
                'branch_id': route['branch_id'], 'binding_id': dog['binding_id'],
                'batch_size': 1}])
        self.local.confirm(self.actor, task_id,
            proposal_digest=proposed['proposal_digest'],
            expected_task_version=proposed['expected_task_version'],
            action='confirm', key='approve-long-search')
        self.local.enqueue(self.actor, task_id)

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            progress = self.local.progress(self.actor, task_id)
            if progress['pagination_stopped']:
                break
            time.sleep(.02)
        else:
            self.fail('requeued background search did not finish')
        self.assertEqual(progress['members'][0]['committed_pages'], 65)
        self.assertEqual(progress['members'][0]['stop_reason'], 'exhausted')

    def test_candidate_budget_bounds_deep_fixture_for_chat_manifest(self):
        self.local.media_gateway = object()
        fixture = json.loads(self.fixture_path.read_text())
        fixture['max_depth'] = 500
        fixture['records'] = [{
            'binding_id': 'dog-v1', 'vector_id': index, 'values': [1.0, 0.0],
            'scope': 'public', 'media': []} for index in range(1, 201)]
        self.fixture_path.write_text(json.dumps(fixture, ensure_ascii=False))
        preview = self.local.preview(self.actor, self.task_id)
        route = preview['routes'][0]
        proposed = self.local.prepare(self.actor, self.task_id,
            routing_digest=preview['routing_digest'], choices=[{
                'branch_id': route['branch_id'], 'binding_id': 'dog-v1',
                'batch_size': 2}])
        self.local.confirm(self.actor, self.task_id,
            proposal_digest=proposed['proposal_digest'],
            expected_task_version=proposed['expected_task_version'],
            action='confirm', key='approve-budgeted-search')
        self.local.enqueue(self.actor, self.task_id)

        progress = self.wait_stopped(self.local, self.task_id)
        candidates = self.local.candidates(self.actor, self.task_id)
        self.assertEqual(progress['members'][0]['committed_pages'], 25)
        self.assertEqual(candidates['raw_hit_count'], 50)

    def test_auto_refill_keeps_continuation_after_initial_candidate_window(self):
        self.local.media_gateway = object()
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = '找动物图片'
        body['query_spec']['target_count'] = 1
        body['query_spec']['refill_policy'] = {
            'mode': 'maintain_target', 'target_count': 1,
            'approved_library_ids': ['dogs'], 'progression': 'auto'}
        body['node_modes']['refill'] = 'auto'
        created, _ = self.tasks.create_task(self.actor, 'auto-refill-task',
            confirmation(self.tasks, self.actor, key='auto-refill-draft', body=body))
        task_id = created['task_id']
        fixture = json.loads(self.fixture_path.read_text())
        fixture['max_depth'] = 20
        fixture['records'] = [{
            'binding_id': 'dog-v1', 'vector_id': index, 'values': [1.0, 0.0],
            'scope': 'public', 'media': []} for index in range(1, 21)]
        self.fixture_path.write_text(json.dumps(fixture, ensure_ascii=False))
        preview = self.local.preview(self.actor, task_id)
        route = preview['routes'][0]
        proposed = self.local.prepare(self.actor, task_id,
            routing_digest=preview['routing_digest'], choices=[{
                'branch_id': route['branch_id'], 'binding_id': 'dog-v1',
                'batch_size': 2}])
        self.local.confirm(self.actor, task_id,
            proposal_digest=proposed['proposal_digest'],
            expected_task_version=proposed['expected_task_version'],
            action='confirm', key='approve-auto-refill-search')
        self.local.enqueue(self.actor, task_id)

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            progress = self.local.progress(self.actor, task_id)
            if progress['initial_window_complete']:
                break
            time.sleep(.01)
        else:
            self.fail('initial automatic-refill candidate window did not finish')
        self.assertFalse(progress['pagination_stopped'])
        self.assertEqual(progress['members'][0]['committed_pages'], 1)
        self.assertEqual(progress['members'][0]['status'], 'more_available')
        # The first candidate snapshot is now confirmable while the next
        # approved cursor remains reserved for a refill iteration.
        self.assertEqual(self.local.preview_manifest(
            self.actor, task_id)['kind'], 'candidate_manifest_proposal')

    def test_auto_refill_initial_window_visits_every_member_before_second_page(self):
        self.local.media_gateway = object()
        body = json.loads(json.dumps(BODY))
        terms = [f'场景{index}' for index in range(9)]
        body['query_spec']['raw_request'] = '找狗图片'
        body['query_spec']['target_count'] = 1
        body['query_spec']['grouped_expansion'] = preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': terms}])['plan']
        body['query_spec']['refill_policy'] = {
            'mode': 'maintain_target', 'target_count': 1,
            'approved_library_ids': ['dogs'], 'progression': 'auto'}
        body['node_modes']['refill'] = 'auto'
        created, _ = self.tasks.create_task(self.actor, 'wide-auto-refill-task',
            confirmation(self.tasks, self.actor, key='wide-auto-refill-draft', body=body))
        task_id = created['task_id']
        branches = compile_query_branches(body['query_spec'])['branches']
        fixture = json.loads(self.fixture_path.read_text())
        fixture['max_depth'] = 3
        known = {(item['binding_id'], item['text']) for item in fixture['query_vectors']}
        for branch in branches:
            key = ('dog-v1', branch['query_text'])
            if key not in known:
                fixture['query_vectors'].append({
                    'binding_id': 'dog-v1', 'text': branch['query_text'],
                    'values': [1.0, 0.0]})
        self.fixture_path.write_text(json.dumps(fixture, ensure_ascii=False))
        self.local.auto_start(self.actor, task_id, key='wide-auto-refill-start')
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            progress = self.local.progress(self.actor, task_id)
            if progress['initial_window_complete']:
                break
            time.sleep(.01)
        else:
            self.fail('wide automatic-refill initial window did not finish')
        self.assertGreater(len(progress['members']), 8)
        self.assertEqual({item['committed_pages'] for item in progress['members']}, {1})

    def test_current_manifest_read_keeps_publication_and_media_authority_checks(self):
        from unittest.mock import patch
        self.local.auto_start(self.actor, self.task_id, key='manifest-read-search')
        self.wait_stopped(self.local, self.task_id)
        preview = self.local.preview_manifest(self.actor, self.task_id)
        published, _ = self.local.confirm_manifest(self.actor, self.task_id,
            proposal_digest=preview['proposal_digest'],
            expected_task_version=preview['expected_task_version'],
            key='manifest-read-confirm', action='confirm')
        repo = self.local.repository
        with patch.object(repo.tasks, '_load', side_effect=AssertionError('decoded full task ledger')):
            saved = repo.get_current_candidate_manifest(self.actor, self.task_id)
            self.assertEqual(saved.manifest_ref, published['manifest_ref'])
        original = repo.authorization.require_access
        def deny_media(context, resource, action):
            if action == 'read':
                raise ServiceError('ACCESS_DENIED', 'media revoked', 403)
            return original(context, resource, action)
        with patch.object(repo.authorization, 'require_access', side_effect=deny_media):
            with self.assertRaises(ServiceError) as error:
                repo.get_current_candidate_manifest(self.actor, self.task_id)
            self.assertEqual(error.exception.code, 'ACCESS_DENIED')
        from contextlib import closing
        with closing(repo.tasks._connect()) as db, db:
            payload = json.loads(db.execute('SELECT payload FROM plan_set_ledger WHERE id=1').fetchone()[0])
            for name, section in payload['value']:
                if name == 'candidate_events':
                    section['value'] = []
            db.execute('UPDATE plan_set_ledger SET payload=? WHERE id=1', (json.dumps(payload),))
        with self.assertRaises(ServiceError) as error:
            repo.get_current_candidate_manifest(self.actor, self.task_id)
        self.assertEqual(error.exception.code, 'STORAGE_CORRUPT')

    def test_finished_search_can_fix_verified_candidate_manifest(self):
        self.select_and_approve()
        with self.assertRaises(ServiceError) as pending:
            self.local.preview_manifest(self.actor, self.task_id)
        self.assertEqual(pending.exception.code, 'SEARCH_IN_PROGRESS')
        self.local.enqueue(self.actor, self.task_id)
        self.wait_stopped(self.local, self.task_id)
        preview = self.local.preview_manifest(self.actor, self.task_id)
        self.assertGreater(preview['candidate_count'], 0)
        self.assertEqual(preview['candidate_count'], preview['verified_count'])
        manifest, replayed = self.local.confirm_manifest(
            self.actor, self.task_id, proposal_digest=preview['proposal_digest'],
            expected_task_version=preview['expected_task_version'],
            key='fix-candidates', action='confirm')
        self.assertFalse(replayed)
        self.assertEqual(manifest['candidate_count'], preview['candidate_count'])
        self.assertEqual(self.local.get_manifest(
            self.actor, self.task_id, manifest['manifest_ref'])['proposal_digest'],
            preview['proposal_digest'])
        replay, replayed = self.local.confirm_manifest(
            self.actor, self.task_id, proposal_digest=preview['proposal_digest'],
            expected_task_version=preview['expected_task_version'],
            key='fix-candidates', action='confirm')
        self.assertTrue(replayed)
        self.assertEqual(replay['manifest_ref'], manifest['manifest_ref'])
        restored = self.make_local().preview_manifest(self.actor, self.task_id)
        self.assertTrue(restored['confirmed'])
        self.assertEqual((restored['kind'], restored['manifest_ref']),
                         ('candidate_input_manifest', manifest['manifest_ref']))

    def test_downstream_manifest_preview_only_contains_new_candidates(self):
        self.select_and_approve()
        self.local.enqueue(self.actor, self.task_id)
        self.wait_stopped(self.local, self.task_id)
        first = self.local.preview_manifest(self.actor, self.task_id)
        manifest, _ = self.local.confirm_manifest(
            self.actor, self.task_id, proposal_digest=first['proposal_digest'],
            expected_task_version=first['expected_task_version'],
            key='fix-first-candidate-batch', action='confirm')

        self.advance_task_for_refill()

        incremental = self.local.preview_manifest(self.actor, self.task_id)
        self.assertFalse(incremental['confirmed'])
        self.assertEqual(incremental['candidate_count'], 0)
        self.assertEqual(incremental['expected_task_version'],
                         first['expected_task_version'] + 1)
        self.assertNotEqual(incremental['proposal_digest'], first['proposal_digest'])
        self.assertEqual(self.local.get_manifest(
            self.actor, self.task_id, manifest['manifest_ref'])['candidate_count'],
            first['candidate_count'])
        with self.assertRaises(ServiceError) as empty:
            self.local.confirm_manifest(
                self.actor, self.task_id,
                proposal_digest=incremental['proposal_digest'],
                expected_task_version=incremental['expected_task_version'],
                key='fix-empty-incremental-batch', action='confirm')
        self.assertEqual(empty.exception.code, 'CANDIDATE_MANIFEST_EMPTY')

    def test_downstream_manifest_excludes_prior_batch_and_keeps_unseen_candidates(self):
        self.select_and_approve()
        self.local.enqueue(self.actor, self.task_id)
        self.wait_stopped(self.local, self.task_id)
        complete = self.local._candidate_manifest_proposal(self.actor, self.task_id)
        self.assertGreater(len(complete.items), 1)
        first_batch = CandidateManifestProposal(
            complete.scope, complete.operation_id, complete.plan_digest,
            complete.items[:1])
        self.local.repository.put_candidate_proposal(self.actor, first_batch)
        first_manifest, _ = self.local.repository.confirm_candidate_manifest(
            self.actor, first_batch, key='fix-partial-first-batch',
            expected_task_version=first_batch.scope.task_version, action='confirm')

        self.advance_task_for_refill()
        incremental = self.local.preview_manifest(self.actor, self.task_id)
        prior_id = first_batch.items[0].candidate_id
        incremental_ids = {item['candidate_id'] for item in incremental['candidates']}
        self.assertNotIn(prior_id, incremental_ids)
        self.assertEqual(incremental['candidate_count'], len(complete.items) - 1)
        self.assertEqual(incremental_ids,
                         {item.candidate_id for item in complete.items[1:]})
        fixed, replayed = self.local.confirm_manifest(
            self.actor, self.task_id,
            proposal_digest=incremental['proposal_digest'],
            expected_task_version=incremental['expected_task_version'],
            key='fix-incremental-batch', action='confirm')
        self.assertFalse(replayed)
        self.assertNotEqual(fixed['manifest_ref'], first_manifest.manifest_ref)
        self.assertEqual(self.local.preview_manifest(
            self.actor, self.task_id)['manifest_ref'], fixed['manifest_ref'])

    def test_one_candidate_manifest_is_published_per_task_version(self):
        self.select_and_approve()
        self.local.enqueue(self.actor, self.task_id)
        self.wait_stopped(self.local, self.task_id)
        complete = self.local._candidate_manifest_proposal(self.actor, self.task_id)
        self.local.repository.put_candidate_proposal(self.actor, complete)
        self.local.repository.confirm_candidate_manifest(
            self.actor, complete, key='fix-complete-batch',
            expected_task_version=complete.scope.task_version, action='confirm')
        competing = CandidateManifestProposal(
            complete.scope, complete.operation_id, complete.plan_digest,
            complete.items[:1])
        self.local.repository.put_candidate_proposal(self.actor, competing)
        with self.assertRaises(ServiceError) as conflict:
            self.local.repository.confirm_candidate_manifest(
                self.actor, competing, key='fix-competing-batch',
                expected_task_version=competing.scope.task_version, action='confirm')
        self.assertEqual(conflict.exception.code,
                         'CANDIDATE_MANIFEST_ALREADY_PUBLISHED')

    def test_empty_verified_candidate_manifest_cannot_be_confirmed(self):
        fixture = json.loads(self.fixture_path.read_text())
        for record in fixture['records']:
            for media in record['media']:
                media['content_digest'] = None
        self.fixture_path.write_text(json.dumps(fixture, ensure_ascii=False))
        self.select_and_approve()
        self.local.enqueue(self.actor, self.task_id)
        self.wait_stopped(self.local, self.task_id)
        preview = self.local.preview_manifest(self.actor, self.task_id)
        self.assertEqual(preview['candidate_count'], 0)
        with self.assertRaises(ServiceError) as rejected:
            self.local.confirm_manifest(
                self.actor, self.task_id, proposal_digest=preview['proposal_digest'],
                expected_task_version=preview['expected_task_version'],
                key='fix-empty-candidates', action='confirm')
        self.assertEqual(rejected.exception.code, 'CANDIDATE_MANIFEST_EMPTY')

    def test_smart_source_policy_queues_all_authorized_preconfigured_indexes(self):
        queued = self.local.auto_start(self.actor, self.task_id, key='terms:smart:1')
        self.assertEqual((queued['status'], queued['source_policy'], queued['source_count']),
                         ('queued', 'smart_authorized', 2))
        self.wait_stopped(self.local, self.task_id)
        candidates = self.local.candidates(self.actor, self.task_id)
        self.assertEqual(candidates['source_policy'], 'smart_authorized')
        self.assertEqual({row['binding_id'] for row in candidates['sources']}, {'dog-v1', 'cat-v1'})

    def test_restart_requeues_persisted_approved_plan(self):
        preview = self.local.preview(self.actor, self.task_id)
        route = preview['routes'][0]
        choices = [{'branch_id': route['branch_id'], 'binding_id': route['options'][0]['binding_id'],
                    'batch_size': 2}]
        proposed = self.local.prepare(self.actor, self.task_id,
            routing_digest=preview['routing_digest'], choices=choices)
        self.local.confirm(self.actor, self.task_id, proposal_digest=proposed['proposal_digest'],
            expected_task_version=proposed['expected_task_version'], action='confirm', key='recover-approve')
        self.local.close()
        restarted = self.make_local()
        self.wait_stopped(restarted, self.task_id)
        self.assertGreater(restarted.candidates(self.actor, self.task_id)['raw_hit_count'], 0)

    def test_approval_required_and_changed_preview_rejected(self):
        preview = self.local.preview(self.actor, self.task_id)
        with self.assertRaises(ServiceError) as error:
            self.local.tick(self.actor, self.task_id, max_requests=1)
        self.assertEqual(error.exception.code, 'PLAN_NOT_APPROVED')
        with self.assertRaises(ServiceError) as error:
            self.local.prepare(self.actor, self.task_id, routing_digest='changed', choices=[])
        self.assertEqual(error.exception.code, 'ROUTING_CHANGED')
        self.assertFalse(preview['status'] == 'approved')

    def test_guided_terms_use_registered_local_rules_and_keep_source_labels(self):
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = '找狗图片\n3张'
        body['query_spec']['grouped_expansion'] = preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': ['柯基', '金毛', '宠物狗']}])['plan']
        created, _ = self.tasks.create_task(self.actor, 'guided-task',
            confirmation(self.tasks, self.actor, key='guided-draft', body=body))
        task_id = created['task_id']
        preview = self.local.preview(self.actor, task_id)
        self.assertEqual(len(preview['routes']), 5)
        self.assertTrue(all(route['availability'] == 'available' and
                            [option['binding_id'] for option in route['options']] == ['dog-v1']
                            for route in preview['routes']))
        choices = [{'branch_id': route['branch_id'], 'binding_id': 'dog-v1', 'batch_size': 2}
                   for route in preview['routes']]
        with self.assertRaises(ServiceError) as error:
            self.local.prepare(self.actor, task_id, routing_digest=preview['routing_digest'],
                               choices=choices[:-1])
        self.assertEqual(error.exception.code, 'BRANCH_SELECTION_INCOMPLETE')
        proposed = self.local.prepare(self.actor, task_id,
            routing_digest=preview['routing_digest'], choices=choices)
        self.local.confirm(self.actor, task_id, proposal_digest=proposed['proposal_digest'],
            expected_task_version=proposed['expected_task_version'], action='confirm', key='guided-approve')
        self.local.tick(self.actor, task_id, max_requests=5)
        candidates = self.local.candidates(self.actor, task_id)
        self.assertEqual(candidates['raw_hit_count'], 10)
        self.assertEqual({item['query_text'] for item in candidates['items']},
                         {'找狗图片 3张', '狗', '狗 柯基', '狗 金毛', '狗 宠物狗'})
        self.assertTrue(all(item['binding_id'] == 'dog-v1' for item in candidates['items']))

    def test_preview_keeps_authorized_fallback_but_requires_query_encoding(self):
        for index, raw in enumerate(('城市风景', 'dogs')):
            body = json.loads(json.dumps(BODY))
            body['query_spec']['raw_request'] = raw
            created, _ = self.tasks.create_task(self.actor, f'unavailable-{index}',
                confirmation(self.tasks, self.actor, key=f'unavailable-draft-{index}', body=body))
            preview = self.local.preview(self.actor, created['task_id'])
            self.assertEqual(preview['routes'][0]['availability'], 'query_encoding_unavailable')
            self.assertEqual(preview['routes'][0]['options'], [])

    def test_legacy_fixture_stays_readable_and_bad_rule_fails_startup(self):
        data = json.loads(self.fixture_path.read_text())
        rules = data.pop('query_rules')
        self.fixture_path.write_text(json.dumps(data))
        legacy = self.make_local()
        self.assertEqual(legacy.preview(self.actor, self.task_id)['routes'][0]['availability'], 'available')
        data['query_rules'] = rules
        data['query_rules'][0]['values'] = [1.0]
        self.fixture_path.write_text(json.dumps(data))
        with self.assertRaises(ServiceError) as error:
            self.make_local()
        self.assertEqual(error.exception.code, 'LOCAL_SEARCH_UNAVAILABLE')

    def test_revocation_blocks_candidate_read(self):
        self.select_and_approve()
        self.local.tick(self.actor, self.task_id, max_requests=1)
        data = json.loads(self.source.read_text())
        data['grants'][0]['library_ids'] = []
        self.source.write_text(json.dumps(data))
        with self.assertRaises(ServiceError) as error:
            self.local.candidates(self.actor, self.task_id)
        self.assertEqual(error.exception.code, 'ACCESS_DENIED')

    def test_changed_fixture_does_not_rebind_approved_first_page(self):
        self.select_and_approve()
        data = json.loads(self.fixture_path.read_text())
        data['records'][0]['values'] = [0.8, 0.2]
        self.fixture_path.write_text(json.dumps(data))
        with self.assertRaises(ServiceError) as error:
            self.make_local().tick(self.actor, self.task_id, max_requests=1)
        self.assertEqual(error.exception.code, 'INDEX_SNAPSHOT_EXPIRED')

    def test_empty_local_corpus_is_exhausted_but_not_effective_count(self):
        data = json.loads(self.fixture_path.read_text())
        data['records'] = []
        self.fixture_path.write_text(json.dumps(data))
        self.local = self.make_local()
        self.select_and_approve()
        result = self.local.tick(self.actor, self.task_id, max_requests=2)
        self.assertTrue(result['pagination_stopped'])
        candidates = self.local.candidates(self.actor, self.task_id)
        self.assertEqual(candidates['raw_hit_count'], 0)
        self.assertIsNone(candidates['accepted_count'])

    def test_old_generation_cannot_execute_or_read_candidates(self):
        self.select_and_approve()
        with closing(sqlite3.connect(self.ledger)) as db, db:
            memory = self.tasks.repository._load(
                db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            task = memory._tasks[self.task_id]
            memory._tasks[self.task_id] = replace(task, run_generation=task.run_generation + 1)
            db.execute('UPDATE local_ledger SET payload=? WHERE id=1', (dump(memory),))
        for action in (lambda: self.local.tick(self.actor, self.task_id, max_requests=1),
                       lambda: self.local.candidates(self.actor, self.task_id)):
            with self.assertRaises(ServiceError) as error:
                action()
            self.assertEqual(error.exception.code, 'REVISION_CONFLICT')

    def test_hard_delete_task_purges_ledgers_without_storage_corrupt(self):
        # Build a task with retrieval, candidate, plan_set and browse-hit rows so
        # the cascade has something to rebuild on each sibling ledger.
        self.select_and_approve()
        self.local.tick(self.actor, self.task_id, max_requests=2)
        self.assertEqual(self.local.candidates(self.actor, self.task_id)['raw_hit_count'], 4)

        # The task was created through the conversation flow, so it carries a
        # conversation_id and the task-scoped force path must refuse it.
        with self.assertRaises(ServiceError) as linked:
            self.tasks.hard_delete_task(self.actor, self.task_id)
        self.assertEqual(linked.exception.code, 'TASK_HAS_CONVERSATION')

        # Resolve the conversation_id from the task summary and force-delete
        # through the conversation path. The SQLite cascade must rebuild every
        # sibling ledger's event invariant so a fresh process can still load the
        # file without STORAGE_CORRUPT.
        task_summary = self.tasks.get_task(self.actor, self.task_id)
        conversation_id = task_summary['conversation_id']
        cascade = self.tasks.hard_delete_conversation(self.actor, conversation_id)
        self.assertIn(self.task_id, cascade['tasks'])

        # A brand-new repository reading the same file must not raise: this is
        # the fail-closed invariant each ledger enforces on load.
        reopened = SQLiteTaskRepository(self.ledger)
        with self.assertRaises(ServiceError) as gone:
            reopened.get(self.actor, self.task_id)
        self.assertEqual(gone.exception.status, 404)
        # The sibling ledgers load cleanly (no STORAGE_CORRUPT) for a surviving
        # task created afterwards.
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = '找鸟图片'
        created, _ = self.tasks.create_task(self.actor, 'survivor',
            confirmation(self.tasks, self.actor, key='survivor-draft', body=body))
        self.assertEqual(reopened.get(self.actor, created['task_id']).task_id, created['task_id'])


if __name__ == '__main__':
    unittest.main()
