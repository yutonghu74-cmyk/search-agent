"""Processing Agent to released filtering recipe; output purposes fail closed."""
from dataclasses import dataclass
import json
from typing import Protocol

from .agent_contracts import AgentStageTask, ProcessingPlanProposal
from .errors import ServiceError
from .models import Task
from .recipe_bridge import compile_confirmed_recipe


@dataclass(frozen=True, slots=True)
class ProcessingHandoff:
    stage: AgentStageTask
    task: Task
    proposal: ProcessingPlanProposal
    receipt_digest: str
    manifest_ref: str


class AgentProcessingHandoffStore(Protocol):
    def load(self, actor, handle) -> ProcessingHandoff: ...
    def save(self, actor, handle, snapshot, manifest_ref, recipe, registry): ...


def _filter_requests(snapshot):
    requirements = json.loads(snapshot.task.confirmed_proposal_json)['query_spec']['agent_requirements']
    requests = {r['requirement_id']: r for r in requirements['processing']}
    if any(r['purpose'] != 'filter' for r in requests.values()):
        raise ServiceError('AGENT_PROCESSING_OUTPUT_UNSUPPORTED',
            'Tag, rank and analyze require a typed output executor', 422)
    return requests


def compile_agent_filter_recipe(snapshot, registry):
    """Keep all confirmed hard rules; match each Agent choice to its exact rule.

    Registry publication and user rule semantics are authoritative. Agent step
    order cannot replace recipe order/instructions or remove other hard rules.
    Caller must obtain snapshot from an authorized current handoff store.
    """
    requests = _filter_requests(snapshot)
    recipe = compile_confirmed_recipe(snapshot.task, registry)
    selections = {s.requirement_id: s.detector for s in snapshot.proposal.steps}
    if len(selections) != len(snapshot.proposal.steps) or set(selections) != set(requests):
        raise ServiceError('PROCESSING_COVERAGE_INVALID', 'Every requirement needs one binding', 422)
    rules = {r.rule_id: r for r in recipe.rules}
    steps = {s.step_id: s for s in recipe.steps}
    for requirement_id, request in requests.items():
        rule = rules.get(request['rule_ref'])
        if (rule is None or rule.group_id != request['group_id']
                or steps[rule.step_id] != selections[requirement_id]):
            raise ServiceError('AGENT_RECIPE_MISMATCH',
                'Agent-selected detector differs from the confirmed rule binding', 409)
    return recipe


class AgentProcessingService:
    def __init__(self, *, handoffs, registry_provider):
        self.handoffs, self.registry_provider = handoffs, registry_provider

    def prepare(self, actor, handle, *, manifest_ref):
        snapshot = self.handoffs.load(actor, handle)
        if manifest_ref != snapshot.manifest_ref:
            raise ServiceError('AGENT_INPUT_CHANGED', 'Candidate manifest differs from the planning input', 409)
        _filter_requests(snapshot)
        registry = self.registry_provider(actor, snapshot.task)
        recipe = compile_agent_filter_recipe(snapshot, registry)
        return self.handoffs.save(actor, handle, snapshot, manifest_ref, recipe, registry)
