from copy import deepcopy
import unittest
from dsh_retrieval.shard_coverage import coverage_report, load_catalog


class ShardCoverageTest(unittest.TestCase):
    def setUp(self):
        self.catalog = dict(schema='image-shard-catalog@1', datasets=[dict(
            library_id='synthetic', index_version='v1', shards=[dict(
                shard_id=f's{i}', file_name=f'part{i}.db', status='ready') for i in range(7)])])
        self.endpoints = dict(schema='clip-search-endpoints@1', entries=[])

    def add(self, numbers):
        self.endpoints['entries'].append(dict(binding=dict(library_id='synthetic',
            index_version='v1', binding_id=str(len(self.endpoints['entries']))),
            shard_paths=[f'part{i}.db' for i in numbers]))

    def test_latest_two_is_incomplete_and_groups_respect_upstream_limit(self):
        self.add([5, 6])
        report = coverage_report(self.catalog, self.endpoints)
        self.assertFalse(report['complete'])
        self.assertEqual(report['datasets'][0]['missing'], ['s0', 's1', 's2', 's3', 's4'])
        self.assertEqual(report['datasets'][0]['planned_groups'],
                         [['s0', 's1', 's2', 's3'], ['s4', 's5', 's6']])

    def test_full_coverage_is_configuration_evidence_only(self):
        self.add([0, 1, 2, 3]); self.add([4, 5, 6])
        report = coverage_report(self.catalog, self.endpoints)
        self.assertTrue(report['complete'])
        self.assertEqual(report['evidence'], 'configuration_only_not_execution')

    def test_unavailable_shard_is_not_silently_removed_from_total(self):
        self.catalog['datasets'][0]['shards'][6]['status'] = 'unavailable'
        self.add([0, 1, 2, 3]); self.add([4, 5])
        row = coverage_report(self.catalog, self.endpoints)['datasets'][0]
        self.assertFalse(row['complete']); self.assertEqual(row['total'], 7)
        self.assertEqual(row['unavailable'], ['s6'])

    def test_wrong_version_unknown_file_and_oversized_group_rejected(self):
        self.add([0])
        for change in ('version', 'file', 'size'):
            value = deepcopy(self.endpoints)
            entry = value['entries'][0]
            if change == 'version': entry['binding']['index_version'] = 'v2'
            if change == 'file': entry['shard_paths'] = ['missing.db']
            if change == 'size': entry['shard_paths'] = [f'part{i}.db' for i in range(5)]
            with self.assertRaises(ValueError): coverage_report(self.catalog, value)

    def test_repeated_coverage_does_not_inflate_counts(self):
        self.add([0, 1]); self.add([0])
        self.assertEqual(coverage_report(self.catalog, self.endpoints)['datasets'][0]['configured'], 2)

    def test_duplicate_identity_and_path_escape_rejected(self):
        for field, value in [('shard_id', 's0'), ('file_name', '../part.db')]:
            catalog = deepcopy(self.catalog)
            catalog['datasets'][0]['shards'][1][field] = value
            with self.assertRaises(ValueError): load_catalog(catalog)
