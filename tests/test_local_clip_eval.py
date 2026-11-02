import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dsh_retrieval.integration_models import QueryVector
from dsh_retrieval.local_clip_eval import evaluate_sentinels


class LocalCLIPSentinelTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'images').mkdir()
        items = []
        assets = []
        for name in ('cat', 'dog'):
            file = f'images/{name}.jpg'
            content = b'\xff\xd8\xff' + name.encode()
            (self.root / file).write_bytes(content)
            digest = 'sha256:' + hashlib.sha256(content).hexdigest()
            items.append({'media_unit_id': name, 'asset_version': 'v1',
                          'source_namespace': 'wikimedia-commons', 'source_asset_id': name,
                          'file': file, 'content_digest': digest,
                          'label_search_intent': name,
                          'source_url': f'https://commons.wikimedia.org/wiki/File:{name}.jpg',
                          'license': 'CC0'})
            assets.append({'file': file, 'content_digest': digest})
        self.manifest = self._write('manifest.json', {'schema': 'test-media-manifest@1',
                                                      'items': items})
        binding = {'binding_id': 'test-binding', 'library_id': 'test', 'index_version': 'v1',
                   'encoder_ref': 'test-encoder', 'vector_dimension': 2, 'metric': 'COSINE'}
        self.catalog = self._write('catalog.json', {'libraries': [{'bindings': [binding]}]})
        self.fixture = self._write('fixture.json', {'records': [
            {'binding_id': 'test-binding', 'media': [{'media_unit_id': 'cat',
             'content_digest': assets[0]['content_digest']}],
             'values': [1.0, 0.0]},
            {'binding_id': 'test-binding', 'media': [{'media_unit_id': 'dog',
             'content_digest': assets[1]['content_digest']}],
             'values': [0.0, 1.0]}]})
        self.judgments_data = {'schema': 'local-clip-sentinels@1', 'assets': assets,
                               'queries': [{'text': '猫', 'relevance': {'cat': True, 'dog': False}}]}
        self.judgments = self._write('judgments.json', self.judgments_data)

    def _write(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload))
        return path

    def _evaluate(self, depth=1):
        class Encoder:
            dimension = 2

            def encode_query(self, text, binding):
                assert text == '猫' and binding.binding_id == 'test-binding'
                return QueryVector('test-encoder', (1.0, 0.0))

        return evaluate_sentinels(self.manifest, self.catalog, self.fixture,
                                  self.judgments, Encoder(), depth=depth)

    def test_partial_judgments_report_ranks_not_global_precision(self):
        report = self._evaluate()['queries'][0]
        self.assertEqual(report['known_positive_found_at_depth'], 1)
        self.assertEqual(report['known_negative_at_depth'], 0)
        self.assertEqual(report['positive_ranks'], {'images/cat.jpg': 1})
        self.assertEqual(report['negative_ranks'], {'images/dog.jpg': 2})
        self.assertEqual(report['top'][0]['judgment'], True)
        self.assertEqual(report['precision_at_depth'], 1.0)
        self.assertNotIn('recall', report)

    def test_precision_only_when_entire_top_depth_is_judged(self):
        self.assertEqual(self._evaluate(depth=2)['queries'][0]['precision_at_depth'], 0.5)
        self.assertIsNone(self._evaluate(depth=3)['queries'][0]['precision_at_depth'])

    def test_changed_image_digest_is_rejected(self):
        self.judgments_data['assets'][0]['content_digest'] = 'sha256:' + '0' * 64
        self._write('judgments.json', self.judgments_data)
        with self.assertRaisesRegex(ValueError, 'missing or changed'):
            self._evaluate()

    def test_incomplete_relevance_is_rejected(self):
        del self.judgments_data['queries'][0]['relevance']['dog']
        self._write('judgments.json', self.judgments_data)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self._evaluate()

    def test_index_media_digest_mismatch_is_rejected(self):
        fixture = json.loads(self.fixture.read_text())
        fixture['records'][0]['media'][0]['content_digest'] = 'sha256:' + '0' * 64
        self._write('fixture.json', fixture)
        with self.assertRaisesRegex(ValueError, 'index media does not match'):
            self._evaluate()

    def test_index_cannot_silently_omit_a_corpus_image(self):
        fixture = json.loads(self.fixture.read_text())
        fixture['records'].pop()
        self._write('fixture.json', fixture)
        with self.assertRaisesRegex(ValueError, 'cover corpus exactly once'):
            self._evaluate()

    def test_integer_is_not_a_relevance_judgment(self):
        self.judgments_data['queries'][0]['relevance']['dog'] = 0
        self._write('judgments.json', self.judgments_data)
        with self.assertRaisesRegex(ValueError, 'incomplete or invalid'):
            self._evaluate()


if __name__ == '__main__':
    unittest.main()
