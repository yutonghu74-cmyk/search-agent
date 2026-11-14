"""Versioned cleaning requirements and source-grounded edits, independent of LLMs.

Legacy specifications are projected on read; only a confirmed edit persists the
structured representation. Natural-language history is never the mutable model.
"""
from copy import deepcopy
import re

from .errors import ServiceError
from .query_spec import digest


def normalize_conditions(value, spec):
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ServiceError('INVALID_SPEC', '清洗条件须保留 1–8 项。', 422)
    groups = {g['group_id'] for g in (spec.get('grouped_expansion') or {}).get('groups', [])}
    seen = set()
    for item in value:
        if (not isinstance(item, dict) or set(item) != {'condition_id', 'text', 'group_id', 'source_text'}
                or not isinstance(item['condition_id'], str)
                or not re.fullmatch(r'condition_[0-9a-f]{24}', item['condition_id'])
                or item['condition_id'] in seen
                or any(not isinstance(item[key], str) or not 1 <= len(item[key].strip()) <= limit
                       for key, limit in (('text', 640), ('source_text', 2000)))
                or item['group_id'] is not None and (not isinstance(item['group_id'], str)
                                                    or item['group_id'] not in groups)):
            raise ServiceError('INVALID_SPEC', '清洗条件标识、原文或适用类别无效。', 422)
        seen.add(item['condition_id'])
    return deepcopy(value)


def conditions_for(spec):
    if 'cleaning_conditions' in spec:
        return normalize_conditions(spec['cleaning_conditions'], spec)
    from .vlm_instruction_plan import confirmed_instruction_plan
    return [{'condition_id': 'condition_' + digest([node['node_id'], node.get('group_id'), node['source']])[7:31],
             'text': node['source'], 'source_text': node['source'], 'group_id': node.get('group_id')}
            for node in confirmed_instruction_plan(spec)]


def apply_changes(spec, changes, source_text):
    """Apply an ordered patch atomically; never guess a missing reference/scope."""
    if not isinstance(changes, list) or not 1 <= len(changes) <= 16:
        raise ServiceError('INVALID_SPEC', '请提供明确的清洗条件修改。', 422)
    rows = conditions_for(spec)
    before = deepcopy(rows)
    for index, change in enumerate(changes):
        if (not isinstance(change, dict)
                or set(change) != {'op', 'condition_id', 'source_text', 'group_id'}
                or change['op'] not in ('add', 'replace', 'remove')):
            raise ServiceError('INVALID_SPEC', '清洗修改只支持增加、替换和删除。', 422)
        op, cid, quote, group = (change[key] for key in ('op', 'condition_id', 'source_text', 'group_id'))
        if op != 'remove' and (not isinstance(quote, str) or not quote.strip()
                              or len(quote) > 320 or quote not in source_text):
            raise ServiceError('CHANGE_NOT_GROUNDED', '新增条件必须引用本条消息的原文。', 422)
        if op == 'remove' and quote is not None:
            raise ServiceError('INVALID_SPEC', '删除条件不接受替换文本。', 422)
        if op == 'add':
            if cid is not None:
                raise ServiceError('INVALID_SPEC', '新增条件不能指定已有标识。', 422)
            cid = 'condition_' + digest([rows, index, quote, group])[7:31]
            rows.append({'condition_id': cid, 'text': quote.strip(), 'source_text': quote, 'group_id': group})
        else:
            row = next((row for row in rows if row['condition_id'] == cid), None)
            if row is None or group != row['group_id']:
                raise ServiceError('CONDITION_CONFLICT', '条件已变化或适用类别不匹配，请重新核对。', 409)
            if op == 'remove':
                rows.remove(row)
            else:
                row.update(text=quote.strip(), source_text=quote)
    rows = normalize_conditions(rows, spec)
    if rows == before:
        raise ServiceError('NO_REQUIREMENT_CHANGE', '清洗条件没有变化。', 422)
    return rows


def preview_changes(before_spec, after_spec):
    before, after = conditions_for(before_spec), conditions_for(after_spec)
    old, new = ({row['condition_id']: row for row in rows} for rows in (before, after))
    return {'before_count': len(before), 'after_count': len(after),
            'target': {'before': before_spec['target_count'], 'after': after_spec['target_count']},
            'retrieval': {'before': deepcopy((before_spec.get('grouped_expansion') or {}).get('groups', [])),
                          'after': deepcopy((after_spec.get('grouped_expansion') or {}).get('groups', [])),
                          'changed': before_spec.get('grouped_expansion') != after_spec.get('grouped_expansion')},
            'changes': [{'op': 'remove' if cid not in new else 'replace', 'condition_id': cid,
                         'before': row['text'], 'after': new[cid]['text'] if cid in new else None}
                        for cid, row in old.items() if new.get(cid) != row] +
                       [{'op': 'add', 'condition_id': cid, 'before': None, 'after': row['text']}
                        for cid, row in new.items() if cid not in old],
            'nodes': [{'node_id': row['condition_id'], 'source': row['text'], 'group_id': row['group_id']}
                      for row in after],
            'impact': {'retrieval': 'retain_candidates' if same_retrieval_input(before_spec, after_spec) else 'rerun',
                       'cleaning': 'recheck', 'packaging': 'invalidate'}}


