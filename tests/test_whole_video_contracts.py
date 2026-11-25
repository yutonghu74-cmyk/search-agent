from dataclasses import replace
from itertools import product
import unittest

from dsh_retrieval.adapters.task_storage.sqlite import decode, encode
from dsh_retrieval.candidate_contracts import candidate_identity
from dsh_retrieval.integration_models import MediaRecord
from dsh_retrieval.multi_rule_contracts import (ApprovedCleaningJob, CleaningBinding, CleaningCandidateRef,
    CleaningCandidateVerdict, CleaningJobResult, CleaningRecipe, CleaningRule, CleaningRuleVerdict,
    DetectionStep, bytes_digest, candidate_manifest_digest)
from dsh_retrieval.query_spec import digest
from dsh_retrieval.result_contracts import build_exact_result_set
from dsh_retrieval.whole_video import (aggregate_video_condition_results, canonical_whole_video_record,
                                     require_whole_video_records)


class WholeVideoContractTest(unittest.TestCase):
    def media(self, source='source', asset='original', version='v1', body=b'video'):
        return canonical_whole_video_record(MediaRecord('vector-unit', version, source, asset,
            'trusted:video', bytes_digest(body)), source_unit_type='whole_video')

    def test_whole_video_identity_ignores_index_alias_but_preserves_origin_and_version(self):
        media = self.media()
        alias = canonical_whole_video_record(replace(media, media_unit_id='another-index-unit'),
                                             source_unit_type='whole_video')
        self.assertEqual(alias, media)
        for changed in (self.media(source='other'), self.media(asset='other'), self.media(version='v2')):
            self.assertNotEqual(candidate_identity(media), candidate_identity(changed))
        for unit in ('frame', 'clip', 'image', None):
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                canonical_whole_video_record(media, source_unit_type=unit)

    def test_conflicting_versions_and_missing_digest_rejected(self):
        for changed in (self.media(version='v2'), self.media(body=b'changed')):
            with self.assertRaises(ValueError):
                require_whole_video_records((self.media(), changed))
        with self.assertRaises(ValueError):
            canonical_whole_video_record(replace(self.media(), content_digest=None), source_unit_type='whole_video')
        with self.assertRaises(ValueError):
            require_whole_video_records((replace(self.media(), media_unit_id='frame-1'),))

    def test_all_any_truth_table_preserves_unknown_and_requires_every_condition(self):
        for op in ('all', 'any'):
            conditions = {'clauses': [{'id': 'one'}, {'id': 'two'}],
                          'relation': {'operator': op, 'scope': 'same_video'}}
            for values in product(('pass', 'fail', 'unknown'), repeat=2):
                verdicts = tuple(CleaningRuleVerdict(name, v, 'OBSERVED', None if v == 'unknown' else 'evidence')
                                 for name, v in zip(('one', 'two'), values))
                expected = ('fail' if 'fail' in values else 'unknown' if 'unknown' in values else 'pass') if op == 'all' else (
                    'pass' if 'pass' in values else 'unknown' if 'unknown' in values else 'fail')
                self.assertEqual(aggregate_video_condition_results(conditions, verdicts), expected)
            with self.assertRaises(ValueError):
                aggregate_video_condition_results(conditions, verdicts[:1])
            with self.assertRaises(ValueError):
                aggregate_video_condition_results(conditions, verdicts[::-1])

    def job_result(self):
        media = (self.media(), self.media(asset='another'), self.media(asset='distinct', body=b'distinct'))
        mapping = {candidate_identity(m): m for m in media}
        refs = tuple(CleaningCandidateRef(key, m.asset_version, m.content_digest) for key, m in mapping.items())
        recipe = CleaningRecipe('video', digest({'review': 1}), digest({'registry': 1}),
            (DetectionStep('video', 'video.detector', 'video.validator', 'video.evidence'),),
            (CleaningRule('video', 'video', 'video.content-match@1', 'eq', True),))
        binding = CleaningBinding('tenant', 'owner', 'project', 'task', 1, 2, 3, 'op',
            candidate_manifest_digest(refs), recipe.recipe_digest)
        job = ApprovedCleaningJob(binding, recipe, refs)
        result = CleaningJobResult(binding, tuple(CleaningCandidateVerdict(c.candidate_id, c.asset_version,
            'pass', (CleaningRuleVerdict('video', 'pass', 'OBSERVED', 'video.evidence'),)) for c in refs))
        return job, result, mapping

    def test_counting_requires_exact_whole_video_mapping_and_valid_current_result(self):
        job, result, mapping = self.job_result()
        preview = build_exact_result_set(job, result, 10, whole_video_media_by_candidate=mapping)
        self.assertEqual((preview.accepted_count, preview.duplicate_count, preview.gap), (2, 1, 8))
        self.assertEqual(decode(encode(preview)), preview)
        with self.assertRaises(ValueError):
            build_exact_result_set(job, result, 10)  # cannot infer videos from bare hit count
        changed = dict(mapping)
        key = next(iter(changed)); changed[key] = replace(changed[key], asset_version='v2')
        with self.assertRaises(ValueError):
            build_exact_result_set(job, result, 10, whole_video_media_by_candidate=changed)
        with self.assertRaises(ValueError):
            build_exact_result_set(job, replace(result, binding=replace(result.binding, generation=3)),
                                   10, whole_video_media_by_candidate=mapping)
