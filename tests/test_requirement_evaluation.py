"""Validate data-driven scenario checks independently from real model transport."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('requirement_evaluation', Path(__file__).resolve().parents[1] / 'tools/evaluate_requirement_understanding.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)

THREE = {'donkey': {'match': ['毛驴'], 'scene': ['奔跑'], 'count': 20},
         'dog': {'match': ['狗'], 'scene': ['吃饭'], 'count': 20},
         'rabbit': {'match': ['兔子'], 'scene': ['睡觉'], 'count': 20}}


def expect(categories=THREE, **values):
    base = {'categories': deepcopy(categories), 'total': 60, 'stage': 'terms', 'ask': False, 'action': None,
            'unchanged': False, 'replace': False, 'reply_forbid': [], 'may_change': None,
            'common_may_change': False, 'tracked': []}
    return {**base, **values}


def stored(pairs=(('毛驴', '毛驴奔跑'), ('狗', '狗吃饭'), ('兔子', '兔子睡觉')), count=20, total=60):
    return {'categories': [{'category_id': 'cat_' + label, 'label': label, 'scene': scene, 'target_count': count,
                            'conditions': [], 'evidence': {}} for label, scene in pairs],
            'target_count': total, 'constraints': [], 'stage': 'terms', 'question': 'review_terms'}


def result(state, **values):
    return {'action': 'update', 'question': state['question'], 'reply': '已更新。',
            'request': {'requirement_draft': {'categories': state['categories']}, 'target_count': state['target_count']},
            **values}


def codes(errors):
    return [e['code'] + ':' + e['detail'] for e in errors]


class RequirementEvaluationTest(unittest.TestCase):
    def check(self, expected, state, response=None, previous=None, previous_mapping=None, restarted='same'):
        restarted = state if restarted == 'same' else restarted
        return evaluation.check_turn(expected, state, response or result(state), previous, previous_mapping, restarted)

    def test_scene_phrase_labels_preserve_the_same_category_semantics(self):
        state = stored((('毛驴奔跑', '毛驴奔跑'), ('狗吃饭', '狗吃饭'), ('兔子睡觉', '兔子睡觉')))
        self.assertEqual(self.check(expect(), state)[1], [])

    def test_wrong_actions_quotas_and_duplicate_entities_are_rejected(self):
        state = stored()
        state['categories'][1]['scene'] = '狗睡觉'
        state['categories'][0]['target_count'] = 60
        state['categories'].append({**state['categories'][2], 'category_id': 'cat_copy'})
        found = codes(self.check(expect(), state)[1])
        self.assertIn('lost_scene:dog:吃饭', found)
        self.assertIn('wrong_category_count:donkey:60', found)
        self.assertIn('duplicate_category:rabbit', found)

    def test_category_specific_condition_cannot_leak_to_other_groups(self):
        categories = deepcopy(THREE)
        categories['dog']['conditions'] = ['水印']
        state = stored()
        state['categories'][0]['conditions'] = [{'text': '不要水印'}]
        found = codes(self.check(expect(categories, tracked=['水印']), state)[1])
        self.assertIn('condition_leak:donkey:水印', found)
        self.assertIn('lost_condition:dog:水印', found)
        # A common constraint applies to every category, so it leaks as well.
        state = stored()
        state['constraints'] = ['不要水印']
        state['categories'][1]['conditions'] = [{'text': '不要水印'}]
        found = codes(self.check(expect(categories, tracked=['水印']), state)[1])
        self.assertIn('condition_leak:rabbit:水印', found)

    def test_scene_may_keep_entity_in_label_and_only_changed_action_in_scene(self):
        categories = deepcopy(THREE)
        categories['rabbit'].update(scene=['吃草'], scene_forbid=['睡觉'])
        state = stored((('毛驴', '毛驴奔跑'), ('狗', '狗吃饭'), ('兔子', '吃草')))
        self.assertEqual(self.check(expect(categories), state)[1], [])

    def test_object_quantity_check_preserves_both_scenes_even_in_one_composition(self):
        pair = {'pair': {'match': ['狗', '兔子'], 'scene': ['一只狗', '吃饭', '两只兔子', '睡觉'], 'count': None}}
        state = stored((('狗和兔子', '一只狗正在吃饭、两只兔子在睡觉'),), count=None, total=None)
        self.assertEqual(self.check(expect(pair, total=None), state)[1], [])
        state['categories'][0]['scene'] = '一只狗正在吃饭'
        self.assertIn('lost_scene:pair:两只兔子', codes(self.check(expect(pair, total=None), state)[1]))

    def test_missing_unexpected_and_ambiguous_identities_are_separate(self):
        state = stored((('毛驴', '毛驴奔跑'), ('猫', '猫'), ('狗和兔子', '狗和兔子')))
        found = codes(self.check(expect(), state)[1])
        self.assertIn('unexpected_category:猫', found)
        self.assertIn('ambiguous_category:狗和兔子->dog/rabbit', found)
        self.assertIn('missing_category:dog', found)

    def test_scope_flags_untouched_category_changes_and_common_constraint_edits(self):
        before = stored()
        mapping, _ = self.check(expect(), before)
        after = deepcopy(before)
        after['categories'][1]['target_count'] = 30
        after['categories'][0]['scene'] = '毛驴走路'
        after['constraints'] = ['不要水印']
        after['target_count'] = 70
        categories = deepcopy(THREE)
        categories['dog']['count'] = 30
        categories['donkey']['scene'] = []
        found = codes(self.check(expect(categories, total=70, may_change=['dog']), after, None, before, mapping)[1])
        self.assertIn('unrequested_change:donkey', found)
        self.assertNotIn('unrequested_change:dog', found)
        self.assertIn('common_constraints_changed:不要水印', found)
        removed = deepcopy(before)
        del removed['categories'][2]
        found = codes(self.check(expect({k: v for k, v in THREE.items() if k != 'rabbit'}, total=[40, 60], may_change=[]),
                                 removed, None, before, mapping)[1])
        self.assertIn('unrequested_removal:rabbit', found)

    def test_questions_must_be_needed_and_specific(self):
        state = stored()
        found = codes(self.check(expect(), state, result(state, question='target_count', reply='需要多少张图片？'))[1])
        self.assertIn('unnecessary_question:target_count: 需要多少张图片？', found)
        found = codes(self.check(expect(), state, result(state, question=None, reply='一共需要几张呢'))[1])
        self.assertIn('repeated_quantity_question:一共需要几张呢', found)
        asked = result(state, question='custom_clarification', reply='需要多少张图片？')
        found = codes(self.check(expect(ask={'mentions': ['狗']}), state, asked)[1])
        self.assertIn('unspecific_question:missing 狗: 需要多少张图片？', found)
        self.assertIn('missing_question:review_terms: 已更新。', codes(self.check(expect(ask=True), state)[1]))

    def test_state_checks_use_saved_draft_restart_and_reply_claims(self):
        state = stored()
        changed = deepcopy(state)
        changed['categories'][1]['target_count'] = 25
        answer = result(changed, action='answer', reply='已开始检索。')
        found = codes(self.check(expect(action='answer', unchanged=True, reply_forbid=['已开始检索']),
                                 changed, answer, state, {})[1])
        self.assertIn('mutated_without_edit:categories', found)
        self.assertIn('false_execution_claim:已开始检索', found)
        claimed = result(state)
        claimed['request']['target_count'] = 70
        self.assertIn('response_differs_from_saved_draft:', codes(self.check(expect(), state, claimed)[1]))
        self.assertIn('restart_mismatch:', codes(self.check(expect(), state, restarted=stored(total=None))[1]))
        self.assertIn('unexpected_confirmation:confirm_terms',
                      codes(self.check(expect(), state, result(state, action='confirm_terms'))[1]))

    def test_expected_states_carry_patches_removals_and_scope_defaults(self):
        scenario = {'tracked_conditions': ['水印'], 'turns': [
            {'expect': {'categories': THREE, 'total': 60}},
            {'expect': {'category_changes': {'dog': {'count': 30}, 'rabbit': None}, 'total': 50, 'may_change': ['dog', 'rabbit']}},
            {'expect': {'total': 50}},
            {'expect': {'categories': {'cat': {'match': ['猫']}}, 'total': 10, 'replace': True}}]}
        states = evaluation.expected_states(scenario)
        self.assertEqual(set(states[1]['categories']), {'donkey', 'dog'})
        self.assertEqual(states[1]['categories']['dog']['count'], 30)
        self.assertEqual(states[1]['categories']['dog']['scene'], ['吃饭'])
        self.assertIsNone(states[0]['may_change'])
        self.assertEqual(states[2]['may_change'], [])
        self.assertIsNone(states[3]['may_change'])
        self.assertEqual(states[2]['tracked'], ['水印'])

    def test_outcomes_separate_service_protocol_save_and_semantic_failures(self):
        base = {'attempts': [{}], 'rejected_proposals': [], 'errors': [], 'reply': '已更新。'}
        self.assertEqual(evaluation.outcome({**base, 'error_code': 'MODEL_RATE_LIMIT'}), 'service_failure')
        self.assertEqual(evaluation.outcome({**base, 'error_code': 'MODEL_OUTPUT_INVALID'}), 'save_rejected')
        self.assertEqual(evaluation.outcome({**base, 'reply': '这次生成的修改未通过校验，之前的需求保持不变。'}), 'protocol_failure')
        self.assertEqual(evaluation.outcome({**base, 'errors': [{'check': 'count'}]}), 'semantic_failure')
        self.assertEqual(evaluation.outcome({**base, 'rejected_proposals': [{}]}), 'passed_after_repair')
        self.assertEqual(evaluation.outcome({**base, 'attempts': [{}, {}]}), 'passed_after_retry')
        self.assertEqual(evaluation.outcome(base), 'passed_first')
        row = {**base, 'outcome': 'semantic_failure', 'model_called': False}
        self.assertEqual(evaluation.attribution(row, 'real'), 'deterministic_path')
        self.assertEqual(evaluation.attribution({**row, 'model_called': True}, 'real'), 'model_or_context')
        self.assertEqual(evaluation.attribution(row, 'fixture'), 'backend')

    def test_fixture_targets_resolve_aliases_to_saved_category_ids(self):
        mapping = {'dog': {'category_id': 'cat_dog'}}
        fixture = {'action': 'revise_terms', 'requirement_edits': [
            {'op': 'set_count', 'targets': ['@dog', '猫'], 'count': 30, 'source_text': '狗30张'}]}
        resolved = evaluation.resolve_fixture(fixture, mapping)
        self.assertEqual(resolved['requirement_edits'][0]['targets'], ['cat_dog', '猫'])
        self.assertEqual(fixture['requirement_edits'][0]['targets'], ['@dog', '猫'])

    def test_core_scenario_file_has_24_scenarios_six_groups_and_fixture_per_turn(self):
        scenarios = evaluation.load_scenarios()
        self.assertEqual(len(scenarios), 24)
        groups = {}
        for scenario in scenarios:
            groups[scenario['group']] = groups.get(scenario['group'], 0) + 1
            self.assertTrue(all(turn.get('fixture') for turn in scenario['turns']), scenario['id'])
        self.assertEqual(sorted(groups.values()), [4] * 6)
        self.assertGreaterEqual(sum(len(s['turns']) for s in scenarios), 70)
        self.assertEqual([s['id'] for s in evaluation.load_scenarios(only={'C1', 'F4'})], ['C1', 'F4'])

    def test_final_report_includes_skipped_turns_after_last_scenario_failure(self):
        scenarios = evaluation.load_scenarios(only={'F4'})
        scenarios[0]['turns'][0]['expect']['total'] = 999
        runtime, transport = evaluation.fixture_runtime()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report.json'
            rows = evaluation.evaluate(runtime, 'fixture', scenarios, mode='fixture', transport=transport, output=output)
            saved = json.loads(output.read_text())
        self.assertEqual([r['outcome'] for r in rows], ['semantic_failure', 'not_run', 'not_run'])
        self.assertEqual(saved['turns'], rows)
        self.assertEqual(saved['summary']['turns'], 3)

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


if __name__ == '__main__':
    unittest.main()
