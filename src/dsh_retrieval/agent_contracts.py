"""Two-role planning boundary, reusing RetrievalScope and DetectionStep.

All validators are side-effect-free. Current grants, capabilities and budgets
MUST come from backend authority, never model input. T05 must recheck in the
reservation/publication transaction. These objects do not grant execution.
"""
from dataclasses import asdict, dataclass
import json
import re

from .errors import ServiceError, invalid
from .integration_models import IndexBinding
from .multi_rule_contracts import DetectionStep
from .processing_requirements import (ACTIONS, METHODS, PURPOSES, identifier,
                                      normalize_agent_requirements, positive)
from .query_spec import digest
from .retrieval_contracts import RetrievalScope, validate_scope


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', value) is not None


def _reject(code, message, status=422):
    raise ServiceError(code, message, status)


@dataclass(frozen=True, slots=True)
class AgentStageTask:
    scope: RetrievalScope
    operation_id: str
    role: str
    input_digest: str
    requirements_digest: str
    approval_digest: str
    policy_version: str
    model_binding_digest: str
    budget_ref: str

    def __post_init__(self):
        if (type(self.scope) is not RetrievalScope or self.role not in ('retrieval', 'cleaning')
                or not all(identifier(v) for v in (self.operation_id, self.policy_version, self.budget_ref))
                or not all(_hash(v) for v in (self.input_digest, self.requirements_digest,
                                              self.approval_digest, self.model_binding_digest))):
            raise invalid('invalid agent stage binding')

    @property
    def stage_digest(self):
        return digest(asdict(self))


def validate_stage_authority(actor, task, submitted, *, current, requirements, spec):
    """current is an authoritative, explicitly approved stage record or None."""
    if type(submitted) is not AgentStageTask:
        raise invalid('stage task required')
    validate_scope(actor, task, submitted.scope)
    if current is None or type(current) is not AgentStageTask or current != submitted:
        _reject('AGENT_CONFIRMATION_REQUIRED', 'Current approved stage required', 409)
    try:
        confirmed_spec = json.loads(task.confirmed_proposal_json)['query_spec']
        frozen = confirmed_spec['agent_requirements']
    except (TypeError, ValueError, KeyError):
        _reject('AGENT_CONFIRMATION_REQUIRED', 'Confirmed agent requirements required', 409)
    if confirmed_spec != spec or frozen != requirements:
        _reject('AGENT_REQUIREMENTS_CONFLICT', 'Requirements differ from confirmed task', 409)
    normalized = normalize_agent_requirements(requirements, confirmed_spec)
    if digest(normalized) != submitted.requirements_digest:
        _reject('AGENT_REQUIREMENTS_CONFLICT', 'Stage requirements changed', 409)
    return normalized


def validate_stage(actor, task, submitted, *, current, requirements, spec,
                   remaining_calls, requested_calls):
    normalized = validate_stage_authority(actor, task, submitted, current=current,
                                         requirements=requirements, spec=spec)
    if (type(remaining_calls) is not int or remaining_calls < 0 or not positive(requested_calls)
            or requested_calls > remaining_calls
            or requested_calls > normalized['autonomy']['max_calls']):
        _reject('AGENT_BUDGET_EXCEEDED', 'Insufficient stage budget', 409)


@dataclass(frozen=True, slots=True)
class RetrievalStrategyProposal:
    stage_digest: str
    action: str
    binding: IndexBinding
    method: str
    query: str
    evidence_refs: tuple[str, ...]
    reason: str

    @property
    def proposal_digest(self):
        return digest(asdict(self))


def _evidence(refs, available):
    if (type(refs) is not tuple or not refs or len(refs) > 64
            or any(not identifier(v) or v not in available for v in refs)
            or len(set(refs)) != len(refs)):
        raise invalid('report must cite current authorized evidence')


def _text(text):
    return isinstance(text, str) and 1 <= len(text.strip()) <= 2000


def validate_retrieval_strategy(proposal, stage, requirements, *, authorized_bindings,
                                evidence_refs):
    """Use after validate_stage; bindings are current authorized/compatible entries."""
    if (type(proposal) is not RetrievalStrategyProposal or stage.role != 'retrieval'
            or proposal.stage_digest != stage.stage_digest
            or digest(requirements) != stage.requirements_digest):
        _reject('AGENT_STAGE_CONFLICT', 'Retrieval proposal stage mismatch', 409)
    autonomy = requirements['autonomy']
    if (not isinstance(proposal.action, str) or proposal.action not in ACTIONS
            or proposal.action not in autonomy['actions'] or not isinstance(proposal.method, str)
            or proposal.method not in METHODS or proposal.method not in autonomy['methods']):
        _reject('AGENT_ACTION_NOT_APPROVED', 'Strategy action or method is outside approval')
    if (type(proposal.binding) is not IndexBinding
            or proposal.binding.library_id not in autonomy['library_ids']
            or proposal.binding not in authorized_bindings):
        _reject('AGENT_SOURCE_NOT_APPROVED', 'Source or physical index binding not authorized', 403)
    if not _text(proposal.query) or not _text(proposal.reason):
        raise invalid('bounded query and reason required')
    _evidence(proposal.evidence_refs, evidence_refs)


@dataclass(frozen=True, slots=True)
class ProcessingStepBinding:
    requirement_id: str
    detector: DetectionStep


