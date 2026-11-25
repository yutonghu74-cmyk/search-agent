"""Pre-task semantic draft. Model edits are grounded proposals, never approval."""
from copy import deepcopy
import hashlib
import re

from .image_quantities import NUMBER, number_value


OPS = ('add_category', 'update_category', 'remove_category', 'set_count',
       'add_condition', 'remove_condition')
FIELDS = {'op', 'targets', 'label', 'scene', 'count', 'text', 'source_text'}


def edit_schema():
    text = lambda n: {'type': 'string', 'minLength': 1, 'maxLength': n}
    common = {'targets': {'type': 'array', 'maxItems': 20, 'items': text(64)},
              'source_text': text(1000)}
    shapes = []
    for op, extra, required in (
        ('add_category', {'label': text(32), 'scene': text(160),
                          'count': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 10000}}, ['label', 'scene']),
        ('update_category', {'label': text(32), 'scene': text(160)}, []),
        ('remove_category', {}, []),
        ('set_count', {'count': {'type': 'integer', 'minimum': 1, 'maximum': 10000}}, ['count']),
        ('add_condition', {'text': text(300)}, ['text']),
        ('remove_condition', {'text': text(300)}, ['text']),
    ):
        shapes.append({'type': 'object', 'properties': {'op': {'enum': [op]}, **common, **extra},
                       'required': ['op', 'targets', 'source_text', *required], 'additionalProperties': False})
    return {'type': 'array', 'maxItems': 40, 'items': {'anyOf': shapes}}


def draft_from_state(state):
    """Old allocations are upgraded without inferring categories from prose."""
    if state.get('requirement_draft') is not None:
        return deepcopy(state['requirement_draft'])
    categories = []
    for item in state.get('category_targets') or []:
        label = item['label']
        categories.append({'category_id': _category_id(label), 'label': label, 'scene': label,
                           'target_count': item['count'], 'conditions': [], 'evidence': {}})
    return {'schema_version': 'requirement-draft@1', 'categories': categories}


def _category_id(label):
    return 'cat_' + hashlib.sha256(label.encode()).hexdigest()[:16]


def apply_edits(state, edits, message):
    """Apply a bounded atomic patch; untouched scenes, quantities and conditions survive."""
    if not isinstance(edits, list) or len(edits) > 40:
        raise ValueError('requirement_edits must contain at most 40 operations')
    draft = draft_from_state(state)
    categories = draft['categories']
    for edit in edits:
        if (not isinstance(edit, dict) or set(edit) - FIELDS
                or not {'op', 'targets', 'source_text'} <= set(edit) or edit['op'] not in OPS):
            raise ValueError('Requirement operations need op, targets, source_text; allowed fields: ' + ', '.join(sorted(FIELDS)))
        edit = {**{f: None for f in ('label', 'scene', 'count', 'text')}, **edit}
        op, targets, source = edit['op'], edit['targets'], edit['source_text']
        if (not isinstance(source, str) or not source.strip() or len(source) > 1000 or (source not in message and not (op == 'add_category'
                    and state.get('requirement_draft') is None and source in (state.get('subject') or '')))
                or not isinstance(targets, list) or len(targets) > 20
                or any(not isinstance(t, str) for t in targets) or len(set(targets)) != len(targets)):
            raise ValueError('Every requirement edit must quote this message and name unique targets')
        for field, limit in (('label', 32), ('scene', 160), ('text', 300)):
            if edit[field] is not None and (not isinstance(edit[field], str)
                    or not edit[field].strip() or len(edit[field]) > limit):
                raise ValueError('Invalid requirement text')
        count = edit['count']
        if count is not None:
            if type(count) is not int or not 1 <= count <= 10000:
                raise ValueError('Image quantity must be an integer between 1 and 10000')
            amounts = {number_value(m[1]) for m in re.finditer(rf'({NUMBER})\s*张', source)}
            if state.get('question') == 'target_count' and re.fullmatch(rf'\s*{NUMBER}\s*', source):
                amounts.add(number_value(source.strip()))
            if count not in amounts:
                raise ValueError('Image quantity must quote an image count, not a scene object or term count')
        allowed = {'add_category': {'label', 'scene', 'count'},
                   'update_category': {'label', 'scene'}, 'remove_category': set(),
                   'set_count': {'count'}, 'add_condition': {'text'}, 'remove_condition': {'text'}}[op]
        if any(edit[f] is not None for f in {'label', 'scene', 'count', 'text'} - allowed):
            raise ValueError('Fields do not match requirement operation')
        if op == 'add_category':
            label, scene = edit['label'], edit['scene']
            if targets or not label or not scene or label not in source or scene not in source:
                raise ValueError('New categories need a literal label and scene from this message')
            if any(c['label'] == label for c in categories):
                raise ValueError('Category exists; use its category_id to update it')
            category_id = _category_id(label)
            if any(c['category_id'] == category_id for c in categories):
                category_id = _category_id(label + ':' + str(len(categories)))
            categories.append({'category_id': category_id, 'label': label, 'scene': scene,
                               'target_count': count, 'conditions': [],
                               'evidence': {f: source for f in ('label', 'scene', 'target_count')
                                            if f != 'target_count' or count is not None}})
            continue
        # IDs are stable; literal labels also address categories created in this patch.
        selected = []
        for target in targets:
            found = [c for c in categories if target in (c['category_id'], c['label'])]
            if len(found) != 1 or found[0] in selected:
                raise ValueError('Unknown or ambiguous requirement category; ask for its scope')
            selected.append(found[0])
        if not selected:
            raise ValueError('Requirement operation needs explicit category targets')
        if op in ('update_category', 'remove_category') and len(selected) != 1:
            raise ValueError('Update or remove exactly one category')
        if op == 'remove_category':
            categories.remove(selected[0])
        elif op == 'update_category':
            if edit['label'] is None and edit['scene'] is None:
                raise ValueError('Category update has no changes')
            for field in ('label', 'scene'):
                if edit[field] is not None:
                    if edit[field] not in source:
                        context = source + selected[0]['scene'] + selected[0]['label']
                        if field != 'scene' or any(char.isalnum() and char not in context for char in edit[field]):
                            raise ValueError('Changed fields must be grounded in this message and the previous scene; do not invent new content')
                        selected[0]['evidence']['scene_context'] = selected[0]['scene']
                    selected[0][field] = edit[field]
                    selected[0]['evidence'][field] = source
        elif op == 'set_count':
            if count is None:
                raise ValueError('set_count needs an explicit count')
            for category in selected:
                category['target_count'] = count
                category['evidence']['target_count'] = source
        else:
            text = edit['text']
            if not text:
                raise ValueError('Condition operation needs text')
            if op == 'add_condition' and text not in source:
                raise ValueError('New conditions must quote the user message')
            for category in selected:
                conditions = category['conditions']
                if op == 'add_condition':
                    if text not in [c['text'] for c in conditions]:
                        conditions.append({'text': text, 'source_text': source})
                else:
                    if text not in [c['text'] for c in conditions]:
                        raise ValueError('Cannot remove an unknown condition')
                    category['conditions'] = [c for c in conditions if c['text'] != text]
                if len(category['conditions']) > 20:
                    raise ValueError('Too many category conditions')
    if len(categories) > 20 or len({c['label'] for c in categories}) != len(categories):
        raise ValueError('Require at most 20 uniquely named categories')
    total = allocated_total(draft)
    if total is not None and total > 10000:
        raise ValueError('Total image quantity exceeds 10000')
    if sum(c['target_count'] or 0 for c in categories) > 10000:
        raise ValueError('Allocated image quantity exceeds 10000')
    return draft


