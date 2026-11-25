"""Read-only, deliberately small HTTP projection of the approved page ledger."""

from .adapters.task_storage.plan_sets import SQLitePlanSetApprovalRepository
from .errors import ServiceError
from .local_catalog import LocalCatalogSource
from .text_catalog_config import CatalogConfigScope
from .conversation_timing import timed_call


def build_local_demo_progress_repository(path, *, projects, source_path):
    """Opt-in demo authority. The local server grants only demo/default tasks."""
    source = LocalCatalogSource(source_path)

    class Authorization:
        def require_access(self, context, resource_ref, action):
            if (context.purpose != 'retrieval' or action != 'search'
                    or not isinstance(resource_ref, str)
                    or not resource_ref.startswith('library:')
                    or not source.authorize(CatalogConfigScope(
                        context.actor.tenant_id, context.actor.user_id, 'demo'),
                        'search', resource_ref[len('library:'):])):
                raise ServiceError('ACCESS_DENIED', 'Library search access denied', 403)

    class DemoProjects:
        def require_access(self, actor, project_id, dimension_id, action):
            if (project_id, dimension_id) != ('demo', 'default'):
                raise ServiceError('PROJECT_ACCESS_DENIED', 'Local demo project required', 403)
            projects.require_access(actor, project_id, dimension_id, action)

    return SQLitePlanSetApprovalRepository(path, projects=DemoProjects(), authorization=Authorization())


def read_retrieval_progress(repository, actor, task_id, *, browsing=False):
    return timed_call('retrieval_progress_read', _read_retrieval_progress,
                      repository, actor, task_id, browsing=browsing)


def _read_retrieval_progress(repository, actor, task_id, *, browsing=False):
    snapshot_reader = getattr(repository, 'get_progress_snapshot', None)
    if callable(snapshot_reader):
        approved, rows = snapshot_reader(actor, task_id, browsing=browsing)
    else:
        # Other storage adapters retain their existing two-read contract.
        reader = repository.get_browse_current if browsing else repository.get_current
        approved = reader(actor, task_id)
        rows = (None if approved is None else
                repository.get_progress(actor, approved, reading=True) if browsing else
                repository.get_progress(actor, approved))
    if approved is None:
        return {'schema_version': 'retrieval-progress@1', 'task_id': task_id,
                'status': 'unapproved', 'members': [], 'pagination_stopped': False}
    if (not isinstance(rows, tuple) or len(rows) != len(approved.members)
            or any(row.plan_id != member.plan.request.plan_id
                   for row, member in zip(rows, approved.members))):
        raise ServiceError('STORAGE_CORRUPT', 'Invalid approved retrieval progress', 500)
    members = []
    for row, member in zip(rows, approved.members):
        if row.operation_id is None:
            status = 'not_started'
        elif row.page is None:
            status = 'pending'
        elif row.page.next_cursor is not None:
            status = 'more_available'
        else:
            status = 'stopped'
        binding = member.plan.request.binding
        members.append({'plan_id': row.plan_id, 'branch_id': member.branch_id,
                        'library_id': binding.library_id, 'binding_id': binding.binding_id,
                        'index_version': binding.index_version,
                        'committed_pages': row.committed_pages, 'status': status,
                        'stop_reason': row.page.stop_reason if row.page else None})
    return {'schema_version': 'retrieval-progress@1', 'task_id': task_id,
            'status': 'approved', 'set_digest': approved.proposal_digest,
            'epoch': approved.epoch, 'task_version': approved.scope.task_version,
            'revision': approved.scope.revision, 'generation': approved.scope.generation,
            'members': members,
            'pagination_stopped': all(member['status'] == 'stopped' for member in members)}
