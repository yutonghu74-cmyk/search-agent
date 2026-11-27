"""Read-only recall evidence over original approved pages and media identities.

This is retrieval feedback, not a cleaning verdict or a ground-truth evaluation.
No search, embedding, model call or business write is permitted here.
"""
import json
from dataclasses import asdict

from .candidate_contracts import candidate_identity
from .errors import ServiceError
from .integration_models import AccessContext
from .multi_branch_contracts import plan_origin_groups
from .query_branches import compile_query_branches
from .query_spec import digest
from .recall_strategy import suggest_term_adjustments


def _head(progress):
    return {key: progress.get(key) for key in ('revision', 'generation', 'task_version',
        'members', 'initial_window_complete', 'pagination_stopped')} | {
        'queue_state': (progress.get('execution_queue') or {}).get('state'),
        'queue_error': (progress.get('execution_queue') or {}).get('error_code'),
        'budget_stop': (progress.get('execution_budget') or {}).get('stop_reason')}


def read_recall(engine, actor, task_id):
    task = engine.service.repository.get(actor, task_id)
    if task.media_type != 'image':
        raise ServiceError('RECALL_NOT_SUPPORTED', '当前召回分析仅支持图片检索', 422)
    spec = json.loads(task.confirmed_proposal_json)['query_spec']
    approved = engine.repository.get_browse_current(actor, task_id)
    if approved is None:
        raise ServiceError('PLAN_NOT_APPROVED', '尚无当前版本的已确认检索计划', 409)
    progress = engine.progress(actor, task_id, browsing=True)
    pages = engine.repository.list_committed_pages(actor, approved, allow_downstream=True)
    if sum(len(page.hits) for _, page in pages) > 10000 or len(approved.members) > 100:
        raise ServiceError('RECALL_INPUT_LIMIT', '当前记录超出召回分析上限，未给出完整结论', 422)
    adapter = engine._adapter_for_plan(actor, task_id, approved)
    context = AccessContext(actor, task_id, 'retrieval', 'current')
    logical = {row['branch_id']: row for row in compile_query_branches(spec)['branches']}
    heads = {row['plan_id']: row for row in progress['members']}
    origin_groups = plan_origin_groups(task, approved)
    quota_task = any(origin_groups.values())
    group_ids = {}
    branches, by_plan, unique, content, raw = {}, {}, set(), {}, []
    attributed, covered = {}, {}
    for member in approved.members:
        plan_id, branch_id = member.plan.request.plan_id, member.branch_id
        if branch_id not in logical or plan_id not in heads:
            raise ServiceError('STORAGE_CORRUPT', '召回记录缺少原查询分支', 500)
        row = branches.setdefault(branch_id, {'branch_id': branch_id,
            'query_text': logical[branch_id]['query_text'], 'query_role': logical[branch_id]['query_role'],
            'raw_hits': 0, 'resolved_hits': 0, 'unresolved_hits': 0,
            'new_unique_candidates': 0, 'repeated_hits': 0, 'sources': []})
        row['sources'].append({'library_id': member.plan.request.binding.library_id,
            'binding_id': member.plan.request.binding.binding_id,
            'plan_id': plan_id, 'index_version': member.plan.request.binding.index_version,
            'committed_pages': heads[plan_id]['committed_pages'],
            'status': 'deferred' if heads[plan_id].get('initial_deferred') else heads[plan_id]['status']})
        by_plan[plan_id] = row
        attributed[plan_id] = set()
        covered[plan_id] = set()
    for plan_id, page in pages:
        if plan_id not in by_plan:
            raise ServiceError('STORAGE_CORRUPT', '召回页面不属于当前计划', 500)
        raw.extend((plan_id, hit) for hit in page.hits)
    for offset in range(0, len(raw), 200):
        batch = raw[offset:offset+200]
        resolved = adapter.resolve_hits(context, tuple(hit for _, hit in batch))
        if len(resolved) != len(batch):
            raise ServiceError('STORAGE_CORRUPT', '召回映射记录数量不一致', 500)
        for (plan_id, hit), resolution in zip(batch, resolved):
            if resolution.hit != hit:
                raise ServiceError('STORAGE_CORRUPT', '召回映射不对应原始命中', 500)
            row = by_plan[plan_id]; row['raw_hits'] += 1
            if resolution.status != 'resolved' or resolution.media is None:
                row['unresolved_hits'] += 1
                continue
            cid = candidate_identity(resolution.media)
            covered[plan_id].add(cid)
            previous, current = content.get(cid), resolution.media.content_digest
            if previous is not None and current is not None and previous != current:
                raise ServiceError('MEDIA_IDENTITY_CONFLICT', '同一候选身份的内容版本不一致', 409)
            content[cid] = previous or current
            group_ids.setdefault(cid, set()).update(origin_groups[plan_id])
            row['resolved_hits'] += 1
            row['repeated_hits' if cid in unique else 'new_unique_candidates'] += 1
            if cid not in unique:
                attributed[plan_id].add(cid)
            unique.add(cid)
    from .retrieval_feedback import cleaning_feedback, source_yield
    cleaning, assessments = cleaning_feedback(getattr(engine, 'cleaning_feedback_reader', None), actor, task)
    if cleaning['status'] == 'observed':
        for row in branches.values():
            for source in row['sources']:
                source['cleaning'] = source_yield(attributed[source['plan_id']], assessments)
                source['cleaning']['uncertain_coverage'] = any(
                    cid not in assessments or assessments[cid].get('cleaning_status') == 'unknown'
                    for cid in covered[source['plan_id']])
    if (engine.repository.get_browse_current(actor, task_id) != approved
            or _head(engine.progress(actor, task_id, browsing=True)) != _head(progress)
            or engine.service.repository.get(actor, task_id) != task):
        raise ServiceError('REVISION_CONFLICT', '检索记录正在变化，请重新读取召回分析', 409)
    queue = progress.get('execution_queue') or {}
    complete = bool(progress.get('initial_window_complete', progress.get('pagination_stopped')))
    complete = complete and not any(r['status'] == 'pending' for r in progress['members'])
    complete = complete and queue.get('state') not in ('failed', 'queued', 'running')
    totals = {key: sum(row[key] for row in branches.values()) for key in
              ('raw_hits', 'resolved_hits', 'unresolved_hits', 'new_unique_candidates', 'repeated_hits')}
    totals['unique_candidates'] = len(unique)
    query_input = spec.get('query_input') or {}
    excluded = (query_input.get('digest') if query_input.get('type') == 'reference_image'
                and query_input.get('exclude_exact_content') else None)
    assignable = {cid for cid in unique if content[cid] is not None and content[cid] != excluded
                  and (group_ids[cid] or not quota_task)}
    totals['assignable_candidates'] = len(assignable)
    totals['unassigned_candidates'] = sum(quota_task and not group_ids[cid] for cid in unique)
    totals['content_unknown_candidates'] = sum(content[cid] is None for cid in unique)
    totals['repeated_hit_ratio'] = totals['repeated_hits'] / totals['resolved_hits'] if totals['resolved_hits'] else None
    report = {'schema_version': 'retrieval-recall@1',
        'scope': {'task_id': task_id, 'revision': task.confirmed_revision,
                  'generation': task.run_generation, 'task_version': task.task_version},
        'plan_digest': approved.proposal_digest, 'target_count': spec['target_count'],
        'coverage': 'current_retrieval_window',
        'source_snapshot_digest': digest({'pages': [(pid, asdict(page)) for pid, page in pages],
                                         'candidate_content': sorted(content.items())}),
        'complete': complete, 'mapping_complete': totals['unresolved_hits'] == 0,
        'attribution': 'approved-member-then-page-first-candidate@1',
        'branches': list(branches.values()), 'totals': totals,
        'grouping': ({'parent_label': spec['grouped_expansion']['parent_label'],
                      'groups': [{'primary_label': g['primary_label'],
                                  'delivery_target': g['delivery_target'],
                                  'term_count': len(g['auxiliary_terms']),
                                  'observed_candidates': sum(g['group_id'] in group_ids[cid] for cid in assignable)}
                                 for g in spec['grouped_expansion']['groups']]}
                     if spec.get('grouped_expansion') else None),
        'quality': {'relevance': 'not_assessed',
                    'accepted_count': None, 'corpus_recall': None},
        'cleaning': cleaning,
        'execution_state': 'incomplete' if not complete else 'observed',
        'execution_budget': progress.get('execution_budget'),
        'can_refine': bool(complete and not totals['unresolved_hits'] and not totals['content_unknown_candidates']
                           and cleaning.get('status') != 'not_current'
                           and not cleaning.get('pending_review_count')
                           and spec.get('grouped_expansion')
                           and any(g['auxiliary_terms'] for g in spec['grouped_expansion']['groups'])),
        'note': '统计当前检索窗口的候选身份与重复覆盖；未执行来源不按零命中，不代表语义相关或全库召回率；清洗有效数仅在当前已发布结果可读时提供，归因不是因果质量评测。'}
    report['suggestions'] = suggest_term_adjustments(report)
    report['evidence_digest'] = digest(report)
    return report