def allocated_total(draft):
    categories = draft['categories']
    return (sum(c['target_count'] for c in categories)
            if categories and all(c['target_count'] is not None for c in categories) else None)


def project_draft(state, draft, explicit_total=None):
    """Compute compatibility fields once; never infer per-category counts from a total."""
    result = dict(state)
    categories = draft['categories']
    total = allocated_total(draft)
    previous = state.get('requirement_draft')
    if (explicit_total is None and categories and previous is not None
            and allocated_total(previous) is None):
        explicit_total = state.get('target_count')
    if total is not None and explicit_total is not None and total != explicit_total:
        raise ValueError(f'类别数量合计为 {total} 张，与总数 {explicit_total} 张冲突')
    if explicit_total is not None and sum(c['target_count'] or 0 for c in categories) > explicit_total:
        raise ValueError('已分配的类别数量超过图片总数')
    result['requirement_draft'] = draft
    result['subject'] = '、'.join(c['scene'] if c['label'] in c['scene'] else c['label'] + '：' + c['scene'] for c in categories) or None
    result['target_count'] = total if total is not None else explicit_total
    result['category_targets'] = ([{'label': c['label'], 'count': c['target_count']} for c in categories]
                                  if total is not None else [])
    if len(render_draft(result)) > 2000:
        raise ValueError('Structured request exceeds the supported 2000-character expansion input; ask to split it')
    return result


def render_draft(state):
    parts = []
    for category in state['requirement_draft']['categories']:
        count = category['target_count']
        part = category['label'] + (f'{count}张' if count is not None else '')
        details = ['画面：' + category['scene']]
        details += ['本类条件：' + c['text'] for c in category['conditions']]
        parts.append(part + '（' + '；'.join(details) + '）')
    text = '、'.join(parts)
    if allocated_total(state['requirement_draft']) is None and state.get('target_count') is not None:
        text += f"；总共{state['target_count']}张"
    if state.get('constraints'):
        text += '；共同条件：' + '；'.join(state['constraints'])
    return text


def check_clarification(proposal, state, message):
    """Known fields cannot be requested again; genuine conflicts have their own scope."""
    clarification = proposal.get('clarification')
    if clarification is not None:
        if (not isinstance(clarification, dict) or set(clarification) != {'field', 'source_text', 'question'}
                or clarification['field'] not in ('media_type', 'subject', 'target_count', 'scope', 'conflict')
                or not isinstance(clarification['source_text'], str) or not clarification['source_text'].strip()
                or clarification['source_text'] not in message
                or not isinstance(clarification['question'], str) or not 1 <= len(clarification['question']) <= 200):
            raise ValueError('Clarification must name a missing field or conflict and quote this message')
        if clarification['field'] in ('media_type', 'subject', 'target_count') and state.get(clarification['field']) is not None:
            raise ValueError('The requested field is already known; use it instead of asking again')
        proposal['question'] = clarification['question']
    # Also guard old-style tool replies and plain text, including update+stale question.
    if clarification is None or clarification['field'] not in ('scope', 'conflict'):
        text = (proposal.get('question') or '') + (proposal.get('reply') or '')
        if state.get('target_count') is not None and re.search(r'多少张|几张|多少幅|几幅|需要多少(?:图片|照片)', text):
            raise ValueError('Image quantity is already known; do not ask for it again')
