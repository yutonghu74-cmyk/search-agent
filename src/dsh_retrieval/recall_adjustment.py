"""Server-owned scope for provisional term changes; original approval still applies."""
from .errors import ServiceError


def require_adjustable(report):
    action = (report.get('diagnosis') or {}).get('next_step', {}).get('action')
    if not report['can_refine'] or action not in (None, 'review_strategy', 'review_candidates'):
        raise ServiceError('RECALL_NOT_READY', '当前应先处理等待、复核、失败或执行限制；暂不据此调整词表', 409)


def adjustment_scope(report, groups):
    require_adjustable(report)
    labels = [g['primary_label'] for g in groups]
    editable = labels
    cleaning = report.get('cleaning') or {}
    quotas = [g for g in groups if g.get('delivery_target') is not None]
    if cleaning.get('status') == 'observed' and quotas:
        gaps = cleaning.get('group_gaps', [])
        by_id = {row['group_id']: row['gap'] for row in gaps}
        expected = {g['group_id'] for g in quotas}
        if len(by_id) != len(gaps) or set(by_id) != expected or any(type(v) is not int or v < 0 for v in by_id.values()):
            raise ServiceError('RECALL_NOT_READY', '分组缺口证据不完整，请重新读取当前结果', 409)
        editable = [g['primary_label'] for g in groups if g.get('delivery_target') is None or by_id[g['group_id']] > 0]
    editable = [label for label in editable if next(g for g in groups if g['primary_label'] == label)['auxiliary_terms']]
    if not editable:
        raise ServiceError('RECALL_NOT_READY', '当前没有可调整的缺口分组或辅助词', 409)
    return {'schema_version': 'recall-adjustment@1', 'editable_groups': editable,
            'preserved_groups': [label for label in labels if label not in editable],
            'evidence_digest': report['evidence_digest'], 'scope': dict(report['scope']),
            'execution_allowed': False, 'requires_owner_confirmation': True}


def adjustment_changes(policy, before, after):
    rows = []
    new = {g['primary_label']: g['auxiliary_terms'] for g in after or []}
    for group in before:
        label = group['primary_label']; old = group['auxiliary_terms']; terms = new.get(label)
        if terms is not None:
            rows.append({'group_label': label, 'editable': label in policy['editable_groups'],
                         'added_terms': [t for t in terms if t not in old],
                         'removed_terms': [t for t in old if t not in terms]})
    return {**policy, 'group_changes': rows}
