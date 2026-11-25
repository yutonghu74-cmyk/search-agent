"""Whole-video ZIP publication uses synthetic bytes and a synthetic inspector."""
from contextlib import closing
from io import BytesIO
import json
import sqlite3
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import test_whole_video_pipeline as video_fixture
from dsh_retrieval.delivery import ImageDeliveryService, digest
from dsh_retrieval.adapters.task_storage.refill import SQLiteRefillReservationRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor, RunStatus
from dsh_retrieval.video_delivery import WholeVideoDeliveryService, MediaDeliveryService


class WholeVideoDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.f = f = video_fixture.WholeVideoPipelineTest()
        if self._testMethodName == 'test_full_qualified_result_completes_on_owner_publication':
            f.fixture_target_count = 1
        f.setUp(); self.addCleanup(f.doCleanups)
        self.actor, self.task_id, self.repo = f.actor, f.task_id, f.repo
        initial, _ = f.execute_video()
        # Fixed-input video publication now persists its own lawful stop.
        if initial['gap'] and initial['stop_reason'] != 'INPUT_SCOPE_COMPLETE':
            stops = SQLiteRefillReservationRepository(f.f.path, projects=f.f.projects, authorization=f.f.auth)
            stops.record_execution_stop(self.actor, self.task_id, 'NO_PROGRESS')
            self.repo.finalize_refill_stop(self.actor, self.task_id, 'NO_PROGRESS')
        self.preview = self.repo.get_current_result_set(self.actor, self.task_id)
        self.inspections = 0
        self.invalid = False
        self.service = WholeVideoDeliveryService(self.repo, f, inspector=self.inspect)

    def inspect(self, content):
        self.inspections += 1
        if self.invalid:
            return {'media_type': 'image', 'container': 'jpg', 'duration_ms': True}
        # Synthetic inspector, deliberately not evidence of real MP4 decoding.
        return {'media_type': 'video', 'container': 'mp4', 'duration_ms': 1500,
                'private_diagnostic': 'must not be exported', 'url': 'must not be exported'}

    def prepare(self):
        return self.service.prepare(self.actor, self.task_id,
            expected_task_version=self.preview['as_of_task_version'],
            result_set_digest=self.preview['result_set_digest'])

    def publish(self, package):
        return self.service.publish(self.actor, self.task_id, package_id=package['package_id'],
            manifest_digest=package['manifest_digest'], expected_task_version=package['current_version'], action='confirm')

    def assert_code(self, code, operation):
        with self.assertRaises(ServiceError) as error:
            operation()
        self.assertEqual(error.exception.code, code)

    def test_stage_confirm_download_restart_and_replay(self):
        package, replayed = self.prepare()
        self.assertFalse(replayed)
        manifest = package['manifest']
        self.assertEqual((manifest['schema'], manifest['count_unit']), ('whole-video-delivery@1', 'distinct_video'))
        self.assertEqual((manifest['accepted_count'], manifest['packaged_file_count']), (1, 1))
        self.assertEqual(manifest['completion'], 'partial')
        self.assertEqual(manifest['video_conditions']['relation']['scope'], 'same_video')
        member = manifest['members'][0]
        self.assertEqual(member['file'], 'videos/0001.mp4')
        self.assertEqual(member['source']['unit_type'], 'whole_video')
        self.assertTrue(member['source']['media_unit_id'].startswith('whole_video_'))
        self.assertEqual(member['source']['duration_ms'], 1500)
        self.assertNotIn('url', member['source'])
        self.assertNotIn('private_diagnostic', member['source'])
        self.assertNotIn('trusted:', json.dumps(manifest))
        self.assert_code('PACKAGE_APPROVAL_REQUIRED', lambda: self.service.download(self.actor, self.task_id, package['package_id']))
        count = self.inspections
        self.assertEqual(self.prepare(), (package, True))
        self.assertEqual(self.inspections, count)
        self.repo = self.f.reopen()
        self.service = WholeVideoDeliveryService(self.repo, self.f, inspector=self.inspect)
        published, replayed = self.publish(package)
        self.assertFalse(replayed)
        self.assertEqual(self.publish(package), (published, True))
        content = self.service.download(self.actor, self.task_id, package['package_id'])
        self.assertEqual(digest(content), published['zip_digest'])
        with ZipFile(BytesIO(content)) as archive:
            self.assertEqual(archive.namelist(), ['videos/0001.mp4', 'manifest.json'])
            self.assertEqual(archive.read('videos/0001.mp4'), b'pass')
            self.assertEqual(json.loads(archive.read('manifest.json')), manifest)
        self.assertEqual(self.repo.tasks.get(self.actor, self.task_id).run_status, RunStatus.PARTIAL)
        self.assertEqual(self.repo.get_current_result_set(self.actor, self.task_id)['kind'], 'delivery')

    def test_file_inspector_can_reopen_and_download_legacy_package(self):
        package,_=self.prepare();self.publish(package)
        service=WholeVideoDeliveryService(self.repo,self.f,
            file_inspector=lambda path:self.inspect(path.read_bytes()))
        self.assertEqual(service.status(self.actor,self.task_id)['status'],'published')
        payload=service.download(self.actor,self.task_id,package['package_id'])
        with ZipFile(BytesIO(payload)) as archive:
            self.assertEqual(archive.read('videos/0001.mp4'),b'pass')

    def test_full_qualified_result_completes_on_owner_publication(self):
        package, _ = self.prepare()
        self.assertEqual((package['manifest']['completion'], package['manifest']['gap']), ('full', 0))
        self.publish(package)
        self.assertEqual(self.repo.tasks.get(self.actor, self.task_id).run_status, RunStatus.COMPLETED)

    def test_partial_still_running_cannot_be_published_prematurely(self):
        self.f.f.change_task(run_status=RunStatus.RUNNING)
        self.assert_code('FEATURE_NOT_SUPPORTED', self.prepare)

    def test_image_service_cannot_export_a_video_task(self):
        image = ImageDeliveryService(self.repo, self.f)
        self.assert_code('FEATURE_NOT_SUPPORTED', lambda: image.prepare(self.actor, self.task_id,
            expected_task_version=self.preview['as_of_task_version'], result_set_digest=self.preview['result_set_digest']))

    def test_format_or_digest_failure_never_stages_a_package(self):
        self.invalid = True
        self.assert_code('VIDEO_INSPECTION_FAILED', self.prepare)
        self.invalid = False
        for ref in self.f.content:
            self.f.content[ref] = b'changed'
        self.assert_code('INPUT_SNAPSHOT_CHANGED', self.prepare)
        with closing(sqlite3.connect(self.f.f.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM image_deliveries').fetchone()[0], 0)
        self.assertEqual(self.repo.tasks.get(self.actor, self.task_id).stage, 'refill')

    def test_missing_inspector_or_boolean_duration_does_not_pass(self):
        with self.assertRaises(ValueError):
            WholeVideoDeliveryService(self.repo, self.f, inspector=None)
        self.service.inspector = lambda _: {'media_type': 'video', 'container': 'mp4', 'duration_ms': True}
        self.assert_code('VIDEO_INSPECTION_FAILED', self.prepare)

    def test_revocation_and_generation_changes_block_publication(self):
        package, _ = self.prepare()
        self.f.f.auth.denied = True
        self.assert_code('ACCESS_DENIED', lambda: self.publish(package))
        self.f.f.auth.denied = False
        task = self.f.f.tasks.get(self.actor, self.task_id)
        self.f.f.change_task(run_generation=task.run_generation+1)
        with self.assertRaises(ServiceError):
            self.publish(package)
        self.assert_code('NOT_FOUND', lambda: self.service.status(Actor(self.actor.tenant_id, 'other'), self.task_id))

    def test_content_change_blocks_both_publication_and_download(self):
        package, _ = self.prepare()
        original = dict(self.f.content)
        self.f.content = {ref: b'changed' for ref in original}
        self.assert_code('INPUT_SNAPSHOT_CHANGED', lambda: self.publish(package))
        self.f.content = original
        self.publish(package)
        self.f.content = {ref: b'changed' for ref in original}
        self.assert_code('INPUT_SNAPSHOT_CHANGED', lambda: self.service.download(self.actor, self.task_id, package['package_id']))

    def test_inspection_drift_blocks_staged_publication(self):
        package, _ = self.prepare()
        self.service.inspector = lambda _: {'media_type': 'video', 'container': 'webm', 'duration_ms': 2000}
        self.assert_code('INPUT_SNAPSHOT_CHANGED', lambda: self.publish(package))
        self.assertEqual(self.service.status(self.actor, self.task_id)['status'], 'staged')

    def test_target_dispatch_is_fail_closed_without_inspector(self):
        service = MediaDeliveryService(self.repo, self.f)
        self.assertEqual(service.status(self.actor, self.task_id),
                         {'available': False, 'reason_code': 'VIDEO_DELIVERY_UNAVAILABLE'})
        self.assert_code('VIDEO_DELIVERY_UNAVAILABLE', lambda: service.prepare(self.actor, self.task_id,
            expected_task_version=self.preview['as_of_task_version'], result_set_digest=self.preview['result_set_digest']))
        self.assert_code('NOT_FOUND', lambda: service.status(Actor(self.actor.tenant_id, 'other'), self.task_id))
        self.service = MediaDeliveryService(self.repo, self.f, video_inspector=self.inspect)
        package, _ = self.prepare()
        self.publish(package)
        self.assertTrue(self.service.download(self.actor, self.task_id, package['package_id']))

    def test_limit_and_archive_tampering_are_rejected(self):
        with patch('dsh_retrieval.video_delivery.MAX_VIDEO_BYTES', 3):
            self.assert_code('MEDIA_LIMIT_EXCEEDED', self.prepare)
        package, _ = self.prepare()
        with closing(sqlite3.connect(self.f.f.path)) as db, db:
            db.execute('UPDATE image_deliveries SET content=? WHERE package_id=?', (b'tampered', package['package_id']))
        self.assert_code('STORAGE_CORRUPT', lambda: self.publish(package))
