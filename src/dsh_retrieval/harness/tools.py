"""Shared logical tools. This T04 broker is planning-only, not a T05 ledger."""
from copy import deepcopy
from dataclasses import dataclass
import json
import math
import threading
import time

from ..errors import ServiceError, invalid
from ..query_spec import digest
from ..processing_requirements import identifier


def validate_json(value, schema):
    """Small explicit schema subset. Unsupported keywords fail at registration."""
    kind = schema.get('type')
    types = {'object': dict, 'array': list, 'string': str, 'boolean': bool,
             'integer': int, 'number': (int, float), 'null': type(None)}
    if kind not in types or (type(value) not in (types[kind] if isinstance(types[kind], tuple) else (types[kind],))):
        raise invalid('tool argument type mismatch')
    if type(value) is float and not math.isfinite(value):
        raise invalid('nonfinite tool argument')
    if 'enum' in schema and value not in schema['enum']:
        raise invalid('tool enum mismatch')
    if kind == 'object':
        props = schema.get('properties', {})
        if (not set(schema.get('required', [])) <= set(value)
                or schema.get('additionalProperties') is False and set(value) - set(props)):
            raise invalid('tool fields mismatch')
        for key in value.keys() & props.keys(): validate_json(value[key], props[key])
    elif kind == 'array':
        if len(value) > schema.get('maxItems', 64): raise invalid('tool array too long')
        for item in value: validate_json(item, schema['items'])
    elif kind == 'string':
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 20000):
            raise invalid('tool string length mismatch')
    elif kind in ('number', 'integer'):
        if value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf):
            raise invalid('tool numeric range mismatch')


def _schema_supported(schema):
    if type(schema) is not dict or set(schema) - {'type', 'properties', 'required', 'additionalProperties',
            'items', 'maxItems', 'minLength', 'maxLength', 'minimum', 'maximum', 'enum'}:
        raise invalid('unsupported tool schema keyword')
    if schema.get('type') not in ('object', 'array', 'string', 'boolean', 'integer', 'number', 'null'):
        raise invalid('explicit tool schema type required')
    for child in schema.get('properties', {}).values(): _schema_supported(child)
    if schema.get('type') == 'array': _schema_supported(schema.get('items'))


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    version: str
    roles: frozenset[str]
    kind: str
    schema_json: str
    description: str

    def __post_init__(self):
        if (not identifier(self.tool_id) or not identifier(self.version)
                or type(self.roles) is not frozenset or not self.roles
                or len(self.roles) > 64 or any(not identifier(role) for role in self.roles)
                or self.kind not in ('read', 'proposal', 'report', 'execute')):
            raise invalid('invalid logical tool')
        _schema_supported(json.loads(self.schema_json))


class ToolCatalog:
    def __init__(self): self._tools = {}

    def register(self, definition, handler):
        if definition.tool_id in self._tools or not callable(handler):
            raise invalid('duplicate logical tool or invalid handler')
        self._tools[definition.tool_id] = (definition, handler)

    def resolve(self, tool_id):
        if not identifier(tool_id) or tool_id not in self._tools:
            raise ServiceError('HARNESS_TOOL_DENIED', 'Logical tool unavailable', 403)
        return self._tools[tool_id]


class PlanningReceipts:
    """Process-local proposal idempotency only. Never execute business writes."""
    def __init__(self):
        self.lock = threading.RLock()
        self.records = {}


