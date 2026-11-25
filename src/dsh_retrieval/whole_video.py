"""Whole-video identity and confirmed-condition semantics; no access grants.

Adapters may canonicalize only a source mapping verified to represent a whole
original video. This module never turns frame/clip mappings into original videos.
"""
from dataclasses import replace
import json
import re

from .candidate_contracts import candidate_identity
from .errors import ServiceError
from .integration_models import MediaRecord
from .models import Task, TaskLifecycle
from .query_spec import digest
from .video_query import normalize_video_fields

VIDEO_PREDICATE = 'video.content-match@1'


def whole_video_unit_id(media):
    if type(media) is not MediaRecord or any(not isinstance(s, str) or not s.strip() for s in
            (media.source_namespace, media.source_asset_id, media.asset_version)):
        raise ValueError('verified source video identity required')
    return 'whole_video_' + digest({'schema': 'whole-video-unit@1',
        'source_namespace': media.source_namespace, 'source_asset_id': media.source_asset_id,
        'asset_version': media.asset_version})[7:]


def canonical_whole_video_record(media, *, source_unit_type):
    """Trusted connector conversion; neither checks bytes nor authorizes reading."""
    if source_unit_type != 'whole_video':
        raise ValueError('only a verified whole-video source mapping can be canonicalized')
    unit_id = whole_video_unit_id(media)
    if (not isinstance(media.authorized_media_ref, str) or not media.authorized_media_ref.strip()
            or not isinstance(media.content_digest, str)
            or re.fullmatch(r'sha256:[0-9a-f]{64}', media.content_digest) is None):
        raise ValueError('immutable video content and authorized reference required')
    return replace(media, media_unit_id=unit_id)


def require_whole_video_records(records):
    """Reject mixed versions or content for the same original in one snapshot."""
    if type(records) is not tuple:
        raise ValueError('bounded immutable video records required')
    seen = {}
    for media in records:
        if media != canonical_whole_video_record(media, source_unit_type='whole_video'):
            raise ValueError('noncanonical whole-video identity')
        key = media.source_namespace, media.source_asset_id
        version = media.asset_version, media.content_digest
        if key in seen and seen[key] != version:
            raise ValueError('conflicting original video versions in one snapshot')
        seen[key] = version
    return records


def require_video_candidate_mapping(candidates, mapping):
    if not isinstance(mapping, dict) or set(mapping) != {c.candidate_id for c in candidates}:
        raise ValueError('complete whole-video mapping required')
    records = require_whole_video_records(tuple(mapping[c.candidate_id] for c in candidates))
    for candidate, media in zip(candidates, records):
        if (candidate_identity(media) != candidate.candidate_id
                or media.asset_version != candidate.asset_version
                or media.content_digest != candidate.content_digest):
            raise ValueError('video candidate differs from immutable source mapping')
    return records


def confirmed_video_spec(task):
    """Pure immutable-task check; caller still fences scope and authorization."""
    try:
        if (type(task) is not Task or task.media_type != 'video'
                or task.lifecycle != TaskLifecycle.ACTIVE
                or type(task.confirmed_revision) is not int or task.confirmed_revision < 1):
            raise ValueError('current confirmed video task required')
        proposal = json.loads(task.confirmed_proposal_json)
        spec = proposal['query_spec']
        normalize_video_fields(spec)
        if (spec['schema_version'] != 'query-spec@3' or spec['raw_request'] != task.raw_request
                or spec['target_count'] != task.target_count
                or spec['unknown_policy'] != 'hold_for_review'
                or task.request_digest != digest({'project_id': task.project_id, 'proposal': proposal})):
            raise ValueError('video task/proposal mismatch')
        return spec
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, ServiceError) as exc:
        raise ServiceError('RECIPE_RULE_MISMATCH', 'Confirmed video conditions unavailable', 409) from exc


def video_conditions_digest(task):
    spec = confirmed_video_spec(task)
    return digest({'schema_version': 'confirmed-video-conditions@1',
        'task_id': task.task_id, 'revision': task.confirmed_revision,
        'request_digest': task.request_digest, 'count_unit': spec['count_unit'],
        'conditions': normalize_video_fields(spec)['video_conditions']})


def video_detector_config_digest(task):
    return digest({'schema_version': 'whole-video-detector-config@1',
                   'review_digest': video_conditions_digest(task)})


def aggregate_video_condition_results(conditions, verdicts):
    """Kleene all/any. Missing evidence is unknown, never a similarity pass.

    Input rows are existing CleaningRuleVerdict objects, one per condition ID.
    This function authorizes nothing and does not validate a worker binding.
    """
    from .multi_rule_contracts import CleaningRuleVerdict
    clauses = conditions['clauses']
    if (type(verdicts) is not tuple or len(verdicts) != len(clauses)
            or any(type(v) is not CleaningRuleVerdict or v.group_id is not None
                   or v.verdict == 'not_applicable' for v in verdicts)
            or tuple(v.rule_id for v in verdicts) != tuple(c['id'] for c in clauses)
            or conditions['relation']['scope'] != 'same_video'):
        raise ValueError('complete video condition evidence required')
    values = [v.verdict for v in verdicts]
    operator = conditions['relation']['operator']
    if operator == 'all':
        return 'fail' if 'fail' in values else 'unknown' if 'unknown' in values else 'pass'
    if operator == 'any':
        return 'pass' if 'pass' in values else 'unknown' if 'unknown' in values else 'fail'
    raise ValueError('unsupported video condition relation')
