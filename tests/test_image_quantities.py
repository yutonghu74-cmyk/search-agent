import unittest

from dsh_retrieval.image_quantities import image_count_evidence, relative_image_count, is_agreement
from dsh_retrieval.dialogue import explicit_category_targets
from dsh_retrieval.model_runtime import ModelRuntime


class ImageQuantityTest(unittest.TestCase):
    def test_count_evidence_and_proposal_survive_validation(self):
        for text, expected in [('猫图来个五十', 50), ('整1,000张狗的图', 1000),
                               ('1万张猫图', 10000), ('80 pics of cats please', 80),
                               ('数量：80，内容：橘猫', 80), ('数量:80,内容:橘猫', 80), ('一百来张就行', 100),
                               ('每张图里有2只猫，一共要100张', 100),
                               ('我要2只猫一起睡觉的照片，50张', 50)]:
            with self.subTest(text=text):
                state = {'question': 'target_count', 'target_count': None, 'constraints': []}
                self.assertEqual(image_count_evidence(text, answering=True), {expected})
                result = ModelRuntime._validate_dialogue_proposal(
                    {'action': 'update', 'target_count': expected}, state, text, None)
                self.assertEqual(result['target_count'], expected)

    def test_object_numbers_and_unspecified_counts_are_not_totals(self):
        for text in ['2只猫一起睡觉', '来一些猫图', '每张图里有2只猫', '编号80的猫', '0张', '10001张', '1,00张', '-80张']:
            with self.subTest(text=text):
                self.assertEqual(image_count_evidence(text), set())
        self.assertEqual(explicit_category_targets('每张图里有2只猫，一共要100张'), [])

    def test_each_category_total_is_grounded(self):
        self.assertEqual(explicit_category_targets('猫图和狗图各50张'),
                         [{'label': '猫', 'count': 50}, {'label': '狗', 'count': 50}])
        result = ModelRuntime._validate_dialogue_proposal({'action': 'update', 'target_count': 100},
                    {'target_count': None}, '猫图和狗图各50张', None)
        self.assertEqual(result['target_count'], 100)
        self.assertEqual(explicit_category_targets('猫图和狗图各6000张'), [])

    def test_relative_operations_and_agreement_are_bounded(self):
        self.assertEqual(relative_image_count('再加50张', 100), 150)
        self.assertEqual(relative_image_count('减少五十张', 100), 50)
        for message, current in [('再加50张', None), ('减去100张', 100), ('再加50张', 9999)]:
            self.assertIsNone(relative_image_count(message, current))
        self.assertTrue(is_agreement('嗯嗯对'))
        self.assertFalse(is_agreement('对，不过要横图'))
        self.assertFalse(is_agreement('不对'))

    def test_empty_constraints_clear_but_null_preserves(self):
        state = {'target_count': 100, 'constraints': ['不要有人']}
        cleared = ModelRuntime._validate_dialogue_proposal(
            {'action': 'update', 'constraints': ''}, state, '算了有人也行，无所谓', None)
        self.assertEqual(cleared['constraints'], '')

    def test_multiturn_draft_add_clear_and_confirm(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from dsh_retrieval.dialogue import DialogueCoordinator, DialogueStore
        from dsh_retrieval.adapters.projects import LocalProjectAccess
        from dsh_retrieval.models import Actor
        from test_dialogue import FakeRuntime, proposal
        with TemporaryDirectory() as directory:
            runtime = FakeRuntime(proposal('update', subject='猫', target_count=100,
                                           media_type='image', constraints='不要有人'),
                                  proposal('update', constraints=''))
            coordinator = DialogueCoordinator(DialogueStore(Path(directory)/'dialogue.sqlite3'), runtime,
                LocalProjectAccess([('tenant-a', 'user-a', 'demo', 'default')]))
            actor = Actor('tenant-a', 'user-a')
            state = None
            for message in ['找100张猫图，不要有人', '再加50张', '算了有人也行，无所谓', '嗯嗯对']:
                state = coordinator.turn(actor, 'demo', {'session_id': state['session_id'] if state else None,
                    'expected_version': state['version'] if state else 0, 'phase': 'request',
                    'message': message, 'llm_id': 'fixture'})
            self.assertEqual(state['stage'], 'terms')
            self.assertEqual(state['request']['target_count'], 150)
            self.assertNotIn('不要有人', state['request']['effective_request'])
            self.assertIn('不要有人', state['request']['raw_request'])
            self.assertEqual(len(runtime.calls), 2)

    def test_plain_answer_cannot_claim_constraint_was_changed(self):
        from dsh_retrieval.errors import ServiceError
        from dsh_retrieval.conversation import natural_reply
        for text in ['已更新需求，包含有人也可以。', '已取消不要有人的限制。']:
            with self.subTest(text=text), self.assertRaises(ServiceError):
                natural_reply(text, 'request')
        with self.assertRaises(ServiceError):
            ModelRuntime._validate_dialogue_proposal(
                {'action': 'answer', 'reply': '已更新需求'},
                {'target_count': 100}, '有人也行', None)

    def test_constraint_change_repairs_false_success_before_saving(self):
        from dsh_retrieval.intent_model import ModelConfig
        from dsh_retrieval.model_runtime import ModelProfile
        from test_model_runtime import Transport
        state = {'stage': 'request', 'question': 'confirm_summary', 'messages': [],
                 'subject': '猫', 'target_count': 100, 'media_type': 'image',
                 'constraints': ['不要有人']}
        transport = Transport(['已更新需求，有人也可以。', {'action': 'update', 'constraints': ''}])
        runtime = ModelRuntime([ModelProfile('fixture', 'llm',
            ModelConfig('fixture', 'fixture', 'https://fixture.invalid/v1', 'fixture'))], opener=transport)
        with self.assertLogs('dsh_retrieval.model_runtime', level='WARNING'):
            result = runtime.interpret_dialogue_turn(state, '算了有人也行，无所谓', 'fixture')
        self.assertEqual(result['action'], 'update')
        self.assertEqual(result['constraints'], '')
        self.assertEqual(len(transport.calls), 2)
        self.assertIn('repair', transport.calls[-1]['messages'][1]['content'])

    def test_absolute_count_operation_uses_same_evidence(self):
        for text, count in [('1万张猫图', 10000), ('80 pics of cats please', 80)]:
            result = ModelRuntime._validate_dialogue_proposal({'action': 'update', 'count_edits': [
                {'target': 'images', 'labels': [], 'operation': 'set', 'value': count}]},
                {'target_count': None}, text, None)
            self.assertEqual(result['target_count'], count)
        self.assertEqual(image_count_evidence('1.00001万张'), set())

    def test_rejected_constraint_never_enters_effective_request(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from dsh_retrieval.dialogue import DialogueCoordinator, DialogueStore
        from dsh_retrieval.adapters.projects import LocalProjectAccess
        from dsh_retrieval.models import Actor
        from test_dialogue import FakeRuntime, proposal
        with TemporaryDirectory() as directory:
            runtime = FakeRuntime(proposal('update', subject='猫', target_count=100, media_type='image'),
                                  proposal('clarify', question='请重试这条消息'))
            coordinator = DialogueCoordinator(DialogueStore(Path(directory)/'dialogue.sqlite3'), runtime,
                LocalProjectAccess([('tenant-a', 'user-a', 'demo', 'default')]))
            actor = Actor('tenant-a', 'user-a')
            first = coordinator.turn(actor, 'demo', {'session_id': None, 'expected_version': 0,
                'phase': 'request', 'message': '找100张猫图', 'llm_id': 'fixture'})
            rejected = coordinator.turn(actor, 'demo', {'session_id': first['session_id'],
                'expected_version': first['version'], 'phase': 'request',
                'message': '不要有人', 'llm_id': 'fixture'})
            self.assertEqual(rejected['request']['effective_request'], first['request']['effective_request'])
            self.assertIn('不要有人', rejected['request']['raw_request'])
            self.assertNotIn('不要有人', rejected['request']['effective_request'])
            snapshot = coordinator.snapshot(actor, 'demo', first['session_id'])
            self.assertEqual(snapshot['request']['effective_request'], rejected['request']['effective_request'])
