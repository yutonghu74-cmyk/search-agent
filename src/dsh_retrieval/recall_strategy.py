"""Deterministic scheduling inside an already approved source set.

Catalog order is relevance evidence, not a content guarantee. Effective output
is measured after cleaning and dedup; unmeasured branches remain explorable.
"""
from .refill_planning import RefillPosition, parse_refill_position


def suggest_term_adjustments(report):
    """Propose term review from measured candidate coverage, never cleaning yield."""
    if not report['complete']:
        return [{'action': 'wait_or_reconcile', 'reason': '检索记录尚未完整，失败或未知不能视为零命中。'}]
    if not report['mapping_complete'] or report['totals'].get('content_unknown_candidates', 0):
        return [{'action': 'repair_mapping', 'reason': '部分命中缺少稳定身份或完整内容摘要，先核对素材映射。'}]
    totals = report['totals']
    suggestions = []
    for group in (report.get('grouping') or {}).get('groups', ()):
        if group['delivery_target'] is not None and group['observed_candidates'] < group['delivery_target']:
            suggestions.append({'action': 'broaden_terms', 'group_label': group['primary_label'],
                'reason': group['primary_label'] + '当前可归组候选少于该组目标，可提出不同场景的词表；尚未计算清洗有效数。'})
    if not suggestions and totals['assignable_candidates'] < report['target_count']:
        suggestions.append({'action': 'broaden_terms',
            'reason': '当前可进入候选范围的数量少于图片目标，可提出不同场景的词表；这不是清洗后的业务缺口。'})
    if totals['repeated_hit_ratio'] is not None and totals['repeated_hit_ratio'] >= .5:
        suggestions.append({'action': 'diversify_terms',
            'reason': '至少一半已映射命中重复覆盖已有候选，可审阅更多不同场景的词表。'})
    return suggestions or [{'action': 'review_candidates',
        'reason': '已有候选供查看；仅凭数量不能判断图片是否符合需求。'}]


def initial_plan_ids(approved):
    """One best-ranked compatible index per query; retain the others for refill."""
    seen, selected = set(), set()
    for member in approved.members:
        if member.branch_id not in seen:
            seen.add(member.branch_id)
            selected.add(member.plan.request.plan_id)
    return frozenset(selected)


def _branch_key(binding):
    return (binding['library_id'], binding['query'], binding['index_version'],
            binding['parameters'].get('plan_id'), binding['parameters'].get('binding_id'))


def rank_refill_positions(positions, reservations, rounds):
    """Rank measured productive branches, then probe alternatives after zero yield.

    Reservations supply durable library/query attribution. Never infer cleaning
    success from raw vector hits or transfer statistics between task generations.
    Stable ties preserve quota/catalog order supplied by the caller.
    """
    by_iteration = {r.iteration: r for r in reservations}
    stats = {}
    for row in sorted(rounds, key=lambda r: r['iteration']):
        reservation = by_iteration.get(row['iteration'])
        if reservation is None or row['outcome'] not in {'empty', 'result'}:
            continue
        if reservation.position_binding_json is None:
            continue
        binding = parse_refill_position(RefillPosition(reservation.library_id,
            reservation.position_fingerprint, reservation.position_binding_json))
        key = _branch_key(binding)
        gain = row.get('new_unique_accepted')
        if gain is None:
            continue
        value = stats.setdefault(key, [0, 0, 0, None])
        value[0] += gain
        value[1] += row.get('candidate_count') or 0
        value[2] += 1
        value[3] = gain
    def key(position):
        binding = parse_refill_position(position)
        branch = _branch_key(binding)
        value = stats.get(branch)
        # Zero gain prompts a probe of a different library/query before deeper
        # pagination. Positive yield keeps its branch ahead of unmeasured ones.
        if value and value[0] > 0:
            score = value[0] / max(1, value[1])
            return (3 if value[3] == 0 else 0, -score)
        if value:
            return (3, value[2])
        return (2, 0)
    ranked = tuple(sorted(positions, key=key))
    reasons = {}
    for position in ranked:
        binding = parse_refill_position(position)
        branch = _branch_key(binding)
        value = stats.get(branch)
        reasons[position.fingerprint] = {
            'strategy': 'effective_unique_yield@1',
            'reason': ('continue_productive_branch' if value and value[0] > 0
                       and not (value[3] == 0) else
                       'probe_approved_alternative' if value is None else 'retry_low_yield_branch'),
            'last_new_unique_accepted': value[3] if value else None,
            'completed_rounds': value[2] if value else 0,
            'new_unique_accepted': value[0] if value else 0,
            'effective_yield': value[0] / value[1] if value and value[1] else None,
        }
    return ranked, reasons
