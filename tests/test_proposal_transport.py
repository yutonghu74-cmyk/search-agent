from copy import deepcopy
import json
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.model_runtime import ModelRuntime, ModelProfile
from dsh_retrieval.intent_model import ModelConfig
from dsh_retrieval.proposal_transport import ProposalReferences
from dsh_retrieval.requirement_changes import apply_requirement_changes
from test_followup_inputs import context_fixture
from test_model_runtime import Transport


class ProposalTransportTest(unittest.TestCase):
    def test_dsh_adapter_keeps_its_existing_tool_protocol(self):
        from dsh_retrieval.adapters.dsh.runtime import DshModelRuntime
        from dsh_retrieval.model_runtime import FOLLOWUP_ROUTING_PROMPT
        legacy = {'kind': 'answer', 'reply': '可以继续说明。', 'target_count': None, 'requested_terms_per_group': None}
        profile = ModelProfile('text', 'llm', ModelConfig('fixture', 'text', 'https://fixture.invalid/v1', 'secret'))
        with TemporaryDirectory() as directory:
            runtime = DshModelRuntime([profile], python='/unavailable', home=directory)
            with patch.object(runtime, '_request', return_value=legacy) as request:
                result = runtime.classify_followup('狗，不要水印', self.groups, '说一下这些条件', 'text', task_context=self.context)
            self.assertEqual(result['kind'], 'answer')
            self.assertEqual(request.call_args.args[1], FOLLOWUP_ROUTING_PROMPT)

    def setUp(self):
        self.groups, self.context = context_fixture()
        self.context['reference_binding'] = {'task_id': 't', 'task_version': 3, 'draft_version': 1}
        self.refs = ProposalReferences(self.context)

    def test_short_edits_decode_to_existing_business_model_with_scope(self):
        original = deepcopy(self.context)
        projected = self.refs.project({'task_context': self.context})
        self.assertEqual(projected['task_context']['cleaning_conditions'][0]['condition_id'], 'c1')
        self.assertEqual(projected['task_context']['requirement_spec']['grouped_expansion']['groups'][0]['group_id'], 'g1')
        self.assertEqual(self.context, original)
        proposal = {'kind': 'requirement', 'scope': self.refs.scope,
            'images': {'op': 'add', 'value': 5},
            'conditions': [{'op': 'replace', 'ref': 'c1', 'text': '猫'}, {'op': 'add', 'text': '画面清晰'}],
            'groups': [{'op': 'replace', 'ref': 'g1', 'label': '猫', 'words': ['窗边的猫'], 'source': '狗换成猫'}]}
        decoded = self.refs.decode(proposal)
        self.assertEqual(decoded['cleaning_changes'][0]['condition_id'], self.context['cleaning_conditions'][0]['condition_id'])
        text = '狗换成猫，多5张，画面清晰'
        validated = ModelRuntime._validate_followup(decoded, text, self.groups, self.context)
        updated = apply_requirement_changes(self.context['requirement_spec'], {key: validated[key] for key in ('count_edits', 'cleaning_changes', 'retrieval_changes')}, text)
        self.assertEqual(updated['target_count'], 15)
        self.assertEqual([row['text'] for row in updated['cleaning_conditions']], ['猫', '不要水印', '画面清晰'])
        self.assertNotIn('"c1"', json.dumps(updated))

    def test_unknown_old_scope_and_cross_scope_movement_are_rejected(self):
        base = {'kind': 'requirement', 'scope': self.refs.scope}
        for proposal in (
                {**base, 'scope': 's_000000000000', 'images': {'op': 'set', 'value': 20}},
                {**base, 'conditions': [{'op': 'remove', 'ref': 'c99'}]},
                {**base, 'groups': [{'op': 'remove', 'ref': 'g99', 'source': '删除'}]},
                {**base, 'conditions': [{'op': 'replace', 'ref': 'c1', 'text': '猫', 'group': 'g1'}]},
                {**base, 'message': '已更新', 'images': {'op': 'set', 'value': 20}},
                {'kind': 'answer', 'message': '你好', 'conditions': [{'op': 'remove', 'ref': 'c1'}]}):
            with self.subTest(proposal=proposal), self.assertRaises(ServiceError):
                self.refs.decode(proposal)
        changed = deepcopy(self.context)
        changed['reference_binding']['draft_version'] = 2
        with self.assertRaises(ServiceError):
            ProposalReferences(changed).decode({**base, 'images': {'op': 'set', 'value': 20}})

    def test_new_schema_and_short_ids_reach_model_but_not_the_saved_proposal(self):
        transport = Transport([{'kind': 'requirement', 'scope': self.refs.scope,
                               'conditions': [{'op': 'add', 'text': '画面清晰'}]}])
        runtime = ModelRuntime([ModelProfile('text', 'llm', ModelConfig('fixture', 'text', 'https://fixture.invalid/v1', 'secret'))], opener=transport)
        result = runtime.classify_followup('狗，不要水印', self.groups, '加画面清晰', 'text', task_context=self.context)
        body = transport.calls[0]
        self.assertEqual(body['tools'][0]['function']['name'], 'submit_task_change')
        context = json.loads(body['messages'][1]['content'])
        self.assertEqual(context['reference_scope'], self.refs.scope)
        self.assertNotIn(self.context['cleaning_conditions'][0]['condition_id'], body['messages'][1]['content'])
        self.assertEqual(result['cleaning_changes'][0]['source_text'], '画面清晰')
        self.assertNotIn('scope', result)
        properties = body['tools'][0]['function']['parameters']['properties']['proposal']['properties']
        self.assertEqual(properties['conditions']['items']['properties']['ref']['enum'], ['c1', 'c2'])
        self.assertEqual(properties['groups']['items']['properties']['ref']['enum'], ['g1'])
        self.assertEqual(properties['scope']['enum'], [self.refs.scope])

    def test_repeated_unchanged_condition_is_not_a_second_detection_node(self):
        base = {'kind': 'requirement', 'scope': self.refs.scope}
        result = self.refs.decode({**base, 'conditions': [
            {'op': 'add', 'text': '不要水印'}, {'op': 'add', 'text': '画面清晰'}, {'op': 'add', 'text': '画面清晰'}]})
        self.assertEqual([row['source_text'] for row in result['cleaning_changes']], ['画面清晰'])
        result = self.refs.decode({**base, 'conditions': [
            {'op': 'remove', 'ref': 'c2'}, {'op': 'add', 'text': '不要水印'}]})
        self.assertEqual(len(result['cleaning_changes']), 2)

    def test_literal_single_subject_tracks_rename_even_when_not_echoed(self):
        decoded = self.refs.decode({'kind': 'requirement', 'scope': self.refs.scope,
            'groups': [{'op': 'replace', 'ref': 'g1', 'label': '猫', 'words': ['窗边的猫'], 'source': '狗改成猫'}],
            'conditions': [{'op': 'add', 'text': '画面清晰'}, {'op': 'add', 'text': '不要水印'}]})
        updated = apply_requirement_changes(self.context['requirement_spec'], {key: decoded[key] for key in
            ('count_edits', 'cleaning_changes', 'retrieval_changes')}, '狗改成猫，画面清晰，保留不要水印')
        self.assertEqual([row['text'] for row in updated['cleaning_conditions']], ['猫', '不要水印', '画面清晰'])
