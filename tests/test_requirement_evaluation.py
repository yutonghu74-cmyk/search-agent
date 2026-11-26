"""Validate semantic checks independently from real model transport."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('requirement_evaluation', Path(__file__).resolve().parents[1] / 'tools/evaluate_requirement_understanding.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class RequirementEvaluationTest(unittest.TestCase):
    def row(self):
        return {'draft': {'categories': [
            {'label': label + action, 'scene': label + action, 'target_count': 20, 'conditions': []}
            for label, action in [('毛驴', '奔跑'), ('狗', '吃饭'), ('兔子', '睡觉')]]}}

    def test_scene_phrase_labels_preserve_the_same_category_semantics(self):
        self.assertEqual(evaluation.check_semantics('three_scenes_each_20', 0, self.row()), [])

    def test_wrong_actions_quotas_and_duplicate_entities_are_rejected(self):
        row = self.row()
        row['draft']['categories'][1]['scene'] = '狗睡觉'
        row['draft']['categories'][0]['target_count'] = 60
        row['draft']['categories'].append(dict(row['draft']['categories'][2]))
        errors = evaluation.check_semantics('three_scenes_each_20', 0, row)
        self.assertIn('wrong_scene:狗', errors)
        self.assertIn('wrong_quota:毛驴', errors)
        self.assertIn('ambiguous_category_identity', errors)

    def test_category_specific_condition_cannot_leak_to_other_groups(self):
        row = self.row()
        row['draft']['categories'][1]['target_count'] = 30
        row['draft']['categories'][0]['conditions'] = [{'text': '不要水印'}]
        errors = evaluation.check_semantics('three_scenes_each_20', 2, row)
        self.assertIn('wrong_condition_scope:毛驴', errors)
        self.assertIn('wrong_condition_scope:狗', errors)

    def test_transient_retry_preserves_body_and_failed_attempt(self):
        from dsh_retrieval.errors import ServiceError
        calls = []
        class Coordinator:
            def turn(self, actor, project, body):
                calls.append(dict(body))
                if len(calls) == 1:
                    raise ServiceError('MODEL_RATE_LIMIT', 'busy', 429)
                return {'version': 1}
        body = {'session_id': None, 'expected_version': 0, 'message': 'synthetic request'}
        attempts = []
        result = evaluation.retry_turn(Coordinator(), None, body, attempts, retry_transient=1)
        self.assertEqual(result, {'version': 1})
        self.assertEqual(calls, [body, body])
        self.assertEqual([a['status'] for a in attempts], ['failed', 'completed'])
        self.assertEqual(attempts[0]['error_code'], 'MODEL_RATE_LIMIT')

    def test_semantic_failure_is_not_retried(self):
        from dsh_retrieval.errors import ServiceError
        class Coordinator:
            def turn(self, *args):
                raise ServiceError('MODEL_OUTPUT_INVALID', 'invalid', 502)
        attempts = []
        with self.assertRaises(ServiceError):
            evaluation.retry_turn(Coordinator(), None, {}, attempts, retry_transient=2)
        self.assertEqual(len(attempts), 1)

    def test_scene_may_keep_entity_in_label_and_only_changed_action_in_scene(self):
        row = self.row()
        row['draft']['categories'][1]['target_count'] = 30
        row['draft']['categories'][1]['conditions'] = [{'text': '不要水印'}]
        row['draft']['categories'][2].update(label='兔子', scene='吃草')
        self.assertEqual(evaluation.check_semantics('three_scenes_each_20', 3, row), [])

    def test_object_quantity_check_preserves_both_scenes_even_in_one_composition(self):
        row = {'draft': {'categories': [{'label': '狗和兔子', 'scene': '一只狗正在吃饭、两只兔子在睡觉',
                                        'target_count': None, 'conditions': []}]}}
        self.assertEqual(evaluation.check_semantics('scene_objects_are_not_images', 0, row), [])
        row['draft']['categories'][0]['scene'] = '一只狗正在吃饭'
        self.assertIn('lost_scene_objects_or_actions', evaluation.check_semantics('scene_objects_are_not_images', 0, row))
