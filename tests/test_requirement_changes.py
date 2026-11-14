from copy import deepcopy
import json
import unittest

from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.proposals import normalize_proposal
from dsh_retrieval.requirement_changes import apply_changes, apply_requirement_changes, conditions_for, preview_changes, same_retrieval_input
from dsh_retrieval.task_conversation import TaskConversationCoordinator
from dsh_retrieval.vlm_instruction_plan import confirmed_instruction_plan
from draft_helpers import BODY, confirmation, make_service


def change(op, text=None, cid=None, group=None):
    return {'op': op, 'condition_id': cid, 'source_text': text, 'group_id': group}


class RequirementChangesTest(unittest.TestCase):
    def retrieval_spec(self):
        from dsh_retrieval.expansion_models import preview_edited_groups
        spec = deepcopy(self.spec)
        spec['raw_request'] = '狗，不要水印'
        spec['cleaning_conditions'] = conditions_for(spec)
        spec['grouped_expansion'] = preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': ['狗在草地', '奔跑的狗']},
            {'primary_label': '猫', 'auxiliary_terms': ['猫在草地']}])['plan']
        from dsh_retrieval.query_spec import digest
        plan = spec['grouped_expansion']
        plan['model_binding'] = {'llm': 'llm', 'vlm': 'vlm', 'config_digest': 'sha256:' + 'a' * 64,
                                 'prompt_version': 'fixture@1'}
        plan['plan_digest'] = digest({k: v for k, v in plan.items() if k != 'plan_digest'})
        return spec

    def test_subject_words_and_conditions_share_one_cumulative_draft(self):
        spec = self.retrieval_spec()
        actor = Actor('tenant', 'owner'); service = make_service(InMemoryTaskRepository())
        task, _ = service.create_task(actor, 'mixed', confirmation(service, actor,
                           body={**deepcopy(BODY), 'query_spec': spec}))
        app = TaskConversationCoordinator(service)
        dog = spec['grouped_expansion']['groups'][0]['group_id']
        cid = spec['cleaning_conditions'][0]['condition_id']
        result = app.apply(actor, task, spec, None, '把狗换成兔子，词用草地兔子，再加画面清晰', 'first', {
            'kind': 'requirement', 'retrieval_changes': [{'op': 'upsert', 'group_id': dog,
                'primary_label': '兔子', 'auxiliary_terms': ['草地兔子'], 'source_text': '把狗换成兔子，词用草地兔子'}],
            'cleaning_changes': [change('replace', '兔子', cid), change('add', '画面清晰')]})
        draft = result['feedback_draft']; updated = draft['proposal']['query_spec']
        self.assertEqual(updated['grouped_expansion']['groups'][1], spec['grouped_expansion']['groups'][1])
        self.assertEqual(updated['grouped_expansion']['model_binding'], spec['grouped_expansion']['model_binding'])
        self.assertEqual(updated['cleaning_conditions'][1]['text'], '不要水印')
        self.assertEqual(service.get_task(actor, task['task_id'])['confirmed_revision'], 1)
        ref = {k: draft[k] for k in ('draft_id', 'draft_version', 'proposal_digest')}
        context, pending = app.context(actor, task, spec, ref)
        rabbit = context['requirement_spec']['grouped_expansion']['groups'][0]['group_id']
        result = app.apply(actor, task, spec, pending, '词换成奔跑的兔子，同时取消清晰检查', 'second', {
            'kind': 'requirement', 'retrieval_changes': [{'op': 'upsert', 'group_id': rabbit,
                'primary_label': '兔子', 'auxiliary_terms': ['奔跑的兔子'], 'source_text': '词换成奔跑的兔子'}],
            'cleaning_changes': [change('remove', cid=updated['cleaning_conditions'][-1]['condition_id'])]})
        final = result['feedback_draft']; preview = result['cleaning_review']['cleaning_plan_preview']
        self.assertTrue(preview['retrieval']['changed'])
        self.assertEqual(preview['retrieval']['before'][0]['primary_label'], '狗')
        self.assertEqual(preview['retrieval']['after'][0]['auxiliary_terms'], ['奔跑的兔子'])
        self.assertEqual(len(final['proposal']['query_spec']['cleaning_conditions']), 2)
        service.confirm_feedback(actor, task['task_id'], 'confirm', {
            'draft_id': final['draft_id'], 'expected_draft_version': final['draft_version'],
            'proposal_digest': final['proposal_digest'], 'expected_task_version': task['task_version'], 'action': 'confirm'})
        self.assertEqual(service.get_task(actor, task['task_id'])['confirmed_revision'], 2)

    def test_invalid_retrieval_never_saves_half_a_patch_or_transfers_scoped_conditions(self):
        spec = self.retrieval_spec(); before = deepcopy(spec)
        group = spec['grouped_expansion']['groups'][0]
        op = {'op': 'upsert', 'group_id': group['group_id'], 'primary_label': '兔子',
              'auxiliary_terms': ['兔子在草地'], 'source_text': '把狗换成兔子'}
        for edits in ([{**op, 'group_id': 'foreign'}], [{**op, 'primary_label': '老虎'}], [op, op],
                      [{**op, 'auxiliary_terms': ['重复', '重复']}], [{**op, 'source_text': '虚构'}]):
            with self.subTest(edits=edits), self.assertRaises(ServiceError):
                apply_requirement_changes(spec, {'count_edits': [], 'retrieval_changes': edits,
                    'cleaning_changes': [change('add', '画面清晰')]}, '把狗换成兔子，画面清晰')
            self.assertEqual(spec, before)
        with self.assertRaises(ServiceError) as caught:
            apply_requirement_changes(spec, {'count_edits': [], 'retrieval_changes': [op], 'cleaning_changes': []}, '把狗换成兔子')
        self.assertEqual(caught.exception.code, 'STALE_SUBJECT_CONDITION')
        compound = deepcopy(spec)
        compound['cleaning_conditions'][0].update(text='一只狗', source_text='一只狗')
        with self.assertRaises(ServiceError) as caught:
            apply_requirement_changes(compound, {'count_edits': [], 'retrieval_changes': [op],
                'cleaning_changes': [change('remove', cid=spec['cleaning_conditions'][0]['condition_id'])]}, '把狗换成兔子')
        self.assertEqual(caught.exception.code, 'MISSING_SUBJECT_CONDITION')
        spec['cleaning_conditions'][0]['group_id'] = group['group_id']
        with self.assertRaises(ServiceError) as caught:
            apply_requirement_changes(spec, {'count_edits': [], 'retrieval_changes': [op], 'cleaning_changes': []}, '把狗换成兔子')
        self.assertEqual(caught.exception.code, 'CONDITION_SCOPE_REQUIRES_CLARIFICATION')

    def test_word_edits_keep_existing_quotas_and_noops_keep_model_binding(self):
        from dsh_retrieval.expansion_models import with_delivery_targets
        spec = self.retrieval_spec()
        spec['grouped_expansion'] = with_delivery_targets(spec['grouped_expansion'], {'狗': 5, '猫': 5})
        group = spec['grouped_expansion']['groups'][0]
        operation = {'op': 'upsert', 'group_id': group['group_id'], 'primary_label': '狗',
                     'auxiliary_terms': group['auxiliary_terms'], 'source_text': '狗组保持不变'}
        updated = apply_requirement_changes(spec, {'count_edits': [], 'retrieval_changes': [operation],
            'cleaning_changes': [change('add', '画面清晰')]}, '狗组保持不变，加画面清晰')
        self.assertEqual(updated['grouped_expansion'], spec['grouped_expansion'])
        operation.update(auxiliary_terms=['户外的狗'], source_text='词改成户外的狗')
        updated = apply_requirement_changes(spec, {'count_edits': [], 'retrieval_changes': [operation],
            'cleaning_changes': []}, '词改成户外的狗')
        self.assertEqual([r['delivery_target'] for r in updated['grouped_expansion']['groups']], [5, 5])
        self.assertEqual(updated['target_count'], 10)
        self.assertEqual(updated['grouped_expansion']['model_binding'], spec['grouped_expansion']['model_binding'])

    def test_quantity_and_conditions_are_one_cumulative_draft_and_one_confirmation(self):
        actor = Actor('tenant', 'owner')
        repository = InMemoryTaskRepository()
        service = make_service(repository)
        spec = deepcopy(BODY['query_spec'])
        spec['rules'] = [{'rule_id': 'semantic-match', 'predicate_ref': 'image.semantic-match@1',
                         'operator': 'eq', 'value': True, 'hardness': 'hard', 'origin': 'user_confirmed'}]
        task, _ = service.create_task(actor, 'task', confirmation(service, actor,
                            body={**deepcopy(BODY), 'query_spec': spec}))
        coordinator = TaskConversationCoordinator(service)
        result = coordinator.apply(actor, task, spec, None, '总共改为20张，同时不要水印', 'mixed-1', {
            'kind': 'requirement', 'count_edits': [{'target': 'images', 'labels': [], 'operation': 'set', 'value': 20}],
            'cleaning_changes': [change('add', '不要水印')]})
        draft = result['feedback_draft']
        preview = result['cleaning_review']['cleaning_plan_preview']
        self.assertEqual(preview['target'], {'before': 10, 'after': 20})
        self.assertEqual(preview['impact']['retrieval'], 'rerun')
        self.assertEqual(service.get_task(actor, task['task_id'])['confirmed_revision'], 1)
        ref = {k: draft[k] for k in ('draft_id', 'draft_version', 'proposal_digest')}
        context, pending = coordinator.context(actor, task, spec, ref)
        self.assertEqual(context['requirement_spec']['target_count'], 20)
        with self.assertRaises(ServiceError):
            coordinator.apply(actor, task, spec, pending, '改30张', 'invalid-mixed', {
                'kind': 'requirement', 'count_edits': [{'target': 'images', 'labels': [], 'operation': 'set', 'value': 30}],
                'cleaning_changes': [change('add', '消息中没有的条件')]})
        self.assertEqual(service.get_feedback(actor, task['task_id'], draft['draft_id'])['draft_version'], 1)
        result = coordinator.apply(actor, task, spec, pending, '再多5张', 'mixed-2', {
            'kind': 'requirement', 'count_edits': [{'target': 'images', 'labels': [], 'operation': 'add', 'value': 5}]})
        draft = result['feedback_draft']
        updated = draft['proposal']['query_spec']
        self.assertEqual(updated['target_count'], 25)
        self.assertEqual(updated['refill_policy']['target_count'], 25)
        self.assertEqual(updated['cleaning_conditions'][-1]['text'], '不要水印')
        self.assertEqual(updated['raw_request'], spec['raw_request'])
        self.assertEqual(draft['draft_version'], 2)
        service.confirm_feedback(actor, task['task_id'], 'confirm-mixed', {
            'draft_id': draft['draft_id'], 'expected_draft_version': 2,
            'proposal_digest': draft['proposal_digest'], 'expected_task_version': task['task_version'], 'action': 'confirm'})
        stored = repository.get(actor, task['task_id'])
        self.assertEqual(stored.confirmed_revision, 2)
        self.assertEqual(json.loads(stored.confirmed_proposal_json)['query_spec']['target_count'], 25)

    def test_unified_patch_validation_never_applies_half_a_change(self):
        spec = deepcopy(BODY['query_spec'])
        original = deepcopy(spec)
        valid = {'target': 'images', 'labels': [], 'operation': 'add', 'value': 5}
        for edits, conditions in [([valid], [change('add', '虚构的条件')]),
                                  ([{**valid, 'value': -100}], [change('add', '无水印')]),
                                  ([{**valid, 'target': 'terms'}], [change('add', '无水印')]),
                                  ([valid, valid], [])]:
            with self.subTest(edits=edits), self.assertRaises(ServiceError):
                apply_requirement_changes(spec, {'count_edits': edits, 'cleaning_changes': conditions}, '多5张，无水印')
            self.assertEqual(spec, original)
        from dsh_retrieval.expansion_models import preview_edited_groups, with_delivery_targets
        spec['grouped_expansion'] = with_delivery_targets(preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': ['狗在草地']}])['plan'], {'狗': 10})
        with self.assertRaises(ServiceError) as caught:
            apply_requirement_changes(spec, {'count_edits': [valid], 'cleaning_changes': [change('add', '无水印')]}, '多5张，无水印')
        self.assertEqual(caught.exception.code, 'QUOTA_CHANGE_REQUIRES_CLARIFICATION')

    def setUp(self):
        self.spec = deepcopy(BODY['query_spec'])
        self.spec['raw_request'] = '找10张狗图片，不要水印'
        self.spec['rules'] = [{'rule_id': 'semantic-match', 'predicate_ref': 'image.semantic-match@1',
                              'operator': 'eq', 'value': True, 'hardness': 'hard', 'origin': 'user_confirmed'}]

    def test_legacy_projection_is_read_only_and_ids_survive_ordered_edits(self):
        original = deepcopy(self.spec)
        initial = conditions_for(self.spec)
        self.assertEqual(self.spec, original)
        rows = apply_changes(self.spec, [change('add', '画面清晰')], '再增加画面清晰')
        self.assertEqual(rows[:2], initial)
        spec = {**self.spec, 'cleaning_conditions': rows}
        updated = apply_changes(spec, [change('remove', cid=initial[1]['condition_id']),
                                     change('replace', '只保留高清', rows[2]['condition_id'])],
                                '取消第二条，把第三条改成只保留高清')
        self.assertEqual(updated[1]['condition_id'], rows[2]['condition_id'])
        self.assertEqual(updated[1]['text'], '只保留高清')
        final = {**self.spec, 'cleaning_conditions': updated}
        self.assertEqual(final['raw_request'], original['raw_request'])
        preview = preview_changes(spec, final)
        self.assertEqual([row['op'] for row in preview['changes']], ['remove', 'replace'])
        self.assertEqual(preview['impact']['retrieval'], 'retain_candidates')
        self.assertTrue(same_retrieval_input(self.spec, final))
        self.assertFalse(same_retrieval_input(self.spec, {**final, 'target_count': 99}))
        self.assertEqual([row['source'] for row in confirmed_instruction_plan(final)],
                         [row['text'] for row in updated])
        normalized = normalize_proposal({**deepcopy(BODY), 'query_spec': final})
        self.assertEqual(normalized['query_spec']['cleaning_conditions'], updated)

    def test_atomic_patch_rejects_hallucination_missing_reference_and_empty_plan(self):
        for changes, text in [([change('add', '版权清晰')], '清晰一点'),
                              ([change('remove', cid='missing')], '取消第二条'),
                              ([change('add', '无水印', group='foreign')], '无水印')]:
            with self.subTest(changes=changes), self.assertRaises(ServiceError):
                apply_changes(self.spec, changes, text)
        rows = conditions_for(self.spec)
        with self.assertRaises(ServiceError):
            apply_changes(self.spec, [change('remove', cid=row['condition_id']) for row in rows], '全部取消')
        self.assertNotIn('cleaning_conditions', self.spec)

    def test_category_scoped_condition_does_not_turn_other_category_into_an_and_requirement(self):
        from dsh_retrieval.expansion_models import preview_edited_groups, with_delivery_targets
        groups = with_delivery_targets(preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': ['狗在草地']},
            {'primary_label': '猫', 'auxiliary_terms': ['猫在草地']}])['plan'], {'狗': 2, '猫': 2})
        spec = {**self.spec, 'raw_request': '狗2张，猫2张', 'grouped_expansion': groups}
        cat = groups['groups'][1]['group_id']
        rows = apply_changes(spec, [change('add', '眼睛睁开', group=cat)], '只给猫增加眼睛睁开')
        nodes = confirmed_instruction_plan({**spec, 'cleaning_conditions': rows})
        self.assertEqual(len(nodes), 3)
        self.assertEqual(nodes[-1]['group_id'], cat)
        self.assertIn('仅适用于类别：猫', nodes[-1]['instruction'])

    def test_coordinator_preserves_pending_edits_and_confirms_one_version(self):
        actor = Actor('tenant', 'owner')
        repository = InMemoryTaskRepository()
        service = make_service(repository)
        task, _ = service.create_task(actor, 'task', confirmation(service, actor,
                          body={**deepcopy(BODY), 'query_spec': self.spec}))
        coordinator = TaskConversationCoordinator(service)
        context, pending = coordinator.context(actor, task, self.spec)
        self.assertIsNone(pending)
        result = coordinator.apply(actor, task, self.spec, pending, '增加画面清晰', 'turn-1',
                                   {'kind': 'cleaning', 'cleaning_changes': [change('add', '画面清晰')]})
        draft = result['feedback_draft']
        self.assertEqual(service.get_task(actor, task['task_id'])['confirmed_revision'], 1)
        ref = {key: draft[key] for key in ('draft_id', 'draft_version', 'proposal_digest')}
        context, pending = coordinator.context(actor, task, self.spec, ref)
        cid = context['cleaning_conditions'][-1]['condition_id']
        result = coordinator.apply(actor, task, self.spec, pending, '改成只保留高清', 'turn-2',
                                   {'kind': 'cleaning', 'cleaning_changes': [change('replace', '只保留高清', cid)]})
        draft = result['feedback_draft']
        self.assertEqual(draft['draft_version'], 2)
        self.assertEqual(len(draft['proposal']['query_spec']['cleaning_conditions']), 3)
        with self.assertRaises(ServiceError):
            coordinator.context(actor, task, self.spec, ref)
        body = {'draft_id': draft['draft_id'], 'expected_draft_version': draft['draft_version'],
                'proposal_digest': draft['proposal_digest'], 'expected_task_version': task['task_version'],
                'action': 'confirm'}
        service.confirm_feedback(actor, task['task_id'], 'confirm-change', body)
        current = repository.get(actor, task['task_id'])
        self.assertEqual(current.confirmed_revision, 2)
        stored = json.loads(current.confirmed_proposal_json)['query_spec']
        self.assertEqual(stored['raw_request'], self.spec['raw_request'])
        self.assertEqual(stored['cleaning_conditions'][-1]['text'], '只保留高清')
        with self.assertRaises(ServiceError):
            coordinator.context(Actor('tenant', 'intruder'), task, self.spec, ref)


if __name__ == '__main__':
    unittest.main()
