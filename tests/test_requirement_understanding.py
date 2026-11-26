"""Semantic draft patches through the model protocol and persisted dialogue coordinator."""
import json
from pathlib import Path
import tempfile
import unittest

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.dialogue import DialogueCoordinator, DialogueStore
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import explicit_group_targets
from dsh_retrieval.intent_model import ModelConfig
from dsh_retrieval.model_runtime import ModelProfile, ModelRuntime
from dsh_retrieval.models import Actor
from dsh_retrieval.requirement_draft import apply_edits, project_draft, render_draft
from test_model_runtime import Transport


def edit(op, targets=None, **values):
    return dict(op=op, targets=targets or [], **{field: values.get(field) for field in
                ('label', 'scene', 'count', 'text', 'source_text')})


INITIAL = '想找一只毛驴在奔跑、一只狗在吃饭、一个兔子在睡觉的图片，每个类型要20张'
SCENES = [('毛驴', '一只毛驴在奔跑'), ('狗', '一只狗在吃饭'), ('兔子', '一个兔子在睡觉')]
EDITS = [edit('add_category', label=label, scene=scene, source_text=scene) for label, scene in SCENES]
EDITS += [edit('set_count', [label for label, _ in SCENES], count=20, source_text='每个类型要20张')]


class RequirementUnderstandingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'dialogue.sqlite'
        self.actor = Actor('test', 'owner')
        self.projects = LocalProjectAccess([('test', 'owner', 'demo', 'default')])

    def coordinator(self, outputs):
        self.transport = Transport(outputs)
        runtime = ModelRuntime((ModelProfile('text', 'llm',
            ModelConfig('fixture', 'text-v1', 'https://fixture.invalid/v1', 'secret')),), opener=self.transport)
        return DialogueCoordinator(DialogueStore(self.path), runtime, self.projects)

    def turn(self, coordinator, text, previous=None):
        return coordinator.turn(self.actor, 'demo', {
            'session_id': previous['session_id'] if previous else None,
            'expected_version': previous['version'] if previous else 0,
            'phase': previous['stage'] if previous else 'request',
            'llm_id': 'text', 'message': text, 'generate_terms_when_complete': True})

    def initial(self, coordinator):
        return self.turn(coordinator, INITIAL)

    def test_model_schema_context_initial_decomposition_and_word_confirmation_gate(self):
        coordinator = self.coordinator([{'action': 'update', 'media_type': 'image', 'requirement_edits': EDITS}])
        result = self.initial(coordinator)
        self.assertEqual(result['request']['target_count'], 60)
        self.assertEqual(result['stage'], 'terms')
        self.assertEqual(result['question'], 'review_terms')
        draft = result['request']['requirement_draft']
        self.assertEqual([(c['label'], c['scene'], c['target_count']) for c in draft['categories']],
                         [(label, scene, 20) for label, scene in SCENES])
        self.assertIn('每个类型要20张', draft['categories'][0]['evidence']['target_count'])
        schema = self.transport.calls[0]['tools'][0]['function']['parameters']['properties']['proposal']
        self.assertIn('requirement_edits', schema['properties'])
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(explicit_group_targets(result['request']['effective_request'],
                                               ['毛驴', '狗', '兔子'], 60), dict.fromkeys(['毛驴', '狗', '兔子'], 20))
        self.assertEqual(coordinator.snapshot(self.actor, 'demo', result['session_id'])['request']['requirement_draft'], draft)

    def test_new_subject_without_categories_repairs_before_saving_total(self):
        message = '找猫在窗边和狗在草地的图片，总共20张，不规定各类数量'
        coordinator = self.coordinator([
            {'action': 'update', 'subject': '猫在窗边和狗在草地',
             'target_count': 20, 'media_type': 'image'},
            {'action': 'update', 'target_count': 20, 'media_type': 'image',
             'requirement_edits': [
                 edit('add_category', label='猫', scene='猫在窗边', source_text='猫在窗边'),
                 edit('add_category', label='狗', scene='狗在草地', source_text='狗在草地')]},
        ])
        result = self.turn(coordinator, message)
        self.assertEqual(result['stage'], 'terms')
        self.assertEqual(result['request']['target_count'], 20)
        categories = result['request']['requirement_draft']['categories']
        self.assertEqual([(c['label'], c['target_count']) for c in categories], [('猫', None), ('狗', None)])
        self.assertEqual(len(self.transport.calls), 2)
        repair = json.loads(self.transport.calls[1]['messages'][1]['content'])['repair']
        self.assertIn('requirement_edits/add_category', repair['reason'])
        self.assertEqual(coordinator.snapshot(self.actor, 'demo', result['session_id'])['request'], result['request'])

    def test_multiturn_quantity_scene_condition_and_removal_keep_unaffected_categories(self):
        coordinator = self.coordinator([
            {'action': 'update', 'media_type': 'image', 'requirement_edits': EDITS},
            {'action': 'revise_terms', 'requirement_edits': [edit('set_count', ['狗'], count=30, source_text='狗改成30张')]},
            {'action': 'revise_terms', 'requirement_edits': [edit('add_condition', ['狗'], text='不要水印', source_text='狗不要水印')]},
            {'action': 'revise_terms', 'requirement_edits': [edit('update_category', ['兔子'], scene='吃草', source_text='兔子改成吃草')]},
            {'action': 'revise_terms', 'requirement_edits': [edit('remove_category', ['兔子'], source_text='兔子不要了')]},
        ])
        result = self.initial(coordinator)
        donkey = result['request']['requirement_draft']['categories'][0]
        for text, count in [('狗改成30张', 70), ('狗不要水印', 70), ('兔子改成吃草', 70), ('兔子不要了', 50)]:
            result = self.turn(coordinator, text, result)
            self.assertEqual(result['request']['target_count'], count)
            self.assertEqual(result['stage'], 'terms')
            categories = result['request']['requirement_draft']['categories']
            self.assertEqual(categories[0], donkey)
        self.assertEqual([c['label'] for c in categories], ['毛驴', '狗'])
        self.assertEqual(categories[1]['conditions'][0]['text'], '不要水印')
        self.assertNotIn('兔子', result['request']['effective_request'])
        context = json.loads(self.transport.calls[-1]['messages'][1]['content'])
        self.assertEqual(context['requirement_draft']['categories'][2]['scene'], '吃草')

    def test_repeated_known_quantity_question_repairs_once(self):
        coordinator = self.coordinator([
            {'action': 'update', 'media_type': 'image', 'requirement_edits': EDITS},
            {'action': 'clarify', 'question': '需要多少张图片？'},
            {'action': 'answer', 'reply': '三类各20张，共60张，检索词仍待确认。'},
        ])
        first = self.initial(coordinator)
        result = self.turn(coordinator, '每类数量刚才已经说了', first)
        self.assertEqual(result['action'], 'answer')
        self.assertEqual(result['request']['target_count'], 60)
        self.assertEqual(len(self.transport.calls), 3)
        repaired = json.loads(self.transport.calls[-1]['messages'][1]['content'])
        self.assertIn('already known', repaired['repair']['reason'])

    def test_second_failed_repair_does_not_overwrite_saved_request(self):
        coordinator = self.coordinator([
            {'action': 'update', 'media_type': 'image', 'requirement_edits': EDITS},
            {'action': 'clarify', 'question': '需要多少张图片？'},
            {'action': 'clarify', 'question': '需要多少张图片？'},
        ])
        first = self.initial(coordinator)
        result = self.turn(coordinator, '每类数量刚才已经说了', first)
        self.assertNotIn('需要多少张', result['reply'])
        self.assertEqual(result['request']['requirement_draft'], first['request']['requirement_draft'])
        self.assertEqual(len(self.transport.calls), 3)

    def test_partial_save_then_clarify_only_unresolved_scope(self):
        coordinator = self.coordinator([{
            'action': 'update', 'media_type': 'image', 'requirement_edits': [
                edit('add_category', label='狗', scene='狗吃饭', source_text='狗吃饭')],
            'clarification': {'field': 'scope', 'source_text': '和那个一样', 'question': '“那个”指哪个画面？'}
        }])
        result = self.turn(coordinator, '狗吃饭和那个一样的图片')
        self.assertEqual(result['stage'], 'request')
        self.assertEqual(result['reply'], '“那个”指哪个画面？')
        self.assertEqual(result['request']['requirement_draft']['categories'][0]['scene'], '狗吃饭')

    def test_scene_object_count_never_supplies_image_quantity(self):
        draft = apply_edits({}, EDITS[:3], INITIAL)
        state = project_draft({}, draft)
        self.assertIsNone(state['target_count'])
        with self.assertRaisesRegex(ValueError, 'not a scene object'):
            apply_edits(state, [edit('set_count', ['狗'], count=1, source_text='一只狗')], '一只狗')

    def test_unknown_scope_and_invented_evidence_fail_atomically(self):
        draft = apply_edits({}, EDITS, INITIAL)
        state = project_draft({}, draft)
        bad = [edit('set_count', ['狗'], count=30, source_text='狗30张'),
               edit('remove_category', ['狐狸'], source_text='狐狸不要了')]
        with self.assertRaises(ValueError):
            apply_edits(state, bad, '狗30张，狐狸不要了')
        self.assertEqual(state['requirement_draft'], draft)
        self.assertEqual(state['target_count'], 60)
        with self.assertRaises(ValueError):
            apply_edits(state, [edit('update_category', ['狗'], scene='狗在游泳', source_text='狗在游泳')], '狗在吃饭')

    def test_unallocated_total_is_preserved_without_inventing_quotas(self):
        draft = apply_edits({}, EDITS[:3], INITIAL)
        state = project_draft({}, draft, 60)
        self.assertEqual(state['category_targets'], [])
        changed = apply_edits(state, [edit('add_condition', ['狗'], text='无水印', source_text='狗无水印')], '狗无水印')
        updated = project_draft(state, changed)
        self.assertEqual(updated['target_count'], 60)
        self.assertEqual(updated['category_targets'], [])
        self.assertTrue(all(c['target_count'] is None for c in changed['categories']))

    def test_conflicting_total_or_oversized_allocation_requires_repair(self):
        draft = apply_edits({}, EDITS, INITIAL)
        with self.assertRaisesRegex(ValueError, '冲突'):
            project_draft({}, draft, 50)
        with self.assertRaises(ValueError):
            apply_edits(project_draft({}, draft), [edit('set_count', ['狗', '毛驴', '兔子'],
                count=10000, source_text='各10000张')], '各10000张')

    def test_remove_condition_preserves_other_scopes_and_restart_preserves_ids(self):
        state = project_draft({}, apply_edits({}, EDITS, INITIAL))
        draft = apply_edits(state, [edit('add_condition', ['狗', '兔子'], text='无水印', source_text='狗和兔子无水印')], '狗和兔子无水印')
        state = project_draft(state, draft)
        changed = apply_edits(state, [edit('remove_condition', ['狗'], text='无水印', source_text='狗可以有水印')], '狗可以有水印')
        self.assertEqual(changed['categories'][1]['conditions'], [])
        self.assertEqual(changed['categories'][2]['conditions'][0]['text'], '无水印')
        self.assertEqual([c['category_id'] for c in changed['categories']], [c['category_id'] for c in draft['categories']])

    def test_scene_recomposition_keeps_old_context_but_rejects_new_ungrounded_content(self):
        state = project_draft({}, apply_edits({}, EDITS, INITIAL))
        changed = apply_edits(state, [edit('update_category', ['兔子'], scene='一个兔子在吃草',
                                          source_text='兔子改成吃草')], '兔子改成吃草')
        self.assertEqual(changed['categories'][2]['scene'], '一个兔子在吃草')
        self.assertEqual(changed['categories'][2]['evidence']['scene_context'], '一个兔子在睡觉')
        with self.assertRaises(ValueError):
            apply_edits(state, [edit('update_category', ['兔子'], scene='兔子在森林里吃草',
                                    source_text='兔子改成吃草')], '兔子改成吃草')

    def test_scoped_condition_is_not_promoted_to_common_by_later_model_turn(self):
        coordinator = self.coordinator([
            {'action': 'update', 'media_type': 'image', 'requirement_edits': EDITS},
            {'action': 'revise_terms', 'constraints': '狗不要水印', 'requirement_edits': [
                edit('update_category', ['兔子'], scene='一个兔子在吃草', source_text='兔子改成吃草')]},
            {'action': 'revise_terms', 'requirement_edits': [
                edit('update_category', ['兔子'], scene='一个兔子在吃草', source_text='兔子改成吃草')]},
        ])
        first = self.initial(coordinator)
        result = self.turn(coordinator, '兔子改成吃草', first)
        state = coordinator.store.read(self.actor, 'demo', result['session_id'])
        self.assertEqual(state['constraints'], [])
        self.assertEqual(len(self.transport.calls), 3)

    def test_model_may_omit_only_nullable_operation_fields(self):
        state = project_draft({}, apply_edits({}, EDITS, INITIAL))
        draft = apply_edits(state, [{'op': 'set_count', 'targets': ['狗'],
                                    'count': 30, 'source_text': '狗30张'}], '狗30张')
        self.assertEqual(project_draft(state, draft)['target_count'], 70)
        with self.assertRaises(ValueError):
            apply_edits(state, [{'op': 'set_count', 'count': 30, 'source_text': '狗30张'}], '狗30张')

    def test_total_without_category_allocation_survives_condition_turn_via_model(self):
        coordinator = self.coordinator([
            {'action': 'update', 'media_type': 'image', 'target_count': 20, 'requirement_edits': EDITS[:3]},
            {'action': 'revise_terms', 'requirement_edits': [
                edit('add_condition', ['狗'], text='不要水印', source_text='狗不要水印')]},
        ])
        first = self.turn(coordinator, '一只毛驴在奔跑、一只狗在吃饭、一个兔子在睡觉的图，总共20张')
        result = self.turn(coordinator, '狗不要水印', first)
        self.assertEqual(result['request']['target_count'], 20)
        self.assertTrue(all(c['target_count'] is None for c in result['request']['requirement_draft']['categories']))

    def test_replacing_request_does_not_inherit_prior_categories(self):
        coordinator = self.coordinator([
            {'action': 'update', 'media_type': 'image', 'requirement_edits': EDITS},
            {'action': 'replace', 'media_type': 'image', 'requirement_edits': [
                edit('add_category', label='猫', scene='猫', source_text='猫')], 'target_count': 5},
        ])
        first = self.initial(coordinator)
        result = self.turn(coordinator, '重新找5张猫图片', first)
        self.assertEqual([c['label'] for c in result['request']['requirement_draft']['categories']], ['猫'])
        self.assertEqual(result['request']['target_count'], 5)


if __name__ == '__main__':
    unittest.main()
