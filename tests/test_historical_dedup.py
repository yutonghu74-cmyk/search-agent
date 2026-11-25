from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
import unittest
from unittest.mock import patch

import draft_helpers
import test_full_chain as chain_fixture
import test_multi_rule_pipeline as pipeline_fixture
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.historical_dedup import normalize_history, resolve, filter_preview
from dsh_retrieval.models import Actor
from dsh_retrieval.query_spec import digest


class HistoricalDedupTest(unittest.TestCase):
    def setUp(self):
        self.source = chain_fixture.FullChainTest()
        self.source.setUp(); self.addCleanup(self.source.doCleanups)
        staged, _ = self.source.prepare()
        self.staged = staged
        self.source.publish(staged)
        self.actor = self.source.actor
        self.tasks = self.source.repo.tasks
        self.ref = self.tasks.historical_options(self.actor, 'demo', 'default')[0]['reference']
        self.policy = self.tasks.resolve_historical(self.actor, 'demo', 'default', [self.ref])

    def test_only_published_owned_same_dimension_snapshots_are_selectable(self):
        self.assertEqual(normalize_history(self.policy), self.policy)
        self.assertEqual(self.tasks.historical_options(Actor(self.actor.tenant_id, 'other'),
                                                      'demo', 'default'), [])
        self.assertEqual(self.tasks.historical_options(self.actor, 'demo', 'different'), [])
        self.assertEqual(self.tasks.historical_options(self.actor, 'other-project', 'default'), [])
        bad = deepcopy(self.policy); bad['references'][0]['snapshot_id'] = 'changed'
        bad['reference_digest'] = digest(bad['references'])
        with self.assertRaises(ServiceError):
            self.tasks.resolve_historical(self.actor, 'demo', 'default', bad['references'])

    def test_confirmation_checks_reference_watermark_and_draft_creates_no_task(self):
        service = draft_helpers.make_service(self.tasks)
        body = deepcopy(draft_helpers.BODY); body['query_spec']['cross_batch_dedup'] = self.policy
        before = len(self.tasks.list_tasks(self.actor, 'demo'))
        draft, _ = service.save_draft(self.actor, 'demo', 'history-draft', body)
        self.assertEqual(len(self.tasks.list_tasks(self.actor, 'demo')), before)
        with sqlite3.connect(self.tasks.path) as db:
            memory = self.tasks._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            memory._tasks[self.source.task_id].task_version += 1
            from dsh_retrieval.adapters.task_storage import ledger_rows
            ledger_rows.write_memory(db, memory)
        with self.assertRaises(ServiceError) as error:
            service.create_task(self.actor, 'history-confirm', dict(draft_id=draft['draft_id'],
                expected_draft_version=draft['draft_version'], proposal_digest=draft['proposal_digest'], action='confirm'))
        self.assertEqual(error.exception.code, 'HISTORY_REFERENCE_CHANGED')
        self.assertEqual(len(self.tasks.list_tasks(self.actor, 'demo')), before)

    def test_filter_removes_history_once_and_reallocates_reserves(self):
        preview = self.source.repo.get_current_result_set(self.actor, self.source.task_id)
        state = self.source.pipeline.f.state()
        raw = state['result_sets'][state['current_result_sets'][self.source.task_id]][0]
        content = raw.accepted[0].content_digest
        policy = dict(mode='maintain_target', target_count=1, approved_library_ids=['library'], progression='auto')
        filtered = filter_preview(raw, {content: ('delivery-one', 'delivery-two')},
                                  digest(['refs']), policy=policy, node_mode='auto')
        self.assertEqual((filtered.accepted_count, filtered.gap, len(filtered.historical_exclusions)), (0, 1, 1))
        self.assertEqual(filter_preview(filtered, {content: ('delivery-one', 'delivery-two')},
            digest(['refs']), policy=policy, node_mode='auto'), filtered)

    def test_filtered_accepted_promotes_eligible_reserve(self):
        state = self.source.pipeline.f.state()
        raw = state['result_sets'][state['current_result_sets'][self.source.task_id]][0]
        original = raw.accepted[0]
        reserve = replace(original, candidate_id='reserve-image', candidate_ids=('reserve-image',),
                          content_digest=digest('different image'), sources=())
        raw = replace(raw, reserve=(reserve,))
        policy = dict(mode='maintain_target', target_count=1, approved_library_ids=['library'], progression='auto')
        filtered = filter_preview(raw, {original.content_digest: ('delivery',)}, digest(['refs']),
                                  policy=policy, node_mode='auto')
        self.assertEqual(filtered.accepted, (reserve,))
        self.assertEqual((filtered.gap, filtered.reserve), (0, ()))
        self.assertEqual(len(filtered.historical_exclusions), 1)

    def test_same_content_from_another_library_stays_excluded_across_rounds(self):
        state = self.source.pipeline.f.state()
        raw = state['result_sets'][state['current_result_sets'][self.source.task_id]][0]
        original = raw.accepted[0]
        policy = dict(mode='maintain_target', target_count=1,
                      approved_library_ids=['first-library', 'other-library'], progression='auto')
        blocked = {original.content_digest: ('frozen-delivery',)}
        reference = digest(['frozen-reference'])
        first = filter_preview(raw, blocked, reference, policy=policy, node_mode='auto')
        # A different candidate/source identity cannot evade the frozen content baseline.
        second_candidate = replace(original, candidate_id='other-library-image',
            candidate_ids=('other-library-image',), sources=tuple(replace(source,
                library_id='other-library') for source in original.sources))
        second = filter_preview(replace(first, accepted=(second_candidate,), gap=0),
            blocked, reference, policy=policy, node_mode='auto')
        self.assertEqual((second.accepted_count, second.gap), (0, 1))
        self.assertEqual(second.historical_reference_digest, reference)
        self.assertEqual(len(second.historical_exclusions), 1)
        self.assertIn('other-library-image', second.historical_exclusions[0][1])
        self.assertEqual(second.historical_exclusions[0][2], ('frozen-delivery',))

    def make_target(self):
        source_task = self.tasks.get(self.actor, self.source.task_id)
        with sqlite3.connect(self.tasks.path) as db:
            source_rows = db.execute('SELECT package_id,metadata,content FROM image_deliveries').fetchall()
        original_init = SQLiteTaskRepository.__init__
        source_path = self.tasks.path
        seeded = set()
        def initialize(repo, path):
            original_init(repo, path)
            if repo.path == source_path or repo.path in seeded:
                return
            seeded.add(repo.path)
            repo.create(self.actor, 'historical-seed', source_task.request_digest,
                        replace(source_task, conversation_id=None))
            with sqlite3.connect(repo.path) as db:
                db.execute('CREATE TABLE IF NOT EXISTS image_deliveries '
                           '(package_id TEXT PRIMARY KEY,metadata TEXT NOT NULL,content BLOB NOT NULL)')
                db.executemany('INSERT INTO image_deliveries VALUES(?,?,?)', source_rows)
        body = deepcopy(draft_helpers.BODY)
        body['query_spec']['target_count'] = 1
        body['query_spec']['refill_policy']['target_count'] = 1
        body['query_spec']['cross_batch_dedup'] = self.policy
        def confirmation(service, actor):
            return draft_helpers.confirmation(service, actor, key='history-target', body=body)
        target = pipeline_fixture.MultiRulePipelineTests()
        with patch.object(SQLiteTaskRepository, '__init__', initialize), patch(
                'core.test_retrieval_storage.confirmation', confirmation), patch(
                'core.test_multi_rule_approval.sha256',
                lambda value: chain_fixture.sha256(chain_fixture.IMAGE if value == b'fixture' else value)):
            target.setUp()
        self.addCleanup(target.doCleanups)
        target.gateway = chain_fixture.ImageGateway(target.f.f.auth)
        return target

    def test_confirmed_history_flows_through_cleaning_publication_and_restart(self):
        target = self.make_target()
        # Source updates after confirmation must not silently replace the frozen baseline.
        with sqlite3.connect(target.repo.tasks.path) as db:
            memory = target.repo.tasks._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            memory._tasks[self.source.task_id].task_version += 1
            from dsh_retrieval.adapters.task_storage import ledger_rows
            ledger_rows.write_memory(db, memory)
        view, _ = target.run_pipeline()
        self.assertEqual((view['accepted_count'], view['gap'], view['historical_exclusion_count']), (0, 1, 1))
        self.assertEqual(target.f.reopen().get_current_result_set(target.actor, target.task_id), view)
        page = target.repo.get_result_read_page(target.actor, target.task_id, view_type='historical')
        self.assertEqual(page['items'][0]['reasons'][-1]['reason_code'], 'HISTORICAL_EXACT_DUPLICATE')
        self.assertEqual(target.run_pipeline(), (view, True))

    def test_manual_accept_cannot_restore_historical_duplicate(self):
        target = self.make_target()
        def unavailable():
            raise RuntimeError('synthetic unknown')
        target.detector.after_detect = unavailable
        view, _ = target.run_pipeline()
        self.assertEqual(view['pending_review_count'], 1)
        candidate = target.approval.job.candidates[0]
        reviewed, _ = target.repo.review_unknown_candidate(target.actor, target.task_id,
            key='history-manual-accept', expected_task_version=view['as_of_task_version'],
            result_set_digest=view['result_set_digest'], candidate_id=candidate.candidate_id,
            asset_version=candidate.asset_version, action='accept', reason='人工确认画面符合')
        self.assertEqual((reviewed['accepted_count'], reviewed['gap'], reviewed['historical_exclusion_count']), (0, 1, 1))
        restored = target.f.reopen().get_current_result_set(target.actor, target.task_id)
        for key in ('accepted_count', 'gap', 'historical_exclusion_count', 'result_set_digest'):
            self.assertEqual(restored[key], reviewed[key])

    def test_confirmed_baseline_is_frozen_and_confirmation_replay_is_idempotent(self):
        service = draft_helpers.make_service(self.tasks)
        body = deepcopy(draft_helpers.BODY); body['query_spec']['cross_batch_dedup'] = self.policy
        draft, _ = service.save_draft(self.actor, 'demo', 'freeze-draft', body)
        confirmation = dict(draft_id=draft['draft_id'], expected_draft_version=draft['draft_version'],
                            proposal_digest=draft['proposal_digest'], action='confirm')
        created, replayed = service.create_task(self.actor, 'freeze-confirm', confirmation)
        self.assertFalse(replayed)
        with sqlite3.connect(self.tasks.path) as db:
            memory = self.tasks._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            memory._tasks[self.source.task_id].task_version += 1
            from dsh_retrieval.adapters.task_storage import ledger_rows
            ledger_rows.write_memory(db, memory)
        replay, replayed = service.create_task(self.actor, 'freeze-confirm', confirmation)
        self.assertTrue(replayed)
        self.assertEqual(replay['task_id'], created['task_id'])
        confirmed = json.loads(self.tasks.get(self.actor, created['task_id']).confirmed_proposal_json)
        self.assertEqual(confirmed['query_spec']['cross_batch_dedup'], self.policy)

    def test_deleted_baseline_blocks_reads_instead_of_disabling_dedup(self):
        target = self.make_target()
        target.run_pipeline()
        target.repo.tasks.hard_delete_task(target.actor, self.source.task_id)
        with self.assertRaises(ServiceError) as error:
            target.repo.get_current_result_set(target.actor, target.task_id)
        self.assertEqual(error.exception.code, 'HISTORY_REFERENCE_UNAVAILABLE')

    def test_http_selection_is_authorized_read_only_and_canonical(self):
        from http.server import ThreadingHTTPServer
        import threading
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from dsh_retrieval.api import create_handler
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(draft_helpers.make_service(self.tasks)))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def request(route, body=None, authenticated=True):
            headers = {'Content-Type': 'application/json'}
            if authenticated:
                headers.update({'X-Tenant-Id': self.actor.tenant_id, 'X-User-Id': self.actor.user_id})
            req = Request(f'http://127.0.0.1:{server.server_port}/v1/retrieval/projects/demo/'+route,
                          data=json.dumps(body).encode() if body is not None else None, headers=headers)
            try:
                with urlopen(req) as response: return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.load(error)
        try:
            with sqlite3.connect(self.tasks.path) as db:
                before = db.execute('PRAGMA data_version').fetchone()
                self.assertEqual(request('historical-batches', authenticated=False)[0], 401)
                status, listing = request('historical-batches?dimension_id=default')
                self.assertEqual((status, len(listing['options'])), (200, 1))
                status, resolved = request('historical-dedup',
                    {'dimension_id': 'default', 'references': [self.ref]})
                self.assertEqual((status, resolved['policy']), (200, self.policy))
                self.assertEqual(db.execute('PRAGMA data_version').fetchone(), before)
        finally:
            server.shutdown(); server.server_close(); thread.join()
