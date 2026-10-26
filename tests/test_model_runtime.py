from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import normalize_grouped_expansion
from dsh_retrieval.intent_model import ModelConfig
from dsh_retrieval.model_runtime import ModelProfile, ModelRuntime, load_model_runtime
from dsh_retrieval.query_branches import compile_query_branches


class Transport:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(json.loads(request.data))
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return BytesIO(json.dumps({'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(output)}}]}).encode())


class ModelRuntimeTest(unittest.TestCase):
    def test_contextual_edit_uses_visible_groups_and_both_sides_of_history(self):
        edits = [{'target': 'terms', 'labels': ['猫'], 'operation': 'scale', 'value': 0.5},
                 {'target': 'images', 'labels': [], 'operation': 'add', 'value': -20}]
        runtime = self.runtime([{'action': 'revise_terms', 'count_edits': edits,
                                 'expansion_instruction': '猫组词数减半，狗组不变，图片目标减少20张。',
                                 'reply': '我会调整猫组的词数和图片目标。'}])
        groups = [{'primary_label': '猫', 'auxiliary_terms': ['猫在窗边', '猫在树下']},
                  {'primary_label': '狗', 'auxiliary_terms': ['狗在公园']}]
        state = {'stage': 'terms', 'question': 'review_terms', 'target_count': 120,
                 'current_groups': groups, 'messages': [
                     {'role': 'user', 'text': '猫组太多了'}, {'role': 'assistant', 'text': '希望减少多少？'}]}
        result = runtime.interpret_dialogue_turn(state, '它减半吧，图再少二十张', 'text')
        self.assertEqual(result['target_count'], 100)
        self.assertEqual(result['count_edits'], edits)
        context = json.loads(self.transport.calls[0]['messages'][1]['content'])
        self.assertEqual(context['current_groups'], groups)
        self.assertEqual(context['recent_messages'], state['messages'])
        self.assertFalse(context['execution_context']['search_started'])
        sent_messages = self.transport.calls[0]['messages']
        self.assertEqual(sent_messages[-1], {'role': 'user', 'content': '它减半吧，图再少二十张'})
        self.assertEqual(sent_messages[-2], {'role': 'assistant', 'content': '希望减少多少？'})

    def test_dialogue_invalid_shape_repairs_with_same_context(self):
        runtime = self.runtime([{'action': 'execute', 'approved': True},
                                {'action': 'answer', 'reply': '确认词表后才能开始检索。'}])
        state = {'stage': 'terms', 'question': 'review_terms', 'target_count': 5, 'messages': []}
        result = runtime.interpret_dialogue_turn(state, '怎么还没开始？', 'text')
        self.assertEqual(result['action'], 'answer')
        self.assertEqual(len(self.transport.calls), 2)
        repaired = json.loads(self.transport.calls[1]['messages'][1]['content'])
        self.assertEqual(repaired['latest_message'], '怎么还没开始？')
        self.assertIn('repair', repaired)
        self.assertNotIn('approved', result)

    def test_group_edits_require_explicit_scope_and_repair_ambiguous_all_groups(self):
        runtime = self.runtime([
            {'action': 'revise_terms', 'count_edits': [{'target': 'terms', 'labels': [], 'operation': 'scale', 'value': 0.5}]},
            {'action': 'revise_terms', 'count_edits': [{'target': 'terms', 'labels': ['猫'], 'operation': 'scale', 'value': 0.5}]}])
        state = {'stage': 'terms', 'question': 'review_terms', 'target_count': 5, 'messages': [],
                 'current_groups': [{'primary_label': '猫', 'auxiliary_terms': ['猫在树下', '猫在窗边']},
                                    {'primary_label': '狗', 'auxiliary_terms': ['狗在公园']} ]}
        result = runtime.interpret_dialogue_turn(state, '猫组减半，狗不动', 'text')
        self.assertEqual(result['count_edits'][0]['labels'], ['猫'])
        self.assertEqual(len(self.transport.calls), 2)

    def test_task_followup_can_answer_or_propose_relative_edits(self):
        runtime = self.runtime([
            {'kind': 'answer', 'target_count': None, 'requested_terms_per_group': None,
             'reply': '这些词覆盖不同画面场景。', 'count_edits': []},
            {'kind': 'expansion', 'target_count': None, 'requested_terms_per_group': None,
             'count_edits': [{'target': 'images', 'labels': [], 'operation': 'scale', 'value': 0.5}]}])
        groups = [{'primary_label': '猫', 'auxiliary_terms': ['猫在树下']}]
        context = {'target_count': 20, 'stage': 'retrieval'}
        answer = runtime.classify_followup('猫图片', groups, '为什么这些词？', 'text', task_context=context)
        self.assertEqual(answer['kind'], 'answer')
        edit = runtime.classify_followup('猫图片', groups, '图片要一半就行', 'text', task_context=context)
        self.assertEqual(edit['target_count'], 10)

    def test_image_count_only_change_can_retain_words(self):
        groups = [{'primary_label': '猫', 'auxiliary_terms': ['猫在树下']}]
        runtime = self.runtime([{'parent_label': None, 'groups': groups}])
        result = runtime.expand('找20张猫图片', 'text', 'vision', target_count=10,
                                current_target_count=20, feedback_text='图片只要一半，词表不变',
                                current_groups=groups, count_edits=[])
        self.assertEqual(result['plan']['groups'][0]['auxiliary_terms'], groups[0]['auxiliary_terms'])

    def test_dialogue_model_can_suggest_bounded_natural_reply(self):
        runtime = self.runtime([{'action': 'update', 'subject': '猫', 'target_count': None,
                                 'media_type': 'image', 'constraints': None, 'question': None,
                                 'reply': '想找多少张猫的图片？'}])
        state = {'stage': 'request', 'question': None, 'messages': [],
                 'subject': None, 'target_count': None, 'media_type': None, 'constraints': []}
        result = runtime.interpret_dialogue_turn(state, '找猫图片', 'text')
        self.assertEqual(result['reply'], '想找多少张猫的图片？')
        self.assertEqual(result['subject'], '猫')

    def test_http_selection_draft_confirmation_and_history(self):
        from http.server import ThreadingHTTPServer
        import threading
        from urllib.request import Request, urlopen
        from dsh_retrieval.api import create_handler
        from dsh_retrieval.dialogue import DialogueCoordinator, DialogueStore
        from dsh_retrieval.models import Actor
        from dsh_retrieval.service import InMemoryTaskRepository
        from draft_helpers import BODY, make_service
        runtime = self.runtime([{'parent_label': None, 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': ['柯基']}]},
            {'kind': 'expansion', 'target_count': 5, 'requested_terms_per_group': 5},
            {'kind': 'answer', 'target_count': None, 'requested_terms_per_group': None,
             'reply': '当前词表包括草地和公园场景。'}])
        service = make_service(InMemoryTaskRepository())
        dialogue = DialogueCoordinator(DialogueStore(), runtime, service.projects)
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(service, model_runtime=runtime, dialogue=dialogue))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def call(path, body=None, key='test'):
            headers = {'X-Tenant-Id': 'tenant', 'X-User-Id': 'owner',
                       'Content-Type': 'application/json', 'Idempotency-Key': key}
            data = json.dumps(body).encode() if body is not None else None
            with urlopen(Request(f'http://127.0.0.1:{server.server_port}' + path, data=data, headers=headers)) as response:
                return json.load(response)
        try:
            self.assertEqual(len(call('/v1/retrieval/projects/demo/models')['items']), 2)
            expanded = call('/v1/retrieval/projects/demo/expansions', {
                'raw_request': '狗，不要人物', 'dimension_id': 'default',
                'target_count': 3,
                'model_selection': {'llm': 'text', 'vlm': 'vision'}})
            body = deepcopy(BODY)
            body['query_spec']['grouped_expansion'] = expanded['plan']
            draft = call('/v1/retrieval/projects/demo/drafts', body)
            task = call('/v1/retrieval/tasks', {'draft_id': draft['draft_id'],
                'expected_draft_version': draft['draft_version'], 'proposal_digest': draft['proposal_digest'], 'action': 'confirm'})
            revisions = call('/v1/retrieval/tasks/' + task['task_id'] + '/revisions')
            self.assertEqual(revisions['items'][0]['proposal']['query_spec']['grouped_expansion']['model_binding'],
                             runtime.binding('text', 'vision'))
            routed = call('/v1/retrieval/tasks/' + task['task_id'] + '/followup-intent', {
                'text': '少找点，主体还是狗，不要情绪，多找户外画面', 'llm_id': 'text',
                'expected_revision': task['confirmed_revision'],
                'expected_task_version': task['task_version']})
            self.assertEqual(routed['kind'], 'expansion')
            self.assertEqual(routed['task_id'], task['task_id'])
            preview = [{'primary_label': '狗', 'auxiliary_terms': ['狗在草地', '狗在公园']}]
            answered = call('/v1/retrieval/tasks/' + task['task_id'] + '/followup-intent', {
                'text': '这些词怎么选的？', 'llm_id': 'text', 'expected_revision': task['confirmed_revision'],
                'expected_task_version': task['task_version'],
                'draft_preview': {'groups': preview, 'target_count': 5}})
            self.assertEqual(answered['kind'], 'answer')
            context = json.loads(self.transport.calls[-1]['messages'][1]['content'])
            self.assertEqual(context['current_groups'], preview)
            self.assertEqual(context['recent_messages'][0]['text'], '少找点，主体还是狗，不要情绪，多找户外画面')
            self.assertEqual(context['task_context']['target_count'], 5)
            self.assertEqual(service.get_task(Actor('tenant', 'owner'), task['task_id'])['task_version'], task['task_version'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def runtime(self, outputs):
        self.transport = Transport(outputs)
        return ModelRuntime([
            ModelProfile('text', 'llm', ModelConfig('fixture', 'text-v1', 'https://fixture.invalid/v1', 'test-secret')),
            ModelProfile('vision', 'vlm', ModelConfig('fixture', 'vision-v1', 'https://fixture.invalid/v1', 'test-secret')),
        ], opener=self.transport)

    def test_selected_llm_expansion_and_vlm_verdict(self):
        runtime = self.runtime([{'parent_label': '动物', 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': ['柯基']}]},
            {'verdict': 'unknown', 'reason': '主体被遮挡'}])
        expansion = runtime.expand('狗，不要人物', 'text', 'vision')
        plan = expansion['plan']
        self.assertEqual(normalize_grouped_expansion(plan), plan)
        self.assertEqual(plan['generator'], 'llm@1')
        query = compile_query_branches({'raw_request': '狗，不要人物', 'grouped_expansion': plan})
        self.assertEqual(query['branches'][0]['query_text'], '狗，不要人物')
        verdict = runtime.assess('狗，不要人物', b'\xff\xd8\xfffixture', 'image/jpeg', plan['model_binding'])
        self.assertEqual(verdict['verdict'], 'unknown')
        self.assertEqual([call['model'] for call in self.transport.calls], ['text-v1', 'vision-v1'])
        self.assertEqual(self.transport.calls[1]['messages'][1]['content'][1]['type'], 'image_url')
        self.assertNotIn('test-secret', json.dumps(runtime.catalog()))
        self.assertNotIn('fixture.invalid', json.dumps(runtime.catalog()))
        self.assertNotIn('test-secret', json.dumps(expansion))
        changed = deepcopy(plan)
        changed['model_binding']['vlm'] = 'different'
        with self.assertRaises(ValueError):
            normalize_grouped_expansion(changed)

    def test_model_expansion_uses_quantity_as_advice_and_accepts_feedback(self):
        self.assertEqual([ModelRuntime.expansion_terms_per_group(count) for count in (1, 100, 101, 250, 10000)],
                         [1, 1, 2, 3, 30])
        runtime = self.runtime([{'parent_label': '动物', 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': ['金毛', '柯基', '边牧']},
            {'primary_label': '猫', 'auxiliary_terms': ['橘猫', '布偶猫', '狸花猫']}]}])
        result = runtime.expand('找250张狗和猫的图片', 'text', 'vision', target_count=250)
        self.assertEqual([group['requested_expansion_count'] for group in result['plan']['groups']], [3, 3])
        self.assertIn('建议每类约 3 个辅助词', self.transport.calls[0]['messages'][0]['content'])
        self.assertEqual(json.loads(self.transport.calls[0]['messages'][1]['content'])['target_count'], 250)
        runtime = self.runtime([{'parent_label': None, 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': ['金毛']}]}])
        shorter = runtime.expand('找250张狗图片', 'text', 'vision', target_count=250,
                                 feedback_text='只保留最有用的词',
                                 current_groups=[{'primary_label': '狗', 'auxiliary_terms': ['宠物狗', '柯基']}])
        self.assertEqual(shorter['plan']['groups'][0]['auxiliary_terms'], ['金毛'])
        sent = json.loads(self.transport.calls[0]['messages'][1]['content'])
        self.assertEqual(sent['expansion_feedback'], '只保留最有用的词')
        self.assertEqual(sent['current_groups'][0]['auxiliary_terms'], ['宠物狗', '柯基'])
        for invalid in (True, 0, 10001, '250'):
            with self.assertRaises(ServiceError) as caught:
                runtime.expand('狗图片', 'text', 'vision', target_count=invalid)
            self.assertEqual(caught.exception.code, 'INVALID_SPEC')

    def test_explicit_feedback_term_count_overrides_quantity_advice(self):
        groups = [{'primary_label': '狗', 'auxiliary_terms': ['狗在草地奔跑', '狗接飞盘',
                    '狗在公园散步', '狗与主人玩耍', '狗在户外训练']}]
        runtime = self.runtime([{'parent_label': None, 'groups': groups}])
        result = runtime.expand('找5张狗图片', 'text', 'vision', target_count=5,
                                feedback_text='要五个召回词，主体是狗，不是狗的情绪',
                                current_groups=[{'primary_label': '狗', 'auxiliary_terms': ['快乐的狗']}],
                                requested_terms_per_group=5)
        self.assertEqual(result['plan']['groups'][0]['auxiliary_terms'], groups[0]['auxiliary_terms'])
        self.assertIn('用户明确要求每类 5 个召回词', self.transport.calls[0]['messages'][0]['content'])
        self.assertIn('不用“可爱”“快乐”', self.transport.calls[0]['messages'][0]['content'])
        self.assertEqual(json.loads(self.transport.calls[0]['messages'][1]['content'])['requested_terms_per_group'], 5)
        runtime = self.runtime([{'parent_label': None, 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': ['狗在草地奔跑']}]}])
        with self.assertRaises(ServiceError) as caught:
            runtime.expand('找5张狗图片', 'text', 'vision', target_count=5,
                           feedback_text='要五个召回词', current_groups=groups,
                           requested_terms_per_group=5)
        self.assertEqual(caught.exception.code, 'MODEL_OUTPUT_INVALID')
        for kwargs in ({'feedback_text': '多加场景'},
                       {'feedback_text': '', 'current_groups': [{'primary_label': '狗', 'auxiliary_terms': []}]},
                       {'feedback_text': '多加场景', 'current_groups': [{'primary_label': '狗', 'auxiliary_terms': ['狗']}]}):
            with self.assertRaises(ServiceError) as caught:
                runtime.expand('狗图片', 'text', 'vision', **kwargs)
            self.assertEqual(caught.exception.code, 'INVALID_SPEC')

    def test_scene_style_feedback_retries_unchanged_words_and_never_claims_change(self):
        current = [{'primary_label': '猫', 'auxiliary_terms': ['猫图片']},
                   {'primary_label': '狗', 'auxiliary_terms': ['狗图片']}]
        unchanged = {'parent_label': None, 'groups': current}
        changed = {'parent_label': None, 'groups': [
            {'primary_label': '猫', 'auxiliary_terms': ['一只猫在花园奔跑']},
            {'primary_label': '狗', 'auxiliary_terms': ['一只哈士奇在花园奔跑']}]}
        runtime = self.runtime([unchanged, changed])
        result = runtime.expand('找猫和狗图片', 'text', 'vision', target_count=20,
                                feedback_text='检索词按照这种来扩充，一只哈士奇在花园奔跑',
                                current_groups=current)
        self.assertEqual(result['plan']['groups'][0]['auxiliary_terms'], ['一只猫在花园奔跑'])
        self.assertEqual(len(self.transport.calls), 2)
        self.assertIn('具体主体 + 可见动作/姿态 + 场景', self.transport.calls[0]['messages'][0]['content'])
        self.assertIn('上次候选词表与旧词表完全相同', self.transport.calls[1]['messages'][0]['content'])
        runtime = self.runtime([unchanged, unchanged])
        with self.assertRaises(ServiceError) as caught:
            runtime.expand('找猫和狗图片', 'text', 'vision', target_count=20,
                           feedback_text='检索词按照这种来扩充，一只哈士奇在花园奔跑',
                           current_groups=current)
        self.assertEqual(caught.exception.code, 'EXPANSION_UNCHANGED')

    def test_relative_double_uses_each_group_count_and_retries_mismatch(self):
        current = [{'primary_label': '猫', 'auxiliary_terms': ['猫在窗边', '猫在草地']},
                   {'primary_label': '狗', 'auxiliary_terms': ['狗在公园']}]
        short = {'parent_label': None, 'groups': current}
        doubled = {'parent_label': None, 'groups': [
            {'primary_label': '猫', 'auxiliary_terms': ['猫在窗边', '猫在草地', '猫在树下', '猫在沙发']},
            {'primary_label': '狗', 'auxiliary_terms': ['狗在公园', '狗在海边']} ]}
        runtime = self.runtime([short, doubled])
        result = runtime.expand('找120张猫和狗图片', 'text', 'vision', target_count=120,
                                feedback_text='检索词的数量在增加一倍', current_groups=current,
                                count_edits=[{'target': 'terms', 'labels': [], 'operation': 'scale', 'value': 2}])
        self.assertEqual([len(group['auxiliary_terms']) for group in result['plan']['groups']], [4, 2])
        self.assertEqual(len(self.transport.calls), 2)
        sent = json.loads(self.transport.calls[0]['messages'][1]['content'])
        self.assertEqual(sent['group_term_targets'], {'猫': 4, '狗': 2})
        self.assertEqual(sent['target_count'], 120)
        self.assertIn('每组目标词数', self.transport.calls[0]['messages'][0]['content'])
        runtime = self.runtime([short, short])
        with self.assertRaises(ServiceError) as caught:
            runtime.expand('找120张猫和狗图片', 'text', 'vision', target_count=120,
                           feedback_text='检索词数量翻倍', current_groups=current,
                           count_edits=[{'target': 'terms', 'labels': [], 'operation': 'scale', 'value': 2}])
        self.assertEqual(caught.exception.code, 'TERM_COUNT_MISMATCH')

    def test_relative_double_above_group_cap_fails_before_model_call(self):
        runtime = self.runtime([])
        current = [{'primary_label': '猫', 'auxiliary_terms': [f'猫场景{i}' for i in range(16)]}]
        with self.assertRaises(ServiceError) as caught:
            runtime.expand('找猫图片', 'text', 'vision', feedback_text='检索词翻倍', current_groups=current,
                           count_edits=[{'target': 'terms', 'labels': [], 'operation': 'scale', 'value': 2}])
        self.assertEqual(caught.exception.code, 'INVALID_SPEC')
        self.assertFalse(self.transport.calls)

    def test_followup_routing_uses_semantics_without_keyword_gate(self):
        runtime = self.runtime([{'kind': 'expansion', 'target_count': None,
                                 'requested_terms_per_group': None},
                                {'kind': 'cleaning', 'target_count': None,
                                 'requested_terms_per_group': None}])
        groups = [{'primary_label': '狗', 'auxiliary_terms': ['宠物狗']}]
        changed = runtime.classify_followup('找10张狗图片', groups,
                                            '少找点，别按情绪搜，多找户外', 'text')
        self.assertEqual(changed, {'kind': 'expansion', 'target_count': None,
                                   'requested_terms_per_group': None})
        sent = json.loads(self.transport.calls[0]['messages'][1]['content'])
        self.assertEqual(sent['feedback_text'], '少找点，别按情绪搜，多找户外')
        self.assertIn('即使没有出现', self.transport.calls[0]['messages'][0]['content'])
        cleaning = runtime.classify_followup('找10张狗图片', groups,
                                             '结果里有水印的不要', 'text')
        self.assertEqual(cleaning['kind'], 'cleaning')
        runtime = self.runtime([{'kind': 'expansion', 'target_count': 10,
                                 'requested_terms_per_group': None},
                                {'kind': 'expansion', 'target_count': 5,
                                 'requested_terms_per_group': 5}])
        implied = runtime.classify_followup('找10张狗图片', groups,
                                            '别按情绪搜，多找户外', 'text')
        self.assertIsNone(implied['target_count'])
        explicit = runtime.classify_followup('找10张狗图片', groups,
                                             '只要五张图，五个召回词', 'text')
        self.assertEqual((explicit['target_count'], explicit['requested_terms_per_group']), (5, 5))
        runtime = self.runtime([{'kind': 'cleaning', 'target_count': 5,
                                 'requested_terms_per_group': None}] * 2)
        with self.assertRaises(ServiceError) as caught:
            runtime.classify_followup('找10张狗图片', groups, '结果里有水印的不要', 'text')
        self.assertEqual(caught.exception.code, 'MODEL_OUTPUT_INVALID')

    def test_feedback_cleaning_suggestion_is_advisory_and_bounded(self):
        runtime = self.runtime([{'feedback_status': 'ready', 'requirement': '必须是狗，且没有可见文字',
                                 'rationale': '保留原主体并加入反馈排除项',
                                 'needs_clarification': False}])
        suggestion = runtime.suggest_cleaning('寻找狗', '去掉文字', 'text', 'vision')
        self.assertEqual(suggestion['requirement'], '必须是狗，且没有可见文字')
        self.assertEqual(suggestion['model_binding'], runtime.binding('text', 'vision'))
        self.assertFalse(suggestion['needs_clarification'])
        self.assertEqual(suggestion['feedback_status'], 'ready')
        self.assertEqual(self.transport.calls[0]['model'], 'text-v1')
        self.assertNotIn('secret', json.dumps(suggestion))
        for invalid in ({'requirement': '狗', 'rationale': '说明'},
                        {'feedback_status': 'ready', 'requirement': '', 'rationale': '说明', 'needs_clarification': False},
                        {'feedback_status': 'ready', 'requirement': '狗', 'rationale': '说明', 'needs_clarification': 'false'},
                        {'feedback_status': 'ready', 'requirement': '狗', 'rationale': '说明', 'needs_clarification': False, 'execute': True},
                        {'feedback_status': 'unrelated', 'requirement': '狗', 'rationale': '说明', 'needs_clarification': True},
                        {'feedback_status': 'unrelated', 'requirement': '', 'rationale': '说明', 'needs_clarification': False}):
            runtime = self.runtime([invalid])
            with self.assertRaises(ServiceError) as caught:
                runtime.suggest_cleaning('寻找狗', '去掉文字', 'text', 'vision')
            self.assertEqual(caught.exception.code, 'MODEL_OUTPUT_INVALID')

    def test_irrelevant_feedback_has_no_candidate_instruction(self):
        runtime = self.runtime([{'feedback_status': 'unrelated', 'requirement': '',
                                 'rationale': '请说明要保留或排除的画面特征',
                                 'needs_clarification': True}])
        suggestion = runtime.suggest_cleaning('找3张狗图片', '谢谢', 'text', 'vision')
        self.assertEqual(suggestion['feedback_status'], 'unrelated')
        self.assertEqual(suggestion['requirement'], '')
        runtime = self.runtime([{'feedback_status': 'ready', 'requirement': '找3张狗图片',
                                 'rationale': '没有新增条件', 'needs_clarification': False}])
        with self.assertRaises(ServiceError) as caught:
            runtime.suggest_cleaning('找3张狗图片', '谢谢', 'text', 'vision')
        self.assertEqual(caught.exception.code, 'MODEL_OUTPUT_INVALID')

    def test_feedback_suggestion_http_requires_current_owner_draft(self):
        from http.server import ThreadingHTTPServer
        import threading
        from urllib.request import Request, urlopen
        from dsh_retrieval.api import create_handler
        from dsh_retrieval.service import InMemoryTaskRepository
        from draft_helpers import BODY, make_service
        runtime = self.runtime([{'feedback_status': 'ready', 'requirement': '狗且无文字', 'rationale': '排除文字',
                                 'needs_clarification': False}])
        service = make_service(InMemoryTaskRepository())
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(service, model_runtime=runtime))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def call(path, body=None, *, owner='owner', key='test'):
            headers = {'X-Tenant-Id': 'tenant', 'X-User-Id': owner,
                       'Content-Type': 'application/json', 'Idempotency-Key': key}
            data = json.dumps(body).encode() if body is not None else None
            try:
                with urlopen(Request(f'http://127.0.0.1:{server.server_port}' + path,
                                     data=data, headers=headers)) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.load(error)
        try:
            _, draft = call('/v1/retrieval/projects/demo/drafts', deepcopy(BODY), key='draft')
            _, task = call('/v1/retrieval/tasks', {'draft_id': draft['draft_id'],
                'expected_draft_version': draft['draft_version'],
                'proposal_digest': draft['proposal_digest'], 'action': 'confirm'}, key='task')
            task_id = task['task_id']
            _, feedback = call(f'/v1/retrieval/tasks/{task_id}/feedback', {
                'text': '去掉文字', 'scope': 'all', 'expected_revision': 1,
                'expected_task_version': task['task_version']}, key='feedback')
            body = {'draft_id': feedback['draft_id'],
                    'expected_draft_version': feedback['draft_version'],
                    'proposal_digest': feedback['proposal_digest'],
                    'model_selection': {'llm': 'text', 'vlm': 'vision'}}
            path = f'/v1/retrieval/tasks/{task_id}/feedback-suggestions'
            status, foreign = call(path, body, owner='intruder')
            self.assertEqual(status, 404)
            self.assertEqual(len(self.transport.calls), 0)
            status, suggested = call(path, body)
            self.assertEqual((status, suggested['status']), (200, 'advisory_only'))
            self.assertEqual(suggested['draft_id'], feedback['draft_id'])
            self.assertEqual(suggested['proposal_digest'], feedback['proposal_digest'])
            self.assertEqual(len(self.transport.calls), 1)
            current_status, current = call(f'/v1/retrieval/tasks/{task_id}')
            self.assertEqual((current_status, current['confirmed_revision']), (200, 1))
            self.assertEqual(current['pending_draft']['draft_id'], feedback['draft_id'])
            status, rejected = call(path, dict(body, expected_draft_version=99))
            self.assertEqual((status, rejected['code']), (409, 'REVISION_CONFLICT'))
            self.assertEqual(len(self.transport.calls), 1)
            def concurrent_confirm(*args):
                confirmed_status, _ = call(f'/v1/retrieval/tasks/{task_id}/confirmations', {
                    'draft_id': feedback['draft_id'],
                    'expected_draft_version': feedback['draft_version'],
                    'proposal_digest': feedback['proposal_digest'],
                    'expected_task_version': feedback['base_task_version'],
                    'action': 'confirm'}, key='confirm-during-model')
                self.assertEqual(confirmed_status, 202)
                return {'feedback_status': 'ready', 'requirement': '狗且无文字', 'rationale': '排除文字',
                        'needs_clarification': False}
            with patch.object(runtime, '_request', side_effect=concurrent_confirm):
                status, stale = call(path, body)
            self.assertEqual((status, stale['code']), (409, 'REVISION_CONFLICT'))
            status, current = call(f'/v1/retrieval/tasks/{task_id}')
            self.assertEqual((status, current['confirmed_revision']), (200, 2))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_wrong_capability_or_missing_model_never_calls_transport(self):
        runtime = self.runtime([])
        for ids in (('vision', 'text'), ('missing', 'vision')):
            with self.assertRaises(ServiceError):
                runtime.expand('狗', *ids)
        self.assertEqual(self.transport.calls, [])

    def test_failures_never_become_pass_and_do_not_leak(self):
        for output in (OSError('test-secret'), {'verdict': 'pass'},
                       {'verdict': True, 'reason': 'bad'}, {'verdict': 'pass', 'reason': '', 'approved': True}):
            runtime = self.runtime([output])
            with self.assertRaises(ServiceError) as raised:
                runtime.assess('狗', b'\xff\xd8\xfffixture', 'image/jpeg', runtime.binding('text', 'vision'))
            self.assertNotIn('test-secret', str(raised.exception))

    def test_temporary_upstream_limit_retries_with_bounded_wait(self):
        limited = HTTPError('https://fixture.invalid/v1/chat/completions', 429,
                            'private upstream error', {'Retry-After': '99'}, BytesIO(b'private'))
        runtime = self.runtime([limited, {'parent_label': None, 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': []}]}])
        with patch('dsh_retrieval.model_runtime.time.sleep') as sleep:
            result = runtime.expand('狗', 'text', 'vision')
        self.assertEqual(result['plan']['groups'][0]['primary_label'], '狗')
        self.assertEqual(len(self.transport.calls), 2)
        sleep.assert_called_once_with(5)

    def test_permanent_http_invalid_response_and_retry_exhaustion_fail_closed(self):
        def error(code):
            return HTTPError('https://fixture.invalid/v1/chat/completions', code,
                             'test-secret', {}, BytesIO(b'test-secret'))
        for outputs, expected_calls in (([error(400)], 1), (['not an object'], 1),
                                        ([error(503), error(503), error(503)], 3)):
            runtime = self.runtime(outputs)
            with patch('dsh_retrieval.model_runtime.time.sleep') as sleep:
                with self.assertRaises(ServiceError) as raised:
                    runtime.expand('狗', 'text', 'vision')
            self.assertEqual(len(self.transport.calls), expected_calls)
            self.assertNotIn('test-secret', str(raised.exception))
            self.assertEqual(sleep.call_count, expected_calls - 1)

    def test_config_drift_and_remote_image_are_rejected_before_call(self):
        runtime = self.runtime([])
        binding = runtime.binding('text', 'vision')
        with self.assertRaises(ServiceError):
            runtime.assess('狗', 'https://private.invalid/image', 'image/jpeg', binding)
        binding['config_digest'] = 'changed'
        with self.assertRaises(ServiceError):
            runtime.assess('狗', b'\xff\xd8\xfffixture', 'image/jpeg', binding)
        self.assertEqual(self.transport.calls, [])

    def test_config_loader_uses_environment_and_empty_registry_is_explicit(self):
        self.assertEqual(ModelRuntime().catalog(), [])
        config = {'models': [{'id': 'text', 'kind': 'llm', 'provider': 'fixture', 'model': 'text-v1',
                             'base_url': 'https://fixture.invalid/v1', 'api_key_env': 'DSH_TEST_MODEL_KEY'}]}
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'models.json'
            path.write_text(json.dumps(config))
            with patch.dict(os.environ, {'DSH_TEST_MODEL_KEY': 'test-secret'}):
                runtime = load_model_runtime(path)
            self.assertEqual(runtime.catalog()[0]['id'], 'text')
            self.assertNotIn('test-secret', repr(runtime.profiles))

    def test_per_model_request_options(self):
        row = {'id': 'text', 'kind': 'llm', 'provider': 'fixture', 'model': 'reasoner',
               'base_url': 'https://fixture.invalid/v1', 'api_key_env': 'DSH_TEST_MODEL_KEY'}
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'models.json'
            def load(**options):
                path.write_text(json.dumps({'models': [row | options]}))
                with patch.dict(os.environ, {'DSH_TEST_MODEL_KEY': 'test-secret'}):
                    return load_model_runtime(path)
            plain = load()
            # Models rejecting `temperature` get it omitted; budgets are per model.
            tuned = load(temperature=None, max_tokens=12000, request_timeout_seconds=150)
            for bad in ({'max_tokens': 10}, {'temperature': True}, {'request_timeout_seconds': 1000},
                        {'reasoning': 'high'}):
                with self.assertRaises(ServiceError):
                    load(**bad)
        timeouts = []
        class Recording(Transport):
            def open(self, request, timeout):
                timeouts.append(timeout)
                return super().open(request, timeout)
        for runtime in (plain, tuned):
            runtime.opener = Recording([{'ok': True}])
            runtime._request(runtime.select('text', 'llm'), 'system', 'message')
            runtime.last_body = runtime.opener.calls[0]
        self.assertEqual((plain.last_body['temperature'], plain.last_body['max_tokens']), (0, 3000))
        self.assertNotIn('temperature', tuned.last_body)
        self.assertEqual(tuned.last_body['max_tokens'], 12000)
        self.assertEqual(timeouts, [60, 150])
        # Default options keep existing bindings valid; changed options invalidate them.
        vision = ModelProfile('vision', 'vlm', ModelConfig('fixture', 'v', 'https://fixture.invalid/v1', 'k'))
        def binding(config):
            return ModelRuntime([ModelProfile('text', 'llm', config), vision]).binding('text', 'vision')
        base = ('fixture', 'reasoner', 'https://fixture.invalid/v1', 'k')
        self.assertEqual(binding(ModelConfig(*base)), binding(ModelConfig(*base, temperature=0, max_tokens=None)))
        self.assertNotEqual(binding(ModelConfig(*base)), binding(ModelConfig(*base, temperature=None)))
        self.assertEqual(tuned.select('text', 'llm').config.options(),
                         {'max_tokens': 12000, 'temperature': None, 'request_timeout_seconds': 150})


if __name__ == '__main__':
    unittest.main()
