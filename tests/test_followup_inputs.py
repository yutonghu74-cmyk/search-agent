from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from draft_helpers import BODY
from dsh_retrieval.expansion_models import preview_edited_groups, with_delivery_targets
from dsh_retrieval.followup_inputs import explicit_requirement, followup_context, normalize_requirement_proposal
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.model_runtime import ModelRuntime, ModelProfile
from dsh_retrieval.intent_model import ModelConfig
from dsh_retrieval.requirement_changes import conditions_for
from test_model_runtime import Transport
from test_workflow_state import status_context


def context_fixture():
    spec = deepcopy(BODY['query_spec'])
    spec['raw_request'] = '狗，不要水印'
    spec['cleaning_conditions'] = conditions_for(spec)
    spec['grouped_expansion'] = preview_edited_groups(None, [{'primary_label': '狗',
        'auxiliary_terms': ['草地的狗', '窗边的狗']}])['plan']
    groups = [{'primary_label': '狗', 'auxiliary_terms': ['草地的狗', '窗边的狗']}]
    return groups, {'structured_changes': True, 'requirement_spec': spec,
        'cleaning_conditions': spec['cleaning_conditions'], 'cleaning_plan': spec['cleaning_conditions'],
        'workflow': status_context()['workflow']}


class FollowupInputsTest(unittest.TestCase):
    def test_single_literal_subject_check_follows_reviewed_rename_without_repair(self):
        groups, context = context_fixture(); spec = context['requirement_spec']
        gid = spec['grouped_expansion']['groups'][0]['group_id']
        proposal = {'kind': 'requirement', 'target_count': None, 'requested_terms_per_group': None,
            'count_edits': [{'target': 'terms', 'labels': ['狗'], 'operation': 'set', 'value': 2}],
            'retrieval_changes': [{'op': 'replace', 'group_id': gid, 'primary_label': '猫',
                'auxiliary_terms': ['窗边的猫', '奔跑的猫'], 'source_text': '把狗改成猫'}],
            'cleaning_changes': [{'op': 'remove', 'condition_id': spec['cleaning_conditions'][0]['condition_id'], 'group_id': None},
                                 {'op': 'add', 'condition_id': None, 'source_text': '画面清晰', 'group_id': None}]}
        transport = Transport([proposal])
        runtime = ModelRuntime([ModelProfile('text', 'llm', ModelConfig('fixture', 'text', 'https://fixture.invalid/v1', 'secret'))], opener=transport)
        text = '把狗改成猫，词用窗边的猫和奔跑的猫，加画面清晰'
        intent = runtime.classify_followup(spec['raw_request'], groups, text, 'text', task_context=context)
        from dsh_retrieval.requirement_changes import apply_requirement_changes, preview_changes
        updated = apply_requirement_changes(spec, {key: intent.get(key, []) for key in ('count_edits', 'retrieval_changes', 'cleaning_changes')}, text)
        self.assertEqual([r['text'] for r in updated['cleaning_conditions']], ['猫', '不要水印', '画面清晰'])
        self.assertEqual(preview_changes(spec, updated)['changes'][0]['op'], 'replace')
        self.assertEqual(len(transport.calls), 1)

    def test_redundant_word_count_is_checked_against_complete_list(self):
        groups, context = context_fixture()
        spec = context['requirement_spec']; gid = spec['grouped_expansion']['groups'][0]['group_id']
        proposal = {'kind': 'requirement', 'count_edits': [
            {'target': 'terms', 'labels': ['狗'], 'operation': 'set', 'value': 2}],
            'retrieval_changes': [{'op': 'upsert', 'group_id': gid, 'primary_label': '猫',
                'auxiliary_terms': ['窗边的猫', '奔跑的猫'], 'source_text': '改成猫'}],
            'cleaning_changes': [{'op': 'remove', 'condition_id': 'existing', 'group_id': None}]}
        result = normalize_requirement_proposal(proposal, spec)
        self.assertEqual(result['count_edits'], [])
        self.assertIsNone(result['cleaning_changes'][0]['source_text'])
        self.assertNotIn('source_text', proposal['cleaning_changes'][0])
        alias = deepcopy(proposal)
        alias['retrieval_changes'][0]['op'] = 'replace'
        self.assertEqual(normalize_requirement_proposal(alias, spec)['retrieval_changes'][0]['op'], 'upsert')
        alias['retrieval_changes'][0]['group_id'] = None
        with self.assertRaises(ServiceError):
            normalize_requirement_proposal(alias, spec)
        for edit in ({'target': 'terms', 'labels': ['狗'], 'operation': 'set', 'value': 3},
                     {'target': 'terms', 'labels': ['狗'], 'operation': 'add', 'value': 2},
                     {'target': 'terms', 'labels': ['无关类别'], 'operation': 'set', 'value': 2}):
            with self.subTest(edit=edit), self.assertRaises(ServiceError):
                normalize_requirement_proposal({**proposal, 'count_edits': [edit]}, spec)

    def test_explicit_quantity_and_quoted_replacement_need_no_model(self):
        groups, context = context_fixture()
        runtime = ModelRuntime(opener=Mock(side_effect=AssertionError('network')))
        for text, operation, value in [('再加五张', 'add', 5), ('再多5张', 'add', 5),
                                       ('减少3张', 'add', -3), ('目标改为20张', 'set', 20),
                                       ('总共改成二十张，其他不变', 'set', 20)]:
            with self.subTest(text=text):
                result = runtime.classify_followup('狗，不要水印', groups, text, '', task_context=context)
                self.assertEqual(result['kind'], 'requirement')
                self.assertEqual(result['count_edits'], [{'target': 'images', 'labels': [], 'operation': operation, 'value': value}])
        result = runtime.classify_followup('狗，不要水印', groups, '把“草地的狗”换成“奔跑的狗”', '', task_context=context)
        self.assertEqual(result['kind'], 'requirement')
        self.assertEqual(result['retrieval_changes'][0]['auxiliary_terms'], ['奔跑的狗', '窗边的狗'])
        runtime.opener.open.assert_not_called()

    def test_extra_conditions_ambiguity_and_object_counts_are_not_shortcut(self):
        groups, context = context_fixture()
        for text in ('每张一只狗，20张图片', '改成20张，并且不要水印', '每组增加5张',
                     '增加一个检测节点', '再多一点', '把“草地的狗”换成“奔跑的狗”，再加无水印'):
            self.assertIsNone(explicit_requirement(text, groups, context), text)
        self.assertIsNone(explicit_requirement('再加5张', groups, {**context, 'has_unconfirmed_preview': True}))
        runtime = ModelRuntime()
        for text in ('改成10001张', '减少10张'):
            self.assertEqual(runtime.classify_followup('狗', groups, text, '', task_context=context)['kind'], 'clarify')
        self.assertEqual(runtime.classify_followup('狗', groups, '改成10张', '', task_context=context)['kind'], 'answer')
        context['requirement_spec']['grouped_expansion'] = with_delivery_targets(
            context['requirement_spec']['grouped_expansion'], {'狗': 10})
        result = runtime.classify_followup('狗', groups, '再多5张', '', task_context=context)
        self.assertEqual(result['kind'], 'clarify')
        self.assertEqual(result['count_edits'], [])

    def test_context_deduplication_preserves_conditions_scopes_facts_and_history(self):
        groups, context = context_fixture()
        original = deepcopy(context)
        spec = context['requirement_spec']
        context['workflow']['nodes'][1]['detail'] = '实际失败原因须保留'
        compact = followup_context(spec['raw_request'], groups, '下一条消息', context)
        effective = compact['task_context']['requirement_spec']
        self.assertEqual(compact['task_context']['cleaning_conditions'], spec['cleaning_conditions'])
        self.assertEqual(effective['grouped_expansion']['groups'][0]['group_id'], spec['grouped_expansion']['groups'][0]['group_id'])
        self.assertEqual(compact['current_groups'][0]['auxiliary_terms'], groups[0]['auxiliary_terms'])
        self.assertEqual(effective['refill_policy'], spec['refill_policy'])
        self.assertNotIn('auxiliary_terms', effective['grouped_expansion']['groups'][0])
        self.assertNotIn('cleaning_conditions', effective)
        self.assertNotIn('cleaning_plan', compact['task_context'])
        self.assertNotIn('raw_request', effective)
        self.assertEqual(compact['task_context']['workflow']['nodes'][1]['detail'], '实际失败原因须保留')
        self.assertEqual(context['requirement_spec'], original['requirement_spec'])
        history = [{'role': 'user' if index % 2 == 0 else 'assistant', 'text': '历史第' + str(index)} for index in range(20)]
        transport = Transport(['请继续。'])
        runtime = ModelRuntime([ModelProfile('text', 'llm', ModelConfig('fixture', 'text', 'https://fixture.invalid/v1', 'secret'))], opener=transport)
        runtime.classify_followup(spec['raw_request'], groups, '说一下这些词的含义', 'text', task_context=context, recent_messages=history)
        self.assertEqual([row['content'] for row in transport.calls[0]['messages'][2:-1]], [row['text'] for row in history])
        baseline = {'original_request': spec['raw_request'], 'current_groups': groups, 'feedback_text': '下一条消息', 'task_context': context}
        self.assertLess(len(json.dumps(compact, ensure_ascii=False)), len(json.dumps(baseline, ensure_ascii=False)))

    def test_single_repair_restores_full_facts_after_invalid_compact_proposal(self):
        groups, context = context_fixture()
        transport = Transport([{'kind': 'invalid', 'target_count': None, 'requested_terms_per_group': None}, '请继续。'])
        runtime = ModelRuntime([ModelProfile('text', 'llm', ModelConfig('fixture', 'text', 'https://fixture.invalid/v1', 'secret'))], opener=transport)
        runtime.classify_followup('狗，不要水印', groups, '能怎么调整', 'text', task_context=context)
        first = json.loads(transport.calls[0]['messages'][1]['content'])
        second = json.loads(transport.calls[1]['messages'][1]['content'])
        self.assertNotIn('auxiliary_terms', first['task_context']['requirement_spec']['grouped_expansion']['groups'][0])
        from dsh_retrieval.proposal_transport import ProposalReferences
        self.assertEqual(second['task_context'], ProposalReferences(context).project({'task_context': context})['task_context'])
        self.assertEqual(first['reference_scope'], second['reference_scope'])
        self.assertEqual(second['current_groups'], groups)
        self.assertIn('repair', second)
        self.assertEqual(len(transport.calls), 2)
