"""Logical tools shared by all Harnesses, backed by T03 authority validators."""
from dataclasses import dataclass
import json

from ..agent_contracts import (AgentStageReport, validate_stage_authority,
    validate_agent_proposal, validate_agent_report)
from ..errors import invalid
from ..agent_proposals import retrieval_strategy, processing_plan
from .tools import ToolCatalog, ToolDefinition


@dataclass(frozen=True)
class StageAuthority:
    actor: object
    task: object
    current: object
    spec: dict
    remaining_calls: int
    authorized_bindings: tuple = ()
    capabilities: tuple = ()
    evidence_refs: tuple = ()

    def options(self):
        return dict(current=self.current, spec=self.spec,
                    requirements=self.spec.get('agent_requirements'))


def stage_catalog(authority):
    """authority(invocation) MUST reread backend state; never expose it to models."""
    catalog = ToolCatalog()
    def guard(invocation):
        a = authority(invocation)
        validate_stage_authority(a.actor, a.task, invocation.stage, **a.options())
    def current(invocation):
        guard(invocation)
        return authority(invocation)
    def read(invocation, arguments):
        # Context is a frozen, authorized snapshot verified by HarnessInvocation.
        current(invocation)
        return json.loads(invocation.context_json)
    def retrieval(invocation, args):
        a = current(invocation)
        value = retrieval_strategy(invocation.stage, args, a.authorized_bindings)
        validate_agent_proposal(a.actor, a.task, invocation.stage, value, **a.options(),
            remaining_calls=a.remaining_calls, requested_calls=1,
            authorized_bindings=a.authorized_bindings, evidence_refs=a.evidence_refs)
        return {'status': 'proposal_validated', 'proposal_digest': value.proposal_digest, 'execution_allowed': False}
    def cleaning(invocation, args):
        a = current(invocation)
        value = processing_plan(invocation.stage, args, a.capabilities)
        validate_agent_proposal(a.actor, a.task, invocation.stage, value, **a.options(),
            remaining_calls=a.remaining_calls, requested_calls=1, capabilities=a.capabilities)
        return {'status': 'proposal_validated', 'proposal_digest': value.proposal_digest, 'execution_allowed': False}
    def report(invocation, args):
        a = current(invocation); value = args['proposal']
        if type(value) is not dict or set(value) != {'outcome', 'summary', 'recommendation', 'evidence_refs'}:
            raise invalid('report fields')
        if type(value['evidence_refs']) is not list: raise invalid('evidence refs must be an array')
        value = AgentStageReport(invocation.stage.stage_digest, value['outcome'], value['summary'],
                                  value['recommendation'], tuple(value['evidence_refs']))
        validate_agent_report(a.actor, a.task, invocation.stage, value, **a.options(), evidence_refs=a.evidence_refs)
        return {'status': 'report_validated', 'execution_allowed': False}
    def register(name, roles, kind, handler, schema):
        catalog.register(ToolDefinition(name, '1', frozenset(roles), kind,
            json.dumps(schema), name), handler)
    schema = {'type': 'object', 'properties': {'proposal': {'type': 'object'}},
              'required': ['proposal'], 'additionalProperties': False}
    register('get_stage_context', ('retrieval', 'cleaning'), 'read', read,
             {'type': 'object', 'properties': {}, 'additionalProperties': False})
    register('submit_retrieval_strategy', ('retrieval',), 'proposal', retrieval, schema)
    register('submit_processing_plan', ('cleaning',), 'proposal', cleaning, schema)
    register('submit_stage_report', ('retrieval', 'cleaning'), 'report', report, schema)
    return catalog, guard
