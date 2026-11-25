import unittest
from tools.enrich_local_recall_metadata import enrich


class RecallMetadataTest(unittest.TestCase):
    def test_only_matching_original_titles_are_used_and_input_stays_unchanged(self):
        fixture = {'records': [{'media': [{'media_unit_id': 'm', 'asset_version': 'v',
                                           'content_digest': 'digest'}]}]}
        manifest = {'items': [{'media_unit_id': 'm', 'asset_version': 'v',
                              'content_digest': 'digest', 'source_title': 'Original title',
                              'label_search_intent': 'unverified label', 'file': 'do-not-use'}]}
        result, report = enrich(fixture, manifest)
        self.assertEqual(result['records'][0]['search_text'], 'Original title')
        self.assertNotIn('search_text', fixture['records'][0])
        self.assertEqual(report['enriched'], 1)
        manifest['items'][0]['content_digest'] = 'stale'
        self.assertEqual(enrich(fixture, manifest)[1]['enriched'], 0)

    def test_curated_text_is_preserved(self):
        fixture = {'records': [{'media': [], 'search_text': 'curated'}]}
        result, report = enrich(fixture, {'items': []})
        self.assertEqual(result, fixture)
        self.assertEqual(report['enriched'], 0)