class ToolBroker:
    def __init__(self, invocation, catalog, *, guard, receipts=None, clock=time.time):
        if not callable(guard): raise invalid('backend authority guard required')
        self.invocation, self.catalog, self.guard = invocation, catalog, guard
        self.receipts = receipts if receipts is not None else PlanningReceipts()
        self.clock, self._cancelled = clock, threading.Event()
        self._accepted = {}
        for tool_id in invocation.role.tool_ids:
            definition, _ = catalog.resolve(tool_id)
            if invocation.role.role not in definition.roles:
                raise ServiceError('HARNESS_TOOL_DENIED', 'Role does not own logical tool', 403)
            if definition.kind == 'execute':
                raise ServiceError('HARNESS_EXECUTION_UNAVAILABLE', 'Durable execution broker required', 409)

    def _check(self):
        if self._cancelled.is_set() or self.clock() >= self.invocation.deadline:
            raise ServiceError('HARNESS_RUN_STOPPED', 'Attempt cancelled or deadline reached', 409)
        self.guard(self.invocation)

    def cancel(self):
        self._cancelled.set()

    def list_tools(self):
        self._check()
        return [{'name': key, 'description': self.catalog.resolve(key)[0].description,
                 'inputSchema': json.loads(self.catalog.resolve(key)[0].schema_json)}
                for key in self.invocation.role.tool_ids]

    def prepare_call(self, tool_id, arguments):
        if tool_id not in self.invocation.role.tool_ids:
            raise ServiceError('HARNESS_TOOL_DENIED', 'Tool outside role allowlist', 403)
        definition, handler = self.catalog.resolve(tool_id)
        try:
            payload = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
            if len(payload.encode()) > 100000: raise ValueError()
            arguments = json.loads(payload)
        except (TypeError, ValueError, RecursionError):
            raise invalid('invalid tool JSON') from None
        validate_json(arguments, json.loads(definition.schema_json))
        fingerprint = digest({'tool': tool_id, 'version': definition.version, 'arguments': arguments})
        return definition, handler, arguments, payload, fingerprint

    def call(self, tool_id, arguments):
        # Identity and idempotency are derived from backend stage, never native call IDs.
        with self.receipts.lock:
            self._check()
            definition, handler, arguments, payload, fingerprint = self.prepare_call(tool_id, arguments)
            # One immutable proposal/report per tool per stage; read keys include arguments.
            key = (self.invocation.stage.stage_digest, tool_id,
                   fingerprint if definition.kind == 'read' else 'submission')
            previous = self.receipts.records.get(key)
            if previous is not None:
                if previous[0] != fingerprint:
                    raise ServiceError('IDEMPOTENCY_CONFLICT', 'Stage submission changed', 409)
                self._accepted[tool_id] = definition.kind
                return deepcopy(previous[1])
            result = handler(self.invocation, arguments)
            self._check()  # Revocation/cancellation during planning cannot publish a proposal.
            try:
                encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
                if len(encoded.encode()) > 100000: raise ValueError()
                result = json.loads(encoded)
            except (TypeError, ValueError, RecursionError):
                raise invalid('invalid tool result') from None
            self.receipts.records[key] = (fingerprint, result, payload)
            self._accepted[tool_id] = definition.kind
            return deepcopy(result)

    def submitted(self):
        with self.receipts.lock:
            self._check()
            return any(kind in ('proposal', 'report') for kind in self._accepted.values())


    def outputs(self):
        """Validated planning material for the coordinator, never execution approval."""
        with self.receipts.lock:
            self.guard(self.invocation)
            if self._cancelled.is_set():
                raise ServiceError('HARNESS_RUN_STOPPED', 'Cancelled attempt has no publishable output', 409)
            return tuple({'tool_id': name, 'arguments': json.loads(record[2]),
                          'receipt': deepcopy(record[1])}
                for name, kind in self._accepted.items() if kind in ('proposal', 'report')
                for key, record in self.receipts.records.items()
                if key == (self.invocation.stage.stage_digest, name, 'submission'))


class LocalTools:
    def __init__(self, broker): self.broker = broker
    def list_tools(self): return self.broker.list_tools()
    def call(self, name, arguments): return self.broker.call(name, arguments)


class McpTools:
    """Transport wrapper; policy and handlers remain in ToolBroker."""
    def __init__(self, broker): self.broker = broker
    def dispatch(self, request):
        try:
            method = request.get('method')
            if method == 'initialize':
                result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'business-tools', 'version': '1'}}
            elif method == 'ping': result = {}
            elif method == 'tools/list': result = {'tools': self.broker.list_tools()}
            elif method == 'tools/call':
                params = request.get('params', {})
                value = self.broker.call(params.get('name'), params.get('arguments', {}))
                result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}
            else:
                return {'jsonrpc': '2.0', 'id': request.get('id'),
                        'error': {'code': -32601, 'message': 'Method unavailable'}}
            return {'jsonrpc': '2.0', 'id': request.get('id'), 'result': result}
        except ServiceError as error:
            return {'jsonrpc': '2.0', 'id': request.get('id'), 'result': {'isError': True,
                    'content': [{'type': 'text', 'text': error.code}]}}
