"""Opt-in external Agent access over the existing business HTTP commands.

The deployment resolves verified human/integration claims. No client header
can self-declare human approval rights, a team identity or capabilities.
"""
from dataclasses import dataclass
import re

from .errors import ServiceError, invalid
from .models import Actor
from .processing_requirements import identifier

CAPABILITIES = frozenset({'retrieval.read', 'retrieval.prepare', 'cleaning.read',
    'cleaning.prepare', 'cleaning.execute', 'results.read', 'task.cancel',
    'agent.retrieval.read', 'agent.retrieval.prepare', 'agent.retrieval.start',
    'agent.retrieval.terms.read', 'agent.retrieval.terms.prepare', 'agent.retrieval.recall.read'})
TASK_ROUTE = re.compile(r'^/v1/retrieval/tasks/([^/]+)(/.*)?$')
SHARED_ROUTE = re.compile(r'^/v1/shared/tasks/([^/]+)/(binding|cancel|receipt)$')


def external_ref(value):
    return isinstance(value, str) and 1 <= len(value) <= 128 and value == value.strip() and all(32 <= ord(c) < 127 for c in value)


@dataclass(frozen=True)
class SharedPrincipal:
    actor: Actor
    project_id: str
    kind: str
    integration_ref: str | None = None
    capabilities: frozenset[str] = frozenset()
    task_ids: frozenset[str] = frozenset()

    def __post_init__(self):
        if (type(self.actor) is not Actor or not isinstance(self.project_id, str) or not self.project_id
                or self.kind not in ('human', 'integration')
                or type(self.capabilities) is not frozenset or not self.capabilities <= CAPABILITIES
                or self.kind == 'integration' and not identifier(self.integration_ref)
                or type(self.task_ids) is not frozenset or len(self.task_ids)>1000
                or any(not identifier(tid) for tid in self.task_ids)
                or self.kind == 'integration' and not self.task_ids
                or self.kind == 'human' and (self.integration_ref is not None or self.capabilities or self.task_ids)):
            raise invalid('invalid verified shared principal')


class SharedAccess:
    def __init__(self, bindings, *, resolve_principal):
        if not callable(resolve_principal): raise ValueError('verified principal resolver required')
        self.bindings, self.resolve_principal = bindings, resolve_principal

    def authorize(self, actor, headers, method, path):
        from .adapters.platform_access import single_header
        principal = self.resolve_principal(actor, headers)
        if (type(principal) is not SharedPrincipal or principal.actor != actor
                or principal.project_id != single_header(headers, 'x-app-id', required=True)):
            raise ServiceError('UNAUTHENTICATED', 'Verified shared principal required', 401)
        if principal.kind == 'human': return principal
        if method == 'GET' and path == '/v1/shared/capabilities': return principal
        shared = SHARED_ROUTE.fullmatch(path)
        if shared:
            task_id, action = shared.groups()
            allowed = ((action == 'binding' and method in ('GET', 'POST'))
                       or (action == 'receipt' and method == 'GET')
                       or (action == 'cancel' and method == 'POST' and 'task.cancel' in principal.capabilities))
            if not allowed: raise ServiceError('SHARED_CAPABILITY_DENIED', 'Shared operation not granted', 403)
            if action == 'cancel': self.bindings.get(principal, task_id, single_header(headers, 'X-External-Task-Ref', required=True), current=False)
            return principal
        match = TASK_ROUTE.fullmatch(path)
        capability = None
        if match:
            task_id, suffix = match.groups(); suffix = suffix or ''
            if method == 'GET':
                if suffix == '/retrieval-agent/recall':
                    capability = 'agent.retrieval.recall.read'
                if suffix == '' or suffix in ('/first-search', '/first-search/progress', '/first-search/candidates', '/first-search/manifest'):
                    capability = 'retrieval.read'
                elif suffix in ('/cleaning/status', '/cleaning/steps'):
                    capability = 'cleaning.read'
                elif suffix in ('/results', '/progress'): capability = 'results.read'
            elif method == 'POST':
                capability = {'/first-search/prepare': 'retrieval.prepare',
                    '/cleaning/prepare': 'cleaning.prepare', '/cleaning/execute': 'cleaning.execute'}.get(suffix)
            terms = re.fullmatch(r'/(?:retrieval-agent/terms|expansion-jobs)(?:/(expjob_[a-f0-9]{32})(?:/(retry))?)?', suffix)
            if terms:
                ref, operation = terms.groups()
                if method == 'GET' and operation is None:
                    capability = 'agent.retrieval.terms.read'
                elif method == 'POST' and (ref is None or operation == 'retry'):
                    capability = 'agent.retrieval.terms.prepare'
            job = re.fullmatch(r'/agent-jobs(?:/(prepare|agentdraft_[a-f0-9]{32})(?:/(start|cancel|artifacts))?)?', suffix)
            if job:
                ref, operation = job.groups()
                if method == 'GET' and ref != 'prepare' and operation in (None, 'artifacts'):
                    capability = 'agent.retrieval.read'
                elif method == 'POST' and ref == 'prepare' and operation is None:
                    capability = 'agent.retrieval.prepare'
                elif method == 'POST' and ref and ref != 'prepare':
                    capability = {'start': 'agent.retrieval.start', 'cancel': 'task.cancel'}.get(operation)
        if capability is None or capability not in principal.capabilities:
            raise ServiceError('SHARED_CAPABILITY_DENIED', 'Integration cannot access this operation or approve owner actions', 403)
        cleanup = method == 'POST' and capability == 'task.cancel' and job is not None
        self.bindings.get(principal, task_id, single_header(headers, 'X-External-Task-Ref', required=True), current=not cleanup)
        return principal
