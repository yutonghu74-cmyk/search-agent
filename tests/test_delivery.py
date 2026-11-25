from contextlib import contextmanager, closing
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import sqlite3
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import draft_helpers
import test_multi_rule_pipeline as pipeline_fixture
from dsh_retrieval.delivery import ImageDeliveryService
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor, RunStatus
from dsh_retrieval.result_views import build_result_page


from image_fixtures import image_bytes

IMAGE = image_bytes()


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        body = deepcopy(draft_helpers.BODY)
        body['query_spec']['target_count'] = 1
        body['query_spec']['refill_policy']['target_count'] = 1
        def confirmation(service, actor):
            return draft_helpers.confirmation(service, actor, body=body)
        self.fixture = pipeline_fixture.MultiRulePipelineTests()
        with patch('core.test_retrieval_storage.confirmation', confirmation), patch(
                'core.test_multi_rule_approval.sha256', lambda data: sha256(IMAGE if data == b'fixture' else data)):
            self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo = self.fixture.repo
        self.actor, self.task_id = self.fixture.actor, self.fixture.task_id
        authority = self.fixture.f.f.auth
        class Gateway:
            changed = False
            hook = None
            @contextmanager
            def open_read(inner, context, ref, *, max_bytes):
                authority.require_access(context, ref, 'read')
                yield BytesIO(b'changed' if inner.changed else IMAGE)
                if inner.hook:
                    inner.hook()
        self.gateway = Gateway()
        self.fixture.gateway = self.gateway
        self.preview, _ = self.fixture.run_pipeline()
        self.service = ImageDeliveryService(self.repo, self.gateway)

    def prepare(self):
        return self.service.prepare(self.actor, self.task_id,
            expected_task_version=self.preview['as_of_task_version'],
            result_set_digest=self.preview['result_set_digest'])

    def publish(self, package):
        return self.service.publish(self.actor, self.task_id,
            package_id=package['package_id'], manifest_digest=package['manifest_digest'],
            expected_task_version=package['current_version'], action='confirm')

    def assert_code(self, code, operation):
        with self.assertRaises(ServiceError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def test_delivery_node_page_uses_bounded_projection_and_rejects_stale_cursor(self):
        from unittest.mock import patch
        from dsh_retrieval.result_views import _encode_cursor
        from dsh_retrieval.models import Actor
        package, _ = self.prepare()
        def read(actor=None, cursor=None):
            return self.repo.tasks.node_read_page(actor or self.actor, self.task_id,
                'packaging', limit=1, cursor=cursor, projects=self.repo.manifests.retrieval.projects)
        with patch.object(self.repo.tasks, '_load', side_effect=AssertionError('full ledger read')):
            page = read()
        self.assertEqual(page['items'], package['manifest']['members'])
        self.assertNotIn('members', page['manifest'])
        self.assertEqual(page['item_count'], 1)
        cursor = _encode_cursor(page['output_version'], 'packaging', 0)
        self.publish(package)
        self.assert_code('NODE_VERSION_CHANGED', lambda: read(cursor=cursor))
        self.assert_code('NOT_FOUND', lambda: read(Actor(self.actor.tenant_id, 'other')))

    def test_staged_gate_publication_zip_restart_and_idempotency(self):
        package, replayed = self.prepare()
        self.assertFalse(replayed)
        self.assertEqual(package['status'], 'staged')
        member = package['manifest']['members'][0]
        self.assertEqual(self.repo.get_current_preview_candidate(
            self.actor, self.task_id, member['candidate_id'])[1], 'trusted:ref')
        self.assertEqual(package['manifest']['schema'], 'image-delivery@2')
        self.assertEqual(package['manifest']['near_duplicate_review_count'], 0)
        self.assertEqual(package['manifest']['near_duplicate_pairs'], [])
        self.assertEqual(package['manifest']['near_duplicate_decisions'], [])
        self.assertEqual(package['manifest']['dedup_policy'], 'exact-content@1')
        self.assertEqual(package['manifest']['eligible_unique_count'], 1)
        self.assertEqual(package['manifest']['exact_duplicate_count'], 0)
        self.assertEqual(package['manifest']['packaged_file_count'], 1)
        self.assertEqual(package['manifest']['packaged_file_count'],
                         len(package['manifest']['members']))
        self.assertEqual(member['candidate_ids'], [member['candidate_id']])
        self.assertEqual(len(member['retrieval_sources']), 1)
        self.assertEqual(self.repo.tasks.get(self.actor, self.task_id).run_status, RunStatus.AWAITING_INPUT)
        self.assertEqual(self.prepare(), (package, True))
        self.assert_code('PACKAGE_APPROVAL_REQUIRED', lambda: self.service.download(
            self.actor, self.task_id, package['package_id']))
        self.assertEqual(self.repo.get_current_result_set(self.actor, self.task_id)['kind'], 'preview')
        self.service = ImageDeliveryService(self.fixture.f.reopen(), self.gateway)
        published, replayed = self.publish(package)
        self.assertFalse(replayed)
        self.assertEqual(self.repo.get_current_preview_candidate(
            self.actor, self.task_id, member['candidate_id'])[1], 'trusted:ref')
        self.assertEqual(self.publish(package), (published, True))
        result = self.repo.get_current_result_set(self.actor, self.task_id)
        self.assertEqual(result['kind'], 'delivery')
        read_view = self.repo.get_current_result_read_snapshot(self.actor, self.task_id)
        self.assertEqual((read_view['kind'], read_view['result_set_digest'],
                          read_view['as_of_task_version']),
                         (result['kind'], result['result_set_digest'],
                          result['as_of_task_version']))
        task = self.repo.tasks.get(self.actor, self.task_id)
        from dsh_retrieval.progress_models import build_task_progress
        saved_progress = self.repo.tasks.list_task_read_page(
            self.actor, task.project_id, limit=20)[0]['progress']
        self.assertEqual(saved_progress, build_task_progress(task.to_summary(),
            json.loads(task.confirmed_proposal_json), result).to_view())
        self.assertEqual(self.repo.get_result_read_page(
            self.actor, self.task_id, limit=1),
            build_result_page(task.to_summary(), json.loads(task.confirmed_proposal_json),
                              result, limit=1))
        self.assertNotEqual(result['result_set_id'], self.preview['result_set_id'])
        self.assertTrue(self.repo.step_results(self.actor, self.task_id)['available'])
        content = self.service.download(self.actor, self.task_id, package['package_id'])
        self.assertEqual('sha256:' + sha256(content).hexdigest(), package['zip_digest'])
        with ZipFile(BytesIO(content)) as archive:
            self.assertEqual(archive.namelist(), ['images/0001.jpg', 'manifest.json'])
            self.assertEqual(archive.read('images/0001.jpg'), IMAGE)
            self.assertEqual(json.loads(archive.read('manifest.json')), package['manifest'])
        self.assertEqual(self.repo.tasks.get(self.actor, self.task_id).run_status, RunStatus.COMPLETED)
        with closing(sqlite3.connect(self.fixture.f.f.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM image_delivery_events').fetchone()[0], 2)

    def test_changed_media_cannot_stage_or_publish_or_download(self):
        self.gateway.changed = True
        self.assert_code('INPUT_SNAPSHOT_CHANGED', self.prepare)
        self.assertFalse(self.service.status(self.actor, self.task_id)['available'])
        self.gateway.changed = False
        package, _ = self.prepare()
        self.gateway.changed = True
        self.assert_code('INPUT_SNAPSHOT_CHANGED', lambda: self.publish(package))
        self.gateway.changed = False
        self.publish(package)
        self.gateway.changed = True
        self.assert_code('INPUT_SNAPSHOT_CHANGED', lambda: self.service.download(self.actor, self.task_id, package['package_id']))

    def test_owner_authorization_and_revocation(self):
        package, _ = self.prepare()
        self.assert_code('NOT_FOUND', lambda: self.service.status(Actor(self.actor.tenant_id, 'other'), self.task_id))
        self.fixture.f.f.auth.denied = True
        self.assert_code('ACCESS_DENIED', lambda: self.publish(package))
        self.fixture.f.f.auth.denied = False
        self.publish(package)
        self.fixture.f.f.auth.denied = True
        self.assert_code('ACCESS_DENIED', lambda: self.service.download(self.actor, self.task_id, package['package_id']))

    def test_cancel_during_io_cannot_commit_package(self):
        self.gateway.hook = lambda: self.fixture.f.f.change_task(run_status=RunStatus.CANCELLED)
        with self.assertRaises(ServiceError):
            self.prepare()
        with closing(sqlite3.connect(self.fixture.f.f.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM image_deliveries').fetchone()[0], 0)

    def test_new_generation_invalidates_staged_approval(self):
        package, _ = self.prepare()
        self.fixture.f.f.change_task(run_generation=2)
        with self.assertRaises(ServiceError):
            self.publish(package)
        self.assertFalse(self.service.status(self.actor, self.task_id)['available'])

    def test_zip_corruption_fails_closed(self):
        package, _ = self.prepare()
        with closing(sqlite3.connect(self.fixture.f.f.path)) as db, db:
            db.execute('UPDATE image_deliveries SET content=?', (b'corrupt',))
        self.assert_code('STORAGE_CORRUPT', lambda: self.publish(package))

    def test_old_cleaning_writer_remains_fenced_after_staging(self):
        self.prepare()
        with self.assertRaises(ServiceError):
            self.repo.get_current(self.actor, self.task_id, self.fixture.operation_id)

    def test_wrong_approval_digest_does_not_publish(self):
        package, _ = self.prepare()
        self.assert_code('REVISION_CONFLICT', lambda: self.publish({**package, 'manifest_digest': 'wrong'}))
        self.assertEqual(self.service.status(self.actor, self.task_id)['status'], 'staged')

    def test_wrong_version_and_missing_confirmation_are_rejected(self):
        package, _ = self.prepare()
        self.assert_code('REVISION_CONFLICT', lambda: self.service.prepare(self.actor, self.task_id,
            expected_task_version=999, result_set_digest=self.preview['result_set_digest']))
        self.assert_code('CONFIRMATION_REQUIRED', lambda: self.service.publish(self.actor, self.task_id,
            package_id=package['package_id'], manifest_digest=package['manifest_digest'],
            expected_task_version=package['current_version'], action='approve'))

    def test_auto_mode_remains_explicitly_unsupported_by_current_contract(self):
        from dsh_retrieval.proposals import normalize_proposal
        body = deepcopy(draft_helpers.BODY)
        body['node_modes']['packaging'] = 'auto'
        self.assert_code('FEATURE_NOT_SUPPORTED', lambda: normalize_proposal(body))

    def test_legally_stopped_nonempty_result_publishes_as_partial(self):
        body = deepcopy(draft_helpers.BODY)
        body['query_spec']['target_count'] = 2
        body['query_spec']['refill_policy']['target_count'] = 2
        def confirmation(service, actor):
            return draft_helpers.confirmation(service, actor, key='partial-delivery', body=body)
        partial = pipeline_fixture.MultiRulePipelineTests()
        with patch('core.test_retrieval_storage.confirmation', confirmation), patch(
                'core.test_multi_rule_approval.sha256',
                lambda data: sha256(IMAGE if data == b'fixture' else data)):
            partial.setUp()
            partial_authority = partial.f.f.auth
            class CleaningGateway:
                @contextmanager
                def open_read(inner, context, ref, *, max_bytes):
                    partial_authority.require_access(context, ref, 'read')
                    yield BytesIO(IMAGE)
            partial.gateway = CleaningGateway()
            preview, _ = partial.run_pipeline()
        self.addCleanup(partial.doCleanups)
        self.assertEqual((preview['accepted_count'], preview['gap']), (1, 1))
        task = partial.repo.tasks.get(partial.actor, partial.task_id)
        with closing(sqlite3.connect(partial.f.f.path)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS refill_execution_stops (
                task_id TEXT NOT NULL, revision INTEGER NOT NULL, generation INTEGER NOT NULL,
                stop_reason TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(task_id,revision,generation))''')
            db.execute('INSERT INTO refill_execution_stops VALUES (?,?,?,?,?)',
                       (partial.task_id, task.confirmed_revision, task.run_generation,
                        'NO_PROGRESS', '2026-10-31T00:00:00+00:00'))
        partial.repo.finalize_refill_stop(partial.actor, partial.task_id, 'NO_PROGRESS')
        authority = partial.f.f.auth
        class Gateway:
            @contextmanager
            def open_read(inner, context, ref, *, max_bytes):
                authority.require_access(context, ref, 'read')
                yield BytesIO(IMAGE)
        delivery = ImageDeliveryService(partial.repo, Gateway())
        stopped = partial.repo.tasks.get(partial.actor, partial.task_id)
        package, _ = delivery.prepare(
            partial.actor, partial.task_id, expected_task_version=stopped.task_version,
            result_set_digest=preview['result_set_digest'])
        self.assertEqual((package['manifest']['completion'], package['manifest']['gap'],
                          package['manifest']['stop_reason']),
                         ('partial', 1, 'NO_PROGRESS'))
        published, _ = delivery.publish(
            partial.actor, partial.task_id, package_id=package['package_id'],
            manifest_digest=package['manifest_digest'],
            expected_task_version=package['current_version'], action='confirm')
        self.assertEqual(published['status'], 'published')
        self.assertEqual(partial.repo.tasks.get(
            partial.actor, partial.task_id).run_status, RunStatus.PARTIAL)

    def test_http_gate_private_download_and_progress(self):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from dsh_retrieval.api import create_handler
        handler = create_handler(draft_helpers.make_service(self.repo.tasks),
                                 cleaning_reader=self.repo, delivery_service=self.service)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}/v1/retrieval/tasks/{self.task_id}'
        def request(path, body=None):
            return urlopen(Request(base + path, data=None if body is None else json.dumps(body).encode(),
                headers={'Content-Type': 'application/json', 'X-Tenant-Id': self.actor.tenant_id,
                         'X-User-Id': self.actor.user_id}), timeout=5)
        with request('/delivery/prepare', {'expected_task_version': self.preview['as_of_task_version'],
                     'result_set_digest': self.preview['result_set_digest']}) as response:
            staged = json.load(response)
        with request('/nodes/packaging/items?limit=1') as response:
            page = json.load(response)
        self.assertEqual(page['items'], staged['manifest']['members'])
        self.assertNotIn('members', page['manifest'])
        with self.assertRaises(HTTPError) as invalid:
            request('/nodes/packaging/items?limit=101')
        self.assertEqual(invalid.exception.code, 422)
        with request('/progress') as response:
            progress = json.load(response)
        self.assertEqual(progress['nodes'][4]['gate_status'], 'awaiting_approval')
        with self.assertRaises(HTTPError) as denied:
            request('/delivery/' + staged['package_id'] + '/download')
        self.assertEqual(denied.exception.code, 409)
        with request('/delivery/publish', {'package_id': staged['package_id'],
                'manifest_digest': staged['manifest_digest'], 'expected_task_version': staged['current_version'],
                'action': 'confirm'}) as response:
            self.assertEqual(json.load(response)['status'], 'published')
        with request('/progress') as response:
            progress = json.load(response)
        self.assertEqual(progress['run_status'], 'COMPLETED')
        self.assertTrue(all(node['node_status'] == 'succeeded' for node in progress['nodes']))
        with request('/delivery/' + staged['package_id'] + '/download') as response:
            self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
            self.assertEqual(response.headers['Content-Type'], 'application/zip')
            self.assertTrue(response.read().startswith(b'PK'))
