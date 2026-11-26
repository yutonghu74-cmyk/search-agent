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
              'source_text': {**text(1000), 'description': 'Exact contiguous quote from the latest user message; no paraphrase.'}}
    shapes = []
    for op, extra, required in (
        ('add_category', {'label': {**text(32), 'description': 'Required short category name copied as a contiguous substring of source_text. Do not concatenate separated words.'},
                          'scene': {**text(160), 'description': 'Required full scene copied as a contiguous substring of source_text, including object quantities and actions.'},
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


def check_request_scope(proposal, previous, message):
    if (draft_from_state(previous)['categories'] and proposal.get('requirement_edits')
            and proposal['action'] != 'replace' and re.search(r'单独|另外', message)
            and re.search(r'(?:跟|与|和)(?:刚才|之前|原来|前面).{0,8}(?:分开|独立)', message)):
        raise ValueError('The user explicitly requests a separate batch; use replace with only the new request, instead of appending categories to the previous draft')


def apply_edits(state, edits, message):
    """Apply a bounded atomic patch; untouched scenes, quantities and conditions survive."""
    if not isinstance(edits, list) or len(edits) > 40:
        raise ValueError('requirement_edits must contain at most 40 operations')
    draft = draft_from_state(state)
    categories = draft['categories']
    if (len(categories) > 1 and edits and re.fullmatch(
            rf'\s*(?:那个|这个|它|那类|这类)\s*(?:改成|改为|改|要)?\s*({NUMBER})\s*张[。.!！]?\s*', message)):
        raise ValueError('Category reference is ambiguous among several categories; ask a scope clarification and preserve the draft')
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
            # Answering "which category?" keeps the quantity quoted in the question's turn.
            pending = pending_scope_amounts(state)
            if count not in amounts and count in pending:
                edit['source_text'] = source = source + '｜' + state['pending_clarification']['source_text']
                amounts |= pending
            if count not in amounts:
                raise ValueError('Image quantity must quote an image count, not a scene object or term count')
        allowed = {'add_category': {'label', 'scene', 'count'},
                   'update_category': {'label', 'scene'}, 'remove_category': set(),
                   'set_count': {'count'}, 'add_condition': {'text'}, 'remove_condition': {'text'}}[op]
        if any(edit[f] is not None for f in {'label', 'scene', 'count', 'text'} - allowed):
            raise ValueError('Fields do not match requirement operation')
        if op == 'add_category':
            label, scene = edit['label'], edit['scene']
            if targets:
                raise ValueError('add_category needs targets=[]; use label to name the new category')
            if not label or label not in source:
                raise ValueError('add_category.label is required and must be a contiguous substring of source_text; do not concatenate separated words')
            if not scene or scene not in source:
                raise ValueError('add_category.scene is required and must be a contiguous substring of source_text, retaining the full scene')
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
            if (edit['scene'] is not None and edit['label'] is None
                    and selected[0]['label'] == selected[0]['scene']
                    and selected[0]['label'] not in edit['scene']):
                raise ValueError('The category label contains the old scene; update label together with scene, quoting the new entity name from this message. Keep category_id unchanged')
            for field in ('label', 'scene'):
                if edit[field] is not None:
                    if edit[field] not in source:
                        context = source + selected[0]['scene'] + selected[0]['label']
                        scene_chars = iter(edit['scene'] or '')
                        label_from_scene = field == 'label' and edit['scene'] is not None and all(
                            any(char == candidate for candidate in scene_chars) for char in edit[field])
                        recomposed_scene = field == 'scene' or label_from_scene
                        if not recomposed_scene or any(char.isalnum() and char not in context for char in edit[field]):
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
            inherited_common = op == 'add_condition' and text in (state.get('constraints') or [])
            if op == 'add_condition' and text not in source and not inherited_common:
                raise ValueError('New conditions must quote the user message')
            for category in selected:
                conditions = category['conditions']
                if op == 'add_condition':
                    if text not in [c['text'] for c in conditions]:
                        condition = {'text': text, 'source_text': source}
                        if inherited_common and text not in source:
                            condition['previous_common_condition'] = text
                        conditions.append(condition)
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


def pending_scope_amounts(state):
    """Image counts quoted by the turn whose scope is still being clarified."""
    pending = state.get('pending_clarification')
    if not pending or pending.get('field') != 'scope' or state.get('question') != 'custom_clarification':
        return set()
    return {number_value(m[1]) for m in re.finditer(rf'({NUMBER})\s*张', pending['source_text'])}


def retarget_total(draft, total, source):
    """Apply a new overall image count without inventing a per-category split.

    One category follows the total; unallocated categories keep the total
    unallocated. Several allocated categories need an explicit per-category edit.
    """
    draft = deepcopy(draft)
    categories = draft['categories']
    if len(categories) == 1 and categories[0]['target_count'] is not None:
        categories[0]['target_count'] = total
        categories[0]['evidence']['target_count'] = source
    elif len(categories) > 1 and any(c['target_count'] is not None for c in categories):
        raise ValueError('各类已有数量，改总数需要说明各类如何调整；请用 set_count 修改具体类别，或追问 scope')
    return draft


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


def check_initial_allocation(proposal, previous, state, message, explicit_targets=()):
    """Reject an omitted explicit global quota before saving a new request.

    This checks a narrow, unambiguous suffix; category interpretation and
    correction still belong to the model. Conflicting totals need clarification.
    """
    if proposal['action'] not in ('update', 'replace', 'revise_terms'):
        return
    if proposal['action'] != 'replace' and draft_from_state(previous)['categories']:
        return
    categories = draft_from_state(state)['categories']
    stated_counts = {number_value(m[1]) for m in re.finditer(
        rf'(?:图片|照片|图)\s*[，,]?\s*({NUMBER})\s*张(?:[，,。.!！]|$)', message)}
    if len(categories) == 1 and len(stated_counts) == 1 and state.get('target_count') is None:
        raise ValueError('Explicit image quantity was omitted from the new single-category request; save the stated total or category count')
    for target in explicit_targets:
        matches = [c for c in categories if target['label'] in (c['label'], c['scene'])]
        if len(matches) == 1 and matches[0]['target_count'] != target['count']:
            raise ValueError('Explicit category image quantity was omitted; preserve each stated quota using add_category.count or set_count')
    total = re.search(rf'(?:总共|一共|总计|合计|总数|总量)\s*({NUMBER})\s*张', message)
    allocated = allocated_total(draft_from_state(state))
    if (total and allocated is not None and allocated != number_value(total[1])
            and (proposal.get('clarification') or {}).get('field') != 'conflict'):
        raise ValueError('Explicit total conflicts with category quotas; preserve the categories and ask a conflict clarification instead of choosing one quantity silently')
    if re.search(r'总共|一共|总计|合计|总数|总量', message):
        return
    match = re.search(
        rf'[，,；;]\s*(?:各|每(?:个)?(?:类型|类别|种类)|每类|每种)'
        rf'\s*(?:都)?(?:要|需要)?\s*({NUMBER})\s*张(?:图片|照片)?[。.!！]?\s*$', message)
    if match and categories and any(c['target_count'] != number_value(match[1]) for c in categories):
        raise ValueError('Explicit per-category image quantity was omitted; apply set_count to every new category instead of asking for quantity again')


def check_clarification(proposal, state, message):
    """Known fields cannot be requested again; genuine conflicts have their own scope."""
    clarification = proposal.get('clarification')
    if ((state.get('pending_clarification') or {}).get('field') == 'conflict'
            and re.fullmatch(rf'\s*(?:就)?按(?:每类|每种|各)\s*({NUMBER})\s*张(?:算|计算)?[。.!！]?\s*', message)
            and not proposal.get('requirement_edits')):
        raise ValueError('Resolve the pending quantity conflict with requirement_edits/set_count for the stated categories; an answer or confirm action does not resolve the saved conflict')
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
