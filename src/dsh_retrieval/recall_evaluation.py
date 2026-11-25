"""Read-only evaluation of frozen retrieval evidence; never changes scheduling."""
import re
from copy import deepcopy
from urllib.parse import quote
import json

from .query_spec import digest


def summarize_run(run):
    if run.get('schema') != 'recall-run@1':
        raise ValueError('unsupported recall run')
    scope = run['scope']
    if (set(scope) != {'task_id', 'revision', 'generation', 'task_version'}
            or not isinstance(scope['task_id'], str)
            or any(type(scope[k]) is not int or scope[k] < 1
                   for k in ('revision', 'generation', 'task_version'))):
        raise ValueError('invalid scope')
    candidates = run['candidates']
    for metric in ('vlm_calls', 'elapsed_ms'):
        value = run.get(metric)
        if value is not None and (type(value) not in (int, float) or value < 0
                                  or value != value or value == float('inf')):
            raise ValueError('invalid measured usage')
        if metric == 'vlm_calls' and value is not None and type(value) is not int:
            raise ValueError('model calls must be an integer')
    for cid, row in candidates.items():
        if (not isinstance(cid, str) or row['verdict'] not in {'pass', 'fail', 'unknown'}
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', row['content_digest'])
                or row['disposition'] not in {'accepted', 'reserve', 'duplicate',
                                               'failed', 'review', 'user_excluded', 'historical_duplicate'}
                or row['verdict'] != 'pass' and row['disposition'] in {'accepted', 'reserve', 'duplicate'}):
            raise ValueError('invalid candidate assessment')
    hits = run['hits']
    identities = set()
    for hit in hits:
        identity = (hit['batch'], hit['branch_id'], hit['hit_id'])
        if (hit['scope'] != scope or type(hit['batch']) is not int or hit['batch'] < 0
                or not isinstance(hit['branch_id'], str) or not hit['branch_id']
                or not isinstance(hit['hit_id'], str) or not hit['hit_id']
                or identity in identities
                or hit.get('candidate_id') is not None and hit['candidate_id'] not in candidates):
            raise ValueError('foreign, duplicate or invalid hit evidence')
        identities.add(identity)
    selected = {c['content_digest'] for c in candidates.values()
                if c['disposition'] == 'accepted' and c['verdict'] == 'pass'}
    reserves = {c['content_digest'] for c in candidates.values()
                if c['disposition'] == 'reserve' and c['verdict'] == 'pass'} - selected
    branches, seen_candidates, credited_content, raw_seen = {}, set(), set(), set()
    first_branch = {}
    for hit in sorted(hits, key=lambda h: (h['batch'], h['branch_id'], h['hit_id'])):
        b = branches.setdefault(hit['branch_id'], dict(raw_hits=0, unresolved=0, unassessed_hits=0,
            first_candidates=0, repeated_coverage=0, passed=0, failed=0, unknown=0,
            new_unique_eligible=0, selected_unique=0, reserve_unique=0, owner_excluded=0))
        raw_key = (hit['batch'], hit['branch_id'], hit.get('raw_hit_id', hit['hit_id']))
        if raw_key not in raw_seen:
            b['raw_hits'] += 1
            raw_seen.add(raw_key)
        cid = hit.get('candidate_id')
        if cid is None:
            b['unassessed_hits' if hit.get('unresolved_reason') == 'unassessed' else 'unresolved'] += 1
            continue
        if cid in seen_candidates:
            b['repeated_coverage'] += 1
            continue
        seen_candidates.add(cid)
        first_branch[cid] = hit['branch_id']
        b['first_candidates'] += 1
        row = candidates[cid]
        b[{'pass': 'passed', 'fail': 'failed', 'unknown': 'unknown'}[row['verdict']]] += 1
        if row['disposition'] == 'user_excluded':
            b['owner_excluded'] += 1
        content = row['content_digest']
        if row['verdict'] == 'pass' and content in selected | reserves:
            if content not in credited_content:
                credited_content.add(content)
                b['new_unique_eligible'] += 1
                # Selection is content-level; a representative can change.
                b['selected_unique'] += content in selected
                b['reserve_unique'] += content in reserves
    usage = run.get('usage')
    expected = {tuple(item) for item in run.get('usage_expected', [])}
    completed = set()
    pending, unattributed = 0, 0
    measurements = set()
    for b in branches.values():
        b['effective_yield_per_raw_hit'] = b['new_unique_eligible'] / b['raw_hits']
        b['http_attempts_observed'] = 0
        b['http_errors'] = 0
        b['pending_http_attempts'] = 0
        b['http_elapsed_ms'] = 0 if usage is not None else None
        b['detector_elapsed_ms'] = 0 if usage is not None else None
        b['vlm_calls'] = None
        b['calls_per_unique'] = None
    for row in usage or ():
        if not isinstance(row.get('measurement_id'), str) or row['measurement_id'] in measurements:
            raise ValueError('duplicate or invalid usage measurement')
        measurements.add(row['measurement_id'])
        cid = row['candidate_id']
        if (cid not in candidates or type(row['supported']) is not bool
                or row['status'] not in {'started', 'completed', 'error'}
                or any(type(row[k]) is not int or row[k] < 0
                       for k in ('http_attempts', 'http_errors', 'pending_http_attempts'))
                or row['http_errors'] > row['http_attempts']):
            raise ValueError('invalid measured usage row')
        if ((row['status'] == 'started') != (row['step_elapsed_ms'] is None)
                or row['http_elapsed_ms'] is None):
            raise ValueError('incomplete measured duration')
        for value in (row['step_elapsed_ms'], row['http_elapsed_ms']):
            if value is not None and (type(value) not in (int, float) or value < 0
                    or value != value or value == float('inf')):
                raise ValueError('invalid measured duration')
        key = (row['operation_id'], cid, row['step_id'])
        if key not in expected:
            raise ValueError('usage belongs to another execution step')
        pending += row['status'] == 'started' or row['pending_http_attempts'] > 0
        if row['supported'] and row['status'] == 'completed' and row['pending_http_attempts'] == 0:
            completed.add(key)
        branch = first_branch.get(cid)
        if branch is None:
            unattributed += 1
            continue
        b = branches[branch]
        b['http_attempts_observed'] += row['http_attempts']
        b['http_errors'] += row['http_errors']
        b['pending_http_attempts'] += row['pending_http_attempts']
        b['http_elapsed_ms'] += row['http_elapsed_ms']
        b['detector_elapsed_ms'] += row['step_elapsed_ms'] or 0
    for branch, b in branches.items():
        keys = {key for key in expected if first_branch.get(key[1]) == branch}
        b['usage_complete'] = bool(keys) and keys <= completed and not any(
            row['candidate_id'] in first_branch and first_branch[row['candidate_id']] == branch
            and (row['status'] == 'started' or row['pending_http_attempts']) for row in usage or ())
        if b['usage_complete']:
            b['vlm_calls'] = b['http_attempts_observed']
            b['calls_per_unique'] = (b['vlm_calls'] / b['new_unique_eligible']
                                      if b['new_unique_eligible'] else None)
    judgments = run.get('judgments', {})
    if any(not re.fullmatch(r'sha256:[0-9a-f]{64}', k) or type(v) is not bool
           for k, v in judgments.items()):
        raise ValueError('judgments require content digests and explicit booleans')
    relevant = {k for k, value in judgments.items() if value}
    judged_eligible = credited_content & set(judgments)
    totals = {key: sum(b[key] for b in branches.values()) for key in
              ('raw_hits', 'unresolved', 'unassessed_hits', 'first_candidates', 'repeated_coverage',
               'passed', 'failed', 'unknown', 'new_unique_eligible',
               'selected_unique', 'reserve_unique', 'owner_excluded')}
    return {'schema': 'recall-evaluation@1', 'scope': scope,
            'requirement_digest': run['requirement_digest'],
            'corpus_digest': run.get('corpus_digest'),
            'corpus_binding': run.get('corpus_binding',
                                      'supplied_snapshot' if run.get('corpus_digest') else 'unavailable'),
            'evidence_digest': digest(run), 'judgments_digest': digest(judgments),
            'attribution': 'first-batch-then-branch@1', 'branches': branches,
            'branch_metadata': run.get('branch_metadata', {}),
            'totals': totals, 'judged_positives': len(relevant),
            'judged_positive_coverage': (len(relevant & credited_content) / len(relevant)
                                         if relevant else None),
            'judged_eligible_count': len(judged_eligible),
            'judged_precision': (len(judged_eligible & relevant) / len(judged_eligible)
                                  if judged_eligible else None),
            'corpus_recall': None,
            'labeling_required': bool({c['content_digest'] for c in candidates.values()} - set(judgments)),
            'unseen_assessments': len(set(candidates) - seen_candidates),
            'attribution_complete': (not (set(candidates) - seen_candidates)
                                      and totals['unassessed_hits'] == 0 and totals['unresolved'] == 0),
            'business_counts': run.get('business_counts'),
            'usage_complete': bool(expected) and expected <= completed and pending == 0,
            'usage_scope': run.get('usage_scope', 'provided_run'),
            'unattributed_usage_steps': unattributed,
            'http_attempts_observed': sum(row['http_attempts'] for row in usage or ()),
            'http_elapsed_ms_observed': (sum(row['http_elapsed_ms'] for row in usage)
                                         if usage is not None else None),
            'elapsed_ms': run.get('elapsed_ms'),
            'vlm_calls': (sum(row['http_attempts'] for row in usage or ())
                           if expected and expected <= completed and not pending else
                           run.get('vlm_calls') if usage is None else None)}