def apply_requirement_changes(spec, changes, source_text):
    """Apply retrieval, total quantity and conditions to one validated copy."""
    from .dialogue import resolve_count_edits
    if (not isinstance(changes, dict) or not {'count_edits', 'cleaning_changes'} <= set(changes)
            or set(changes) - {'count_edits', 'cleaning_changes', 'retrieval_changes'}
            or not isinstance(changes['count_edits'], list)
            or not isinstance(changes['cleaning_changes'], list)
            or not any(changes.values())
            or any(not isinstance(edit, dict) or edit.get('target') != 'images'
                   for edit in changes['count_edits'])):
        raise ServiceError('INVALID_SPEC', '统一修改需要有效的数量、词表或清洗操作列表。', 422)
    result = deepcopy(spec)
    try:
        count, _ = resolve_count_edits(changes['count_edits'], spec['target_count'], [])
    except ValueError as error:
        raise ServiceError('INVALID_SPEC', str(error), 422) from None
    groups = (spec.get('grouped_expansion') or {}).get('groups', [])
    if count != spec['target_count'] and any(group.get('delivery_target') is not None for group in groups):
        raise ServiceError('QUOTA_CHANGE_REQUIRES_CLARIFICATION', '当前有各组数量，请同时明确各组新数量后再修改总数。', 422)
    retrieval_plan, removed = (apply_retrieval_changes(spec, changes['retrieval_changes'], source_text)
                                if changes.get('retrieval_changes') else (None, []))
    condition_edits = deepcopy(changes['cleaning_changes'])
    # A sole, literal subject check follows an explicitly renamed sole group.
    # This is a reviewed invariant, not generated prose. Compound/scoped checks
    # still require an explicit interpretation and may never be silently moved.
    if retrieval_plan is not None and len(groups) == len(changes['retrieval_changes']) == 1:
        edit = changes['retrieval_changes'][0]
        if edit['op'] == 'upsert' and edit['group_id'] == groups[0]['group_id'] and edit['primary_label'] != groups[0]['primary_label']:
            subjects = [row for row in conditions_for(spec) if row['text'] == groups[0]['primary_label'] and row['group_id'] is None]
            if len(subjects) == 1:
                matching = [row for row in condition_edits if isinstance(row, dict)
                            and row.get('condition_id') == subjects[0]['condition_id']]
                derived = not matching
                if derived:
                    condition_edits.append({'op': 'replace', 'condition_id': subjects[0]['condition_id'],
                                            'source_text': edit['primary_label'], 'group_id': None})
                for row in condition_edits:
                    if (isinstance(row, dict) and set(row) == {'op', 'condition_id', 'source_text', 'group_id'}
                            and row['op'] == 'remove' and row['condition_id'] == subjects[0]['condition_id']
                            and row['group_id'] is None and row['source_text'] is None):
                        row.update(op='replace', source_text=edit['primary_label'])
                        derived = True
                if derived:
                    condition_edits = [row for row in condition_edits if not (
                        isinstance(row, dict) and row.get('op') == 'add' and row.get('condition_id') is None
                        and row.get('group_id') is None and isinstance(row.get('source_text'), str)
                        and row['source_text'].strip() == edit['primary_label'] and row['source_text'] in source_text)]
    result['cleaning_conditions'] = (apply_changes(spec, condition_edits, source_text)
                                     if condition_edits else conditions_for(spec))
    if changes.get('retrieval_changes'):
        result['grouped_expansion'] = retrieval_plan
        current_groups = {row['group_id'] for row in result['grouped_expansion']['groups']}
        if any(row['group_id'] is not None and row['group_id'] not in current_groups
               for row in result['cleaning_conditions']):
            raise ServiceError('CONDITION_SCOPE_REQUIRES_CLARIFICATION', '类别已变化，请明确旧类别专属条件是否删除或重新设置。', 422)
        normalize_conditions(result['cleaning_conditions'], result)
        if any(label in row['text'] for label in removed for row in result['cleaning_conditions']):
            raise ServiceError('STALE_SUBJECT_CONDITION', '主体已修改，请同时替换或删除仍要求旧主体的检测条件。', 422)
        old_labels = {row['group_id']: row['primary_label'] for row in groups}
        for edit in changes['retrieval_changes']:
            if (edit['op'] == 'upsert' and edit['group_id'] in old_labels
                    and old_labels[edit['group_id']] != edit['primary_label']
                    and not any(edit['primary_label'] in row['text'] for row in result['cleaning_conditions'])):
                raise ServiceError('MISSING_SUBJECT_CONDITION', '更换主体时请将旧主体检测替换为新主体原文，不要只删除主体检测。', 422)
    elif 'retrieval_changes' in changes and changes['retrieval_changes'] != []:
        raise ServiceError('INVALID_SPEC', '检索修改须为操作列表。', 422)
    result['target_count'] = count
    result['refill_policy']['target_count'] = count
    if (count == spec['target_count'] and result['cleaning_conditions'] == conditions_for(spec)
            and result.get('grouped_expansion') == spec.get('grouped_expansion')):
        raise ServiceError('NO_REQUIREMENT_CHANGE', '需求没有变化。', 422)
    return result


