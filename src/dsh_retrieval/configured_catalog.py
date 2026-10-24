"""Compose saved personal project settings into authorized catalogue discovery."""
import re
import unicodedata

from .errors import ServiceError
from .integration_models import AccessContext
from .physical_query_preparation import PhysicalQueryPreparer
from .query_routing import QueryRoutingService
from .query_spec import digest
from .retrieval_contracts import RetrievalScope, validate_scope
from .text_catalog_config import CatalogConfigScope


def matches_topic(topic, entry):
    """Literal recall only, not semantic reasoning or source/negative filtering.

    Latin aliases require word boundaries (cat must not match education).
    Chinese aliases allow substring matching; no fuzzy one-character expansion.
    """
    normalize = lambda text: unicodedata.normalize('NFKC', text).casefold().strip()
    query = normalize(topic)
    def contains(haystack, needle):
        if re.fullmatch(r'[a-z0-9 _-]+', needle):
            return re.search(r'(?<!\w)' + re.escape(needle) + r'(?!\w)', haystack) is not None
        return needle in haystack
    if contains(normalize(entry['description']), query):
        return True
    return any(contains(query, normalize(term))
               for term in [entry['library_id'], *entry['aliases']])


class ConfiguredCatalog:
    """Backend-only CatalogRepository composition, no authority from client scope."""
    def __init__(self, *, tasks, projects, configs, authorization):
        self.tasks, self.projects = tasks, projects
        self.configs, self.authorization = configs, authorization

    def _task(self, context):
        if (type(context) is not AccessContext or context.purpose != 'search'
                or not isinstance(context.authorization_version, str)
                or not context.authorization_version.strip()):
            raise ServiceError('INVALID_RETRIEVAL_PLAN', 'Search context required', 422)
        task = self.tasks.get(context.actor, context.task_id)
        self.projects.require_access(context.actor, task.project_id, task.dimension_id, 'read')
        validate_scope(context.actor, task, RetrievalScope.from_task(task))
        return task

    def _scope(self, context):
        task = self._task(context)
        return task, CatalogConfigScope(task.tenant_id, task.owner_user_id, task.project_id)

    def _saved(self, scope):
        saved = self.configs.read(scope)
        if saved is None:
            raise ServiceError('CATALOG_SELECTION_REQUIRED', 'Save an index configuration first', 409)
        return saved

    def _authorize(self, context, library_id):
        for action in ('discover', 'search'):
            self.authorization.require_access(context, 'library:' + library_id, action)

    def configuration_digest(self, context):
        task, scope = self._scope(context)
        saved = self._saved(scope)
        validate_scope(context.actor, self._task(context), RetrievalScope.from_task(task))
        return digest({'version': saved['version'], 'proposal_digest': saved['proposal_digest'],
                       'catalog_digest': saved['catalog_digest']})

    def find_libraries(self, context, topic, media_type):
        if (not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 4000
                or media_type not in ('image', 'video', 'text')):
            raise ServiceError('INVALID_RETRIEVAL_PLAN', 'Bounded query and media type required', 422)
        task, scope = self._scope(context)
        before = self._saved(scope)
        libraries = self.configs.configured_libraries(scope)
        entries = {entry['library_id']: entry for entry in before['entries']}
        result = []
        for library in libraries:
            try:
                self._authorize(context, library.library_id)
            except ServiceError as error:
                if error.code == 'ACCESS_DENIED':
                    continue
                raise
            entry = entries.get(library.library_id)
            if entry is None:
                raise ServiceError('CATALOG_CHANGED', 'Configured libraries changed', 409)
            if library.media_type == media_type and matches_topic(topic, entry):
                result.append(library)
        if self._saved(scope) != before:
            raise ServiceError('CATALOG_CHANGED', 'Configuration changed during discovery', 409)
        validate_scope(context.actor, self._task(context), RetrievalScope.from_task(task))
        for library in result:
            self._authorize(context, library.library_id)
        return tuple(result)


def build_configured_routing(*, tasks, projects, configs, authorization, embeddings, capabilities):
    """Explicit trusted assembly; no fake encoder or capability inferred from dimensions."""
    catalog = ConfiguredCatalog(tasks=tasks, projects=projects, configs=configs, authorization=authorization)
    return QueryRoutingService(PhysicalQueryPreparer(tasks=tasks, projects=projects,
        catalog=catalog, authorization=authorization, embeddings=embeddings, capabilities=capabilities))