def compare_runs(baseline, proposed):
    a, b = summarize_run(baseline), summarize_run(proposed)
    for key in ('requirement_digest', 'corpus_digest', 'judgments_digest', 'attribution', 'usage_scope'):
        if a[key] != b[key] or key == 'corpus_digest' and a[key] is None:
            raise ValueError('comparison requires matching frozen requirements, corpus and judgments')
    return {'schema': 'recall-comparison@1', 'baseline': a, 'proposed': b,
            'new_unique_eligible_delta': b['totals']['new_unique_eligible'] - a['totals']['new_unique_eligible'],
            'selected_unique_delta': b['totals']['selected_unique'] - a['totals']['selected_unique'],
            'vlm_calls_delta': (None if a['vlm_calls'] is None or b['vlm_calls'] is None
                               else b['vlm_calls'] - a['vlm_calls']),
            'elapsed_ms_delta': (None if a['elapsed_ms'] is None or b['elapsed_ms'] is None
                                else b['elapsed_ms'] - a['elapsed_ms']),
            'http_elapsed_ms_delta': (b['http_elapsed_ms_observed'] - a['http_elapsed_ms_observed']
                                       if a['usage_complete'] and b['usage_complete'] else None),
            'corpus_recall': None}


def prepare_review(run):
    summarize_run(run)
    return {'schema': 'recall-judgments@1', 'evidence_digest': digest(run),
            'scope': run['scope'],
            'requirement_digest': run['requirement_digest'],
            'corpus_digest': run.get('corpus_digest'),
            'context': run.get('review_context'),
            'task_view_path': '/production?task=' + quote(run['scope']['task_id'], safe='') + '&node=cleaning',
            'preview_access': 'requires_current_authorization',
            'items': {d: [{'candidate_id': cid,
                'preview_path': '/v1/retrieval/tasks/' + run['scope']['task_id'] +
                    '/cleaning/candidates/' + cid + '/preview'}
                for cid, c in run['candidates'].items() if c['content_digest'] == d]
                for d in sorted({c['content_digest'] for c in run['candidates'].values()})},
            'labels': {d: None for d in sorted({c['content_digest']
                                               for c in run['candidates'].values()})}}