@dataclass(frozen=True, slots=True)
class ProcessingCapability:
    """Backend-published capability; never deserialize from an Agent proposal."""
    detector: DetectionStep
    media_type: str
    purposes: frozenset[str]
    output_types: frozenset[str]

    def __post_init__(self):
        if (type(self.detector) is not DetectionStep or self.media_type not in ('image', 'video', 'text')
                or type(self.purposes) is not frozenset or not self.purposes
                or not self.purposes <= PURPOSES or type(self.output_types) is not frozenset
                or not self.output_types <= {'boolean', 'number', 'text', 'enum'}):
            raise invalid('invalid released processing capability')


@dataclass(frozen=True, slots=True)
class ProcessingPlanProposal:
    stage_digest: str
    requirements_digest: str
    steps: tuple[ProcessingStepBinding, ...]

    @property
    def proposal_digest(self):
        return digest(asdict(self))


def validate_processing_plan(proposal, stage, requirements, *, media_type, capabilities):
    """Never compiles YAML/code; every requested purpose needs a released binding."""
    if (type(proposal) is not ProcessingPlanProposal or stage.role != 'cleaning'
            or proposal.stage_digest != stage.stage_digest
            or proposal.requirements_digest != stage.requirements_digest
            or digest(requirements) != stage.requirements_digest):
        _reject('AGENT_STAGE_CONFLICT', 'Processing proposal stage mismatch', 409)
    if type(proposal.steps) is not tuple or any(type(s) is not ProcessingStepBinding for s in proposal.steps):
        raise invalid('typed processing steps required')
    requests = {r['requirement_id']: r for r in requirements['processing']}
    ids = [s.requirement_id for s in proposal.steps]
    if any(not identifier(v) for v in ids) or len(ids) != len(set(ids)) or set(ids) != set(requests):
        _reject('PROCESSING_COVERAGE_INVALID', 'Every processing requirement must be covered exactly once')
    for step in proposal.steps:
        request = requests[step.requirement_id]
        capability = next((c for c in capabilities if type(c) is ProcessingCapability
                           and c.detector == step.detector and c.media_type == media_type
                           and request['purpose'] in c.purposes
                           and (request['output_schema'] is None
                                or request['output_schema']['type'] in c.output_types)), None)
        if capability is None:
            _reject('DETECTOR_NOT_RELEASED', 'No released capability covers this processing requirement', 409)


@dataclass(frozen=True, slots=True)
class StageFeedback:
    """Business-produced statistics. Agents can cite, but cannot publish counts."""
    stage_digest: str
    evidence_ref: str
    status: str
    candidate_count: int
    new_unique_accepted: int | None
    deficit: int | None
    unknown_count: int
    error_count: int

    def __post_init__(self):
        counts = (self.candidate_count, self.unknown_count, self.error_count)
        if (not _hash(self.stage_digest) or not identifier(self.evidence_ref)
                or self.status not in ('completed', 'pending', 'failed', 'unknown')
                or any(type(v) is not int or v < 0 for v in counts)
                or self.unknown_count + self.error_count > self.candidate_count):
            raise invalid('invalid business stage feedback')
        if self.status != 'completed':
            if self.new_unique_accepted is not None or self.deficit is not None:
                raise invalid('unfinished feedback cannot claim effective yield or deficit')
        elif (type(self.new_unique_accepted) is not int or type(self.deficit) is not int
              or not 0 <= self.new_unique_accepted <= self.candidate_count - self.unknown_count - self.error_count
              or self.deficit < 0):
            raise invalid('completed feedback requires authoritative yield and deficit')


@dataclass(frozen=True, slots=True)
class AgentStageReport:
    stage_digest: str
    outcome: str
    summary: str
    recommendation: str
    evidence_refs: tuple[str, ...]


def validate_stage_report(report, stage, *, evidence_refs):
    if (type(report) is not AgentStageReport or report.stage_digest != stage.stage_digest
            or report.outcome not in ('ready', 'needs_confirmation', 'blocked', 'stopped')
            or report.recommendation not in ('continue', 'refill', 'change_request', 'stop')
            or report.recommendation == 'change_request' and report.outcome != 'needs_confirmation'
            or not _text(report.summary)):
        raise invalid('invalid stage report')
    _evidence(report.evidence_refs, evidence_refs)


def validate_agent_proposal(actor, task, stage, proposal, *, current, requirements,
                            spec, remaining_calls, requested_calls,
                            authorized_bindings=(), capabilities=(), evidence_refs=()):
    """Unified preflight; repository must repeat under its execution transaction.

    Backend must resolve current, remaining_calls, bindings/capabilities and
    evidence from authority for THIS task, not copy them from model arguments.
    No dispatch, persistence, budget reservation or implicit confirmation here.
    """
    validate_stage(actor, task, stage, current=current, requirements=requirements,
                   spec=spec, remaining_calls=remaining_calls, requested_calls=requested_calls)
    if stage.role == 'retrieval':
        validate_retrieval_strategy(proposal, stage, requirements,
                                    authorized_bindings=authorized_bindings,
                                    evidence_refs=evidence_refs)
    else:
        validate_processing_plan(proposal, stage, requirements,
                                 media_type=spec['media_type'], capabilities=capabilities)


def validate_agent_report(actor, task, stage, report, *, current, requirements,
                          spec, evidence_refs):
    """Report preflight rechecks scope/approval but consumes no execution call."""
    validate_stage_authority(actor, task, stage, current=current, requirements=requirements, spec=spec)
    validate_stage_report(report, stage, evidence_refs=evidence_refs)
