"""Measured cleaning feedback and bounded continuation receipts; no execution here."""
from collections import Counter

from .errors import ServiceError
from .query_spec import digest


def cleaning_feedback(reader, actor, task):
    if reader is None:
        return {'status': 'not_configured'}, {}
    try:
        result = reader.get_current_result_set(actor, task.task_id)
    except ServiceError as error:
        if error.code == 'NOT_FOUND':
            return {'status': 'not_published'}, {}
        if error.code == 'REVISION_CONFLICT':
            return {'status': 'not_current'}, {}
        raise
    if (result.get('as_of_task_version') != task.task_version
            or result.get('generation') != task.run_generation
            or result.get('counts_status') != 'fresh'):
        return {'status': 'not_current'}, {}
    counts = ('accepted_count', 'eligible_unique_count', 'failed_count',
              'pending_review_count', 'duplicate_count', 'gap')
    if any(type(result.get(k)) is not int or result[k] < 0 for k in counts):
        raise ServiceError('STORAGE_CORRUPT', 'Published cleaning counts are invalid', 500)
    reasons = Counter()
    assessments = result.get('candidate_assessments') or {}
    for candidate in assessments.values():
        # Reasons are validator codes, not model-authored advice or new rules.
        for rule in candidate.get('reasons', ()):
            if rule.get('verdict') in ('fail', 'unknown'):
                reasons[(rule.get('rule_id'), rule['verdict'], rule.get('reason_code'))] += 1
    if len(reasons) > 200:
        raise ServiceError('RECALL_INPUT_LIMIT', 'Cleaning reason summary exceeds the feedback limit', 422)
    summary = {'status': 'observed', 'coverage': 'current_published_result_set',
        'result_set_id': result['result_set_id'], 'result_set_digest': result['result_set_digest'],
        **{key: result[key] for key in counts},
        'historical_exclusion_count': result.get('historical_exclusion_count', 0),
        'group_gaps': [{'group_id': row.group_id, 'gap': row.gap}
                       for row in result.get('group_quotas', ())],
        'reason_counts': [{'rule_id': rule, 'verdict': verdict, 'reason_code': code, 'count': count}
                          for (rule, verdict, code), count in sorted(reasons.items(), key=lambda item: repr(item[0]))]}
    return summary, assessments


def source_yield(candidate_ids, assessments):
    counts = dict(effective_unique=0, failed=0, unknown=0, duplicates=0,
                  reserve=0, excluded=0, unassessed=0)
    for cid in candidate_ids:
        row = assessments.get(cid)
        if row is None:
            counts['unassessed'] += 1
        elif row.get('cleaning_status') == 'unknown':
            counts['unknown'] += 1
        elif row.get('disposition') == 'accepted':
            counts['effective_unique'] += 1
        elif row.get('disposition') == 'reserve':
            counts['reserve'] += 1
        elif row.get('disposition') in ('duplicate', 'historical_duplicate'):
            counts['duplicates'] += 1
        elif row.get('cleaning_status') == 'fail':
            counts['failed'] += 1
        elif row.get('disposition') == 'user_excluded':
            counts['excluded'] += 1
        else:
            counts['unassessed'] += 1
    return {**counts, 'candidate_count': len(candidate_ids),
            'attribution': 'first_observed_candidate_identity'}


def continuation_feedback(view, recall=None):
    """Explain exactly the business planner's action without authorizing new scope."""
    action, stop = view['action'], view['stop_reason']
    if view.get('waiting_for_review'):
        code, text = 'review_cleaning', '部分清洗结果待复核，先走原待复核流程；未知不计为通过、淘汰或零产出，不重复派发补数。'
    elif action == 'search_more':
        code, text = 'continue_approved', '按实际有效产出，在已批准的查询、素材库和预算内继续检索。'
    elif action == 'wait':
        code, text = 'wait_for_result', '本轮尚未完成，等待原检索或清洗结果，不重复派发。'
    elif action == 'await_owner':
        code, text = 'owner_confirmation', '当前没有自动补数授权，继续检索仍需发起人确认。'
    elif view['gap'] == 0 and not any(row.get('gap', 0) for row in view.get('group_gaps', ())):
        code, text = 'target_reached', '有效数量已达标，停止检索；交付仍按原流程确认。'
    elif stop == 'EXECUTION_LIMIT_REACHED':
        code, text = 'stopped', '已达到批准的执行预算或轮数上限；扩大预算或增加新词表均须重新确认。'
    else:
        code, text = 'stopped', '已停止本轮补数；新词表、扩大来源或预算、放宽条件均须重新确认。'
    receipt = {'schema_version': 'retrieval-feedback@1',
        'scope': {k: view[k] for k in ('task_id', 'revision', 'generation', 'task_version')},
        'action': code, 'reason': text, 'stop_reason': stop,
        'accepted_count': view['accepted_count'], 'gap': view['gap'],
        'group_gaps': view.get('group_gaps', []),
        'next_position': view.get('next_position'),
        'execution': view['execution'],
        'recall_evidence_digest': recall['evidence_digest'] if recall else None,
        'cleaning': recall.get('cleaning') if recall else None,
        'requires_confirmation_for': ['new_terms', 'new_sources', 'higher_budget', 'relaxed_conditions']}
    receipt['feedback_digest'] = digest(receipt)
    return receipt
