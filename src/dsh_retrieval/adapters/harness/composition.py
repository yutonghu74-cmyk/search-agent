"""Explicit planning-runtime composition. Configuration cannot select code imports."""
from pathlib import Path

from ...errors import invalid
from ...harness.contracts import AdapterRegistry, RuntimeRelease
from ...harness.agents import AgentDomain, build_registered_agent_runner
from ...agent_contracts import AgentStageTask
from ...harness.roles import DEFAULT_ROLES
from ...harness.stage_tools import stage_catalog
from .dsh import DshAdapter
from .dsh_releases import LEGACY_DSH, CANDIDATE_DSH

SDK_RELEASES = {r.release_id: r for r in (LEGACY_DSH, CANDIDATE_DSH)}


def build_planning_runner(config, *, authority, model_environment, model_binding, journal=None, actor=None,
                          installed_adapters=None, domain=None):
    """Build only explicit routes; live business execution remains gated until T05/T06."""
    if (type(config) is not dict or set(config) != {'schema_version', 'releases', 'roles'}
            or config['schema_version'] not in ('harness-routes@1', 'harness-routes@2')
            or type(config['releases']) is not dict or not 1 <= len(config['releases']) <= 8):
        raise invalid('invalid Harness route configuration')
    if (journal is None) != (actor is None):
        raise invalid('durable planning requires both journal and backend actor')
    role_definitions = DEFAULT_ROLES if domain is None else domain.roles
    selected = config['roles']
    if (type(selected) is not dict or not selected or not set(selected) <= set(role_definitions)
            or config['schema_version'] == 'harness-routes@1' and set(selected) != set(DEFAULT_ROLES)
            or any(not isinstance(ref, str) or ref not in config['releases'] for ref in selected.values())):
        raise invalid('explicit installed role routes required')
    registry, releases = AdapterRegistry(), {}
    installed_adapters = {} if installed_adapters is None else dict(installed_adapters)
    for ref, item in config['releases'].items():
        if type(item) is not dict:
            raise invalid('invalid runtime publication')
        provider = item.get('provider', 'dsh')
        if config['schema_version'] == 'harness-routes@1' and 'provider' in item:
            raise invalid('provider routing requires harness-routes@2')
        if provider == 'registered':
            if set(item) != {'provider', 'adapter_id', 'artifact_digest'}:
                raise invalid('registered implementation accepts identity/digest only')
            adapter = installed_adapters.get(item['adapter_id'])
            if adapter is None:
                raise invalid('Agent implementation must be installed by the backend')
            registry.register(item['adapter_id'], adapter)
            releases[ref] = RuntimeRelease(ref, item['adapter_id'], item['artifact_digest'])
            continue
        if provider != 'dsh' or 'sdk_release' not in item:
            raise invalid('explicit SDK publication required')
        sdk_release = SDK_RELEASES.get(item['sdk_release'])
        if sdk_release is None: raise invalid('unregistered SDK publication')
        required = {'adapter_id', 'sdk_release', 'python', 'home'}
        if sdk_release.runtime_mode == 'npm': required |= {'dsh_bin', 'node', 'npm_lock'}
        if 'provider' in item: required.add('provider')
        if set(item) != required:
            raise invalid('runtime installation paths do not match release type')
        paths = {k: item[k] for k in required - {'adapter_id', 'sdk_release', 'provider'}}
        if any(not isinstance(p, str) or not Path(p).is_absolute() for p in paths.values()):
            raise invalid('runtime installation paths must be absolute')
        adapter = DshAdapter(**paths, model_environment=model_environment,
                             release=sdk_release, adapter_id=item['adapter_id'])
        registry.register(item['adapter_id'], adapter)
        releases[ref] = RuntimeRelease(ref, item['adapter_id'], adapter.artifact_digest)
    if domain is None:
        catalog, guard = stage_catalog(authority)
        domain = AgentDomain('retrieval-cleaning@1', 'retrieval-cleaning-stage@1',
            stage_type=AgentStageTask, roles=(DEFAULT_ROLES[role] for role in selected), catalog=catalog, guard=guard)
    return build_registered_agent_runner(domain=domain, routes=config['roles'],
        releases=releases, adapters=registry, model_binding=model_binding, journal=journal, actor=actor)


def build_agent_planning_service(task_service, config, *, authority, context_provider,
                                 model_environment, model_binding, journal=None, review_store=None,
                                 lease_seconds=30, adapters_provider=None, project_routes=None, agent_management=None):
    """Opt-in API composition; actor-scoped callbacks, no ambient HTTP identity.

    authority(actor, invocation) must return current authorized StageAuthority.
    Model callbacks resolve immutable, actor-authorized profile snapshots.
    No SDK run or business execution is started merely by constructing this service.
    """
    if agent_management is not None:
        if project_routes is not None:
            raise invalid('choose managed or custom project routes')
        project_routes = agent_management.project_routes
    if (journal is None) != (review_store is None):
        raise invalid('provide both persistence ports or neither')
    if journal is None:
        from ..task_storage.harness import SQLiteHarnessJournal
        from ..task_storage.agent_stages import AgentStageStore
        journal = SQLiteHarnessJournal(task_service.repository, task_service.projects)
        review_store = AgentStageStore(journal)
    from ...harness.stage_service import AgentPlanningService
    def runner_factory(actor):
        runner = build_planning_runner(config, authority=lambda inv: authority(actor, inv),
            model_environment=lambda ref: model_environment(actor, ref),
            model_binding=lambda ref: model_binding(actor, ref), journal=journal, actor=actor,
            installed_adapters=adapters_provider(actor) if adapters_provider is not None else None)
        if agent_management is not None:
            agent_management.validate_runner(runner)
        return runner
    return AgentPlanningService(task_service, journal=journal, review_store=review_store,
        runner_factory=runner_factory, context_provider=context_provider, lease_seconds=lease_seconds,
        project_routes=project_routes)


def build_retrieval_agent_jobs(*, planning, compiler, executor, materializer, manifests, links=None,
                             batch_size=25, max_pages=1):
    """Opt-in job facade; caller explicitly manages AgentJobDispatcher lifecycle."""
    if links is None:
        from ..task_storage.agent_jobs import SQLiteAgentJobLinks
        links = SQLiteAgentJobLinks(stages=planning.store, retrieval=executor.retrieval, manifests=manifests)
    from ...agent_jobs import AgentJobRegistry, AgentJobService
    from ...retrieval_agent_jobs import RetrievalAgentJobs
    workflow = RetrievalAgentJobs(planning=planning, compiler=compiler, executor=executor,
        materializer=materializer, links=links, batch_size=batch_size, max_pages=max_pages)
    registry = AgentJobRegistry(); registry.register(workflow)
    if max_pages > 1:
        registry.register(RetrievalAgentJobs(planning=planning, compiler=compiler, executor=executor,
            materializer=materializer, links=links, batch_size=batch_size), default=False)
    return AgentJobService(registry=registry, links=links)


def enable_retrieval_agent_window(task_service, jobs, dispatcher, **configuration):
    """Deployment opt-in only. Existing projects/tasks stay on their own contracts."""
    from ...agent_retrieval_window import RetrievalAgentWindow
    if task_service.agent_execution is not None:
        raise ValueError('Agent admission is already configured for this service')
    window = RetrievalAgentWindow(task_service, jobs, dispatcher, **configuration)
    task_service.agent_execution = window
    return window
