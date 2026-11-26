"""Confirmed terms delegate execution, never permission to rewrite the request."""
import re

from .errors import invalid
from .query_spec import digest

WORKFLOW = 'confirmed-terms-retrieval@1'


def delegates_candidate_window(spec):
    """Only the versioned intake delegation permits fixing an initial prefix."""
    execution = spec.get('retrieval_execution') or {}
    return (spec.get('media_type') == 'image'
            and execution.get('schema_version') == 'confirmed-terms-execution@2'
            and execution.get('workflow_ref') == WORKFLOW
            and execution.get('candidate_intake') in ('auto', 'manual'))


def normalize_execution(value):
    fields = {'schema_version', 'workflow_ref', 'binding_ids', 'max_calls', 'max_hit_slots', 'policy_digest'}
    version = value.get('schema_version') if type(value) is dict else None
    if version == 'confirmed-terms-execution@2':
        fields = fields | {'candidate_intake'}
    if (type(value) is not dict or set(value) != fields
            or version not in ('confirmed-terms-execution@1', 'confirmed-terms-execution@2') or value['workflow_ref'] != WORKFLOW
            or version == 'confirmed-terms-execution@2' and value.get('candidate_intake') not in ('auto', 'manual')
            or type(value['binding_ids']) is not list or not 1 <= len(value['binding_ids']) <= 64
            or any(not isinstance(v, str) or not v or len(v) > 255 for v in value['binding_ids'])
            or len(set(value['binding_ids'])) != len(value['binding_ids'])
            or type(value['max_calls']) is not int or not 1 <= value['max_calls'] <= 100000
            or type(value['max_hit_slots']) is not int or not 1 <= value['max_hit_slots'] <= 1000000
            or not isinstance(value['policy_digest'], str)
            or not re.fullmatch(r'sha256:[a-f0-9]{64}', value['policy_digest'])):
        raise invalid('confirmed terms execution policy required', '$.query_spec.retrieval_execution')
    normalized = {**value, 'binding_ids': sorted(value['binding_ids'])}
    if digest({k: v for k, v in normalized.items() if k != 'policy_digest'}) != value['policy_digest']:
        raise invalid('execution policy digest differs', '$.query_spec.retrieval_execution.policy_digest')
    return normalized


def execution_policy(bindings, limits, *, candidate_intake=None):
    value = {'schema_version': 'confirmed-terms-execution@1', 'workflow_ref': WORKFLOW,
             'binding_ids': sorted({b.binding_id for b in bindings}),
             'max_calls': limits.max_calls, 'max_hit_slots': limits.max_hit_slots}
    if candidate_intake is not None:
        value.update(schema_version='confirmed-terms-execution@2', candidate_intake=candidate_intake)
    return normalize_execution({**value, 'policy_digest': digest(value)})


def initial_cleaning_calls(db, task_id, revision, generation):
    """Count @2's conservative first-cleaning reservation in the existing scope."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_job_links'").fetchone() is None:
        return 0
    import json
    from .errors import ServiceError
    total = 0
    for payload, expected_digest in db.execute(
            'SELECT payload,payload_digest FROM agent_job_links WHERE task_id=? AND workflow_ref=?',
            (task_id, WORKFLOW)):
        value = json.loads(payload)
        if digest(value) != expected_digest:
            raise ServiceError('STORAGE_CORRUPT', 'Initial cleaning reservation changed', 500)
        if (value.get('revision'), value.get('generation')) != (revision, generation):
            continue
        if value.get('policy', {}).get('schema_version') != 'confirmed-terms-execution@2':
            continue
        calls = (value.get('intake') or {}).get('vlm_calls', 0)
        if type(calls) is not int or not 0 <= calls <= 1000000:
            raise ServiceError('STORAGE_CORRUPT', 'Invalid initial cleaning reservation', 500)
        total += calls
    return total
