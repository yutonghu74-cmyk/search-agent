"""Authorized index choices and whole-set preparation; no approval or search.

CatalogRepository must authorize before matching descriptions. This layer
rechecks discover/search permissions and exposes only registered capabilities.
"""
from dataclasses import asdict, dataclass
import json

from .errors import ServiceError
from .integration_models import AccessContext, IndexBinding, Library
from .multi_branch_contracts import RetrievalPlanSet, validate_plan_set
from .physical_query_preparation import PhysicalQueryPreparer, TextQueryCapability
from .query_branches import compile_query_branches
from .query_spec import digest
from .retrieval_contracts import RetrievalScope, validate_scope


@dataclass(frozen=True, slots=True)
class QueryRoute:
    branch_id: str
    query_text: str
    options: tuple[TextQueryCapability, ...]


@dataclass(frozen=True, slots=True)
class QueryRoutingPreview:
    scope: RetrievalScope
    logical_preview_digest: str
    authorization_version: str
    routes: tuple[QueryRoute, ...]
    catalog_config_digest: str | None = None

    @property
    def routing_digest(self):
        return digest({'schema_version': 'query-routing@2', **asdict(self)})

    def to_view(self):
        return {
            'task_id': self.scope.task_id, 'revision': self.scope.revision,
            'task_version': self.scope.task_version, 'generation': self.scope.generation,
            'routing_digest': self.routing_digest, 'status': 'unapproved',
            'catalog_config_digest': self.catalog_config_digest,
            'routes': [{'branch_id': route.branch_id, 'query_text': route.query_text,
                'options': [{**asdict(option.binding), 'max_batch_size': option.max_batch_size}
                            for option in route.options],
                'availability': 'available' if route.options else 'no_available_index'}
                       for route in self.routes],
        }


@dataclass(frozen=True, slots=True)
class QueryRouteChoice:
    branch_id: str
    binding_id: str
    batch_size: int


