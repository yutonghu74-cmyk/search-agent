from copy import deepcopy
import unittest

from dsh_retrieval.recall_evaluation import summarize_run, compare_runs, prepare_review, attach_judgments, review_worksheet


class RecallEvaluationTest(unittest.TestCase):
    def fixture(self):
        scope = dict(task_id='test', revision=1, generation=2, task_version=4)
        candidates = {cid: dict(content_digest='sha256:' + content * 64,
                               verdict=verdict, disposition=disposition)
                      for cid, content, verdict, disposition in [
                          ('a', 'a', 'pass', 'duplicate'), ('b', 'a', 'pass', 'accepted'),
                          ('c', 'c', 'fail', 'failed'), ('d', 'd', 'unknown', 'review'),
                          ('e', 'e', 'pass', 'reserve')]}
        hits = [dict(scope=scope, batch=batch, branch_id=branch, hit_id=str(i), candidate_id=cid)
                for i, (batch, branch, cid) in enumerate([
                    (0, 'z', 'b'), (0, 'a', 'a'), (1, 'z', 'a'),
                    (1, 'a', 'c'), (2, 'z', 'd'), (2, 'z', 'e')])]
        return dict(schema='recall-run@1', scope=scope, requirement_digest='requirement',
                    corpus_digest='corpus', candidates=candidates, hits=hits, judgments={})

    def test_unique_credit_is_stable_across_order_and_representative_changes(self):
        run = self.fixture()
        report = summarize_run(run)
        self.assertEqual(report['totals']['new_unique_eligible'], 2)
        self.assertEqual(report['branches']['a']['selected_unique'], 1)
        self.assertEqual(report['branches']['z']['repeated_coverage'], 1)
        run['hits'].reverse()
        self.assertEqual(summarize_run(run), {**report, 'evidence_digest': summarize_run(run)['evidence_digest']})
        run['candidates']['a']['disposition'] = 'accepted'
        run['candidates']['b']['disposition'] = 'duplicate'
        self.assertEqual(summarize_run(run)['totals'], report['totals'])

    def test_unknown_fail_and_owner_exclusions_never_count_as_eligible(self):
        run = self.fixture()
        run['candidates']['e']['disposition'] = 'user_excluded'
        r = summarize_run(run)
        self.assertEqual((r['totals']['new_unique_eligible'], r['totals']['failed'],
                          r['totals']['unknown'], r['totals']['owner_excluded']), (1, 1, 1, 1))
        self.assertIsNone(r['corpus_recall'])
        self.assertIsNone(r['judged_positive_coverage'])
        self.assertIsNone(r['branches']['a']['vlm_calls'])

    def test_foreign_scope_duplicate_event_and_unverified_pass_rejected(self):
        for change in ('scope', 'duplicate', 'verdict'):
            run = deepcopy(self.fixture())
            if change == 'scope': run['hits'][0]['scope'] = {**run['scope'], 'generation': 1}
            if change == 'duplicate': run['hits'].append(deepcopy(run['hits'][0]))
            if change == 'verdict': run['candidates']['b']['verdict'] = 'unknown'
            with self.assertRaises(ValueError): summarize_run(run)

    def test_comparison_requires_frozen_corpus_requirements_and_judgments(self):
        run = self.fixture()
        self.assertEqual(compare_runs(run, run)['selected_unique_delta'], 0)
        for field in ('requirement_digest', 'corpus_digest', 'judgments'):
            other = deepcopy(run)
            other[field] = {'sha256:' + 'a' * 64: True} if field == 'judgments' else 'different'
            with self.assertRaises(ValueError): compare_runs(run, other)

    def test_partial_judgments_are_not_corpus_recall(self):
        run = self.fixture()
        run['judgments'] = {'sha256:' + 'a' * 64: True, 'sha256:' + 'f' * 64: True}
        self.assertEqual(summarize_run(run)['judged_positive_coverage'], .5)
        self.assertIsNone(summarize_run(run)['corpus_recall'])

    def test_one_vector_mapping_multiple_materials_counts_one_raw_hit(self):
        run = self.fixture()
        for hit in run['hits'][:2]:
            hit['branch_id'] = 'a'
            hit['raw_hit_id'] = 'same-vector'
        r = summarize_run(run)
        self.assertEqual(r['totals']['raw_hits'], 5)

    def test_review_is_frozen_and_unjudged_is_not_negative(self):
        run = self.fixture()
        review = prepare_review(run)
        self.assertEqual(attach_judgments(run, review)['judgments'], {})
        review['labels']['sha256:' + 'a' * 64] = True
        annotated = attach_judgments(run, review)
        self.assertEqual(summarize_run(annotated)['judged_positive_coverage'], 1)
        changed = deepcopy(run)
        changed['scope']['generation'] += 1
        with self.assertRaises(ValueError): attach_judgments(changed, review)

    def test_human_worksheet_contains_constraints_and_preview_without_model_labels(self):
        run = self.fixture()
        run['review_context'] = {'request': 'dog without watermark', 'rules': ['no watermark']}
        review = prepare_review(run)
        text = review_worksheet(review)
        self.assertIn('no watermark', text)
        self.assertIn('/cleaning/candidates/a/preview', text)
        self.assertIn('node=cleaning', text)
        self.assertNotIn('"verdict"', text)
        self.assertTrue(all(value is None for value in review['labels'].values()))

    def test_measured_usage_is_attributed_once_and_incomplete_stays_unknown(self):
        run = self.fixture()
        run['usage_expected'] = [['op', 'a', 'step']]
        run['usage'] = [dict(measurement_id='one', operation_id='op', candidate_id='a',
            step_id='step', supported=True, status='completed', step_elapsed_ms=120,
            http_attempts=2, pending_http_attempts=0, http_errors=1, http_elapsed_ms=100)]
        r = summarize_run(run)
        self.assertEqual(r['branches']['a']['vlm_calls'], 2)
        self.assertEqual(r['branches']['a']['calls_per_unique'], 2)
        self.assertEqual(r['vlm_calls'], 2)
        run['usage'][0]['pending_http_attempts'] = 1
        self.assertIsNone(summarize_run(run)['vlm_calls'])
        self.assertFalse(summarize_run(run)['branches']['a']['usage_complete'])

    def test_duplicate_usage_record_is_rejected(self):
        run = self.fixture(); run['usage_expected'] = [['op', 'a', 'step']]
        row = dict(measurement_id='same', operation_id='op', candidate_id='a', step_id='step',
                   supported=True, status='completed', step_elapsed_ms=1,
                   http_attempts=1, pending_http_attempts=0, http_errors=0, http_elapsed_ms=1)
        run['usage'] = [row, row]
        with self.assertRaises(ValueError): summarize_run(run)