def attach_judgments(run, review):
    if (review.get('schema') != 'recall-judgments@1'
            or review.get('evidence_digest') != digest(run)
            or review.get('requirement_digest') != run['requirement_digest']
            or review.get('corpus_digest') != run.get('corpus_digest')):
        raise ValueError('judgments belong to another frozen run')
    if review.get('scope', run['scope']) != run['scope']:
        raise ValueError('judgment scope changed')
    labels = review.get('labels')
    if not isinstance(labels, dict):
        raise ValueError('labels must be a mapping')
    # Null is explicitly unjudged; it never silently becomes a negative.
    if any(v is not None and type(v) is not bool for v in labels.values()):
        raise ValueError('labels must be true, false or null')
    result = deepcopy(run)
    result['judgments'] = {k: v for k, v in labels.items() if v is not None}
    summarize_run(result)
    return result


def review_worksheet(review):
    """Blind human worksheet: no cleaning verdicts or generated labels."""
    lines = ['# 人工相关性标注', '',
             '在配套JSON的labels填写true（符合）、false（不符合）；不确定保留null。',
             '先核对冻结需求及硬约束，再看原图；不要直接照抄模型结论。', '',
             '工作区版本必须与下列scope一致；不一致时重新采集，不对新版图片套用旧标签。', '',
             '    scope: ' + json.dumps(review.get('scope'), ensure_ascii=False), '',
             '## 冻结需求与硬约束', '']
    lines.extend('    ' + line for line in json.dumps(review.get('context'),
                 ensure_ascii=False, indent=2).splitlines())
    lines.extend(['', '[打开已授权清洗工作区](http://127.0.0.1:8096' + review['task_view_path'] + ')',
                  '', '预览接口仍须当前授权；本清单不复制图片，也不授予访问权限。', ''])
    for index, (content, items) in enumerate(review['items'].items(), 1):
        lines.extend([f'## 素材组 {index}', '', '内容摘要：`' + content + '`', '',
                      '人工判断：待确认；理由：待填写。', ''])
        lines.extend('    素材ID：' + item['candidate_id'] + '\n    预览接口：' + item['preview_path']
                     for item in items)
        lines.append('')
    return '\n'.join(lines) + '\n'
