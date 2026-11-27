"""Conservative reuse decisions from saved requirement facts, never browser claims."""
from copy import deepcopy
import re

# These are post-retrieval checks. Scene, object, source and resolution changes
# are deliberately absent: an unrecognized condition goes through planning.
_CLEANING_CONDITIONS = frozenset((
    '无水印', '不要水印', '不带水印', '没有水印', '排除水印',
    '无文字', '不要文字', '不带文字', '没有文字',
    '无字幕', '不要字幕', '不带字幕',
    '不要模糊', '不要模糊图', '不要模糊图片', '排除模糊图片',
    '清晰', '图片清晰', '清晰图片', '清晰的图片',
))


def _conditions(values):
    return sorted({part.strip(' 。.!！') for value in values
                   for part in re.split(r'[；;，,、\n]', value) if part.strip(' 。.!！')})


def request_facts(state):
    """Drop transcript/evidence only; preserve category identity, scope and order."""
    draft = state.get('requirement_draft')
    categories = None if draft is None else [
        {key: deepcopy(c[key]) for key in ('category_id', 'label', 'scene', 'target_count')}
        | {'conditions': _conditions([item['text'] for item in c['conditions']])}
        for c in draft['categories']]
    return {'subject': state.get('subject'), 'media_type': state.get('media_type'),
            'target_count': state.get('target_count'), 'categories': categories,
            'category_targets': deepcopy(state.get('category_targets') or []),
            'conditions': _conditions(state.get('constraints') or [])}


def reusable_change(before, after):
    """Return the exact retained scope, or None for any query-affecting change."""
    if before is None or after is None or before['media_type'] != after['media_type']:
        return None
    left, right = deepcopy(before), deepcopy(after)
    counts_changed = left.pop('target_count') != right.pop('target_count')
    counts_changed |= left.pop('category_targets') != right.pop('category_targets')
    changed_conditions = set(left.pop('conditions')) ^ set(right.pop('conditions'))
    if (left['categories'] is None) != (right['categories'] is None):
        return None
    if left['categories'] is not None:
        if len(left['categories']) != len(right['categories']):
            return None
        for old, new in zip(left['categories'], right['categories']):
            counts_changed |= old.pop('target_count') != new.pop('target_count')
            changed_conditions |= set(old.pop('conditions')) ^ set(new.pop('conditions'))
    if left != right or not changed_conditions <= _CLEANING_CONDITIONS:
        return None
    if counts_changed and changed_conditions:
        return 'quantity_and_cleaning'
    if counts_changed:
        return 'quantity'
    if changed_conditions:
        return 'cleaning'
    return None


def planning_change(prior, current, proposal, action):
    if (action == 'confirm_terms' and prior.get('stage') == current.get('stage') == 'terms'
            and not proposal.get('expansion_instruction') and not proposal.get('count_edits')
            and request_facts(prior) == request_facts(current)):
        return 'unchanged'
    if (action not in ('update', 'revise_terms') or prior.get('stage') != 'terms'
            or current.get('stage') != 'terms' or proposal.get('expansion_instruction')
            or any(edit.get('target') == 'terms' for edit in proposal.get('count_edits', []))):
        return None
    return reusable_change(request_facts(prior), request_facts(current))
