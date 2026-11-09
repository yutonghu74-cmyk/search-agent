import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import AccessContext
from dsh_retrieval.local_test_media import LocalTestMediaCorpus
from dsh_retrieval.models import Actor


class Service:
    def get_task(self, actor, task_id):
        if (actor.tenant_id, actor.user_id, task_id) != ('tenant-a', 'user-a', 'task-1'):
            raise ServiceError('NOT_FOUND', 'Task unavailable', 404)
        return {'project_id': 'demo'}


class LocalTestMediaTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'images').mkdir()
        self.content = b'\xff\xd8\xff' + b'synthetic test image'
        self.path = self.root / 'images' / 'one.jpg'
        self.path.write_bytes(self.content)
        self.row = {
            'media_unit_id': 'one', 'asset_version': 'v1',
            'source_namespace': 'wikimedia-commons', 'source_asset_id': '1',
            'file': 'images/one.jpg',
            'content_digest': 'sha256:' + hashlib.sha256(self.content).hexdigest(),
            'label_search_intent': 'dog',
            'source_url': 'https://commons.wikimedia.org/wiki/File:One.jpg',
            'license': 'CC BY 4.0',
        }
        self.manifest_path = self.root / 'manifest.json'
        self.save()

    def save(self):
        self.manifest_path.write_text(json.dumps({
            'schema': 'test-media-manifest@1', 'items': [self.row]}))

    def test_builds_fixture_and_authorized_reader_rechecks_bytes(self):
        corpus = LocalTestMediaCorpus(self.manifest_path, Service())
        out = corpus.build_fixture(self.root / 'generated')
        fixture = json.loads((out / 'fixture.json').read_text())
        self.assertEqual(fixture['records'][0]['media'][0]['content_digest'],
                         self.row['content_digest'])
        ref = corpus.items[0][1]
        context = AccessContext(Actor('tenant-a', 'user-a'), 'task-1',
                                'cleaning_input', 'current')
        with corpus.open_read(context, ref, max_bytes=100) as stream:
            self.assertEqual(stream.read(), self.content)
        preview_context = AccessContext(Actor('tenant-a', 'user-a'), 'task-1',
                                        'candidate_preview', 'current')
        with corpus.open_read(preview_context, ref, max_bytes=100) as stream:
            self.assertEqual(stream.read(), self.content)
        wrong_actor = AccessContext(Actor('tenant-a', 'another-user'), 'task-1',
                                    'candidate_preview', 'current')
        with self.assertRaises(ServiceError) as denied:
            with corpus.open_read(wrong_actor, ref, max_bytes=100):
                pass
        self.assertEqual(denied.exception.code, 'NOT_FOUND')
        self.path.write_bytes(b'\xff\xd8\xffchanged')
        with self.assertRaises(ServiceError) as changed:
            with corpus.open_read(context, ref, max_bytes=100):
                pass
        self.assertEqual(changed.exception.code, 'INPUT_SNAPSHOT_CHANGED')

    def test_rejects_path_escape_and_wrong_context(self):
        self.row['file'] = '../outside.jpg'
        self.save()
        with self.assertRaises(ServiceError) as invalid:
            LocalTestMediaCorpus(self.manifest_path, Service())
        self.assertEqual(invalid.exception.code, 'LOCAL_TEST_MEDIA_INVALID')
        self.row['file'] = 'images/one.jpg'
        self.save()
        corpus = LocalTestMediaCorpus(self.manifest_path, Service())
        context = AccessContext(Actor('tenant-a', 'user-a'), 'task-1',
                                'retrieval', 'current')
        with self.assertRaises(ServiceError) as denied:
            with corpus.open_read(context, corpus.items[0][1], max_bytes=100):
                pass
        self.assertEqual(denied.exception.code, 'ACCESS_DENIED')

    def test_thumbnail_cache_rechecks_authority_and_source(self):
        from io import BytesIO
        from unittest.mock import patch
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow required for thumbnail rendering')
        out = BytesIO()
        Image.new('RGB', (1280, 960), 'blue').save(out, format='JPEG')
        self.content = out.getvalue()
        self.path.write_bytes(self.content)
        self.row['content_digest'] = 'sha256:' + hashlib.sha256(self.content).hexdigest()
        self.save()
        corpus = LocalTestMediaCorpus(self.manifest_path, Service())
        ref = corpus.items[0][1]
        context = AccessContext(Actor('tenant-a','user-a'),'task-1','candidate_preview','current')
        thumb = corpus.thumbnail(context, ref, max_bytes=10*1024*1024)
        with Image.open(BytesIO(thumb)) as image:
            self.assertEqual(image.size, (640,480))
        with patch.object(Image, 'open', side_effect=AssertionError('cached thumbnail must not decode')):
            self.assertEqual(corpus.thumbnail(context, ref, max_bytes=10*1024*1024), thumb)
            wrong = AccessContext(Actor('tenant-a','other'),'task-1','candidate_preview','current')
            with self.assertRaises(ServiceError) as denied:
                corpus.thumbnail(wrong, ref, max_bytes=10*1024*1024)
            self.assertEqual(denied.exception.code,'NOT_FOUND')
            self.path.write_bytes(self.content+b'changed')
            with self.assertRaises(ServiceError) as changed:
                corpus.thumbnail(context, ref, max_bytes=10*1024*1024)
            self.assertEqual(changed.exception.code,'INPUT_SNAPSHOT_CHANGED')
