"""Video draft semantics. Validation does not grant retrieval/execution capability."""
from copy import deepcopy
import re

from .errors import ServiceError, invalid

VIDEO_FIELDS = frozenset({'query_input', 'video_options', 'video_conditions'})
VIDEO_CHANNELS = frozenset({'visual'})


def _object(value, required, optional, path):
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise invalid('missing or unsupported video fields', path)
    return value


def normalize_video_fields(spec):
    """Normalize only new v3 fields; shared proposal validation stays in proposals."""
    path = '$.query_spec'
    if spec.get('media_type') != 'video':
        raise ServiceError('FEATURE_NOT_SUPPORTED', 'v3 currently accepts only video targets', 422,
                           field_path=path + '.media_type')
    if spec.get('count_unit') != 'distinct_video':
        raise ServiceError('FEATURE_NOT_SUPPORTED', 'only distinct original videos can be delivered', 422,
                           field_path=path + '.count_unit')
    if 'video_options' in spec:
        raise ServiceError('FEATURE_NOT_SUPPORTED', 'clip localization and delivery are not enabled', 422,
                           field_path=path + '.video_options')
    query = _object(spec.get('query_input'), {'mode', 'text'}, set(), path + '.query_input')
    if query['mode'] != 'text':
        raise ServiceError('QUERY_UNSUPPORTED', 'only text-to-video drafts are supported', 422,
                           field_path=path + '.query_input.mode')
    if not isinstance(query['text'], str) or not query['text'].strip() or len(query['text']) > 8000:
        raise invalid('query text must contain 1-8000 characters', path + '.query_input.text')
    # Until video-specific compilers exist, never inherit image-only rules.
    for field in ('rules', 'quotas', 'preferences', 'grouped_expansion', 'cleaning_conditions'):
        if field in spec and spec[field] != []:
            raise ServiceError('FEATURE_NOT_SUPPORTED', 'use explicit video_conditions for video drafts', 422,
                               field_path=path + '.' + field)
        if field in ('grouped_expansion', 'cleaning_conditions') and field in spec:
            raise invalid('field is not supported in video drafts', path + '.' + field)
    conditions = _object(spec.get('video_conditions'), {'clauses', 'relation'}, set(),
                         path + '.video_conditions')
    clauses = conditions['clauses']
    if not isinstance(clauses, list) or not 1 <= len(clauses) <= 8:
        raise invalid('require 1-8 video conditions', path + '.video_conditions.clauses')
    ids, normalized = set(), []
    for index, clause in enumerate(clauses):
        where = path + f'.video_conditions.clauses[{index}]'
        clause = _object(clause, {'id', 'channel', 'predicate'}, {'verification_scope'}, where)
        if (not isinstance(clause['id'], str)
                or re.fullmatch(r'[A-Za-z][A-Za-z0-9_\-]{0,63}', clause['id']) is None
                or clause['id'] in ids):
            raise invalid('condition id must be valid and unique', where + '.id')
        ids.add(clause['id'])
        if not isinstance(clause['channel'], str) or clause['channel'] not in VIDEO_CHANNELS:
            raise invalid('unsupported condition channel', where + '.channel')
        if (not isinstance(clause['predicate'], str) or not clause['predicate'].strip()
                or len(clause['predicate']) > 640):
            raise invalid('predicate must contain 1-640 characters', where + '.predicate')
        scope = clause.get('verification_scope', 'video_content')
        if scope != 'video_content':
            raise ServiceError('FEATURE_NOT_SUPPORTED', 'clip, temporal and full-duration verification are not enabled',
                               422, field_path=where + '.verification_scope')
        normalized.append({**clause, 'verification_scope': scope})
    relation = _object(conditions['relation'], {'operator', 'scope'}, set(),
                       path + '.video_conditions.relation')
    if relation['operator'] not in ('all', 'any') or relation['scope'] != 'same_video':
        raise ServiceError('QUERY_UNSUPPORTED', 'only all/any on the same original video is supported', 422,
                           field_path=path + '.video_conditions.relation')
    fields = {'query_input': deepcopy(query),
              'video_conditions': {'clauses': normalized, 'relation': deepcopy(relation)}}
    return fields


def video_draft_summary(spec):
    """Reviewable semantics; this is not an approval token or capability grant."""
    current = (spec.get('count_unit') == 'distinct_video'
               and spec.get('video_conditions', {}).get('relation', {}).get('scope') == 'same_video'
               and all(c.get('channel') == 'visual' and c.get('verification_scope') == 'video_content'
                       for c in spec.get('video_conditions', {}).get('clauses', [])))
    return {
        'media_type': 'video', 'query_mode': 'text',
        'count_unit': spec['count_unit'], 'target_count': spec['target_count'],
        'qualification_scope': 'video_content_match' if current else 'legacy_video_scope',
        'requires_scope_update': not current,
        'video_options': deepcopy(spec.get('video_options')),
        'video_conditions': deepcopy(spec['video_conditions']),
        'execution': {'available': False, 'reason_code': 'VIDEO_EXECUTION_UNAVAILABLE' if current else 'VIDEO_SCOPE_CHANGED',
                      'message': '视频检索、验证和交付链路尚未接通；可保存草稿，暂不能确认执行。'},
    }


def require_video_execution(spec):
    if spec.get('media_type') == 'video':
        raise ServiceError('VIDEO_EXECUTION_UNAVAILABLE',
                           '视频检索、验证和交付链路尚未接通；草稿已保留，暂不能确认执行。', 422)
