"""Retrieval Agent's provisional grouped-term policy, independent of HTTP/storage.

The model port may be backed by DSH, another Harness or a trusted adapter.
No method confirms a proposal, creates a business Task or searches a source.
"""
from copy import deepcopy
from typing import Protocol

from .dialogue import resolve_count_edits
from .errors import ServiceError
from .expansion_models import normalize_grouped_expansion, preview_edited_groups, seal_explicit_targets


WORKFLOW = 'retrieval-terms@1'
IMPLEMENTATION = 'grouped-terms-agent@1'


class TermsModelPort(Protocol):
    def binding(self, llm_id, vlm_id): ...
    def expand_chunk(self, raw_request, llm_id, vlm_id, *, limit, label=None,
                     existing_terms=None, feedback_text=None, current_groups=None, recall_context=None): ...
    def bind(self, result, binding, *, generated=False): ...


class RetrievalTermsAgent:
    """Advance at most one generation batch; the existing queue checkpoints it."""
    def __init__(self, models: TermsModelPort):
        self.models = models

    @staticmethod
    def descriptor():
        return {'capability': 'retrieval', 'phase': 'terms',
                'workflow_ref': WORKFLOW, 'implementation_ref': IMPLEMENTATION}

    def _generate(self, payload, *, limit, label=None, existing_terms=None):
        selection = payload['model_selection']
        value = self.models.expand_chunk(payload['raw_request'], selection['llm'], selection['vlm'],
            limit=limit, **({'label': label, 'existing_terms': list(existing_terms)} if label else
                           {'current_groups': deepcopy(payload.get('current_groups'))}),
            **({'recall_context': deepcopy(payload['recall_context'])} if payload.get('recall_context') else {}),
            feedback_text=payload.get('feedback_text'))
        try:
            groups = normalize_grouped_expansion(value['plan'])['groups']
            if label is not None and (len(groups) != 1 or groups[0]['primary_label'] != label):
                raise ValueError('Generated group label changed')
            for group in groups:
                terms = group['auxiliary_terms']
                if (not 1 <= len(terms) <= limit or label is not None and len(terms) != limit
                        or set(terms) & set(existing_terms or [])):
                    raise ValueError('Invalid or repeated batch terms')
        except (KeyError, TypeError, ValueError) as error:
            raise ServiceError('MODEL_OUTPUT_INVALID', '本批检索词未通过校验，原词表已保留', 422) from error
        return [{'primary_label': group['primary_label'], 'auxiliary_terms': list(group['auxiliary_terms'])}
                for group in groups]

    def advance(self, payload, state, *, now):
        """Mutate only a validated batch; failed_batch is set before model I/O."""
        selection = payload['model_selection']
        if self.models.binding(selection['llm'], selection['vlm']) != state['model_binding']:
            raise ServiceError('MODEL_CONFIG_CHANGED', 'Model profile changed; restart expansion', 409)
        if state['groups'] is None:
            small = (payload['target_count'] <= 300 and not payload.get('current_groups')
                     and not state.get('initial_count') and not state.get('targets')
                     and not state.get('deferred_count_edits'))
            limit = 3 if small else 8
            state['failed_batch'] = {'group': '初始分组', 'offset': 0, 'size': limit}
            groups = self._generate(payload, limit=limit)
            if state.get('deferred_count_edits'):
                try:
                    _, targets = resolve_count_edits(state['deferred_count_edits'], payload['target_count'], groups)
                except ValueError as error:
                    raise ServiceError('INVALID_SPEC', str(error), 422) from None
            else:
                targets = state['targets'] or {g['primary_label']: state.get('initial_count')
                                              or len(g['auxiliary_terms']) for g in groups}
            if set(targets) != {g['primary_label'] for g in groups}:
                raise ServiceError('MODEL_OUTPUT_INVALID', 'Generated group labels changed', 422)
            for group in groups:
                group['auxiliary_terms'] = group['auxiliary_terms'][:targets[group['primary_label']]]
            state.update(groups=groups, targets=targets, total=sum(targets.values()),
                         first_batch_at=state.get('first_batch_at') or now())
        else:
            groups, targets = state['groups'], state['targets']
            group = next((g for g in groups if len(g['auxiliary_terms']) < targets[g['primary_label']]), None)
            if group is not None:
                label = group['primary_label']
                limit = min(10, targets[label] - len(group['auxiliary_terms']))
                state['failed_batch'] = {'group': label, 'offset': len(group['auxiliary_terms']), 'size': limit}
                addition = self._generate(payload, limit=limit, label=label,
                                          existing_terms=group['auxiliary_terms'])
                group['auxiliary_terms'].extend(addition[0]['auxiliary_terms'])
            else:
                current = payload.get('current_groups')
                if (current is not None and payload.get('current_target_count') in (None, payload['target_count'])
                        and {g['primary_label']: set(g['auxiliary_terms']) for g in current}
                        == {g['primary_label']: set(g['auxiliary_terms']) for g in groups}):
                    raise ServiceError('EXPANSION_UNCHANGED', '新词表没有落实修改，旧词表已保留', 422)
                adjustment = (payload.get('recall_context') or {}).get('adjustment')
                if adjustment:
                    old = {g['primary_label']: g['auxiliary_terms'] for g in payload['current_groups']}
                    if ({g['primary_label'] for g in groups} != set(old)
                            or any(g['auxiliary_terms'] != old[g['primary_label']]
                                   for g in groups if g['primary_label'] in adjustment['preserved_groups'])):
                        raise ServiceError('MODEL_OUTPUT_INVALID', '调整超出允许分组，原词表已保留', 422)
                result = seal_explicit_targets(preview_edited_groups(None, groups),
                                               payload['raw_request'], payload['target_count'])
                if payload.get('recall_context'):
                    grouping = payload['recall_context']['grouping']
                    quotas = {g['primary_label']: g['delivery_target'] for g in grouping['groups']}
                    result = preview_edited_groups(grouping['parent_label'], [
                        {**g, 'delivery_target': quotas[g['primary_label']]} for g in groups])
                bound = self.models.bind(deepcopy(result), state['model_binding'],
                    generated=not (payload.get('term_count_only') or state.get('direct_edit')))
                try:
                    plan = normalize_grouped_expansion(bound['plan'])
                    if plan['groups'] != result['plan']['groups']:
                        raise ValueError('Model binding changed the reviewed term groups')
                except (KeyError, TypeError, ValueError) as error:
                    raise ServiceError('MODEL_OUTPUT_INVALID', '词表绑定校验失败，原词表已保留', 422) from error
                if payload.get('term_count_only'):
                    bound['note'] = '仅按原词表顺序保留指定数量的检索词；未调用扩词模型。当前仍是待确认草稿。'
                if state.get('direct_edit'):
                    bound['note'] = '按用户逐字指定的完整词表生成；未调用扩词模型。当前仍是待确认草稿。'
                state.update(result=bound, status='completed', completed_at=now())
        state['completed'] = sum(min(len(g['auxiliary_terms']), targets[g['primary_label']]) for g in groups)
        state['failed_batch'] = None
