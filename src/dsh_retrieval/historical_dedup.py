"""Explicit immutable delivered-image references; never infer a history scope."""
from dataclasses import replace
import json
import re

from .errors import ServiceError
from .query_spec import digest


NONE = {'scope': 'none', 'references': [], 'comparison_methods': [], 'reference_digest': None}
REF_FIELDS = {'task_id', 'batch_id', 'delivery_id', 'snapshot_id', 'manifest_digest', 'task_version'}


def merge_rows(*groups):
    rows = {}
    for group in groups:
        for content, ids, refs in group:
            known_ids, known_refs = rows.setdefault(content, (set(), set()))
            known_ids.update(ids); known_refs.update(refs)
    return tuple((content, tuple(sorted(ids)), tuple(sorted(refs)))
                 for content, (ids, refs) in sorted(rows.items()))


def normalize_history(value):
    if value == NONE:
        return dict(NONE, references=[], comparison_methods=[])
    if (not isinstance(value, dict) or set(value) != set(NONE)
            or value.get('scope') != 'selected_batches'
            or value.get('comparison_methods') != ['exact-content@1']
            or not isinstance(value.get('references'), list)
            or not 1 <= len(value['references']) <= 100):
        raise ServiceError('FEATURE_NOT_SUPPORTED', 'Select explicit delivered image batches for exact-content dedup', 422)
    refs = value['references']
    for ref in refs:
        if (not isinstance(ref, dict) or set(ref) != REF_FIELDS
                or any(not isinstance(ref[k], str) or not ref[k] or len(ref[k]) > 200
                       for k in REF_FIELDS - {'task_version'})
                or type(ref['task_version']) is not int or ref['task_version'] < 1
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', ref['manifest_digest'])):
            raise ServiceError('INVALID_SPEC', 'Invalid historical delivery reference', 422)
    if (len({r['delivery_id'] for r in refs}) != len(refs)
            or value['reference_digest'] != digest(refs)):
        raise ServiceError('INVALID_SPEC', 'Historical reference digest or uniqueness invalid', 422)
    return json.loads(json.dumps(value))


def options(db, memory, actor, project_id, dimension_id):
    from .delivery import load_delivery_records
    rows = []
    for record in load_delivery_records(db).values():
        task = memory._tasks.get(record['task_id'])
        if (task is None or (task.tenant_id, task.owner_user_id, task.project_id, task.dimension_id)
                != (actor.tenant_id, actor.user_id, project_id, dimension_id)
                or record['status'] != 'published' or task.media_type != 'image'
                or not isinstance(task.batch_id, str) or not task.batch_id):
            continue
        ref = dict(task_id=task.task_id, batch_id=task.batch_id,
                   delivery_id=record['package_id'], snapshot_id=record['source_id'],
                   manifest_digest=record['manifest_digest'], task_version=task.task_version)
        rows.append({'reference': ref, 'label': task.raw_request,
                     'count': len(record['manifest']['members'])})
    return sorted(rows, key=lambda r: (r['reference']['batch_id'], r['reference']['delivery_id']))


def resolve(db, memory, actor, project_id, dimension_id, policy, *, fresh=False,
            target_task_id=None, target_batch_id=None, delivery_records=None):
    from .delivery import load_delivery_records
    policy = normalize_history(policy)
    if policy['scope'] == 'none':
        return {}
    records = {r['package_id']: r for r in (load_delivery_records(db)
               if delivery_records is None else delivery_records).values()}
    blocked = {}
    for ref in policy['references']:
        source = memory._tasks.get(ref['task_id'])
        record = records.get(ref['delivery_id'])
        if (source is None or (source.tenant_id, source.owner_user_id, source.project_id, source.dimension_id)
                != (actor.tenant_id, actor.user_id, project_id, dimension_id)
                or source.media_type != 'image' or source.batch_id != ref['batch_id']
                or record is None or record['status'] != 'published'
                or record['task_id'] != source.task_id or record['source_id'] != ref['snapshot_id']
                or record['manifest_digest'] != ref['manifest_digest']):
            raise ServiceError('HISTORY_REFERENCE_UNAVAILABLE', 'Historical baseline is unavailable or outside scope', 409)
        if source.task_id == target_task_id or source.batch_id == target_batch_id:
            raise ServiceError('HISTORY_SELF_REFERENCE', 'Current batch cannot exclude itself', 422)
        if fresh and source.task_version != ref['task_version']:
            raise ServiceError('HISTORY_REFERENCE_CHANGED', 'Historical batch changed; select and confirm again', 409)
        for member in record['manifest']['members']:
            content = member['content_digest']
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', content):
                raise ServiceError('STORAGE_CORRUPT', 'Invalid historical content digest', 500)
            blocked.setdefault(content, set()).add(ref['delivery_id'])
    return {key: tuple(sorted(value)) for key, value in blocked.items()}


def filter_preview(preview, blocked, reference_digest, *, policy, node_mode):
    from .result_contracts import allocate_group_quotas, _stop_reason
    rows = {}
    for content, ids, refs in preview.historical_exclusions:
        saved_ids, saved_refs = rows.setdefault(content, (set(), set()))
        saved_ids.update(ids); saved_refs.update(refs)
    keep = []
    for item in preview.accepted + preview.reserve:
        if item.content_digest in blocked:
            ids, refs = rows.setdefault(item.content_digest, (set(), set()))
            ids.update(item.candidate_ids or (item.candidate_id,))
            refs.update(blocked[item.content_digest])
        else:
            keep.append(item)
    if preview.group_quotas:
        accepted, reserve, quotas = allocate_group_quotas(tuple(keep),
            tuple((r.group_id, r.target_count) for r in preview.group_quotas))
    else:
        accepted, reserve, quotas = tuple(keep[:preview.target_count]), tuple(keep[preview.target_count:]), ()
    gap = max(0, preview.target_count - len(accepted))
    surviving = {item.candidate_id for item in accepted + reserve}
    return replace(preview, accepted=accepted, reserve=reserve, group_quotas=quotas, gap=gap,
        stop_reason=_stop_reason(preview.target_count, len(accepted), policy, node_mode),
        historical_reference_digest=reference_digest,
        historical_exclusions=tuple((content, tuple(sorted(ids)), tuple(sorted(refs)))
            for content, (ids, refs) in sorted(rows.items())),
        near_duplicate_pairs=tuple(p for p in preview.near_duplicate_pairs
            if p.candidate_id in surviving and p.other_candidate_id in surviving))
