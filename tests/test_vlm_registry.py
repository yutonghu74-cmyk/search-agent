from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import preview_edited_groups
from dsh_retrieval.intent_model import ModelConfig
from dsh_retrieval.model_runtime import ModelProfile, ModelRuntime
from dsh_retrieval.models import Actor
from dsh_retrieval.recipe_bridge import confirmed_rules_digest, compile_confirmed_recipe
from dsh_retrieval.vlm_registry import ConfirmedTaskVLMRegistry
from draft_helpers import BODY, confirmation, make_service


class ConfirmedTaskVLMRegistryTest(unittest.TestCase):
    def test_structured_conditions_compile_after_add_replace_and_remove_to_one(self):
        from dsh_retrieval.requirement_changes import conditions_for
        import json
        old = self.task(raw_request='客厅')
        spec = json.loads(old.confirmed_proposal_json)['query_spec']
        original_id = conditions_for(spec)[0]['condition_id']
        provider = ConfirmedTaskVLMRegistry(self.runtime)
        for index, (text, changes) in enumerate([
            ('新增无水印', [{'op': 'add', 'condition_id': None, 'source_text': '无水印', 'group_id': None}]),
            ('取消第一条，最后改成高清', None),
        ]):
            current = self.repository.get(self.actor, old.task_id)
            if changes is None:
                rows = conditions_for(json.loads(current.confirmed_proposal_json)['query_spec'])
                changes = [{'op': 'remove', 'condition_id': original_id, 'source_text': None, 'group_id': None},
                           {'op': 'replace', 'condition_id': rows[-1]['condition_id'], 'source_text': '高清', 'group_id': None}]
            draft, _ = self.service.save_feedback(self.actor, old.task_id, f'edit-{index}', {
                'text': text, 'scope': 'all', 'cleaning_feedback': True, 'cleaning_changes': changes,
                'expected_revision': current.confirmed_revision, 'expected_task_version': current.task_version})
            self.service.confirm_feedback(self.actor, old.task_id, f'approve-{index}', {
                'draft_id': draft['draft_id'], 'expected_draft_version': draft['draft_version'],
                'proposal_digest': draft['proposal_digest'], 'expected_task_version': draft['base_task_version'],
                'action': 'confirm'})
            current = self.repository.get(self.actor, old.task_id)
            registry = provider.build(self.actor, current)
            recipe = compile_confirmed_recipe(current, registry)
            self.assertEqual(len(recipe.steps), 2 if index == 0 else 1)
            self.assertEqual(recipe.steps[0].step_id, registry.instruction_plan[0]['node_id'])
        self.assertEqual(registry.instruction_plan[0]['source'], '高清')

    def setUp(self):
        self.runtime = ModelRuntime((
            ModelProfile('text', 'llm', ModelConfig(
                'fixture', 'text-v1', 'https://fixture.invalid/v1', 'secret')),
            ModelProfile('vision', 'vlm', ModelConfig(
                'fixture', 'vision-v1', 'https://fixture.invalid/v1', 'secret')),
        ))
        self.repository = InMemoryTaskRepository()
        self.service = make_service(self.repository)
        self.actor = Actor('tenant', 'owner')
        self.task_index = 0

    def task(self, *, rule=True, binding=True, raw_request=None):
        self.task_index += 1
        suffix = str(self.task_index)
        body = deepcopy(BODY)
        if raw_request is not None:
            body['query_spec']['raw_request'] = raw_request
        plan = preview_edited_groups(None, [{
            'primary_label': '客厅', 'auxiliary_terms': ['现代客厅'],
        }])
        if binding:
            plan = self.runtime.bind(
                plan, self.runtime.binding('text', 'vision'))
        body['query_spec']['grouped_expansion'] = plan['plan']
        if rule:
            body['query_spec']['rules'] = [{
                'rule_id': 'semantic-match',
                'predicate_ref': 'image.semantic-match@1',
                'operator': 'eq', 'value': True, 'hardness': 'hard',
                'origin': 'user_confirmed',
            }]
        confirmed, _ = self.service.create_task(
            self.actor, 'confirm-' + suffix, confirmation(
                self.service, self.actor, key='draft-' + suffix, body=body))
        return self.repository.get(self.actor, confirmed['task_id'])

    def test_confirmed_cleaning_feedback_adds_executable_detection_node(self):
        old = self.task(raw_request='明亮的客厅')
        provider = ConfirmedTaskVLMRegistry(self.runtime)
        self.assertEqual(len(provider.build(self.actor, old).instruction_plan), 1)
        draft, _ = self.service.save_feedback(self.actor, old.task_id, 'extra-check', {
            'text': '清洗时，再加一个检测：图片里不能有水印', 'scope': 'all', 'cleaning_feedback': True,
            'expected_revision': old.confirmed_revision, 'expected_task_version': old.task_version})
        self.service.confirm_feedback(self.actor, old.task_id, 'confirm-extra-check', {
            'draft_id': draft['draft_id'], 'expected_draft_version': draft['draft_version'],
            'proposal_digest': draft['proposal_digest'], 'expected_task_version': draft['base_task_version'],
            'action': 'confirm'})
        current = self.repository.get(self.actor, old.task_id)
        registry = provider.build(self.actor, current)
        recipe = compile_confirmed_recipe(current, registry)
        self.assertEqual(len(recipe.steps), 2)
        self.assertEqual(len(recipe.rules), 2)
        self.assertIn('明亮的客厅', registry.instruction_plan[0]['source'])
        self.assertEqual('图片里不能有水印', registry.instruction_plan[1]['source'])
        self.assertEqual(len(provider.build(self.actor, old).instruction_plan), 1)

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_builds_released_detector_from_confirmed_requirement_and_binding(self):
        task = self.task()
        provider = ConfirmedTaskVLMRegistry(self.runtime)
        registry = provider.build(self.actor, task)
        self.assertEqual(len(registry.capabilities), 1)
        detector = registry.detectors['vlm.requirement@1']
        with patch.object(self.runtime, 'assess', return_value={'verdict': 'pass'}):
            result = detector.detect(b'\xff\xd8\xffimage', None, None)
        self.assertEqual(result.facts, {'image.semantic-match@1': True})
        self.assertEqual(detector.source_review_digest, confirmed_rules_digest(task))
        execution = provider.describe(task)
        self.assertEqual(execution['model'], {
            'id': 'vision', 'provider': 'fixture', 'name': 'vision-v1'})
        self.assertIn('已确认需求：', execution['instruction'])
        self.assertEqual(execution['backend_ref'], 'vlm.requirement@1')

    def test_missing_rule_or_model_binding_fails_closed(self):
        self.assert_code('VLM_RULE_REQUIRED', lambda:
            ConfirmedTaskVLMRegistry(self.runtime).build(
                self.actor, self.task(rule=False)))
        self.assert_code('VLM_BINDING_REQUIRED', lambda:
            ConfirmedTaskVLMRegistry(self.runtime).build(
                self.actor, self.task(binding=False)))

    def test_model_configuration_drift_requires_reconfirmation(self):
        task = self.task()
        self.runtime.profiles['vision'] = ModelProfile(
            'vision', 'vlm', ModelConfig(
                'fixture', 'vision-v2', 'https://fixture.invalid/v1', 'secret'))
        self.assert_code('MODEL_BINDING_CHANGED', lambda:
            ConfirmedTaskVLMRegistry(self.runtime).build(self.actor, task))

    def test_multi_instruction_recipe_runs_same_vlm_for_each_node(self):
        from dsh_retrieval.adapters.cleaning.runner import GenericCleaningExecutor
        from dsh_retrieval.multi_rule_contracts import (
            ApprovedCleaningJob, CleaningBinding, CleaningCandidateRef, bytes_digest,
            candidate_manifest_digest)
        from dsh_retrieval.recipe_bridge import compile_confirmed_recipe
        task = self.task(raw_request='有人和狗互动，画面没有水印，主体清晰')
        provider = ConfirmedTaskVLMRegistry(self.runtime)
        registry = provider.build(self.actor, task)
        recipe = compile_confirmed_recipe(task, registry)
        self.assertEqual(len(recipe.steps), 3)
        self.assertEqual(len(recipe.rules), 3)
        self.assertEqual([node['source'] for node in provider.describe(task)['nodes']],
                         ['有人和狗互动', '画面没有水印', '主体清晰'])
        content = b'\xff\xd8\xffsynthetic-image'
        candidate = CleaningCandidateRef('candidate_1', 'v1', bytes_digest(content))
        job = ApprovedCleaningJob(CleaningBinding(
            'tenant', 'owner', 'demo', task.task_id, task.confirmed_revision,
            task.run_generation, task.task_version, 'operation_1',
            candidate_manifest_digest((candidate,)), recipe.recipe_digest,
            registry.release_digest), recipe, (candidate,))
        class Guard:
            def require_current(self, binding):
                pass
        class Reader:
            def read(self, candidate, binding):
                return content
        inputs = []
        def assess(requirement, image_bytes, mime, binding):
            inputs.append(requirement)
            return {'verdict': 'fail' if '画面没有水印' in requirement and
                    '节点原文：画面没有水印' in requirement else 'pass',
                    'reason': '可见水印'}
        with patch.object(self.runtime, 'assess', side_effect=assess):
            result = GenericCleaningExecutor(guard=Guard(), reader=Reader(),
                detectors=registry.require_recipe(recipe)).run(job)
        self.assertEqual(len(inputs), 3)
        self.assertEqual(result.candidates[0].verdict, 'fail')
        self.assertEqual([rule.verdict for rule in result.candidates[0].rules],
                         ['pass', 'fail', 'pass'])

    def test_multi_instruction_bounds_do_not_drop_conditions(self):
        from dsh_retrieval.vlm_instruction_plan import instruction_plan
        self.assert_code('VLM_PLAN_LIMIT', lambda: instruction_plan('，'.join(['条件'] * 9)))
        self.assert_code('VLM_PLAN_LIMIT', lambda: instruction_plan('条件' * 321))

    def test_confirmed_instructions_are_frozen_without_model_rewriting(self):
        from dsh_retrieval.recipe_bridge import compile_confirmed_recipe
        from dsh_retrieval.adapters.task_storage.sqlite import encode, decode
        task = self.task(raw_request='有人和狗，画面无水印')
        provider = ConfirmedTaskVLMRegistry(self.runtime)
        with patch.object(self.runtime, 'plan_vlm_cleaning',
                          return_value=('检查有人和狗', '检查画面无水印')) as generated:
            instructions = provider.generate(task)
        generated.assert_not_called()
        self.assertTrue(all('模型整理' not in text for text in instructions))
        registry = provider.build(self.actor, task, instructions=instructions)
        recipe = compile_confirmed_recipe(task, registry)
        self.assertEqual(recipe.instructions, instructions)
        self.assertEqual(decode(encode(recipe)), recipe)
        legacy = encode(recipe)
        legacy['value'].pop('instructions')
        self.assertEqual(decode(legacy).instructions, ())
        self.assertEqual([registry.detectors[step.backend_ref]._requirement
                          for step in recipe.steps], list(instructions))
        with self.assertRaises(ServiceError):
            registry.require_recipe(replace(
                recipe, instructions=(instructions[0], instructions[1] + '。额外条件')))
        self.assertEqual([node['instruction'] for node in provider.describe(
            task, instructions=recipe.instructions)['nodes']], list(instructions))
        changed = (instructions[0], '检查别的条件')
        self.assert_code('VLM_PLAN_INVALID', lambda: provider.build(
            self.actor, task, instructions=changed))

    def test_multi_step_resume_reuses_saved_first_check(self):
        from dsh_retrieval.adapters.cleaning.runner import GenericCleaningExecutor
        from dsh_retrieval.multi_rule_contracts import (
            ApprovedCleaningJob, CleaningBinding, CleaningCandidateRef,
            bytes_digest, candidate_manifest_digest)
        from dsh_retrieval.recipe_bridge import compile_confirmed_recipe
        task = self.task(raw_request='有人和狗，画面无水印')
        registry = ConfirmedTaskVLMRegistry(self.runtime).build(self.actor, task)
        recipe = compile_confirmed_recipe(task, registry)
        content = b'\xff\xd8\xffsynthetic-image'
        candidate = CleaningCandidateRef('candidate_1', 'v1', bytes_digest(content))
        job = ApprovedCleaningJob(CleaningBinding(
            'tenant', 'owner', 'demo', task.task_id, task.confirmed_revision,
            task.run_generation, task.task_version, 'operation_1',
            candidate_manifest_digest((candidate,)), recipe.recipe_digest,
            registry.release_digest), recipe, (candidate,))
        class Guard:
            def require_current(self, binding):
                pass
        class Reader:
            def read(self, candidate, binding):
                return content
        runner = GenericCleaningExecutor(guard=Guard(), reader=Reader(),
                                         detectors=registry.require_recipe(recipe))
        saved = []
        calls = []
        def interrupted(requirement, image, mime, binding):
            calls.append(requirement)
            if len(calls) == 2:
                raise ServiceError('MODEL_REQUEST_FAILED', 'transient', 502, True)
            return {'verdict': 'pass', 'reason': '可见'}
        with patch.object(self.runtime, 'assess', side_effect=interrupted):
            with self.assertRaises(ServiceError):
                runner.run(job, step_checkpoint=lambda c, s, v: saved.append(v))
        self.assertEqual(len(saved), 1)
        with patch.object(self.runtime, 'assess', side_effect=interrupted):
            result = runner.run(job, partial=(candidate.candidate_id, tuple(saved)))
        self.assertEqual(len(calls), 3)
        self.assertEqual(result.candidates[0].verdict, 'pass')


if __name__ == '__main__':
    unittest.main()
