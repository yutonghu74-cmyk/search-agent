"""Resolve model identifiers through current backend records, never model bindings."""
from .agent_contracts import RetrievalStrategyProposal, ProcessingPlanProposal, ProcessingStepBinding
from .errors import ServiceError, invalid


def retrieval_strategy(stage, arguments, bindings):
    if type(arguments) is not dict or set(arguments) != {'proposal'}:
        raise invalid('retrieval proposal envelope required')
    proposal = arguments['proposal']
    expected = {'action', 'binding_id', 'method', 'query', 'evidence_refs', 'reason'}
    if type(proposal) is not dict or set(proposal) != expected:
        raise invalid('retrieval proposal fields')
    if type(proposal['evidence_refs']) is not list:
        raise invalid('evidence refs must be an array')
    matches = [b for b in bindings if b.binding_id == proposal['binding_id']]
    if len(matches) != 1:
        raise ServiceError('AGENT_SOURCE_NOT_APPROVED', 'Source unavailable or ambiguous', 403)
    return RetrievalStrategyProposal(stage.stage_digest, proposal['action'], matches[0],
        proposal['method'], proposal['query'], tuple(proposal['evidence_refs']), proposal['reason'])


def processing_plan(stage, arguments, capabilities):
    if type(arguments) is not dict or set(arguments) != {'proposal'}:
        raise invalid('processing proposal envelope required')
    proposal = arguments['proposal']
    if type(proposal) is not dict or set(proposal) != {'steps'} or type(proposal['steps']) is not list:
        raise invalid('processing proposal fields')
    steps = []
    for item in proposal['steps']:
        if type(item) is not dict or set(item) != {'requirement_id', 'step_id'}:
            raise invalid('processing binding fields')
        matches = {c.detector for c in capabilities if c.detector.step_id == item['step_id']}
        if len(matches) != 1:
            raise invalid('ambiguous or unavailable released step')
        steps.append(ProcessingStepBinding(item['requirement_id'], next(iter(matches))))
    return ProcessingPlanProposal(stage.stage_digest, stage.requirements_digest, tuple(steps))
