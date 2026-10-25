import tempfile
import unittest
import stat
from pathlib import Path

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.dialogue import DialogueCoordinator, DialogueStore, explicit_category_targets, resolve_count_edits
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.model_runtime import ModelRuntime, ModelProfile
from dsh_retrieval.intent_model import ModelConfig


def proposal(action, **changes):
    value = {'action': action, 'subject': None, 'target_count': None,
             'media_type': None, 'constraints': None, 'question': None}
    value.update(changes)
    return value


class FakeRuntime:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def interpret_dialogue_turn(self, state, message, llm_id, *, count_hint=None):
        self.calls.append((state, message, llm_id))
        return self.answers.pop(0)


class DialogueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'dialogue.sqlite3'
        self.actor = Actor('tenant-a', 'user-a')
        self.projects = LocalProjectAccess([('tenant-a', 'user-a', 'demo', 'default')])

    def tearDown(self):
        self.tmp.cleanup()

    def turn(self, coordinator, session_id, version, message, phase='request'):
        return coordinator.turn(self.actor, 'demo', {'session_id': session_id,
            'expected_version': version, 'message': message, 'llm_id': 'chat-llm', 'phase': phase})

    def test_term_count_double_revises_terms_without_changing_image_count(self):
        store = DialogueStore(self.path)
        session_id, _ = store.save(self.actor, 'demo', None, 0, {
            'subject': '猫、狗', 'target_count': 120, 'media_type': 'image',
            'constraints': [], 'stage': 'terms', 'question': 'custom_clarification',
            'messages': [], 'context_start': 0, 'raw_request': '找120张猫和狗图片'})
        model = FakeRuntime(proposal('revise_terms', count_edits=[
            {'target': 'terms', 'labels': [], 'operation': 'scale', 'value': 2}]))
        result = self.turn(DialogueCoordinator(store, model, self.projects), session_id, 1,
                           '检索词的数量在增加一倍', 'terms')
        self.assertEqual(result['action'], 'revise_terms')
        self.assertEqual(result['request']['target_count'], 120)
        self.assertEqual(result['question'], 'review_terms')
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(result['count_edits'][0]['operation'], 'scale')

    def test_generic_count_operations_select_groups_and_reject_conflicts(self):
        groups = [{'primary_label': '猫', 'auxiliary_terms': ['猫1', '猫2', '猫3']},
                  {'primary_label': '狗', 'auxiliary_terms': ['狗1', '狗2']}]
        edits = [{'target': 'terms', 'labels': ['猫'], 'operation': 'scale', 'value': 0.5},
                 {'target': 'images', 'labels': [], 'operation': 'add', 'value': -20}]
        self.assertEqual(resolve_count_edits(edits, 120, groups), (100, {'猫': 2, '狗': 2}))
        for invalid in ([edits[0], edits[0]], [{'target': 'terms', 'labels': ['兔'], 'operation': 'set', 'value': 2}],
                        [{'target': 'terms', 'labels': [], 'operation': 'scale', 'value': 20}],
                        [{'target': 'images', 'labels': [], 'operation': 'set', 'value': True}]):
            with self.assertRaises(ValueError):
                resolve_count_edits(invalid, 120, groups)

    def test_answer_preserves_draft_and_persists_both_sides_for_next_turn(self):
        model = FakeRuntime(proposal('update', subject='猫', target_count=5, media_type='image'),
                            proposal('answer', reply='目前还在确认需求，确认后我会给出检索词。'),
                            proposal('confirm'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = self.turn(coordinator, None, 0, '找5张猫图片')
        second = self.turn(coordinator, first['session_id'], 1, '为什么还没搜？')
        self.assertEqual(second['action'], 'answer')
        self.assertEqual(second['request'], first['request'])
        self.assertEqual(second['stage'], 'request')
        self.assertIn('还在确认需求', second['reply'])
        restarted = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        third = self.turn(restarted, first['session_id'], 2, '好，按这个来')
        self.assertEqual(third['stage'], 'terms')
        history = model.calls[-1][0]['messages']
        self.assertEqual([item['role'] for item in history], ['user', 'assistant', 'user', 'assistant'])
        self.assertEqual(history[-1]['text'], second['reply'])

    def test_current_screen_terms_are_saved_and_used_after_restart(self):
        model = FakeRuntime(proposal('update', subject='猫', target_count=5, media_type='image'),
                            proposal('confirm'), proposal('answer', reply='这组词覆盖窗边和树下场景。'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = self.turn(coordinator, None, 0, '找5张猫图片')
        self.turn(coordinator, first['session_id'], 1, '是的')
        groups = [{'primary_label': '猫', 'auxiliary_terms': ['猫在树下', '猫在窗边']}]
        coordinator.turn(self.actor, 'demo', {'session_id': first['session_id'], 'expected_version': 2,
            'phase': 'terms', 'llm_id': 'text', 'message': '这些怎么选的？',
            'current_groups': groups, 'current_target_count': 5})
        self.assertEqual(model.calls[-1][0]['current_groups'], groups)
        reopened = DialogueStore(self.path)
        self.assertEqual(reopened.read(self.actor, 'demo', first['session_id'])['current_groups'], groups)

    def test_task_history_is_persistent_and_actor_project_task_scoped(self):
        store = DialogueStore(self.path)
        store.append_task_turn(self.actor, 'demo', 'task-a', 1, '为什么选这些词？', '它们覆盖多个场景。')
        reopened = DialogueStore(self.path)
        self.assertEqual(len(reopened.task_history(self.actor, 'demo', 'task-a')), 2)
        self.assertEqual(reopened.task_history(Actor('tenant-a', 'other'), 'demo', 'task-a'), [])
        self.assertEqual(reopened.task_history(self.actor, 'other', 'task-a'), [])
        self.assertEqual(reopened.task_history(self.actor, 'demo', 'task-b'), [])

    def test_animal_category_quantities_supply_total_without_reasking(self):
        self.assertEqual(explicit_category_targets('1000只猫，200只狗，20只兔子'), [
            {'label': '猫', 'count': 1000}, {'label': '狗', 'count': 200},
            {'label': '兔子', 'count': 20}])
        model = FakeRuntime(proposal('clarify', subject='猫、狗、兔子',
                                     question='需要多少张图片？'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        result = self.turn(coordinator, None, 0, '1000只猫，200只狗，20只兔子')
        self.assertEqual(result['action'], 'update')
        self.assertEqual(result['request']['target_count'], 1220)
        self.assertEqual(result['request']['media_type'], 'image')
        self.assertEqual(result['question'], 'confirm_summary')
        self.assertIn('1000 张猫图片', result['reply'])
        self.assertIn('200 张狗图片', result['reply'])
        self.assertIn('20 张兔子图片', result['reply'])
        self.assertIn('共 1220 张', result['reply'])
        self.assertEqual(coordinator.snapshot(self.actor, 'demo', result['session_id'])['request']['target_count'], 1220)

    def test_old_session_recovers_category_total_before_yes_confirmation(self):
        store = DialogueStore(self.path)
        session_id, _ = store.save(self.actor, 'demo', None, 0, {
            'subject': '猫、狗、兔子', 'target_count': None, 'media_type': 'image',
            'constraints': [], 'stage': 'request', 'question': 'target_count',
            'messages': [{'role': 'user', 'text': '1000只猫，200只狗，20只兔子'},
                         {'role': 'user', 'text': '1000只猫，200只狗，20只兔子'}],
            'context_start': 0, 'raw_request': '1000只猫，200只狗，20只兔子\n1000只猫，200只狗，20只兔子'})
        coordinator = DialogueCoordinator(store, FakeRuntime(proposal('clarify', question='需要多少张图片？')),
                                          self.projects)
        snapshot = coordinator.snapshot(self.actor, 'demo', session_id)
        self.assertEqual(snapshot['request']['target_count'], 1220)
        self.assertEqual(snapshot['question'], 'confirm_summary')
        result = self.turn(coordinator, session_id, 1, '是的')
        self.assertEqual(result['action'], 'confirm')
        self.assertEqual(result['stage'], 'terms')
        self.assertEqual(result['request']['target_count'], 1220)
        self.assertEqual(store.read(self.actor, 'demo', session_id)['target_count'], 1220)

    def test_model_words_are_used_but_verified_summary_and_gates_stay_authoritative(self):
        model = FakeRuntime(
            proposal('update', subject='猫', media_type='image', reply='猫图想要多少张？'),
            proposal('update', target_count=5, reply='数量补充好了，请看看我是否理解准确。'),
            proposal('update', subject='狗', target_count=5, reply='我已开始检索。'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = self.turn(coordinator, None, 0, '找猫图')
        self.assertEqual(first['reply'], '猫图想要多少张？')
        second = self.turn(coordinator, first['session_id'], 1, '5张')
        self.assertIn('数量补充好了', second['reply'])
        self.assertIn('找 5 张猫图片', second['reply'])
        self.assertIn('对吗？', second['reply'])
        third = self.turn(coordinator, first['session_id'], 2, '改成5张狗图片')
        self.assertNotIn('我已开始检索', third['reply'])
        self.assertIn('找 5 张狗图片', third['reply'])

    def test_multiturn_completion_confirmation_and_restart(self):
        model = FakeRuntime(proposal('update', subject='狗', media_type='image'),
                            proposal('update', target_count=5), proposal('confirm'),
                            proposal('revise_terms'), proposal('confirm_terms'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = self.turn(coordinator, None, 0, '找狗图片')
        self.assertEqual(first['question'], 'target_count')
        second = self.turn(coordinator, first['session_id'], 1, '5张')
        self.assertEqual(second['question'], 'confirm_summary')
        self.assertIn('5 张狗图片', second['reply'])
        self.assertEqual(second['request']['raw_request'], '找狗图片\n5张')
        restarted = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        self.assertEqual(restarted.snapshot(self.actor, 'demo', first['session_id'])['version'], 2)
        third = self.turn(restarted, first['session_id'], 2, '就是这个意思')
        self.assertEqual(third['stage'], 'terms')
        self.assertEqual(third['request']['target_count'], 5)
        self.assertEqual(third['request']['raw_request'], '找狗图片\n5张')
        fourth = self.turn(restarted, first['session_id'], 3, '换成狗的动作词', 'terms')
        self.assertEqual(fourth['action'], 'revise_terms')
        self.assertEqual(fourth['stage'], 'terms')
        fifth = self.turn(restarted, first['session_id'], 4, '这些词可以了', 'terms')
        self.assertEqual(fifth['action'], 'confirm_terms')

    def test_new_topic_replaces_unconfirmed_request(self):
        model = FakeRuntime(proposal('update', subject='老虎', target_count=100, media_type='image'),
                            proposal('replace', subject='猫', target_count=3, media_type='image'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = self.turn(coordinator, None, 0, '找100张老虎图片')
        second = self.turn(coordinator, first['session_id'], 1, '改成3张猫图片')
        self.assertEqual(second['request']['subject'], '猫')
        self.assertEqual(second['request']['target_count'], 3)
        self.assertEqual(second['request']['raw_request'], '改成3张猫图片')
        self.assertNotIn('老虎', second['summary'])
        stored = coordinator.store.read(self.actor, 'demo', first['session_id'])
        self.assertEqual(stored['context_start'], 2)
        self.assertEqual(stored['messages'][stored['context_start']:][0]['text'], '改成3张猫图片')

    def test_old_version_and_other_user_are_denied(self):
        coordinator = DialogueCoordinator(DialogueStore(self.path), FakeRuntime(proposal('update', subject='狗')), self.projects)
        first = self.turn(coordinator, None, 0, '找狗')
        with self.assertRaises(ServiceError) as stale:
            self.turn(coordinator, first['session_id'], 0, '5张')
        self.assertEqual(stale.exception.code, 'DIALOGUE_CONFLICT')
        with self.assertRaises(ServiceError) as other:
            coordinator.snapshot(Actor('tenant-a', 'user-b'), 'demo', first['session_id'])
        self.assertEqual(other.exception.code, 'PROJECT_ACCESS_DENIED')

    def test_model_cannot_confirm_without_prior_summary_question(self):
        coordinator = DialogueCoordinator(DialogueStore(self.path), FakeRuntime(proposal('confirm')), self.projects)
        result = self.turn(coordinator, None, 0, '是')
        self.assertEqual(result['action'], 'clarify')
        self.assertEqual(result['stage'], 'request')
        self.assertEqual(result['question'], 'media_type')

    def test_custom_question_cannot_be_mistaken_for_summary_approval(self):
        model = FakeRuntime(proposal('update', subject='狗', target_count=5, media_type='image'),
                            proposal('clarify', question='要改成猫吗？'), proposal('confirm'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = self.turn(coordinator, None, 0, '找5张狗图片')
        second = self.turn(coordinator, first['session_id'], 1, '不太对')
        self.assertEqual(second['question'], 'custom_clarification')
        third = self.turn(coordinator, first['session_id'], 2, '是')
        self.assertEqual(third['action'], 'clarify')
        self.assertEqual(third['stage'], 'request')

    def test_model_count_must_refer_to_images_not_recall_terms(self):
        runtime = ModelRuntime([ModelProfile('chat-llm', 'llm',
            ModelConfig('fixture', 'test', 'https://fixture.invalid/v1', 'not-used'))])
        runtime._request = lambda *args, **kwargs: proposal('update', target_count=5)
        state = {'stage': 'request', 'question': None, 'messages': [], 'target_count': None}
        self.assertIsNone(runtime.interpret_dialogue_turn(state, '要五个召回词', 'chat-llm')['target_count'])
        self.assertEqual(runtime.interpret_dialogue_turn(state, '找5张图片', 'chat-llm')['target_count'], 5)

    def test_invalid_model_output_becomes_safe_clarification(self):
        runtime = ModelRuntime([ModelProfile('chat-llm', 'llm',
            ModelConfig('fixture', 'test', 'https://fixture.invalid/v1', 'not-used'))])
        runtime._request = lambda *args, **kwargs: {'action': 'update', 'target_count': 5, 'approved': True}
        coordinator = DialogueCoordinator(DialogueStore(self.path), runtime, self.projects)
        result = self.turn(coordinator, None, 0, '5张狗图片')
        self.assertEqual(result['action'], 'clarify')
        self.assertIn('需求保持不变', result['reply'])
        self.assertEqual(coordinator.store.connection.execute('SELECT count(*) FROM dialogue_sessions').fetchone()[0], 1)

    def test_terms_phase_maps_natural_update_and_confirm(self):
        model = FakeRuntime(proposal('update', subject='盒马', target_count=100, media_type='image'),
                            proposal('confirm'), proposal('update', constraints='不同姿势、横图'),
                            proposal('confirm'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = self.turn(coordinator, None, 0, '找100张盒马图片')
        self.turn(coordinator, first['session_id'], 1, '是的')
        revision = self.turn(coordinator, first['session_id'], 2, '要横图', 'terms')
        self.assertEqual(revision['action'], 'revise_terms')
        self.assertEqual(revision['stage'], 'terms')
        approved = self.turn(coordinator, first['session_id'], 3, '可以，就这些词', 'terms')
        self.assertEqual(approved['action'], 'confirm_terms')

    def test_terms_same_subject_replace_is_treated_as_revision(self):
        model = FakeRuntime(proposal('update', subject='盒马', target_count=100, media_type='image'),
                            proposal('confirm'), proposal('replace', subject='盒马不同姿势'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = self.turn(coordinator, None, 0, '找100张盒马图片')
        self.turn(coordinator, first['session_id'], 1, '是的')
        result = self.turn(coordinator, first['session_id'], 2, '检索词换成盒马不同姿势', 'terms')
        self.assertEqual(result['action'], 'revise_terms')
        self.assertEqual(result['stage'], 'terms')

    def test_older_persisted_conversation_without_context_start_still_resumes(self):
        store = DialogueStore(self.path)
        session_id, version = store.save(self.actor, 'demo', None, 0, {
            'subject': '狗', 'target_count': 5, 'media_type': 'image',
            'constraints': [], 'stage': 'terms', 'question': None,
            'messages': [{'role': 'user', 'text': '找5张狗图片'}]})
        coordinator = DialogueCoordinator(store, FakeRuntime(proposal('update', constraints='横图')), self.projects)
        result = self.turn(coordinator, session_id, version, '要横图', 'terms')
        self.assertEqual(result['action'], 'revise_terms')
        self.assertEqual(result['version'], 2)

    def test_changed_count_setting_requires_new_summary_confirmation(self):
        model = FakeRuntime(proposal('update', subject='狗', target_count=5, media_type='image'),
                            proposal('confirm'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = self.turn(coordinator, None, 0, '找5张狗图片')
        second = coordinator.turn(self.actor, 'demo', {'session_id': first['session_id'],
            'expected_version': 1, 'message': '是', 'llm_id': 'chat-llm',
            'phase': 'request', 'count_hint': 8})
        self.assertEqual(second['stage'], 'request')
        self.assertEqual(second['question'], 'confirm_summary')
        self.assertEqual(second['request']['target_count'], 8)

    def test_new_ledger_is_private(self):
        DialogueStore(self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)


if __name__ == '__main__':
    unittest.main()