def apply_retrieval_changes(spec, changes, source_text):
    """Reuse the public group model; untouched groups and quotas are retained."""
    from .expansion_models import preview_edited_groups, normalize_grouped_expansion
    if not isinstance(changes, list) or not 1 <= len(changes) <= 20:
        raise ServiceError('INVALID_SPEC', '检索修改须为 1–20 项。', 422)
    plan = spec.get('grouped_expansion') or {}
    rows = deepcopy(plan.get('groups', []))
    removed, touched = [], set()
    for edit in changes:
        if (not isinstance(edit, dict)
                or set(edit) != {'op', 'group_id', 'primary_label', 'auxiliary_terms', 'source_text'}
                or edit['op'] not in ('upsert', 'remove')
                or not isinstance(edit['source_text'], str) or not edit['source_text'].strip()
                or len(edit['source_text']) > 1000 or edit['source_text'] not in source_text):
            raise ServiceError('CHANGE_NOT_GROUNDED', '检索修改必须引用本轮原文。', 422)
        gid = edit['group_id']
        if gid is not None and not isinstance(gid, str):
            raise ServiceError('INVALID_SPEC', '类别标识无效。', 422)
        row = next((row for row in rows if row.get('group_id') == gid), None) if gid else None
        if gid is not None and (row is None or gid in touched):
            raise ServiceError('GROUP_CONFLICT', '类别已变化或同一类别被重复修改。', 409)
        if gid is not None:
            touched.add(gid)
        if edit['op'] == 'remove':
            if row is None or edit['primary_label'] is not None or edit['auxiliary_terms'] is not None:
                raise ServiceError('INVALID_SPEC', '删除类别须引用已有标识。', 422)
            removed.append(row['primary_label']); rows.remove(row)
            continue
        label, terms = edit['primary_label'], edit['auxiliary_terms']
        if (not isinstance(label, str) or not label.strip() or not isinstance(terms, list)
                or not 0 <= len(terms) <= 200 or label != label.strip()
                or (row is None or label != row['primary_label']) and label not in edit['source_text']):
            raise ServiceError('CHANGE_NOT_GROUNDED', '新主体必须出现在修改原文中；需提供完整检索词列表。', 422)
        replacement = {'primary_label': label, 'auxiliary_terms': terms,
                       'delivery_target': row.get('delivery_target') if row else None}
        if row is None:
            rows.append(replacement)
        else:
            if label != row['primary_label']:
                removed.append(row['primary_label'])
            rows[rows.index(row)] = replacement
    try:
        result = preview_edited_groups(plan.get('parent_label'), [
            {key: row[key] for key in ('primary_label', 'auxiliary_terms', 'delivery_target')} for row in rows])['plan']
    except ValueError as error:
        raise ServiceError('INVALID_SPEC', str(error), 422) from None
    old_quota = sum(row.get('delivery_target') or 0 for row in plan.get('groups', []))
    new_quota = sum(row.get('delivery_target') or 0 for row in result['groups'])
    if old_quota != new_quota:
        raise ServiceError('QUOTA_CHANGE_REQUIRES_CLARIFICATION', '类别变动影响各组数量，请先明确新的配额。', 422)
    if 'model_binding' in plan:
        payload = {key: value for key, value in result.items() if key != 'plan_digest'}
        payload['model_binding'] = deepcopy(plan['model_binding'])
        result = normalize_grouped_expansion({**payload, 'plan_digest': digest(payload)})
    if result['groups'] == plan.get('groups'):
        result = deepcopy(plan)
    return result, removed


def same_retrieval_input(before, after):
    """Conservative dependency boundary: all non-cleaning fields must match."""
    return ({k: v for k, v in before.items() if k != 'cleaning_conditions'} ==
            {k: v for k, v in after.items() if k != 'cleaning_conditions'})