class QueryRoutingService:
    """Backend-only composition using the existing physical preparation port."""

    def __init__(self, preparer: PhysicalQueryPreparer):
        if type(preparer) is not PhysicalQueryPreparer:
            raise ValueError('trusted physical preparer required')
        self.preparer = preparer

    def _task(self, actor, task_id, scope):
        task = self.preparer.tasks.get(actor, task_id)
        self.preparer.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
        validate_scope(actor, task, scope)
        return task

    def _authorize(self, context, library_id):
        for action in ('discover', 'search'):
            self.preparer.authorization.require_access(context, 'library:' + library_id, action)

    def _configuration_digest(self, context):
        reader = getattr(self.preparer.catalog, 'configuration_digest', None)
        if reader is None:
            return None
        value = reader(context)
        if not isinstance(value, str) or not value.startswith('sha256:') or len(value) != 71:
            raise ServiceError('CATALOG_INVALID', 'Invalid catalog configuration digest', 502)
        return value

    def discover(self, actor, task_id, *, expected_scope, authorization_version,
                 branch_ids=None):
        task = self._task(actor, task_id, expected_scope)
        if not isinstance(authorization_version, str) or not authorization_version.strip():
            raise ServiceError('INVALID_RETRIEVAL_PLAN', 'Authorization version required', 422)
        query = json.loads(task.confirmed_proposal_json)['query_spec']
        logical = compile_query_branches(query)
        if query['media_type'] != 'image':
            raise ServiceError('QUERY_UNSUPPORTED', 'Only registered image text queries supported', 422)
        if query.get('source_constraints'):
            raise ServiceError('SOURCE_CONSTRAINT_UNSUPPORTED', 'Verified source filter compiler required', 422)
        all_branches = {row['branch_id']: row for row in logical['branches']}
        if branch_ids is None:
            branch_ids = tuple(all_branches)
        if (type(branch_ids) is not tuple or not 1 <= len(branch_ids) <= 64
                or any(not isinstance(value, str) for value in branch_ids)
                or len(set(branch_ids)) != len(branch_ids)
                or any(value not in all_branches for value in branch_ids)):
            raise ServiceError('INVALID_ROUTE_SELECTION', 'Select 1-64 distinct known branches', 422)
        original = logical['branches'][0]['branch_id']
        if original not in branch_ids:
            raise ServiceError('ORIGINAL_QUERY_REQUIRED', 'Original query branch must be retained', 422)
        context = AccessContext(actor, task_id, 'search', authorization_version)
        config_digest = self._configuration_digest(context)
        routes, bindings = [], {}
        for branch_id in branch_ids:
            branch = all_branches[branch_id]
            libraries = self.preparer.catalog.find_libraries(context, branch['query_text'], query['media_type'])
            if type(libraries) is not tuple or len(libraries) > 256:
                raise ServiceError('CATALOG_INVALID', 'Bounded catalog response required', 502)
            options, seen_libraries = [], set()
            for library in libraries:
                if type(library) is not Library or not isinstance(library.library_id, str) or not library.library_id:
                    raise ServiceError('CATALOG_INVALID', 'Invalid catalog entry', 502)
                try:
                    self._authorize(context, library.library_id)
                except ServiceError as error:
                    if error.code == 'ACCESS_DENIED':
                        continue
                    raise
                if library.library_id in seen_libraries:
                    raise ServiceError('INDEX_BINDING_MISMATCH', 'Ambiguous catalog library', 409)
                seen_libraries.add(library.library_id)
                if library.media_type != query['media_type']:
                    continue
                if type(library.bindings) is not tuple or len(library.bindings) > 64:
                    raise ServiceError('CATALOG_INVALID', 'Bounded index bindings required', 502)
                seen_bindings = set()
                for binding in library.bindings:
                    if (type(binding) is not IndexBinding or binding.library_id != library.library_id
                            or any(not isinstance(value, str) or not value.strip() for value in
                                   (binding.binding_id, binding.index_version, binding.encoder_ref, binding.metric))
                            or type(binding.vector_dimension) is not int or binding.vector_dimension < 1
                            or binding.binding_id in seen_bindings
                            or binding.binding_id in bindings and bindings[binding.binding_id] != binding):
                        raise ServiceError('INDEX_BINDING_MISMATCH', 'Ambiguous or inconsistent index binding', 409)
                    seen_bindings.add(binding.binding_id)
                    bindings[binding.binding_id] = binding
                    capability = self.preparer.capabilities.get(binding)
                    if capability is not None and capability.media_type == query['media_type']:
                        options.append(capability)
            if len(options) > 64:
                raise ServiceError('INPUT_LIMIT', 'At most 64 compatible indexes per branch', 422)
            # Preserve catalogue order: lexical/alias matches are recommendations,
            # followed by the remaining authorized preconfigured sources.
            routes.append(QueryRoute(branch_id, branch['query_text'], tuple(options)))
        self._task(actor, task_id, expected_scope)
        for library_id in sorted({option.binding.library_id for route in routes for option in route.options}):
            self._authorize(context, library_id)
        if self._configuration_digest(context) != config_digest:
            raise ServiceError('CATALOG_CHANGED', 'Configuration changed during discovery', 409)
        return QueryRoutingPreview(expected_scope, logical['preview_digest'], authorization_version,
                                   tuple(routes), config_digest)

    def prepare(self, actor, task_id, *, preview, choices, epoch):
        if (type(preview) is not QueryRoutingPreview or type(choices) is not tuple
                or not 1 <= len(choices) <= 64 or type(epoch) is not int or epoch < 1):
            raise ServiceError('INVALID_ROUTE_SELECTION', 'Bound routing preview and 1-64 choices required', 422)
        current = self.discover(actor, task_id, expected_scope=preview.scope,
            authorization_version=preview.authorization_version,
            branch_ids=tuple(route.branch_id for route in preview.routes))
        if current.routing_digest != preview.routing_digest:
            raise ServiceError('ROUTING_CHANGED', 'Available indexes or capabilities changed', 409)
        available = {(route.branch_id, option.binding.binding_id): option
                     for route in current.routes for option in route.options}
        seen, selected = set(), []
        for choice in choices:
            if (type(choice) is not QueryRouteChoice or not isinstance(choice.branch_id, str)
                    or not isinstance(choice.binding_id, str) or type(choice.batch_size) is not int):
                raise ServiceError('INVALID_ROUTE_SELECTION', 'Invalid branch/index choice', 422)
            key = choice.branch_id, choice.binding_id
            if key in seen or key not in available:
                raise ServiceError('INVALID_ROUTE_SELECTION', 'Duplicate or unavailable branch/index', 422)
            option = available[key]
            if not 1 <= choice.batch_size <= option.max_batch_size:
                raise ServiceError('INPUT_LIMIT', 'Batch exceeds selected index limit', 422)
            seen.add(key)
            selected.append((choice, option))
        task = self._task(actor, task_id, preview.scope)
        original = compile_query_branches(json.loads(task.confirmed_proposal_json)['query_spec'])['branches'][0]
        if not any(choice.branch_id == original['branch_id'] for choice in choices):
            raise ServiceError('ORIGINAL_QUERY_REQUIRED', 'Original query branch must be retained', 422)
        members = []
        for choice, option in selected:
            member = self.preparer.prepare(actor, task_id, expected_scope=preview.scope,
                preview_digest=preview.logical_preview_digest, branch_id=choice.branch_id,
                binding_id=choice.binding_id, epoch=epoch, batch_size=choice.batch_size,
                authorization_version=preview.authorization_version)
            if (member.plan.request.binding != option.binding
                    or member.plan.request.filter_template_ref != option.filter_template_ref):
                raise ServiceError('ROUTING_CHANGED', 'Selected index changed during preparation', 409)
            members.append(member)
        final = self.discover(actor, task_id, expected_scope=preview.scope,
            authorization_version=preview.authorization_version,
            branch_ids=tuple(route.branch_id for route in preview.routes))
        if final.routing_digest != preview.routing_digest:
            raise ServiceError('ROUTING_CHANGED', 'Available indexes changed during preparation', 409)
        plan_set = RetrievalPlanSet(preview.scope, epoch, tuple(members))
        validate_plan_set(actor, self._task(actor, task_id, preview.scope), plan_set)
        return plan_set
