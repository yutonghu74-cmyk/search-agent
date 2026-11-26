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
        if self.calls[-1].get('tools') and isinstance(output, dict):
            tool = self.calls[-1]['tools'][0]['function']['name']
            message = {'tool_calls': [{'type': 'function', 'id': 'fixture-call', 'function': {
                'name': tool, 'arguments': json.dumps({'proposal': output})}}]}
            finish = 'tool_calls'
        else:
            message = {'content': output if self.calls[-1].get('tools') and isinstance(output, str)
                       else json.dumps(output)}
            finish = 'stop'
        return BytesIO(json.dumps({'choices': [{'message': message, 'finish_reason': finish}]}).encode())


class ModelRuntimeTest(unittest.TestCase):
    def test_unified_model_proposal_keeps_quantity_and_condition_in_one_intent(self):
        from draft_helpers import BODY
        spec = deepcopy(BODY['query_spec'])
        spec['target_count'] = spec['refill_policy']['target_count'] = 20
        intent = {'kind': 'requirement', 'target_count': None, 'requested_terms_per_group': None,
                  'count_edits': [{'target': 'images', 'labels': [], 'operation': 'add', 'value': 5}],
                  'cleaning_changes': [{'op': 'add', 'condition_id': None, 'source_text': '无水印', 'group_id': None}]}
        runtime = self.runtime([intent])
        result = runtime.classify_followup('客厅图片', [], '再多5张，只要无水印', 'text',
            task_context={'structured_changes': True, 'requirement_spec': spec, 'target_count': 20})
        self.assertEqual(result['kind'], 'requirement')
        self.assertEqual(result['count_edits'][0]['value'], 5)
        self.assertEqual(result['cleaning_changes'][0]['source_text'], '无水印')
        self.assertEqual(len(self.transport.calls), 1)
        self.assertFalse(self.transport.calls[0]['parallel_tool_calls'])
        consistent = ModelRuntime._validate_followup({**intent, 'target_count': 25}, '再多5张，只要无水印', [],
                                                    {'structured_changes': True, 'requirement_spec': spec})
        self.assertIsNone(consistent['target_count'])
        mislabeled = ModelRuntime._validate_followup({**intent, 'kind': 'cleaning', 'cleaning_instruction': '只要无水印'},
            '再多5张，只要无水印', [], {'structured_changes': True, 'requirement_spec': spec})
        self.assertEqual(mislabeled['kind'], 'requirement')
        self.assertNotIn('cleaning_instruction', mislabeled)
        with self.assertRaises(ServiceError):
            ModelRuntime._validate_followup({**intent, 'target_count': 99}, '再多5张，只要无水印', [],
                                           {'structured_changes': True, 'requirement_spec': spec})
        with self.assertRaises(ServiceError):
            ModelRuntime._validate_followup({**intent, 'requested_terms_per_group': 3}, '再多5张，只要无水印', [],
                                           {'structured_changes': True, 'requirement_spec': spec})

    def test_ordinary_conversation_can_bypass_dsh_without_changing_selected_model(self):
        from dsh_retrieval.adapters.dsh.runtime import DshModelRuntime
        runtime = self.runtime(['请说明希望保留什么画面。'])
        with TemporaryDirectory() as directory:
            harness = DshModelRuntime(runtime.profiles.values(), python='/unavailable/python',
                                      home=directory, opener=self.transport)
            direct = harness.conversation_runtime()
            self.assertIs(type(direct), ModelRuntime)
            answer = direct.classify_followup('客厅图片', [], '能怎么筛选', 'text')
            self.assertEqual(answer['kind'], 'answer')
            self.assertEqual(self.transport.calls[0]['model'], 'text-v1')
            self.assertFalse(list(Path(directory).glob('*/retrieval-*')))

    def test_structured_followup_uses_current_condition_ids_and_grounded_operations(self):
        from dsh_retrieval.requirement_changes import conditions_for
        spec = {'raw_request': '狗，不要水印'}
        rows = conditions_for(spec)
        runtime = self.runtime([{'kind': 'cleaning', 'target_count': None,
            'requested_terms_per_group': None, 'count_edits': [], 'cleaning_changes': [
                {'op': 'remove', 'condition_id': rows[1]['condition_id'], 'source_text': None, 'group_id': None},
                {'op': 'add', 'condition_id': None, 'source_text': '画面清晰', 'group_id': None}]}])
        result = runtime.classify_followup('狗，不要水印', [], '删掉第二条，增加画面清晰', 'text',
            task_context={'structured_changes': True, 'cleaning_conditions': rows, 'requirement_spec': spec})
        self.assertEqual([row['op'] for row in result['cleaning_changes']], ['remove', 'add'])
        self.assertEqual(len(self.transport.calls), 1)

    def test_task_conversation_endpoint_saves_preview_without_second_model_call(self):
        from http.server import ThreadingHTTPServer
        import threading
        from urllib.request import Request, urlopen
        from dsh_retrieval.api import create_handler
        from draft_helpers import BODY, make_service, confirmation
        from dsh_retrieval.adapters.memory import InMemoryTaskRepository
        from dsh_retrieval.models import Actor
        from dsh_retrieval.requirement_changes import conditions_for
        outputs = [{'kind': 'cleaning', 'target_count': None, 'requested_terms_per_group': None,
                    'cleaning_changes': [{'op': 'add', 'condition_id': None,
                                          'source_text': '无水印', 'group_id': None}]}]
        runtime = self.runtime(outputs)
        service = make_service(InMemoryTaskRepository())
        actor = Actor('tenant', 'owner')
        body = deepcopy(BODY)
        body['query_spec']['rules'] = [{'rule_id': 'semantic-match', 'predicate_ref': 'image.semantic-match@1',
            'operator': 'eq', 'value': True, 'hardness': 'hard', 'origin': 'user_confirmed'}]
        task, _ = service.create_task(actor, 'create', confirmation(service, actor, body=body))
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(service, model_runtime=runtime))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def call(body, key, path='conversation-turn', owner='owner'):
            request = Request(f'http://127.0.0.1:{server.server_port}/v1/retrieval/tasks/{task["task_id"]}/{path}',
                data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type': 'application/json',
                'X-Tenant-Id': 'tenant', 'X-User-Id': owner, 'Idempotency-Key': key})
            try:
                with urlopen(request) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.load(error)
        try:
            base = {'text': '增加无水印', 'llm_id': 'text', 'expected_revision': 1,
                    'expected_task_version': task['task_version']}
            status, result = call(base, 'turn1')
            self.assertEqual(status, 200, result)
            draft = result['feedback_draft']
            self.assertEqual(result['cleaning_review']['cleaning_plan_preview']['after_count'], 2)
            self.assertEqual(len(self.transport.calls), 1)
            self.assertEqual(service.get_task(actor, task['task_id'])['confirmed_revision'], 1)
            status, repeated = call(base, 'turn1')
            self.assertEqual(status, 200, repeated)
            self.assertEqual(repeated['feedback_draft'], draft)
            self.assertEqual(len(self.transport.calls), 1)
            status, receipt = call(None, 'read', 'conversation-turns/turn1')
            self.assertEqual(status, 200, receipt)
            self.assertEqual(receipt['status'], 'completed')
            status, denied = call(None, 'read', 'conversation-turns/turn1', owner='intruder')
            self.assertNotEqual(status, 200, denied)
            rows = conditions_for(draft['proposal']['query_spec'])
            outputs.append({'kind': 'cleaning', 'target_count': None, 'requested_terms_per_group': None,
                'cleaning_changes': [{'op': 'replace', 'condition_id': rows[-1]['condition_id'],
                                      'source_text': '不要文字', 'group_id': None}]})
            ref = {key: draft[key] for key in ('draft_id', 'draft_version', 'proposal_digest')}
            status, result = call({**base, 'text': '第二条改成不要文字', 'feedback_ref': ref}, 'turn2')
            self.assertEqual(status, 200, result)
            self.assertEqual(result['feedback_draft']['draft_version'], 2)
            self.assertEqual(result['cleaning_review']['cleaning_plan_preview']['nodes'][-1]['source'], '不要文字')
            saved = result['feedback_draft']
            status, result = call({'draft_id': saved['draft_id'], 'expected_draft_version': saved['draft_version'],
                'proposal_digest': saved['proposal_digest'], 'model_selection': {'llm': '', 'vlm': ''}},
                'restore', 'feedback-suggestions')
            self.assertEqual(status, 200, result)
            self.assertEqual(result['status'], 'reviewable')
            self.assertEqual(result['cleaning_plan_preview']['nodes'][-1]['source'], '不要文字')
            status, obsolete = call(base, 'turn1')
            self.assertEqual(status, 200, obsolete)
            self.assertFalse(obsolete['is_current'])
            status, result = call({**base, 'text': '第二条改成不要文字', 'feedback_ref': ref}, 'turn3')
            self.assertEqual(status, 409)
            self.assertEqual(len(self.transport.calls), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_cleaning_advisory_alias_cannot_change_image_quantity(self):
        proposal = {'kind': 'cleaning', 'target_count': None,
                    'requested_terms_per_group': None, 'count_edits': [],
                    'expansion_instruction': '排除模糊图片'}
        result = ModelRuntime._validate_followup(dict(proposal), '太模糊的去掉', [], {'target_count': 10})
        self.assertEqual(result['cleaning_instruction'], '排除模糊图片')
        self.assertNotIn('expansion_instruction', result)
        self.assertIsNone(result['target_count'])
        with self.assertRaises(ServiceError):
            ModelRuntime._validate_followup({**proposal, 'count_edits': [
                {'target': 'images', 'labels': [], 'operation': 'add', 'value': 1}]},
                '增加一个检测', [], {'target_count': 10})

    def test_explicit_new_cleaning_node_is_routed_without_losing_context(self):
        runtime = self.runtime([])
        for text in ('新增一个检测节点检查是否有水印', '增加一个节点检查两只动物是否同框', '清洗时再检查有没有文字'):
            result = runtime.classify_followup('大象和老虎合影10张', [], text, 'text',
                task_context={'cleaning_plan': [{'node_id': 'step_1', 'source': '大象和老虎合影'}]})
            self.assertEqual(result['kind'], 'cleaning')
        self.assertEqual(self.transport.calls, [])

    def test_slow_search_question_does_not_invent_causes_or_ignore_completion(self):
        from test_workflow_state import status_context
        runtime = self.runtime([])
        for question in ('为什么检索这么慢', '检索怎么这么慢？', '检索速度很慢', '搜索太慢了'):
            answer = runtime.classify_followup('大象和老虎合影10张', [], question, '', task_context=status_context())
            self.assertTrue(answer['status_requested'])
            self.assertIn('检索已结束', answer['reply'])
            self.assertIn('尚未确认', answer['reply'])
            self.assertNotIn('网络', answer['reply'])
            self.assertNotIn('数据量', answer['reply'])
        reply = runtime.classify_followup('大象和老虎合影10张', [], '为什么检索这么慢', '',
                                           task_context=status_context(stopped=False))['reply']
        self.assertIn('没有足够', reply)
        self.assertEqual(self.transport.calls, [])

    def test_status_question_uses_execution_facts_without_model_guessing(self):
        from test_workflow_state import status_context
        runtime = self.runtime([])
        groups = [{'primary_label': '毛驴', 'auxiliary_terms': ['毛驴在田野']}]
        answer = runtime.classify_followup('毛驴10张', groups, '接下来做什么', '',
            task_context=status_context(confirmed=True), recent_messages=[{'role': 'assistant', 'text': '正在检索，请等待'}])
        self.assertEqual(answer['kind'], 'answer')
        self.assertIn('检索已结束', answer['reply'])
        self.assertIn('确认清洗方案', answer['reply'])
        self.assertNotIn('预计', answer['reply'])
        self.assertIn('检索正在进行', runtime.classify_followup('毛驴10张', groups, '现在什么状态',
            '', task_context=status_context(stopped=False))['reply'])
        self.assertIn('清洗正在进行', runtime.classify_followup('毛驴10张', groups, '下一步怎么做',
            '', task_context=status_context(cleaning=True))['reply'])
        self.assertEqual(self.transport.calls, [])

    def test_model_status_routing_and_unsupported_execution_claims_use_the_shared_projection(self):
        from test_workflow_state import status_context
        context = status_context(confirmed=True)
        for output in ({'kind': 'status', 'target_count': None, 'requested_terms_per_group': None},
                       '现在正在检索目标图片，预计会找到10张。'):
            runtime = self.runtime([output])
            answer = runtime.classify_followup('狗10张', [], '嗯，这个事情呢？', 'text', task_context=context)
            self.assertTrue(answer['status_requested'])
            self.assertEqual(answer['reply'], context['workflow']['status_reply'])
            self.assertNotIn('预计', answer['reply'])
            self.assertEqual(len(self.transport.calls), 1)

    def test_unchanged_constraint_proposal_is_read_only(self):
        result = ModelRuntime._validate_dialogue_proposal(
            {'action': 'revise_terms', 'constraints': '不要水印',
             'reply': '词表还在等待确认，确认后才会检索。'},
            {'constraints': ['不要水印'], 'target_count': 12},
            '为什么还没开始检索？', None)
        self.assertEqual(result['action'], 'answer')
        self.assertIsNone(result['constraints'])

    def test_quantity_after_natural_clarification_keeps_scene_in_expansion_input(self):
        from dsh_retrieval.dialogue import DialogueCoordinator, DialogueStore
        from dsh_retrieval.adapters.projects import LocalProjectAccess
        from dsh_retrieval.models import Actor
        runtime = self.runtime([{'action': 'update', 'media_type': 'image',
            'requirement_edits': [{'op': 'add_category', 'targets': [], 'label': '小狗',
                'scene': '小狗在海边散步', 'source_text': '小狗在海边散步'}],
            'constraints': '不要水印', 'reply': '需要多少张这样的图片？'}])
        coordinator = DialogueCoordinator(DialogueStore(), runtime,
            LocalProjectAccess([('tenant-a', 'user-a', 'demo', 'default')]))
        actor = Actor('tenant-a', 'user-a')
        first = coordinator.turn(actor, 'demo', {'session_id': None, 'expected_version': 0,
            'phase': 'request', 'llm_id': 'text', 'message': '想找小狗在海边散步，不要水印',
            'generate_terms_when_complete': True})
        second = coordinator.turn(actor, 'demo', {'session_id': first['session_id'],
            'expected_version': first['version'], 'phase': 'request', 'llm_id': 'text',
            'message': '12张', 'generate_terms_when_complete': True})
        self.assertEqual(second['stage'], 'terms')
        self.assertIn('小狗在海边散步', second['request']['effective_request'])
        self.assertIn('不要水印', second['request']['effective_request'])
        sent = self.transport.calls[-1]['messages']
        self.assertIn('想找小狗在海边散步，不要水印', [item['content'] for item in sent])

    def test_light_conversation_effort_is_scoped_to_known_models_and_dialogue(self):
        from dsh_retrieval.dialogue import PROMPT
        runtime = self.runtime(['可以先聊画面内容。', {'parent_label': None, 'groups': []}])
        profile = ModelProfile('glm', 'llm', ModelConfig('fixture', 'glm-5.3', 'https://fixture.invalid/v1', 'fake'))
        runtime._request(profile, PROMPT, '你好')
        self.assertEqual(self.transport.calls[-1]['reasoning_effort'], 'low')
        runtime._request(profile, 'Return JSON expansion', '生成词表')
        self.assertNotIn('reasoning_effort', self.transport.calls[-1])

    def test_conversation_answers_use_text_and_draft_edits_use_tools_across_turns(self):
        from dsh_retrieval.dialogue import DialogueCoordinator, DialogueStore
        from dsh_retrieval.adapters.projects import LocalProjectAccess
        from dsh_retrieval.models import Actor
        runtime = self.runtime([
            '可以先聊画面内容，数量确定后再整理检索词。',
            {'action': 'update', 'subject': '小狗在雪地玩耍', 'media_type': 'image',
             'requirement_edits': [{'op': 'add_category', 'targets': [], 'label': '小狗',
                 'scene': '小狗在雪地玩耍', 'source_text': '小狗在雪地玩耍'}],
             'constraints': '不要水印', 'reply': '画面和无水印要求记下了，想要几张？'},
            '数量用来确定交付目标，你也可以先补充画面要求。',
            '这些词分别覆盖了奔跑和趴卧两种画面。',
            {'action': 'revise_terms', 'count_edits': [
                {'target': 'images', 'labels': [], 'operation': 'scale', 'value': .5}],
             'reply': '目标减到 10 张，检索词沿用。'},
            '现在要 10 张小狗在雪地玩耍的图片，要求无水印。'])
        actor = Actor('tenant-a', 'user-a')
        projects = LocalProjectAccess([('tenant-a', 'user-a', 'demo', 'default')])
        with TemporaryDirectory() as folder:
            store = DialogueStore(Path(folder) / 'dialogue.sqlite3')
            coordinator = DialogueCoordinator(store, runtime, projects)
            current = {'session_id': None, 'version': 0, 'stage': 'request'}
            groups = [{'primary_label': '小狗', 'auxiliary_terms': ['小狗在雪地奔跑', '小狗趴在雪地上']}]
            def turn(text):
                nonlocal current
                body = {'session_id': current['session_id'], 'expected_version': current['version'],
                        'phase': current['stage'], 'llm_id': 'text', 'message': text,
                        'generate_terms_when_complete': True}
                if current['stage'] == 'terms':
                    body.update(current_groups=groups, current_target_count=current['request']['target_count'])
                current = coordinator.turn(actor, 'demo', body)
                return current
            self.assertEqual(turn('你好，先聊聊怎么找素材')['action'], 'answer')
            self.assertNotIn('response_format', self.transport.calls[0])
            self.assertEqual(self.transport.calls[0]['tools'][0]['function']['name'], 'update_request_draft')
            supplied = turn('想找小狗在雪地玩耍，先不要水印')
            self.assertEqual(supplied['reply'], '画面和无水印要求记下了，想要几张？')
            self.assertEqual(supplied['question'], 'target_count')
            self.assertEqual(turn('为什么先问数量')['request'], supplied['request'])
            self.assertEqual(turn('20张')['stage'], 'terms')
            explained = turn('这些词为什么这么写？')
            self.assertEqual(explained['action'], 'answer')
            changed = turn('图片数量减半，词不要动')
            self.assertEqual(changed['request']['target_count'], 10)
            self.assertEqual(changed['reply'], '目标减到 10 张，检索词沿用。')
            self.assertEqual(turn('再说一下我现在要什么')['action'], 'answer')
            context = json.loads(self.transport.calls[-1]['messages'][1]['content'])
            self.assertEqual(context['current_request']['target_count'], 10)
            self.assertEqual(context['current_request']['constraints'], '不要水印')
            self.assertNotIn('recent_messages', context)
            self.assertEqual(len(self.transport.calls), 6)
            reopened = DialogueCoordinator(DialogueStore(Path(folder) / 'dialogue.sqlite3'), runtime, projects)
            restored = reopened.snapshot(actor, 'demo', current['session_id'])
            self.assertEqual(restored['request'], current['request'])
            self.assertEqual(restored['messages'][-1]['text'], current['reply'])

    def test_plain_text_cannot_claim_or_encode_an_unsubmitted_change(self):
        from dsh_retrieval.dialogue import PROMPT
        runtime = self.runtime(['我已经把目标改成3张。', '{"action":"update","target_count":3}'])
        for _ in range(2):
            with self.assertRaises(ServiceError) as rejected:
                runtime._request(runtime.select('text', 'llm'), PROMPT, '为什么这样选词？')
            self.assertEqual(rejected.exception.code, 'MODEL_OUTPUT_INVALID')
        runtime = self.runtime(['当前仍在检索中，尚无已发布的交付结果。'])
        answer = runtime.classify_followup('找20张狗图片', [], '任务怎么样了？', 'text',
                                           task_context={'run_status': 'RUNNING', 'target_count': 20})
        self.assertEqual(answer['kind'], 'answer')
        self.assertIsNone(answer['target_count'])

    def test_exact_group_term_count_followup_skips_model_and_preserves_other_groups(self):
        runtime = self.runtime([])
        groups = [{'primary_label': '猫', 'auxiliary_terms': ['猫在草地', '猫在窗边', '猫在树下']},
                  {'primary_label': '狗', 'auxiliary_terms': ['狗在草地', '狗在室内']}]
        result = runtime.classify_followup('找20张猫狗图片', groups,
            '把猫组检索词改成2个，其他不变', 'text')
        self.assertEqual(result['count_edits'], [
            {'target': 'terms', 'labels': ['猫'], 'operation': 'set', 'value': 2}])
        self.assertTrue(result['term_count_only'])
        self.assertEqual(self.transport.calls, [])
        unchanged = runtime.classify_followup('找20张猫狗图片', groups,
            '猫组检索词改成3个，其他不变', 'text')
        self.assertEqual(unchanged['kind'], 'answer')
        self.assertEqual(self.transport.calls, [])
        runtime = self.runtime([{'kind': 'clarify', 'target_count': None,
                                 'requested_terms_per_group': None, 'reply': '狗组需要怎样调整？'}])
        runtime.classify_followup('找20张猫狗图片', groups,
            '猫组检索词改成2个，狗组多找户外场景', 'text')
        self.assertEqual(len(self.transport.calls), 1)

    def test_vlm_cleaning_plan_uses_selected_vision_model_and_requires_coverage(self):
        runtime = self.runtime([{'instructions': ['检查有人和狗', '检查画面无水印']}])
        binding = runtime.binding('text', 'vision')
        self.assertEqual(runtime.plan_vlm_cleaning(('有人和狗', '画面无水印'), binding),
                         ('检查有人和狗', '检查画面无水印'))
        self.assertEqual(self.transport.calls[0]['model'], 'vision-v1')
        self.assertEqual(json.loads(self.transport.calls[0]['messages'][1]['content'])['chunks'],
                         ['有人和狗', '画面无水印'])
        runtime = self.runtime([{'instructions': ['检查有人和狗']},
                                {'instructions': ['检查有人和狗']}])
        with self.assertRaises(ServiceError) as caught:
            runtime.plan_vlm_cleaning(('有人和狗', '画面无水印'),
                                      runtime.binding('text', 'vision'))
        self.assertEqual(caught.exception.code, 'MODEL_OUTPUT_INVALID')
        self.assertEqual(len(self.transport.calls), 2)

    def test_vlm_cleaning_plan_repairs_one_invalid_shape_without_losing_chunks(self):
        runtime = self.runtime([{'instructions': ['遗漏了一条']},
                                {'instructions': ['检查有人和狗', '检查画面无水印']}])
        binding = runtime.binding('text', 'vision')
        self.assertEqual(runtime.plan_vlm_cleaning(('有人和狗', '画面无水印'), binding),
                         ('检查有人和狗', '检查画面无水印'))
        self.assertEqual(len(self.transport.calls), 2)
        repaired = json.loads(self.transport.calls[1]['messages'][1]['content'])
        self.assertEqual(repaired['chunks'], ['有人和狗', '画面无水印'])
        self.assertIn('repair', repaired)

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
        self.assertNotIn('recent_messages', context)
        self.assertEqual(self.transport.calls[0]['messages'][2:-1],
                         [{'role': item['role'], 'content': item['text']} for item in state['messages']])
        self.assertIsNone(context['reference_request'])
        self.assertFalse(context['execution_context']['search_started'])
        sent_messages = self.transport.calls[0]['messages']
        self.assertEqual(sent_messages[-1], {'role': 'user', 'content': '它减半吧，图再少二十张'})
        self.assertEqual(sent_messages[-2], {'role': 'assistant', 'content': '希望减少多少？'})

    def test_new_request_reference_is_context_not_latest_user_message(self):
        runtime = self.runtime([{'action': 'update', 'subject': '猫', 'target_count': 20,
            'requirement_edits': [{'op': 'add_category', 'targets': [], 'label': '猫',
                'scene': '猫', 'source_text': '猫'}],
            'media_type': 'image', 'constraints': '无水印'}])
        state = {'stage': 'request', 'question': None, 'messages': [],
                 'reference_request': '找10张狗图片；无水印'}
        result = runtime.interpret_dialogue_turn(state, '再找20张猫，条件一样', 'text')
        self.assertEqual(result['subject'], '猫')
        context = json.loads(self.transport.calls[0]['messages'][1]['content'])
        self.assertEqual(context['reference_request'], '找10张狗图片；无水印')
        self.assertEqual(self.transport.calls[0]['messages'][-1]['content'], '再找20张猫，条件一样')

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

    def test_single_group_term_reduction_recovers_explicit_target(self):
        groups = [{'primary_label': '人与宠物合影',
                   'auxiliary_terms': [f'人与宠物合影场景{i}' for i in range(100)]}]
        runtime = self.runtime([{'kind': 'expansion', 'target_count': None,
                                 'requested_terms_per_group': None, 'count_edits': [],
                                 'expansion_instruction': '将召回词减少到两个'},
                                {'kind': 'expansion', 'target_count': None,
                                 'requested_terms_per_group': 2, 'count_edits': [],
                                 'expansion_instruction': '将召回词减少到两个'}])
        result = runtime.classify_followup('找1000张人与宠物合影', groups,
                                           '检索词减少到两个', 'text',
                                           task_context={'target_count': 1000})
        self.assertEqual(result['requested_terms_per_group'], 2)
        self.assertIsNone(result['target_count'])
        self.assertTrue(result['term_count_only'])
        self.assertFalse(self.transport.calls)

        runtime = self.runtime([{'kind': 'expansion', 'target_count': None,
                                 'requested_terms_per_group': 2, 'count_edits': [],
                                 'term_count_only': True}])
        result = runtime.classify_followup('找1000张人与宠物合影', groups,
                                           '检索词减少到两个', 'text',
                                           task_context={'target_count': 1000})
        self.assertTrue(result['term_count_only'])

        runtime = self.runtime([{'kind': 'expansion', 'target_count': None,
                                 'requested_terms_per_group': 2, 'count_edits': []}])
        result = runtime.classify_followup('找1000张人与宠物合影', groups,
                                           '搜索短语收敛成俩', 'text',
                                           task_context={'target_count': 1000})
        self.assertEqual(result['requested_terms_per_group'], 2)

        runtime = self.runtime([{'kind': 'expansion', 'target_count': None,
                                 'requested_terms_per_group': 100, 'count_edits': []},
                                {'kind': 'expansion', 'target_count': None,
                                 'requested_terms_per_group': 2, 'count_edits': []}])
        result = runtime.classify_followup('找1000张人与宠物合影', groups,
                                           '检索词减少到两个', 'text',
                                           task_context={'target_count': 1000})
        self.assertEqual(result['requested_terms_per_group'], 2)
        self.assertFalse(self.transport.calls)

    def test_explicit_order_only_reduction_recovers_missing_model_flag(self):
        groups = [{'primary_label': '小孩抱狗',
                   'auxiliary_terms': ['词一', '词二', '词三']}]
        output = {'kind': 'expansion', 'target_count': None,
                  'requested_terms_per_group': None,
                  'count_edits': [{'target': 'terms', 'labels': ['小孩抱狗'],
                                   'operation': 'set', 'value': 2}],
                  'term_count_only': False,
                  'expansion_instruction': '按原顺序保留前两条'}
        runtime = self.runtime([output])
        result = runtime.classify_followup('小孩抱狗', groups,
            '只按原词表顺序保留前两个检索词，不替换词的内容，1张目标不变。',
            'text', task_context={'target_count': 1})
        self.assertTrue(result['term_count_only'])
        runtime = self.runtime([output])
        result = runtime.classify_followup('小孩抱狗', groups,
            '按原词表顺序保留前两个检索词，同时改成黑狗',
            'text', task_context={'target_count': 1})
        self.assertFalse(result['term_count_only'])

    def test_complete_quoted_term_list_routes_without_model(self):
        runtime = self.runtime([])
        groups = [{'primary_label': '小孩抱狗', 'auxiliary_terms': ['旧词一', '旧词二']}]
        result = runtime.classify_followup('找1张小孩抱狗图片', groups,
            '检索词减少到两个，保留“小孩抱狗”和“小女孩在草地上抱着狗”，其他需求和1张目标不变。',
            'text', task_context={'target_count': 1})
        self.assertEqual(result['kind'], 'expansion')
        self.assertEqual(result['requested_terms_per_group'], 1)
        self.assertFalse(result['term_count_only'])
        self.assertFalse(self.transport.calls)

    def test_pure_six_term_revision_routes_without_model(self):
        runtime = self.runtime([])
        result = runtime.classify_followup('找1张小孩抱狗图片',
            [{'primary_label': '小孩抱狗', 'auxiliary_terms': ['词一', '词二']}],
            '检索词改成6个', 'text', task_context={'target_count': 1})
        self.assertEqual(result['requested_terms_per_group'], 6)
        self.assertTrue(result['term_count_only'])
        self.assertFalse(self.transport.calls)

    def test_independent_search_is_not_a_revision_of_current_task(self):
        runtime = self.runtime([
            {'kind': 'new_request', 'target_count': None, 'requested_terms_per_group': None,
             'count_edits': [], 'reply': '这是另一项检索需求。'}])
        groups = [{'primary_label': '狗', 'auxiliary_terms': ['宠物狗']}]
        result = runtime.classify_followup('找20张狗图片', groups, '另外找20张兔子图片', 'text',
                                           task_context={'target_count': 20, 'run_status': 'RUNNING'})
        self.assertEqual(result['kind'], 'new_request')
        self.assertIsNone(result['target_count'])
        self.assertEqual(result['count_edits'], [])
        self.assertIn('new_request', self.transport.calls[0]['messages'][0]['content'])
        invalid = self.runtime([{'kind': 'new_request', 'target_count': 20,
                                 'requested_terms_per_group': None}] * 2)
        with self.assertRaises(ServiceError) as caught:
            invalid.classify_followup('找20张狗图片', groups, '另外找20张兔子图片', 'text')
        self.assertEqual(caught.exception.code, 'MODEL_OUTPUT_INVALID')

    def test_image_count_only_change_can_retain_words(self):
        groups = [{'primary_label': '猫', 'auxiliary_terms': ['猫在树下']}]
        runtime = self.runtime([{'parent_label': None, 'groups': groups}])
        result = runtime.expand('找20张猫图片', 'text', 'vision', target_count=10,
                                current_target_count=20, feedback_text='图片只要一半，词表不变',
                                current_groups=groups, count_edits=[])
        self.assertEqual(result['plan']['groups'][0]['auxiliary_terms'], groups[0]['auxiliary_terms'])

    def test_dialogue_model_can_suggest_bounded_natural_reply(self):
        runtime = self.runtime([{'action': 'update', 'subject': '猫', 'target_count': None,
            'requirement_edits': [{'op': 'add_category', 'targets': [], 'label': '猫',
                'scene': '猫', 'source_text': '猫'}],
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
            self.assertNotIn('recent_messages', context)
            self.assertEqual(self.transport.calls[-1]['messages'][2]['content'], '少找点，主体还是狗，不要情绪，多找户外画面')
            self.assertEqual(context['task_context']['target_count'], 5)
            history = call('/v1/retrieval/conversations/' + task['conversation_id'])
            self.assertEqual([item['role'] for item in history['task_messages']],
                             ['user', 'assistant', 'user', 'assistant'])
            self.assertEqual(history['task_messages'][0]['text'],
                             '少找点，主体还是狗，不要情绪，多找户外画面')
            self.assertEqual(service.get_task(Actor('tenant', 'owner'), task['task_id'])['task_version'], task['task_version'])
            self.assertEqual(history['clarification_messages'], [])
            session_id, _ = dialogue.store.save(Actor('tenant', 'owner'), 'demo', None, None, {
                'messages': [{'role': 'user', 'text': '找狗'}, {'role': 'assistant', 'text': '几张？'}]})
            archived = call(f'/v1/retrieval/projects/demo/dialogue-sessions/{session_id}/archive',
                            {'task_id': task['task_id']})
            self.assertEqual(archived['message_count'], 2)
            self.assertEqual(call('/v1/retrieval/projects/demo/dialogue-sessions')['items'], [])
            history = call('/v1/retrieval/conversations/' + task['conversation_id'])
            self.assertEqual([item['text'] for item in history['clarification_messages']], ['找狗', '几张？'])
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
        prompt = self.transport.calls[0]['messages'][0]['content']
        self.assertIn('同义词', prompt)
        self.assertIn('不编造别名或缩写', prompt)
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
                         [3, 3, 3, 3, 100])
        runtime = self.runtime([{'parent_label': '动物', 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': ['金毛', '柯基', '边牧']},
            {'primary_label': '猫', 'auxiliary_terms': ['橘猫', '布偶猫', '狸花猫']}]}])
        result = runtime.expand('找250张狗和猫的图片', 'text', 'vision', target_count=250)
        self.assertEqual([group['requested_expansion_count'] for group in result['plan']['groups']], [3, 3])
        self.assertIn('初始词表每类至少建议 3 个', self.transport.calls[0]['messages'][0]['content'])
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

    def test_initial_expansion_uses_each_explicit_category_count_and_repairs_short_list(self):
        one_each = {'parent_label': None, 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': ['狗在草地奔跑']},
            {'primary_label': '兔子', 'auxiliary_terms': ['兔子在草地吃草']}]}
        diverse = {'parent_label': None, 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': ['狗在草地奔跑', '狗在室内坐着', '狗的侧面肖像']},
            {'primary_label': '兔子', 'auxiliary_terms': ['兔子在草地吃草', '兔子在笼内站立', '兔子的正面肖像']}]}
        runtime = self.runtime([one_each, diverse])
        result = runtime.expand('查找20张狗20张兔子的照片', 'text', 'vision', target_count=40)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual([group['requested_expansion_count'] for group in result['plan']['groups']], [3, 3])
        sent = json.loads(self.transport.calls[0]['messages'][1]['content'])
        self.assertEqual(sent['category_targets'], [
            {'label': '狗', 'count': 20}, {'label': '兔子', 'count': 20}])
        self.assertEqual(sent['suggested_terms_by_category'], {'狗': 3, '兔子': 3})
        self.assertIn('明确类别缺少', self.transport.calls[1]['messages'][0]['content'])

        runtime = self.runtime([{'parent_label': None, 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': ['狗在草地', '狗在室内', '狗在街道']},
            {'primary_label': '兔子', 'auxiliary_terms': [f'兔子场景{i}' for i in range(5)]}]}])
        result = runtime.expand('20张狗，500张兔子', 'text', 'vision', target_count=520)
        self.assertEqual([group['requested_expansion_count'] for group in result['plan']['groups']], [3, 5])
        sent = json.loads(self.transport.calls[0]['messages'][1]['content'])
        self.assertEqual(sent['suggested_terms_by_category'], {'狗': 3, '兔子': 5})

        runtime = self.runtime([one_each])
        result = runtime.expand('查找20张狗20张兔子的照片，每类要一个检索词',
                                'text', 'vision', target_count=40)
        self.assertEqual([group['requested_expansion_count'] for group in result['plan']['groups']], [1, 1])
        sent = json.loads(self.transport.calls[0]['messages'][1]['content'])
        self.assertEqual(sent['requested_terms_per_group'], 1)

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
        current = [{'primary_label': '猫', 'auxiliary_terms': [f'猫场景{i}' for i in range(101)]}]
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
            self.assertGreater(suggested['cleaning_plan_preview']['after_count'], suggested['cleaning_plan_preview']['before_count'])
            self.assertIn('去掉文字', suggested['cleaning_plan_preview']['nodes'][-1]['source'])
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
                            'private upstream error', {'Retry-After': '8'}, BytesIO(b'private'))
        runtime = self.runtime([limited, {'parent_label': None, 'groups': [
            {'primary_label': '狗', 'auxiliary_terms': []}]}])
        with patch('dsh_retrieval.model_runtime.time.sleep') as sleep:
            result = runtime.expand('狗', 'text', 'vision')
        self.assertEqual(result['plan']['groups'][0]['primary_label'], '狗')
        self.assertEqual(len(self.transport.calls), 2)
        sleep.assert_called_once_with(8)

    def test_long_upstream_cooldown_does_not_retry_prematurely(self):
        limited = HTTPError('https://fixture.invalid/v1/chat/completions', 429,
                            'private upstream error', {'Retry-After': '99'}, BytesIO(b'private'))
        runtime = self.runtime([limited])
        with patch('dsh_retrieval.model_runtime.time.sleep') as sleep:
            with self.assertRaises(ServiceError) as caught:
                runtime.expand('狗', 'text', 'vision')
        self.assertEqual(caught.exception.code, 'MODEL_RATE_LIMIT')
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(len(self.transport.calls), 1)
        sleep.assert_not_called()

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
            if outputs and isinstance(outputs[-1], HTTPError) and outputs[-1].code == 503:
                self.assertTrue(raised.exception.retryable)

    def test_missing_catalog_model_reports_actionable_error_without_upstream_body(self):
        missing = HTTPError('https://fixture.invalid/v1/chat/completions', 404,
                            'test-secret', {}, BytesIO(b'{"error":{"code":"model_not_found","message":"test-secret"}}'))
        runtime = self.runtime([missing])
        with self.assertRaises(ServiceError) as raised:
            runtime.expand('狗', 'text', 'vision')
        self.assertEqual(raised.exception.code, 'MODEL_NOT_AVAILABLE')
        self.assertIn('HTTP 404', raised.exception.message)
        self.assertNotIn('test-secret', str(raised.exception))

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
