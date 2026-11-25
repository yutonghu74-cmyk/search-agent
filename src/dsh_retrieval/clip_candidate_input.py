"""Read-only combined-CLIP input proposal; never grants cleaning approval."""
from dataclasses import asdict
import json
from .candidate_contracts import CandidateManifestProposal, ManifestItem, candidate_identity
from .errors import ServiceError
from .query_branches import compile_query_branches
from .query_spec import digest


def build_clip_input(task, job, rows, mappings):
    if mappings is None:
        raise ServiceError('MEDIA_MAPPING_REQUIRED', 'Stable authorized media mapping required', 409)
    if task.media_type != 'image':
        raise ServiceError('QUERY_UNSUPPORTED', 'Combined candidate input supports images only', 422)
    spec = json.loads(task.confirmed_proposal_json)['query_spec']
    branches = {r['branch_id']: r for r in compile_query_branches(spec)['branches']}
    members = {m.request.plan_id: m for m in job.members}
    grouped = {}
    skipped = 0
    quota = any(g.get('delivery_target') is not None
                for g in (spec.get('grouped_expansion') or {}).get('groups', []))
    query_input = spec.get('query_input') or {}
    excluded = query_input.get('digest') if query_input.get('exclude_exact_content') else None
    for row in rows:
        plan = members.get(row['plan_id'])
        if plan is None or row['branch_id'] != plan.branch_id or plan.branch_id not in branches:
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Combined hit provenance changed', 409)
        from .clip_search_jobs import _match_from_dict
        # matches() supplies typed hits; view() supplies JSON dictionaries.
        match = _match_from_dict({k: v for k, v in row.items() if k not in ('branch_id', 'plan_id')})
        if match.hit.binding != plan.request.binding:
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Combined binding changed', 409)
        resolution = mappings.resolve(match)
        media = resolution.media
        if resolution.status != 'resolved' or media is None or not media.content_digest:
            skipped += 1
            continue
        if excluded and media.content_digest == excluded:
            skipped += 1
            continue
        groups = {o['group_id'] for o in branches[plan.branch_id]['origins'] if o['group_id'] is not None}
        if quota and not groups:
            skipped += 1
            continue
        identity = candidate_identity(media)
        old = grouped.setdefault(identity, [media, [], set()])
        if old[0] != media:
            raise ServiceError('MAPPING_CONFLICT', 'Candidate identity maps to conflicting media', 409)
        if match.hit not in old[1]: old[1].append(match.hit)
        old[2].update(groups)
    items = tuple(ManifestItem(media, tuple(hits), tuple(sorted(groups)))
                  for media, hits, groups in grouped.values())
    if len(items) > 100 or sum(len(i.hits) for i in items) > 100:
        raise ServiceError('INPUT_LIMIT', 'Candidate input exceeds 100 items/hits', 422)
    from .retrieval_contracts import RetrievalScope
    proposal = CandidateManifestProposal(RetrievalScope.from_task(task), job.job_id,
        digest({'job': job.job_id, 'members': [asdict(m) for m in job.members], 'rows': rows}), items)
    return proposal, skipped
