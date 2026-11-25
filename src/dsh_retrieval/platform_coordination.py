"""Platform coordination role over installed capabilities, not vendor child trees.

DSH (or another installed Harness) chooses a bounded plan. Original business
services authorize and deliver its steps; models cannot approve owner gates.
"""
from dataclasses import asdict, dataclass
import json

from .errors import ServiceError, invalid
from .harness.contracts import RoleDefinition
from .harness.tools import ToolCatalog, ToolDefinition
from .processing_requirements import identifier
from .query_spec import digest
from .retrieval_contracts import RetrievalScope
from .models import TaskLifecycle, RunStatus

WORKFLOW = 'platform-coordinator@1'
TASK_CONTRACT = 'platform-coordination-stage@1'


@dataclass(frozen=True)
class CoordinationStage:
    # This first implementation coordinates an existing retrieval/cleaning root
    # Task. Future domains need their own root scope, never a fake retrieval Task.
    scope: RetrievalScope
    operation_id: str
    input_digest: str
    model_binding_digest: str
    policy_digest: str
    role: str = 'coordinator'

    def __post_init__(self):
        from .agent_contracts import _hash
        if (type(self.scope) is not RetrievalScope or self.role != 'coordinator'
                or not identifier(self.operation_id)
                or not all(_hash(v) for v in (self.input_digest, self.model_binding_digest, self.policy_digest))):
            raise invalid('invalid platform coordination stage')

    @property
    def stage_digest(self): return digest(asdict(self))


def current_scope(actor, task, scope):
    if (task.tenant_id, task.owner_user_id) != (actor.tenant_id, actor.user_id):
        raise ServiceError('NOT_FOUND', 'Task not found', 404)
    if task.lifecycle != TaskLifecycle.ACTIVE or task.run_status == RunStatus.CANCELLED:
        raise ServiceError('INVALID_STATE_TRANSITION', 'Task is not active', 409)
    if RetrievalScope.from_task(task) != scope:
        raise ServiceError('REVISION_CONFLICT', 'Task changed; refresh coordination context', 409)


@dataclass(frozen=True)
class CoordinationPolicy:
    model_calls: int = 3
    generation_model_calls: int = 30
    max_seconds: int = 60
    max_steps: int = 4

    def __post_init__(self):
        if (any(type(v) is not int for v in asdict(self).values())
                or not 1 <= self.model_calls <= self.generation_model_calls <= 1000
                or not 1 <= self.max_seconds <= 300 or not 1 <= self.max_steps <= 8):
            raise ValueError('bounded coordinator policy required')


@dataclass(frozen=True)
class CoordinationCapability:
    capability: str
    release_ref: str
    kind: str
    describe: object
    invoke: object

    def __post_init__(self):
        if (not identifier(self.capability) or not identifier(self.release_ref)
                or self.kind not in ('agent', 'business_module', 'external_workflow')
                or not callable(self.describe) or not callable(self.invoke)):
            raise invalid('trusted installed coordination capability required')


class CoordinationRegistry:
    def __init__(self): self._ports, self._sealed = {}, False

    def seal(self):
        """Publish an immutable installation; a new release needs a new instance."""
        self._sealed = True

    def register(self, port):
        if self._sealed or type(port) is not CoordinationCapability or port.capability in self._ports:
            raise invalid('unique installed coordination capability required')
        self._ports[port.capability] = port

    @property
    def release_digest(self):
        return digest([(key, p.release_ref, p.kind) for key, p in sorted(self._ports.items())])

    def context(self, actor, task):
        from .agent_contracts import _hash
        result = {}
        for name, port in sorted(self._ports.items()):
            value = port.describe(actor, task)
            if (type(value) is not dict or type(value.get('actions')) is not list
                    or not _hash(value.get('binding_digest'))
                    or len(value['actions']) > 8 or any(not identifier(a) for a in value['actions'])):
                raise ServiceError('COORDINATOR_CAPABILITY_INVALID', 'Invalid capability snapshot', 503)
            result[name] = {**value, 'release_ref': port.release_ref, 'kind': port.kind}
        return result

    def invoke(self, actor, task, step, *, key, expected):
        port = self._ports.get(step['capability'])
        if port is None or port.release_ref != expected['release_ref']:
            raise ServiceError('COORDINATOR_CAPABILITY_CHANGED', 'Installed capability changed', 409)
        fresh = port.describe(actor, task)
        if step['action'] not in fresh.get('actions', ()):
            raise ServiceError('COORDINATOR_GATE_CHANGED', 'Capability is running, unknown or needs owner confirmation', 409)
        if step['action'] != 'inspect' and fresh.get('binding_digest') != expected.get('binding_digest'):
            raise ServiceError('COORDINATOR_INPUT_CHANGED', 'Capability input artifact or authorization changed', 409)
        return port.invoke(actor, task, step['action'], key, fresh)


