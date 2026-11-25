"""Optional planning requirements; absence preserves legacy wire data/digests.

This describes user intent, never executable detector paths or approval tokens.
"""
from copy import deepcopy
import re

from .errors import ServiceError, invalid

PURPOSES = frozenset({'filter', 'tag', 'rank', 'analyze'})
ACTIONS = frozenset({'rewrite_query', 'switch_source', 'paginate', 'retry'})
METHODS = frozenset({'text', 'vector', 'reference_image'})


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._@-]{0,127}', value) is not None


def positive(value):
    return type(value) is int and 0 < value <= 1000000


def fields(value, expected, path):
    if type(value) is not dict or set(value) != set(expected):
        raise invalid('invalid contract fields', path)


def strings(value, allowed=None, *, nonempty=False):
    return (type(value) is list and len(value) <= 64 and (bool(value) or not nonempty)
            and all(identifier(v) and (allowed is None or v in allowed) for v in value)
            and len(value) == len(set(value)))


def normalize_output_schema(value):
    fields(value, {'version', 'type', 'values', 'required'}, '$.output_schema')
    if (not identifier(value['version']) or value['type'] not in {'boolean', 'number', 'text', 'enum'}
            or type(value['required']) is not bool
            or type(value['values']) is not list or len(value['values']) > 64
            or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 128 for v in value['values'])
            or len(value['values']) != len(set(value['values']))
            or value['type'] == 'enum' and not value['values']
            or value['type'] != 'enum' and value['values']):
        raise invalid('invalid output schema', '$.output_schema')
    return deepcopy(value)


def _normalize_agent_requirements(value, spec):
    fields(value, {'schema_version', 'processing', 'autonomy'}, '$.agent_requirements')
    if value['schema_version'] not in ('agent-requirements@1', 'agent-requirements@2'):
        raise invalid('unsupported agent requirements version')
    groups = {g['group_id'] for g in (spec.get('grouped_expansion') or {}).get('groups', [])}
    rules = {r['rule_id'] for r in spec.get('rules', [])}
    conditions = {c['condition_id'] for c in spec.get('cleaning_conditions', [])}
    requests = value['processing']
    minimum = 1 if value['schema_version'] == 'agent-requirements@1' else 0
    if type(requests) is not list or not minimum <= len(requests) <= 32:
        raise invalid('processing request count does not match requirements version')
    seen = set()
    for request in requests:
        fields(request, {'requirement_id', 'purpose', 'instruction', 'source_text', 'group_id',
                         'rule_ref', 'output_field', 'output_schema', 'unknown_policy'}, '$.processing')
        key, purpose = request['requirement_id'], request['purpose']
        if (not identifier(key) or key in seen or purpose not in PURPOSES
                or request['group_id'] is not None and request['group_id'] not in groups
                or any(not isinstance(request[k], str) or not 1 <= len(request[k].strip()) <= 2000
                       for k in ('instruction', 'source_text'))):
            raise invalid('invalid processing identity, purpose or instruction')
        if request['source_text'] not in spec['raw_request']:
            raise invalid('processing source_text must quote the user request')
        seen.add(key)
        if purpose == 'filter':
            if (request['rule_ref'] not in rules | conditions or request['output_field'] is not None
                    or request['output_schema'] is not None or request['unknown_policy'] != 'hold_for_review'):
                raise invalid('filter must reference an existing rule and hold unknown results')
            if request['rule_ref'] in rules:
                rule = next(r for r in spec['rules'] if r['rule_id'] == request['rule_ref'])
                if rule['hardness'] != 'hard' or request['group_id'] is not None:
                    raise invalid('filter must preserve hard rule and global scope')
            if request['rule_ref'] in conditions:
                condition = next(c for c in spec['cleaning_conditions'] if c['condition_id'] == request['rule_ref'])
                if condition['group_id'] != request['group_id']:
                    raise invalid('filter scope must match its confirmed condition')
        else:
            if (request['rule_ref'] is not None or not identifier(request['output_field'])
                    or request['unknown_policy'] not in {'hold_for_review', 'allow_unknown'}):
                raise invalid('non-filter output must have a field and unknown policy')
            schema = normalize_output_schema(request['output_schema'])
            if schema['required'] and request['unknown_policy'] == 'allow_unknown':
                raise invalid('required output cannot allow unknown')
            if purpose == 'rank' and schema['type'] != 'number':
                raise invalid('rank output must be numeric')
    outputs = [(r['group_id'], r['output_field']) for r in requests if r['purpose'] != 'filter']
    if len(outputs) != len(set(outputs)):
        raise invalid('output fields must be unique within each group')
    autonomy = value['autonomy']
    fields(autonomy, {'actions', 'library_ids', 'methods', 'max_rounds', 'max_calls',
                      'max_seconds', 'max_no_progress_rounds'}, '$.autonomy')
    approved = set(spec.get('refill_policy', {}).get('approved_library_ids', []))
    # Retrieval-only v2 declares its own search scope, independently of refill.
    # These identifiers never grant access; admission and tools reauthorize them.
    independent = (value['schema_version'] == 'agent-requirements@2' and not requests
                   and spec.get('refill_policy', {}).get('mode') == 'input_only')
    if (not strings(autonomy['actions'], ACTIONS) or not strings(autonomy['library_ids'], None if independent else approved)
            or not strings(autonomy['methods'], METHODS, nonempty=True)
            or any(not positive(autonomy[k]) for k in
                   ('max_rounds', 'max_calls', 'max_seconds', 'max_no_progress_rounds'))
            or autonomy['max_no_progress_rounds'] > autonomy['max_rounds']):
        raise invalid('invalid autonomous scope or budget')
    if not requests and (not autonomy['actions'] or not autonomy['library_ids']):
        raise invalid('retrieval-only requirements need explicit actions and approved sources')
    return deepcopy(value)


def require_agent_execution_available(spec):
    """Fail closed until the T04–T06 runtime consumes these requirements."""
    if 'agent_requirements' in spec:
        raise ServiceError('AGENT_EXECUTION_UNAVAILABLE',
                           'Agent requirements are draft-only until the execution adapter is available', 422)


def normalize_agent_requirements(value, spec):
    try:
        return _normalize_agent_requirements(value, spec)
    except (TypeError, KeyError, ValueError, RecursionError):
        raise invalid('malformed agent requirements') from None
