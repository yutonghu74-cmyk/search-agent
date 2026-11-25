"""Capture a coherent local ledger snapshot without initializing or writing it."""
from pathlib import Path
from dataclasses import replace
import json
import sqlite3

from .adapters.task_storage.sqlite import SQLiteTaskRepository, decode
from .adapters.task_storage.plan_sets import restore
from .adapters.task_storage.multi_rule import load_state, SQLiteMultiRuleApprovalRepository
from .models import RunStatus
from .retrieval_contracts import RetrievalScope
from .multi_branch_contracts import validate_plan_set
from .query_spec import digest
from .query_branches import compile_query_branches
from .cleaning_usage import read_usage


def capture_run(path, actor, task_id, *, corpus_digest=None):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('BEGIN')
        memory = SQLiteTaskRepository._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
        task = memory.get(actor, task_id)
        plans = restore(db.execute('SELECT format,payload FROM plan_set_ledger WHERE id=1').fetchone())
        approved = plans['current'].get(task_id)
        if approved is None:
            raise ValueError('current approved local plan unavailable')
        current_scope = RetrievalScope.from_task(task)
        if (task.task_version < approved.scope.task_version or
                replace(current_scope, task_version=approved.scope.task_version) != approved.scope):
            raise ValueError('approved plan revision, generation or requirement is stale')
        validate_plan_set(actor, replace(task, task_version=approved.scope.task_version,
                                        stage='retrieving', run_status=RunStatus.RUNNING),
                          approved, reading=True)
        state = load_state(db.execute('SELECT format,payload FROM multi_rule_cleaning_ledger WHERE id=1').fetchone())
        result_id = state['current_result_sets'].get(task_id)
        if result_id is None:
            raise ValueError('current published cleaning result unavailable; keep task pending')
        preview, version = state['result_sets'][result_id]
        if preview.generation != task.run_generation or version > task.task_version:
            raise ValueError('result scope differs from task')
        scope = dict(task_id=task_id, revision=task.confirmed_revision,
                     generation=task.run_generation, task_version=task.task_version)
        audit = SQLiteMultiRuleApprovalRepository._result_audit_view(state, preview)
        candidate_rows = {}
        usage_approvals, usage_expected = {}, []
        for operation_id in preview.source_operation_ids or (preview.operation_id,):
            approval = state['approvals'][operation_id]
            usage_approvals[operation_id] = approval
            if (approval.proposal.task_id, approval.proposal.revision, approval.proposal.generation) != (
                    task_id, task.confirmed_revision, task.run_generation):
                raise ValueError('foreign cleaning source')
            for candidate in approval.proposal.candidates:
                assessment = audit['candidate_assessments'][candidate.candidate_id]
                row = dict(content_digest=candidate.content_digest,
                           verdict=assessment['cleaning_status'], disposition=assessment['disposition'])
                if candidate.candidate_id in candidate_rows and candidate_rows[candidate.candidate_id] != row:
                    raise ValueError('conflicting candidate assessment')
                candidate_rows[candidate.candidate_id] = row
                verdict = next(c for c in state['results'][operation_id].candidates
                               if c.candidate_id == candidate.candidate_id)
                applicable = {r.rule_id for r in verdict.rules if r.verdict != 'not_applicable'}
                for step_id in {r.step_id for r in approval.job.recipe.rules if r.rule_id in applicable}:
                    usage_expected.append([operation_id, candidate.candidate_id, step_id])
        manifests = list(plans['candidate_manifests'].values())
        candidate_ledger = decode(json.loads(db.execute('SELECT payload FROM candidate_ledger WHERE id=1').fetchone()[0]))
        manifests += list(candidate_ledger['manifests'].values())
        hit_candidates = {}
        def key(hit):
            return (hit.binding, type(hit.vector_id).__name__, hit.vector_id)
        for manifest in manifests:
            s = manifest.proposal.scope
            if (s.task_id, s.revision, s.generation) != (task_id, task.confirmed_revision, task.run_generation):
                continue
            for item in manifest.proposal.items:
                for hit in item.hits:
                    hit_candidates.setdefault(key(hit), set()).add(item.candidate_id)
        rows = db.execute('SELECT seq,entry,branch,payload FROM candidate_browse_hits WHERE source=? ORDER BY seq',
                          (approved.proposal_digest,)).fetchall()
        expected_hits = sum(len(page.hits) for selected, _, _, page in plans['operations'].values()
                            if selected == approved and page is not None)
        if len(rows) != expected_hits:
            raise ValueError('candidate hit projection is incomplete')
        batches, hits = {}, []
        allowed_branches = {m.branch_id for m in approved.members}
        logical = compile_query_branches(json.loads(task.confirmed_proposal_json)['query_spec'])
        metadata = {b['branch_id']: {k: b[k] for k in ('query_text', 'query_role', 'origins')}
                    for b in logical['branches'] if b['branch_id'] in allowed_branches}
        for branch_id in metadata:
            metadata[branch_id]['libraries'] = sorted({m.plan.request.binding.library_id
                for m in approved.members if m.branch_id == branch_id})
        for seq, entry, branch, payload in rows:
            if branch not in allowed_branches:
                raise ValueError('unknown projected branch')
            operation_id = entry.rsplit(':', 1)[0]
            operation = plans['operations'].get(operation_id)
            if operation is None or operation[0] != approved or operation[3] is None:
                raise ValueError('uncommitted or foreign projected hit')
            if next(m.branch_id for m in approved.members
                    if m.plan.request.plan_id == operation[1]) != branch:
                raise ValueError('projected branch differs from committed source')
            hit = decode(json.loads(payload))
            index = int(entry.rsplit(':', 1)[1])
            if index < 0 or index >= len(operation[3].hits) or operation[3].hits[index] != hit:
                raise ValueError('projected hit differs from committed source')
            batches.setdefault(operation_id, len(batches))
            ids = hit_candidates.get(key(hit), set())
            if not ids:
                hits.append(dict(scope=scope, batch=batches[operation_id], branch_id=branch,
                                 hit_id=entry, raw_hit_id=entry, candidate_id=None, unresolved_reason='unmapped'))
            for cid in sorted(ids):
                hits.append(dict(scope=scope, batch=batches[operation_id], branch_id=branch,
                    hit_id=entry + ':' + cid, raw_hit_id=entry, candidate_id=cid if cid in candidate_rows else None,
                    unresolved_reason=None if cid in candidate_rows else 'unassessed'))
        return dict(schema='recall-run@1', scope=scope, result_set_digest=preview.result_set_digest,
                    requirement_digest=digest(json.loads(task.confirmed_proposal_json)),
                    corpus_digest=corpus_digest, candidates=candidate_rows, hits=hits,
                    corpus_binding='operator_declared' if corpus_digest else 'unavailable',
                    branch_metadata=metadata,
                    judgments={}, vlm_calls=None, elapsed_ms=None,
                    usage=read_usage(db, usage_approvals), usage_expected=usage_expected,
                    usage_scope='published_result_lineage',
                    review_context={'request': json.loads(task.confirmed_proposal_json)['query_spec']['raw_request'],
                        'rules': json.loads(task.confirmed_proposal_json)['query_spec'].get('rules', []),
                        'cleaning_conditions': json.loads(task.confirmed_proposal_json)['query_spec'].get('cleaning_conditions'),
                        'video_conditions': json.loads(task.confirmed_proposal_json)['query_spec'].get('video_conditions'),
                        'source_constraints': json.loads(task.confirmed_proposal_json)['query_spec'].get('source_constraints', [])},
                    business_counts={'accepted': preview.accepted_count,
                                     'reserve': len(preview.reserve), 'gap': preview.gap},
                    projection_watermark=max((r[0] for r in rows), default=0))
    finally:
        db.close()
