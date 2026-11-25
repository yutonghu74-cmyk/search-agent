from dataclasses import asdict, dataclass
import math
from typing import Protocol, runtime_checkable

from ..errors import ServiceError, invalid
from ..processing_requirements import identifier
from ..query_spec import digest

PROTOCOL = 'harness@1'
EVENTS = frozenset({'started', 'tool_requested', 'tool_completed', 'usage',
                    'proposal_submitted', 'checkpoint', 'finished', 'failed', 'cancelled'})
REQUIRED = frozenset({'controlled_tools', 'deadline', 'attempt_tracking'})


@runtime_checkable
class AgentStageBinding(Protocol):
    """Minimal runtime binding; business domains own their scope and task models.

    Production/training must not impersonate RetrievalScope. Domain guards and
    journals validate the concrete task; this protocol alone grants no authority.
    """
    role: str
    input_digest: str
    model_binding_digest: str

    @property
    def stage_digest(self) -> str: ...


@dataclass(frozen=True)
class RoleDefinition:
    release_ref: str
    role: str
    system_prompt: str
    tool_ids: tuple[str, ...]
    output_contract: str

    def __post_init__(self):
        if (not identifier(self.role) or not identifier(self.release_ref)
                or not identifier(self.output_contract) or not isinstance(self.system_prompt, str)
                or not 1 <= len(self.system_prompt) <= 16000
                or type(self.tool_ids) is not tuple or not 1 <= len(self.tool_ids) <= 64
                or any(not identifier(v) for v in self.tool_ids)
                or len(set(self.tool_ids)) != len(self.tool_ids)):
            raise invalid('invalid Harness role release')

    @property
    def release_digest(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class RuntimeRelease:
    release_ref: str
    adapter_id: str
    artifact_digest: str
    required_capabilities: frozenset[str] = REQUIRED

    def __post_init__(self):
        from ..agent_contracts import _hash
        if (not identifier(self.release_ref) or not identifier(self.adapter_id)
                or not _hash(self.artifact_digest) or type(self.required_capabilities) is not frozenset
                or not REQUIRED <= self.required_capabilities
                or any(not identifier(v) for v in self.required_capabilities)):
            raise invalid('invalid runtime release')

    @property
    def release_digest(self):
        value = asdict(self)
        value['required_capabilities'] = sorted(self.required_capabilities)
        return digest(value)


@dataclass(frozen=True)
class HarnessInvocation:
    stage: AgentStageBinding
    run_id: str
    attempt_id: str
    role: RoleDefinition
    runtime: RuntimeRelease
    model_profile_ref: str
    task_kind: str
    deadline: float
    context_json: str

    def __post_init__(self):
        import json
        from ..agent_contracts import _hash
        if (not isinstance(self.stage, AgentStageBinding) or type(self.role) is not RoleDefinition
                or type(self.runtime) is not RuntimeRelease or self.stage.role != self.role.role
                or not all(_hash(v) for v in (self.stage.stage_digest,
                    self.stage.input_digest, self.stage.model_binding_digest))
                or not all(identifier(v) for v in
                           (self.run_id, self.attempt_id, self.model_profile_ref, self.task_kind))
                or type(self.deadline) not in (float, int) or not math.isfinite(self.deadline)
                or self.deadline <= 0 or not isinstance(self.context_json, str)
                or len(self.context_json.encode()) > 200000):
            raise invalid('invalid Harness invocation')
        try:
            context = json.loads(self.context_json)
            if type(context) is not dict or digest(context) != self.stage.input_digest:
                raise ValueError()
        except (ValueError, TypeError):
            raise invalid('input snapshot digest mismatch') from None

    @property
    def invocation_digest(self):
        # No credentials or SDK session object in the application envelope.
        return digest({'stage': self.stage.stage_digest, 'run': self.run_id, 'attempt': self.attempt_id,
                       'role': self.role.release_digest, 'runtime': self.runtime.release_digest,
                       'model': self.model_profile_ref, 'task_kind': self.task_kind,
                       'deadline': self.deadline})


@dataclass(frozen=True)
class ProbeResult:
    adapter_id: str
    protocol: str
    version: str
    capabilities: frozenset[str]
    available: bool
    reason_code: str | None = None
    artifact_digest: str | None = None


@dataclass(frozen=True)
class RuntimeHandle:
    run_id: str
    attempt_id: str
    runtime_digest: str
    invocation_digest: str


@dataclass(frozen=True)
class RuntimeEvent:
    cursor: int
    kind: str
    code: str | None = None

    def __post_init__(self):
        if type(self.cursor) is not int or self.cursor <= 0 or self.kind not in EVENTS:
            raise invalid('invalid normalized Harness event')


class HarnessAdapter(Protocol):
    def describe(self) -> ProbeResult: ...
    def probe(self) -> ProbeResult: ...
    def start(self, invocation: HarnessInvocation, tools) -> RuntimeHandle: ...
    def poll(self, handle: RuntimeHandle, after_cursor: int = 0) -> tuple[RuntimeEvent, ...]: ...
    def cancel(self, handle: RuntimeHandle) -> str: ...
    def close(self, handle: RuntimeHandle) -> None: ...


class AdapterRegistry:
    """Explicit installed objects only; config cannot import arbitrary modules."""
    def __init__(self):
        self._adapters = {}

    def register(self, adapter_id, adapter: HarnessAdapter):
        if not identifier(adapter_id) or adapter_id in self._adapters:
            raise invalid('duplicate or invalid adapter registration')
        if adapter.describe().adapter_id != adapter_id:
            raise invalid('adapter identity mismatch')
        self._adapters[adapter_id] = adapter

    def resolve(self, release):
        adapter = self._adapters.get(release.adapter_id)
        if adapter is None:
            raise ServiceError('HARNESS_NOT_REGISTERED', 'Runtime adapter unavailable', 503)
        result = adapter.probe()
        if (not result.available or result.adapter_id != release.adapter_id or result.protocol != PROTOCOL
                or not release.required_capabilities <= result.capabilities
                or result.artifact_digest != release.artifact_digest):
            raise ServiceError('HARNESS_CAPABILITY_UNAVAILABLE', 'Runtime probe did not meet requirements', 503)
        return adapter
