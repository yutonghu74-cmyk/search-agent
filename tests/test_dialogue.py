import tempfile
import unittest
import stat
from unittest.mock import patch
from pathlib import Path

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.dialogue import (DialogueCoordinator, DialogueStore, explicit_category_targets,
                                    explicit_image_count_update, explicit_simple_image_request,
                                    explicit_scene_request, resolve_count_edits)
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
    def test_each_type_quantity_initial_and_followup(self):
        subject = '一只毛驴在奔跑、一只狗在吃饭、一个兔子在睡觉'
        expected = [{'label': label, 'count': 20} for label in subject.split('、')]
        self.assertEqual(explicit_category_targets(subject + '的图，每个类型要20张'), expected)
        self.assertEqual(explicit_category_targets('每个类型要20张', subject), expected)
        self.assertEqual(explicit_category_targets('每张图20只狗'), [])
        self.assertEqual(explicit_category_targets('每个类型要20张'), [])
        for initial in (True, False):
            model = FakeRuntime(proposal('update', subject=subject, media_type='image'),
                                proposal('update', target_count=60))
            coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
            if initial:
                result = self.turn(coordinator, None, 0, subject + '的图，每个类型要20张')
            else:
                first = self.turn(coordinator, None, 0, subject + '的图')
                result = self.turn(coordinator, first['session_id'], first['version'], '每个类型要20张')
            self.assertEqual(result['request']['target_count'], 60)
            state = coordinator.store.read(self.actor, 'demo', result['session_id'])
            self.assertEqual(state['category_targets'], expected)
            self.assertNotEqual(result['question'], 'target_count')

    def test_transcript_failure_rolls_back_draft_and_version(self):
        store = DialogueStore(self.path)
        coordinator = DialogueCoordinator(store, FakeRuntime(proposal('update', subject='猫', media_type='image')),
                                          self.projects)
        first = self.turn(coordinator, None, 0, '找猫图片')
        state = store.read(self.actor, 'demo', first['session_id'])
        changed = {**state, 'subject': '狗'}
        with patch.object(store, '_append_messages', side_effect=RuntimeError('fixture write failure')):
            with self.assertRaises(RuntimeError):
                store.save(self.actor, 'demo', first['session_id'], first['version'], changed,
                           new_messages=[{'role': 'user', 'text': '改成狗'}])
        restored = store.read(self.actor, 'demo', first['session_id'])
        self.assertEqual(restored['version'], first['version'])
        self.assertEqual(restored['subject'], '猫')
        self.assertEqual(len(store.messages(self.actor, 'demo', first['session_id'])), 2)

    def test_long_dialogue_keeps_full_transcript_bounded_context_and_latest_facts(self):
        model = FakeRuntime(*[proposal('update', subject='雪地里的小狗', media_type='image',
                                      constraints=f'条件{index}') for index in range(40)])
        store = DialogueStore(self.path)
        coordinator = DialogueCoordinator(store, model, self.projects)
        current = None
        for index in range(40):
            current = self.turn(coordinator, current['session_id'] if current else None,
                                current['version'] if current else 0, f'条件说明{index}：'+('补充描述' * 30))
        stored = store.read(self.actor, 'demo', current['session_id'])
        self.assertEqual(len(stored['messages']), 30)
        self.assertEqual(stored['message_count'], 80)
        self.assertLessEqual(len(current['request']['raw_request']), 2000)
        self.assertIn('条件39', current['request']['effective_request'])
        self.assertEqual(store.list(self.actor, 'demo')[0]['message_count'], 80)
        reopened = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        transcript = reopened.snapshot(self.actor, 'demo', current['session_id'])['messages']
        self.assertEqual(len(transcript), 80)
        self.assertTrue(transcript[0]['text'].startswith('条件说明0：'))
        with self.assertRaises(ServiceError):
            store.save(self.actor, 'demo', current['session_id'], 1, stored,
                       new_messages=[{'role': 'user', 'text': '过期写入'}])
        self.assertEqual(len(store.messages(self.actor, 'demo', current['session_id'])), 80)
        result = store.archive_to_task(self.actor, 'demo', current['session_id'], 'task-transcript')
        self.assertEqual(result['message_count'], 80)
        self.assertEqual(len(store.task_origin(self.actor, 'demo', 'task-transcript')), 80)
        self.assertEqual(store.connection.execute('SELECT COUNT(*) FROM dialogue_transcript').fetchone()[0], 0)

    def test_legacy_transcript_is_seeded_on_next_turn_and_stays_owner_scoped(self):
        store = DialogueStore(self.path)
        coordinator = DialogueCoordinator(store, FakeRuntime(
            proposal('update', subject='猫', media_type='image'),
            proposal('answer', reply='可以先讨论画面要求。')), self.projects)
        first = self.turn(coordinator, None, 0, '找猫图片')
        store.connection.execute('DELETE FROM dialogue_transcript')
        store.connection.commit()
        second = self.turn(coordinator, first['session_id'], first['version'], '先聊聊场景')
        self.assertEqual(len(coordinator.snapshot(self.actor, 'demo', second['session_id'])['messages']), 4)
        with self.assertRaises(ServiceError):
            store.messages(Actor('tenant-a', 'other'), 'demo', second['session_id'])

    def test_natural_clarification_and_category_constraints_are_preserved(self):
        model = FakeRuntime(proposal('update', subject='小猫', media_type='image', reply='你准备收集几幅？'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = coordinator.turn(self.actor, 'demo', {'session_id': None, 'expected_version': 0,
            'phase': 'request', 'llm_id': 'chat-llm', 'message': '我想收集小猫的画面',
            'generate_terms_when_complete': True})
        self.assertEqual(first['reply'], '你准备收集几幅？')
        self.assertEqual(first['reply_source'], 'model')
        state = {'subject': '猫、狗', 'target_count': 30, 'constraints': ['无水印'],
                 'category_targets': [{'label': '猫', 'count': 10}, {'label': '狗', 'count': 20}]}
        self.assertIn('无水印', coordinator._summary(state))

    def test_scene_object_count_does_not_invent_image_target_and_quantity_reply_is_fast(self):
        model = FakeRuntime()
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = coordinator.turn(self.actor, 'demo', {
            'session_id': None, 'expected_version': 0, 'phase': 'request', 'llm_id': 'chat-llm',
            'message': '一只站在树上的松鼠', 'generate_terms_when_complete': True})
        self.assertEqual(first['question'], 'target_count')
        self.assertIsNone(first['request']['target_count'])
        self.assertEqual(first['request']['subject'], '一只站在树上的松鼠')
        self.assertEqual(first['reply'], '需要多少张图片？')
        second = coordinator.turn(self.actor, 'demo', {
            'session_id': first['session_id'], 'expected_version': first['version'],
            'phase': 'request', 'llm_id': 'chat-llm', 'message': '12张',
            'generate_terms_when_complete': True})
        self.assertEqual(second['request']['target_count'], 12)
        self.assertEqual(second['stage'], 'terms')
        self.assertEqual(second['question'], 'review_terms')
        self.assertEqual(model.calls, [])
        self.assertNotIn('task_id', second)
        for text in ('一只松鼠多少钱', '一个人为什么会飞', '一只松鼠的图片，12张', '一只没有水印的松鼠'):
            self.assertIsNone(explicit_scene_request(text))

    def test_complete_new_image_request_needs_no_model_and_stays_unconfirmed(self):
        model = FakeRuntime()
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        result = coordinator.turn(self.actor, 'demo', {
            'session_id': None, 'expected_version': 0, 'phase': 'request', 'llm_id': 'chat-llm',
            'message': '想要松鼠和鸽子的照片，12张', 'generate_terms_when_complete': True})
        self.assertEqual(model.calls, [])
        self.assertEqual(result['stage'], 'terms')
        self.assertEqual(result['question'], 'review_terms')
        self.assertEqual(result['request']['subject'], '松鼠和鸽子')
        self.assertEqual(result['request']['target_count'], 12)
        self.assertEqual(result['request']['raw_request'], '想要松鼠和鸽子的照片，12张')
        self.assertNotIn('task_id', result)
        for message in ('想要没有水印的兔子照片，20张', '想要兔子的照片，至少20张',
                        '想要和刚才一样的照片，20张', '想要兔子照片，20张，不要老鹰',
                        '想要兔子照片，20000张'):
            with self.subTest(message=message):
                self.assertIsNone(explicit_simple_image_request(message))

    def test_literal_object_scene_and_explicit_image_count_skip_redundant_inference(self):
        for text, subject, count in [('一只骆驼的照片，5张', '一只骆驼', 5),
                                     ('找一只骆驼的照片，5张', '一只骆驼', 5),
                                     ('两个人的图片，12张', '两个人', 12)]:
            model = FakeRuntime()
            coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
            value = coordinator.turn(self.actor, 'demo', {'session_id': None, 'expected_version': 0,
                'phase': 'request', 'llm_id': 'chat-llm', 'message': text,
                'generate_terms_when_complete': True})
            self.assertEqual(value['request']['subject'], subject)
            self.assertEqual(value['request']['target_count'], count)
            self.assertEqual(value['request']['raw_request'], text)
            self.assertEqual(value['stage'], 'terms'); self.assertNotIn('task_id', value)
            self.assertEqual(model.calls, [])
        for text in ['一只骆驼的照片', '一只骆驼的照片，至少5张', '一只没有水印的骆驼照片，5张',
                     '只要骆驼的照片，5张', '一只骆驼和一只狗的照片，各5张', '一个什么人的照片，5张',
                     '一只骆驼的照片，5张，不要水印', '一只骆驼的照片，20000张']:
            self.assertIsNone(explicit_simple_image_request(text), text)

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

    def test_dialogue_sessions_list_start_time_and_can_be_deleted(self):
        store = DialogueStore(self.path)
        coordinator = DialogueCoordinator(store, FakeRuntime(), self.projects)
        result = self.turn(coordinator, None, 0, '100张猫，100张狗')
        items = store.list(self.actor, 'demo')
        self.assertEqual(items[0]['session_id'], result['session_id'])
        self.assertEqual(items[0]['title'], '100张猫，100张狗')
        self.assertTrue(items[0]['history_visible'])
        self.assertIn('T', items[0]['created_at'])
        self.assertTrue(items[0]['deletable'])
        self.assertTrue(store.delete(self.actor, 'demo', result['session_id'])['deleted'])
        with self.assertRaises(ServiceError) as caught:
            store.read(self.actor, 'demo', result['session_id'])
        self.assertEqual(caught.exception.code, 'DIALOGUE_NOT_FOUND')

    def test_new_chat_is_one_container_for_all_followup_messages(self):
        store = DialogueStore(self.path)
        coordinator = DialogueCoordinator(store, FakeRuntime(
            proposal('update', subject='猫', target_count=5, media_type='image'),
            proposal('answer', reply='仍然是同一个 Chat。')), self.projects)
        created = coordinator.start(self.actor, 'demo')
        self.assertEqual(store.list(self.actor, 'demo')[0]['message_count'], 0)
        first = self.turn(coordinator, created['session_id'], created['version'], '找5张猫图片')
        second = self.turn(coordinator, created['session_id'], first['version'], '这是同一个需求吗')
        items = store.list(self.actor, 'demo')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['session_id'], created['session_id'])
        self.assertEqual(items[0]['message_count'], 4)
        self.assertEqual(second['session_id'], created['session_id'])

    def test_legacy_request_rows_are_not_promoted_to_history_chats_until_continued(self):
        store = DialogueStore(self.path)
        legacy_id, _ = store.save(self.actor, 'demo', None, 0, {
            'subject': '猫', 'target_count': 5, 'media_type': 'image',
            'constraints': [], 'stage': 'request', 'question': None,
            'messages': [{'role': 'user', 'text': '找5张猫图片'}],
            'context_start': 0, 'raw_request': '找5张猫图片'})
        self.assertFalse(store.list(self.actor, 'demo')[0]['history_visible'])
        coordinator = DialogueCoordinator(store, FakeRuntime(proposal('answer', reply='还是这个 Chat。')),
                                          self.projects)
        self.turn(coordinator, legacy_id, 1, '继续')
        self.assertTrue(store.list(self.actor, 'demo')[0]['history_visible'])

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
                        [{'target': 'terms', 'labels': [], 'operation': 'scale', 'value': 100}],
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

    def test_confirmed_clarification_is_archived_with_task_not_left_as_separate_chat(self):
        store = DialogueStore(self.path)
        state = {'stage': 'terms', 'messages': [
            {'role': 'user', 'text': '找狗的照片'}, {'role': 'assistant', 'text': '要多少张？'},
            {'role': 'user', 'text': '10 张，真实照片'}, {'role': 'assistant', 'text': '  '}]}
        session_id, _ = store.save(self.actor, 'demo', None, None, state)
        result = store.archive_to_task(self.actor, 'demo', session_id, 'task-a')
        self.assertEqual(result['message_count'], 3)
        self.assertEqual(store.list(self.actor, 'demo'), [])
        reopened = DialogueStore(self.path)
        self.assertEqual([item['text'] for item in reopened.task_origin(self.actor, 'demo', 'task-a')],
                         ['找狗的照片', '要多少张？', '10 张，真实照片'])
        self.assertEqual(reopened.archive_to_task(self.actor, 'demo', session_id, 'task-a')['message_count'], 3)
        self.assertEqual(reopened.task_origin(Actor('tenant-a', 'other'), 'demo', 'task-a'), [])
        self.assertEqual(reopened.task_origin(self.actor, 'other', 'task-a'), [])
        other, _ = reopened.save(self.actor, 'demo', None, None, state)
        with self.assertRaises(ServiceError) as conflict:
            reopened.archive_to_task(self.actor, 'demo', other, 'task-a')
        self.assertEqual(conflict.exception.code, 'DIALOGUE_CONFLICT')
        with self.assertRaises(ServiceError) as missing:
            reopened.archive_to_task(Actor('tenant-a', 'other'), 'demo', other, 'task-b')
        self.assertEqual(missing.exception.code, 'DIALOGUE_NOT_FOUND')

    def test_task_history_is_persistent_and_actor_project_task_scoped(self):
        store = DialogueStore(self.path)
        store.append_task_turn(self.actor, 'demo', 'task-a', 1, '为什么选这些词？', '它们覆盖多个场景。')
        reopened = DialogueStore(self.path)
        self.assertEqual(len(reopened.task_history(self.actor, 'demo', 'task-a')), 2)
        self.assertEqual(reopened.task_history(Actor('tenant-a', 'other'), 'demo', 'task-a'), [])
        self.assertEqual(reopened.task_history(self.actor, 'other', 'task-a'), [])
        self.assertEqual(reopened.task_history(self.actor, 'demo', 'task-b'), [])
        for index in range(11):
            store.append_task_turn(self.actor, 'demo', 'task-a', 1, f'第 {index} 条', '已记录')
        self.assertEqual(len(reopened.task_history(self.actor, 'demo', 'task-a')), 20)
        self.assertEqual(len(reopened.task_history(self.actor, 'demo', 'task-a', limit=100)), 24)

    def test_animal_category_quantities_supply_total_without_reasking(self):
        self.assertEqual(explicit_category_targets('查找20张狗20张兔子的照片'), [
            {'label': '狗', 'count': 20}, {'label': '兔子', 'count': 20}])
        self.assertEqual(explicit_category_targets('1000只猫，200只狗，20只兔子'), [
            {'label': '猫', 'count': 1000}, {'label': '狗', 'count': 200},
            {'label': '兔子', 'count': 20}])
        self.assertEqual(explicit_category_targets('100张猫，100张狗，1000张狐狸'), [
            {'label': '猫', 'count': 100}, {'label': '狗', 'count': 100},
            {'label': '狐狸', 'count': 1000}])
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

    def test_adjacent_category_counts_do_not_trigger_quantity_question(self):
        class UnexpectedRuntime:
            def interpret_dialogue_turn(self, *args, **kwargs):
                raise AssertionError('explicit quantities should not call the model')

        coordinator = DialogueCoordinator(DialogueStore(self.path), UnexpectedRuntime(), self.projects)
        result = self.turn(coordinator, None, 0, '查找20张狗20张兔子的照片')
        self.assertEqual(result['action'], 'update')
        self.assertEqual(result['request']['target_count'], 40)
        self.assertEqual(result['question'], 'confirm_summary')
        self.assertIn('20 张狗图片', result['reply'])
        self.assertIn('20 张兔子图片', result['reply'])
        self.assertNotIn('需要多少张', result['reply'])

    def test_complete_new_request_generates_terms_without_summary_question(self):
        model = FakeRuntime(proposal('update', subject='狗', target_count=5, media_type='image'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        result = coordinator.turn(self.actor, 'demo', {
            'session_id': None, 'expected_version': 0, 'message': '找5张狗图片',
            'llm_id': 'chat-llm', 'phase': 'request', 'generate_terms_when_complete': True})
        self.assertEqual(result['action'], 'update')
        self.assertEqual(result['stage'], 'terms')
        self.assertEqual(result['question'], 'review_terms')
        self.assertIn('正在生成检索词', result['reply'])
        self.assertEqual(result['request']['target_count'], 5)
        self.assertEqual(coordinator.snapshot(self.actor, 'demo', result['session_id'])['stage'], 'terms')

    def test_direct_terms_still_asks_for_missing_count_and_never_approves_search(self):
        model = FakeRuntime(proposal('update', subject='狗', media_type='image'),
                            proposal('update', target_count=5))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        body = {'session_id': None, 'expected_version': 0, 'message': '找狗图片',
                'llm_id': 'chat-llm', 'phase': 'request', 'generate_terms_when_complete': True}
        first = coordinator.turn(self.actor, 'demo', body)
        self.assertEqual(first['question'], 'target_count')
        self.assertEqual(first['stage'], 'request')
        second = coordinator.turn(self.actor, 'demo', {**body, 'session_id': first['session_id'],
            'expected_version': first['version'], 'message': '5张'})
        self.assertEqual(second['stage'], 'terms')
        self.assertEqual(second['question'], 'review_terms')
        self.assertNotEqual(second['action'], 'confirm_terms')

    def test_direct_terms_does_not_override_genuine_clarification(self):
        model = FakeRuntime(proposal('clarify', question='你要的是图片还是视频？'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        result = coordinator.turn(self.actor, 'demo', {'session_id': None, 'expected_version': 0,
            'message': '找5个猫素材', 'llm_id': 'chat-llm', 'phase': 'request',
            'generate_terms_when_complete': True})
        self.assertEqual(result['stage'], 'request')
        self.assertEqual(result['question'], 'custom_clarification')

    def test_explicit_category_quantities_do_not_wait_for_model(self):
        class UnexpectedRuntime:
            def interpret_dialogue_turn(self, *args, **kwargs):
                raise AssertionError('unambiguous category quantities should not call the model')

        coordinator = DialogueCoordinator(DialogueStore(self.path), UnexpectedRuntime(), self.projects)
        result = self.turn(coordinator, None, 0, '100张猫，100张狗，1000张狐狸')
        self.assertEqual(result['action'], 'update')
        self.assertEqual(result['question'], 'confirm_summary')
        self.assertEqual(result['request']['target_count'], 1200)
        self.assertIn('100 张猫图片', result['reply'])
        self.assertIn('1000 张狐狸图片', result['reply'])
        confirmed = self.turn(coordinator, result['session_id'], result['version'], '对')
        self.assertEqual(confirmed['action'], 'confirm')
        self.assertEqual(confirmed['stage'], 'terms')

    def test_explicit_group_term_target_is_fast_and_enforces_limit(self):
        class UnexpectedRuntime:
            def interpret_dialogue_turn(self, *args, **kwargs):
                raise AssertionError('explicit group term target should not call the model')

        store = DialogueStore(self.path)
        session_id, version = store.save(self.actor, 'demo', None, 0, {
            'subject': '猫、狗、狐狸', 'target_count': 1200, 'media_type': 'image',
            'constraints': [], 'stage': 'terms', 'question': 'review_terms',
            'messages': [], 'context_start': 0, 'raw_request': '需要100张猫，100张狗，1000张狐狸'})
        coordinator = DialogueCoordinator(store, UnexpectedRuntime(), self.projects)
        groups = [{'primary_label': label, 'auxiliary_terms': [label + '全身照']}
                  for label in ('猫', '狗', '狐狸')]
        too_many = coordinator.turn(self.actor, 'demo', {'session_id': session_id,
            'expected_version': version, 'message': '狐狸可以扩词到201个', 'llm_id': 'chat-llm',
            'phase': 'terms', 'current_groups': groups, 'current_target_count': 1200})
        self.assertEqual(too_many['action'], 'clarify')
        self.assertIn('最多 200 个', too_many['reply'])
        accepted = coordinator.turn(self.actor, 'demo', {'session_id': session_id,
            'expected_version': too_many['version'], 'message': '狐狸扩词到40个', 'llm_id': 'chat-llm',
            'phase': 'terms', 'current_groups': groups, 'current_target_count': 1200})
        self.assertEqual(accepted['action'], 'revise_terms')
        self.assertEqual(accepted['count_edits'], [{'target': 'terms', 'labels': ['狐狸'],
                                                   'operation': 'set', 'value': 40}])

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
        self.assertEqual(second['reply'], '收到，共 5 张，对吗？')
        self.assertNotIn('数量补充好了', second['reply'])
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
        self.assertEqual(second['reply'], '收到，共 5 张，对吗？')
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

    def test_count_only_followup_does_not_repeat_long_request_or_model_paraphrase(self):
        subject = '宠物和人的合影，宠物包含狗、猫、老虎、鹦鹉等'
        model = FakeRuntime(
            proposal('update', subject=subject, media_type='image', reply='需要多少张图片？'),
            proposal('update', target_count=1000,
                     reply='好的，已补充数量：1000张。当前需求为：宠物和人的合影图片，共1000张。'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = self.turn(coordinator, None, 0, subject)
        self.assertEqual(first['reply'], '需要多少张图片？')
        second = self.turn(coordinator, first['session_id'], first['version'], '1000张')
        self.assertEqual(second['reply'], '收到，共 1000 张，对吗？')
        self.assertNotIn(subject, second['reply'])
        self.assertNotIn('我记录的是', second['reply'])

    def test_media_limitation_and_choice_do_not_repeat_the_full_request(self):
        subject = '宠物和人的合影，宠物包含狗、猫、老虎、鹦鹉等'
        model = FakeRuntime(
            proposal('update', subject=subject, media_type='image'),
            proposal('update', target_count=1000),
            proposal('update', media_type='video'),
            proposal('answer', reply='因为当前只支持图片。您可以选择1）把整段需求改为图片；2）保持视频。'),
            proposal('update', media_type='image'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = self.turn(coordinator, None, 0, subject)
        second = self.turn(coordinator, first['session_id'], first['version'], '1000张')
        third = self.turn(coordinator, first['session_id'], second['version'], '需要的是视频')
        self.assertEqual(third['reply'], '视频可通过“视频草稿”入口保存整段视频需求，检索与交付尚未开放。')
        fourth = self.turn(coordinator, first['session_id'], third['version'], '为什么只能检索图片')
        self.assertEqual(fourth['reply'], '视频草稿可以保存；内网视频检索、验证和交付链路接通前暂不能执行。')
        self.assertNotIn(subject, fourth['reply'])
        fifth = self.turn(coordinator, first['session_id'], fourth['version'], '1')
        self.assertEqual(fifth['reply'], '已改为图片，对吗？')
        self.assertNotIn(subject, fifth['reply'])

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

    def test_revised_request_uses_current_fields_for_expansion(self):
        model = FakeRuntime(proposal('update', subject='狗', target_count=10, media_type='image',
                                     constraints='无水印'),
                            proposal('update', subject='猫', target_count=20, constraints='无水印'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = self.turn(coordinator, None, 0, '找10张狗图片，不要水印')
        second = self.turn(coordinator, first['session_id'], 1, '改成20张猫，其他不变')
        self.assertEqual(second['request']['effective_request'], '找 20 张猫图片；无水印')
        stored = coordinator.store.read(self.actor, 'demo', first['session_id'])
        self.assertIn('找10张狗图片，不要水印', stored['raw_request'])
        self.assertIn('改成20张猫，其他不变', stored['raw_request'])

    def test_absolute_image_count_edit_skips_model_without_changing_terms(self):
        self.assertEqual(explicit_image_count_update('改成20张，其他不变', 10), 20)
        self.assertIsNone(explicit_image_count_update('再找20张猫', 10))
        self.assertIsNone(explicit_image_count_update('增加20张', 10))
        store = DialogueStore(self.path)
        session_id, _ = store.save(self.actor, 'demo', None, 0, {
            'subject': '狗', 'target_count': 10, 'media_type': 'image',
            'constraints': ['无水印'], 'stage': 'terms', 'question': 'review_terms',
            'messages': [], 'context_start': 0, 'raw_request': '找10张狗图片，不要水印'})
        model = FakeRuntime()
        result = self.turn(DialogueCoordinator(store, model, self.projects), session_id, 1,
                           '改成20张，其他不变', 'terms')
        self.assertEqual(result['request']['target_count'], 20)
        self.assertEqual(result['request']['effective_request'], '找 20 张狗图片；无水印')
        self.assertEqual(result['action'], 'revise_terms')
        self.assertEqual(model.calls, [])
        request_id, _ = store.save(self.actor, 'demo', None, 0, {
            'subject': '狗', 'target_count': 10, 'media_type': 'image',
            'constraints': ['无水印'], 'stage': 'request', 'question': 'confirm_summary',
            'messages': [], 'context_start': 0, 'raw_request': '找10张狗图片，不要水印'})
        request_result = self.turn(DialogueCoordinator(store, model, self.projects), request_id, 1,
                                   '目标改成30张')
        self.assertEqual(request_result['request']['target_count'], 30)
        self.assertEqual(request_result['request']['effective_request'], '找 30 张狗图片；无水印')
        self.assertEqual(model.calls, [])

    def test_new_request_reference_is_first_turn_only_and_not_transcript(self):
        model = FakeRuntime(proposal('update', subject='猫', target_count=20,
                                     media_type='image', constraints='无水印'))
        coordinator = DialogueCoordinator(DialogueStore(self.path), model, self.projects)
        first = coordinator.turn(self.actor, 'demo', {'session_id': None, 'expected_version': 0,
            'message': '再找20张猫，条件一样', 'llm_id': 'chat-llm', 'phase': 'request',
            'reference_request': '找10张狗图片；无水印'})
        self.assertEqual(model.calls[0][0]['reference_request'], '找10张狗图片；无水印')
        self.assertEqual(first['request']['effective_request'], '找 20 张猫图片；无水印')
        self.assertEqual(first['request']['raw_request'], '再找20张猫，条件一样')
        with self.assertRaises(ServiceError):
            coordinator.turn(self.actor, 'demo', {'session_id': first['session_id'],
                'expected_version': first['version'], 'message': '继续',
                'llm_id': 'chat-llm', 'phase': 'request',
                'reference_request': '找10张狗图片'})

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

    def test_followup_can_defer_all_group_scaling_until_missing_terms_are_generated(self):
        runtime = ModelRuntime([ModelProfile('chat-llm', 'llm',
            ModelConfig('fixture', 'test', 'https://fixture.invalid/v1', 'not-used'))])
        value = {'kind': 'expansion', 'target_count': None, 'requested_terms_per_group': None,
                 'count_edits': [{'target': 'terms', 'labels': [], 'operation': 'scale', 'value': 2}],
                 'expansion_instruction': '把生成后的每组检索词数量翻倍', 'reply': None}
        result = runtime._validate_followup(value, '词数翻倍', [], {'target_count': 200})
        self.assertEqual(result['count_edits'], value['count_edits'])

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
                            proposal('update', constraints='不同姿势、横图'),
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
                            proposal('replace', subject='盒马不同姿势'))
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
