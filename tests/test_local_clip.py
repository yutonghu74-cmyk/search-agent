import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.local_clip import build_local_clip_fixture


class LocalCLIPFixtureTest(unittest.TestCase):
    def test_build_uses_real_encoder_output_and_preserves_media_identity(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'images').mkdir()
            content = b'\xff\xd8\xffsynthetic'
            (root / 'images' / 'dog.jpg').write_bytes(content)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'schema': 'test-media-manifest@1', 'items': [{
                'media_unit_id': 'unit-1', 'asset_version': 'v1',
                'source_namespace': 'wikimedia-commons', 'source_asset_id': '1',
                'file': 'images/dog.jpg',
                'content_digest': 'sha256:' + hashlib.sha256(content).hexdigest(),
                'label_search_intent': 'dog',
                'source_url': 'https://commons.wikimedia.org/wiki/File:Dog.jpg',
                'license': 'CC BY 4.0'}]}))

            class Encoder:
                encoder_ref = 'local-clip:verified-model'
                dimension = 3

                def __init__(self, model_path):
                    self.model_path = model_path

                def encode_images(self, paths, *, batch_size):
                    assert paths == [root / 'images' / 'dog.jpg']
                    return [(0.1, 0.2, 0.3)]

            out = root / 'index'
            with patch('dsh_retrieval.local_clip.LocalCLIPEncoder', Encoder):
                build_local_clip_fixture(manifest, out, root / 'model')
            fixture = json.loads((out / 'fixture.json').read_text())
            self.assertEqual(fixture['query_vectors'], [])
            self.assertNotIn('query_rules', fixture)
            self.assertEqual(fixture['records'][0]['values'], [0.1, 0.2, 0.3])
            self.assertEqual(fixture['records'][0]['media'][0]['content_digest'],
                             'sha256:' + hashlib.sha256(content).hexdigest())
            catalog = json.loads((out / 'catalog.json').read_text())
            self.assertEqual(catalog['libraries'][0]['bindings'][0]['encoder_ref'],
                             Encoder.encoder_ref)
            with self.assertRaises(ServiceError) as caught:
                build_local_clip_fixture(manifest, out, root / 'model')
            self.assertEqual(caught.exception.code, 'CLIP_INDEX_EXISTS')


if __name__ == '__main__':
    unittest.main()
