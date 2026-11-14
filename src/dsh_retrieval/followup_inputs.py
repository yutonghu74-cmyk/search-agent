"""Lossless context deduplication and narrowly explicit, unapproved edits."""
from copy import deepcopy
import re

from .image_quantities import NUMBER, number_value
from .explicit_term_edits import explicit_term_list
from .errors import ServiceError


def normalize_requirement_proposal(value, spec):
    """Normalize only redundant assertions, never infer missing edit content."""
    result = deepcopy(value)
    for row in result.get('cleaning_changes') or []:
        if isinstance(row, dict) and row.get('op') == 'remove':
            row.setdefault('source_text', None)
    edits = result.get('count_edits', [])
    if not isinstance(edits, list):
        return result
    old = {row['group_id']: row['primary_label'] for row in (spec.get('grouped_expansion') or {}).get('groups', [])}
    for row in result.get('retrieval_changes') or []:
        if isinstance(row, dict) and row.get('op') == 'replace':
            if not isinstance(row.get('group_id'), str) or row['group_id'] not in old:
                raise ServiceError('MODEL_OUTPUT_INVALID', 'Replacing a group requires its existing identifier', 502)
            row['op'] = 'upsert'
    touched = set()
    for edit in edits:
        if not isinstance(edit, dict) or edit.get('target') != 'terms':
            continue
        if (set(edit) != {'target', 'labels', 'operation', 'value'} or edit['operation'] != 'set'
                or not isinstance(edit['labels'], list) or len(edit['labels']) != 1
                or not isinstance(edit['labels'][0], str) or type(edit['value']) is not int
                or not 0 <= edit['value'] <= 200):
            raise ServiceError('MODEL_OUTPUT_INVALID', 'Term assertions require one named group and an integer set count', 502)
        matches = [row for row in result.get('retrieval_changes') or [] if isinstance(row, dict)
                   and row.get('op') == 'upsert' and edit['labels'][0] in
                   (row.get('primary_label'), old.get(row.get('group_id')))]
        if (len(matches) != 1 or not isinstance(matches[0].get('auxiliary_terms'), list)
                or len(matches[0]['auxiliary_terms']) != edit['value']):
            raise ServiceError('MODEL_OUTPUT_INVALID', 'Term count conflicts with the complete proposed word list', 502)
        identity = matches[0].get('group_id') or matches[0].get('primary_label')
        if identity in touched:
            raise ServiceError('MODEL_OUTPUT_INVALID', 'Duplicate term assertion', 502)
        touched.add(identity)
    result['count_edits'] = [edit for edit in edits if not isinstance(edit, dict) or edit.get('target') != 'terms']
    return result


def explicit_requirement(text, groups, context):
    if not context.get('structured_changes') or context.get('has_unconfirmed_preview'):
        return None
    spec = context['requirement_spec']
    if type(spec.get('target_count')) is not int:
        return None
    # Whole-message matches only: additional constraints must go to the model.
    sentence = re.sub(r'[，,]\s*其他不变[。.!！]?\s*$', '', text.strip())
    absolute = re.fullmatch(rf'(?:把|将)?(?:(?:总共|总数|总目标|目标数量|图片数量|目标)\s*)?'
                            rf'(?:改成|改为|调整为|设为|设成)\s*({NUMBER})\s*张(?:图片|照片)?[。.!！]?\s*', sentence)
    relative = re.fullmatch(rf'(再加|再多|增加|追加|减少|减去|减)\s*({NUMBER})\s*张(?:图片|照片)?[吧。.!！]?\s*', sentence)
    base = {'kind': 'requirement', 'target_count': None, 'requested_terms_per_group': None,
            'count_edits': [], 'cleaning_changes': [], 'retrieval_changes': [], 'term_count_only': False}
    if absolute or relative:
        value = number_value((absolute or relative).group(1 if absolute else 2))
        if relative and relative.group(1) in ('减少', '减去', '减'):
            value = -value
        count = value if absolute else spec['target_count'] + value
        if not 1 <= count <= 10000:
            return {**base, 'kind': 'clarify', 'reply': '总目标须为 1–10000 张，请重新说明数量。'}
        if count == spec['target_count']:
            subject = '当前草稿' if context.get('has_pending_requirement_draft') else '当前需求'
            return {**base, 'kind': 'answer', 'reply': f'{subject}的总目标已是 {count} 张。'}
        base['count_edits'] = [{'target': 'images', 'labels': [], 'operation': 'set' if absolute else 'add', 'value': value}]
        return base
    edited = explicit_term_list(text, groups, target_count=spec['target_count'])
    canonical = (spec.get('grouped_expansion') or {}).get('groups', [])
    if edited is not None and len(text) <= 1000 and len(canonical) == 1 and groups == [
            {'primary_label': canonical[0]['primary_label'], 'auxiliary_terms': canonical[0]['auxiliary_terms']}]:
        if edited[0]['auxiliary_terms'] == canonical[0]['auxiliary_terms']:
            return {**base, 'kind': 'answer', 'reply': '检索词与当前方案一致。'}
        base['retrieval_changes'] = [{'op': 'upsert', 'group_id': canonical[0]['group_id'],
            **edited[0], 'source_text': text}]
        return base
    return None


def followup_context(original, groups, text, context):
    if not context.get('structured_changes'):
        return {'original_request': original, 'current_groups': groups,
                'feedback_text': text, 'task_context': context}
    value = deepcopy(context)
    spec = value['requirement_spec']
    # Audit text already appears once as original_request. Keep every other
    # semantic spec field, all recent messages and the effective edited facts.
    if spec.get('raw_request') == original:
        spec.pop('raw_request', None)
    if spec.get('cleaning_conditions') is not None:
        value['cleaning_conditions'] = spec.pop('cleaning_conditions')
    value.pop('cleaning_plan', None)
    plan = spec.get('grouped_expansion')
    if plan:
        spec['grouped_expansion'] = {'parent_label': plan.get('parent_label'),
            'groups': [{key: row[key] for key in ('group_id', 'primary_label', 'requested_expansion_count', 'delivery_target')}
                       for row in plan['groups']]}
        if 'model_binding' in plan:
            spec['grouped_expansion']['model_binding'] = deepcopy(plan['model_binding'])
    workflow = value.get('workflow')
    if workflow:
        value['workflow'] = {key: workflow[key] for key in (
            'schema_version', 'binding', 'state', 'summary', 'counts', 'quality', 'blocking_reasons', 'available_actions') if key in workflow}
        value['workflow']['nodes'] = deepcopy(workflow.get('nodes', []))
    return {'original_request': original, 'task_context': value, 'current_groups': groups}
