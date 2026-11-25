"""Registered business-domain plugins over the common Agent runtime ports.

An Agent's domain, role, implementation and Harness are separate choices.
Plugins/implementations are installed backend objects; configuration cannot
import code, grant tools, or turn a retrieval journal into a training ledger.
"""
from types import MappingProxyType

from ..errors import ServiceError, invalid
from ..processing_requirements import identifier
from .contracts import RoleDefinition
from .runner import HarnessRunner, load_role_routes


class AgentDomain:
    def __init__(self, domain_ref, task_contract_ref, *, stage_type, roles,
                 catalog, guard, task_kinds=frozenset({'plan'})):
        roles = tuple(roles)
        if (not identifier(domain_ref) or not identifier(task_contract_ref)
                or not isinstance(stage_type, type) or not callable(guard)
                or not roles or len(roles) > 64
                or any(type(role) is not RoleDefinition for role in roles)
                or len({role.role for role in roles}) != len(roles)
                or type(task_kinds) is not frozenset or not task_kinds
                or any(not identifier(kind) for kind in task_kinds)):
            raise invalid('invalid installed Agent domain')
        for role in roles:
            for tool_id in role.tool_ids:
                definition, _ = catalog.resolve(tool_id)
                if role.role not in definition.roles:
                    raise ServiceError('HARNESS_TOOL_DENIED', 'Domain role does not own its tools', 403)
                if definition.kind == 'execute':
                    raise ServiceError('HARNESS_EXECUTION_UNAVAILABLE', 'Planning domains cannot grant execution tools', 409)
        self.domain_ref, self.task_contract_ref = domain_ref, task_contract_ref
        self.stage_type, self.catalog = stage_type, catalog
        self.roles = MappingProxyType({role.role: role for role in roles})
        self.task_kinds, self._guard = task_kinds, guard

    def guard(self, invocation):
        role = self.roles.get(invocation.role.role)
        if (type(invocation.stage) is not self.stage_type
                or role is None or invocation.role.release_digest != role.release_digest
                or invocation.task_kind not in self.task_kinds):
            raise ServiceError('AGENT_DOMAIN_MISMATCH', 'Task or role does not belong to this business domain', 409)
        self._guard(invocation)


class AgentDomainRegistry:
    def __init__(self): self._domains = {}

    def register(self, domain):
        if type(domain) is not AgentDomain or domain.domain_ref in self._domains:
            raise invalid('duplicate or invalid Agent domain')
        self._domains[domain.domain_ref] = domain

    def resolve(self, domain_ref):
        if not identifier(domain_ref) or domain_ref not in self._domains:
            raise ServiceError('AGENT_DOMAIN_UNAVAILABLE', 'Business Agent domain is not installed', 503)
        return self._domains[domain_ref]


def build_registered_agent_runner(*, domain, routes, releases, adapters, model_binding,
                                  journal=None, actor=None):
    """Same lifecycle for DSH, a wrapped independent Agent, or a custom Harness.

    A production/training domain additionally needs its own authorized task,
    output validators and journal. Merely registering its role enables no work.
    """
    if type(domain) is not AgentDomain or not callable(model_binding):
        raise invalid('installed Agent domain and model resolver required')
    routes = load_role_routes(routes, releases, roles=domain.roles)
    if (journal is None) != (actor is None):
        raise invalid('durable Agent runtime requires both actor and journal')
    if journal is not None:
        if getattr(journal, 'task_contract_ref', None) != domain.task_contract_ref:
            raise ServiceError('AGENT_JOURNAL_DOMAIN_MISMATCH', 'Business journal does not implement this task contract', 503)
        from .durable import DurableHarnessRunner
        return DurableHarnessRunner(adapters, routes, domain.roles, domain.catalog,
            guard=domain.guard, model_binding=model_binding, journal=journal, actor=actor, releases=releases)
    return HarnessRunner(adapters, routes, domain.roles, domain.catalog,
        guard=domain.guard, model_binding=model_binding, releases=releases)
