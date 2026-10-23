"""Trusted backend preparation only: no ledger writes, approval or search."""
from dataclasses import asdict, dataclass
import json

from .errors import ServiceError
from .integration_models import AccessContext, IndexBinding, QueryVector, SearchRequest
from .query_branches import compile_query_branches
from .query_spec import digest
from .retrieval_contracts import RetrievalPlan, RetrievalScope, validate_plan, validate_scope


@dataclass(frozen=True, slots=True)
class TextQueryCapability:
    """Server registration for an exact binding, never client-supplied config."""
    binding: IndexBinding
    media_type: str
    filter_template_ref: str
    max_batch_size: int


@dataclass(frozen=True, slots=True)
class PreparedQuery:
    plan: RetrievalPlan
    branch_id: str
    query_fingerprint: str
    logical_preview_digest: str

    @property
    def preparation_digest(self):
        return digest({"plan_digest": self.plan.proposal_digest,
                       "branch_id": self.branch_id,
                       "query_fingerprint": self.query_fingerprint,
                       "logical_preview_digest": self.logical_preview_digest})


class PhysicalQueryPreparer:
    """Select one explicit authorized branch/binding into an unapproved plan.

    Catalog implementations must filter authority before matching descriptions.
    Dependencies are trusted backend ports; callers cannot supply vectors,
    physical addresses, filter expressions or capability registrations.
    """
    def __init__(self, *, tasks, projects, catalog, embeddings, authorization,
                 capabilities: tuple[TextQueryCapability, ...]):
        self.tasks, self.projects = tasks, projects
        self.catalog, self.embeddings = catalog, embeddings
        self.authorization = authorization
        self.capabilities = {}
        for capability in capabilities:
            if (not isinstance(capability, TextQueryCapability)
                    or not isinstance(capability.binding, IndexBinding)
                    or capability.binding in self.capabilities
                    or capability.media_type != 'image'
                    or not isinstance(capability.filter_template_ref, str)
                    or not capability.filter_template_ref.strip()
                    or type(capability.max_batch_size) is not int
                    or not 1 <= capability.max_batch_size <= 100):
                raise ValueError('invalid text query capability registration')
            self.capabilities[capability.binding] = capability

    def prepare(self, actor, task_id, *, expected_scope: RetrievalScope,
                preview_digest: str, branch_id: str, binding_id: str,
                epoch: int, batch_size: int, authorization_version: str) -> PreparedQuery:
        task = self.tasks.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
        validate_scope(actor, task, expected_scope)
        if (type(epoch) is not int or epoch < 1 or type(batch_size) is not int
                or not 1 <= batch_size <= 100
                or not isinstance(authorization_version, str) or not authorization_version.strip()):
            raise ServiceError('INVALID_RETRIEVAL_PLAN', 'Invalid preparation bounds', 422)
        query = json.loads(task.confirmed_proposal_json)['query_spec']
        preview = compile_query_branches(query)
        if preview_digest != preview['preview_digest']:
            raise ServiceError('PLAN_DIGEST_MISMATCH', 'Logical preview changed', 409)
        branch = next((b for b in preview['branches'] if b['branch_id'] == branch_id), None)
        if branch is None:
            raise ServiceError('INVALID_RETRIEVAL_PLAN', 'Unknown query branch', 422)
        if query.get('source_constraints'):
            raise ServiceError('SOURCE_CONSTRAINT_UNSUPPORTED',
                               'Source constraints need a verified filter compiler', 422)
        context = AccessContext(actor, task_id, 'search', authorization_version)
        libraries = self.catalog.find_libraries(context, branch['query_text'], query['media_type'])
        matches = []
        for library in libraries:
            self.authorization.require_access(context, 'library:' + library.library_id, 'discover')
            if library.media_type != query['media_type']:
                continue
            for binding in library.bindings:
                if binding.library_id != library.library_id:
                    raise ServiceError('INDEX_BINDING_MISMATCH', 'Invalid catalog binding', 409)
                if binding.binding_id == binding_id:
                    matches.append(binding)
        if len(matches) != 1:
            raise ServiceError('INDEX_BINDING_MISMATCH', 'Selected binding unavailable or ambiguous', 409)
        binding = matches[0]
        capability = self.capabilities.get(binding)
        if capability is None or capability.media_type != query['media_type']:
            raise ServiceError('QUERY_UNSUPPORTED', 'No verified text query capability', 422)
        if batch_size > capability.max_batch_size:
            raise ServiceError('INPUT_LIMIT', 'Batch exceeds binding limit', 422)
        self.authorization.require_access(context, 'library:' + binding.library_id, 'search')
        vector = self.embeddings.encode_query(context, branch['query_text'], binding)
        if not isinstance(vector, QueryVector):
            raise ServiceError('ENCODER_MISMATCH', 'Invalid encoder response', 422)
        plan_id = 'plan_' + digest({
            'scope': asdict(expected_scope), 'branch': branch_id,
            'binding': asdict(binding), 'preview': preview_digest,
            'epoch': epoch, 'batch_size': batch_size,
            'vector': asdict(vector), 'filter': capability.filter_template_ref,
        })[7:]
        plan = RetrievalPlan(expected_scope, epoch, SearchRequest(
            plan_id, epoch, binding, vector, capability.filter_template_ref, batch_size))
        validate_plan(plan)
        current = self.tasks.get(actor, task_id)
        self.projects.require_access(actor, current.project_id, current.dimension_id, 'read')
        validate_scope(actor, current, expected_scope)
        self.authorization.require_access(context, 'library:' + binding.library_id, 'search')
        return PreparedQuery(plan, branch_id, branch['query_fingerprint'], preview_digest)