ROLE = RoleDefinition('platform-coordinator-role@1', 'coordinator', '''
你是当前Chat的任务中控角色。先读取get_platform_context，依据真实任务状态和已安装能力安排下一步。
通过submit_coordination_plan提交有上限的步骤和简短解释。只能选择上下文列出的capability/action。
未知/失败/未完成不能当作已完成或零收益。正在运行的作业只查询或等待，不重复启动。
需要确认词表、候选清单或清洗方案时，在对应原入口等待发起人确认，不代批、不跳过。
不能修改主体、数量、配额、来源、预算或模型设置；不能直接访问数据库、网络、脚本或任意工具。
专业Agent/业务模块可自带运行框架，不要求把每个能力包装成DSH子Agent。数据生产、训练、RHF未开放。
不要在reply中宣称业务已经完成；执行状态以业务服务回执为准。无可执行步骤可提交空steps和说明。
''', ('get_platform_context', 'submit_coordination_plan'), 'coordination-plan@1')


def validate_plan(value, context, policy):
    if (type(value) is not dict or set(value) != {'reply', 'steps'}
            or not isinstance(value['reply'], str) or not 1 <= len(value['reply'].strip()) <= 600
            or type(value['steps']) is not list or len(value['steps']) > policy.max_steps):
        raise invalid('bounded coordination plan required')
    seen, choices = set(), set()
    for step in value['steps']:
        if (type(step) is not dict or set(step) != {'step_id', 'capability', 'action', 'depends_on'}
                or not identifier(step['step_id']) or step['step_id'] in seen
                or not identifier(step['capability']) or not identifier(step['action'])
                or type(step['depends_on']) is not list or len(step['depends_on']) > policy.max_steps
                or any(not identifier(ref) or ref not in seen for ref in step['depends_on'])):
            raise invalid('ordered acyclic coordination steps required')
        available = context['capabilities'].get(step['capability'], {})
        choice = (step['capability'], step['action'])
        if step['action'] not in available.get('actions', ()) or choice in choices:
            raise ServiceError('COORDINATOR_CAPABILITY_DENIED', 'Capability/action outside current published scope', 403)
        seen.add(step['step_id']); choices.add(choice)
    return value


def coordination_catalog(policy):
    catalog = ToolCatalog()
    empty = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    step = {'type': 'object', 'additionalProperties': False,
        'required': ['step_id', 'capability', 'action', 'depends_on'], 'properties': {
            key: {'type': 'string', 'minLength': 1, 'maxLength': 128} for key in ('step_id', 'capability', 'action')}}
    step['properties']['depends_on'] = {'type': 'array', 'maxItems': policy.max_steps,
                                       'items': {'type': 'string', 'maxLength': 128}}
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['reply', 'steps'], 'properties': {
        'reply': {'type': 'string', 'minLength': 1, 'maxLength': 600},
        'steps': {'type': 'array', 'maxItems': policy.max_steps, 'items': step}}}
    catalog.register(ToolDefinition('get_platform_context', '1', frozenset({'coordinator'}), 'read',
        json.dumps(empty), '读取当前任务、能力与原确认门禁'), lambda inv, args: json.loads(inv.context_json))
    def submit(inv, args):
        value = validate_plan(args, json.loads(inv.context_json), policy)
        return {'status': 'coordination_plan_validated', 'proposal_digest': digest(value), 'execution_allowed': False}
    catalog.register(ToolDefinition('submit_coordination_plan', '1', frozenset({'coordinator'}), 'proposal',
        json.dumps(schema), '提交受控委派方案；不构成人工确认'), submit)
    return catalog
