"""Explicit local deployment of the platform role over existing business ports."""
from contextlib import nullcontext
from dataclasses import asdict
import json

from .adapters.harness.composition import build_planning_runner
from .coordinator_jobs import PlatformCoordinatorJobs
from .errors import ServiceError
from .platform_coordination import CoordinationCapability, CoordinationPolicy, CoordinationRegistry
from .query_spec import digest
from .retrieval_contracts import RetrievalScope


def install_local_coordinator(deployment, *, cleaning=None, adapters_provider=None):
    config = deployment.configuration.get('coordinator')
    if config is None or config == {'enabled': False}: return None
    fields = {'enabled', 'release_ref', 'model_calls', 'generation_model_calls', 'max_seconds', 'max_steps'}
    if (type(config) is not dict or set(config) != fields or config['enabled'] is not True
            or config['release_ref'] not in deployment.configuration['harness']['releases']):
        raise ValueError('explicit installed coordinator publication and bounded policy required')
    policy = CoordinationPolicy(**{key: config[key] for key in fields - {'enabled', 'release_ref'}})
    service, engine = deployment.service, deployment.search
    registry = CoordinationRegistry()

    def writable(actor, task):
        try:
            service.projects.require_access(actor, task.project_id, task.dimension_id, 'create')
            return True
        except ServiceError as error:
            if error.status != 403: raise
            return False

    def retrieval_context(actor, task):
        service.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
        proposal = json.loads(task.confirmed_proposal_json)
        delegated = 'retrieval_execution' in proposal['query_spec']
        job = deployment.confirmed_terms.current_job(actor, task.task_id) if delegated else None
        state = job['status'] if job else 'not_dispatched' if delegated else 'legacy_workflow'
        actions = ['inspect']
        if delegated and writable(actor, task) and state not in ('unknown', 'failed', 'cancelled'):
            deployment.confirmed_terms.require(actor, task.project_id, proposal, task=task)
            actions.append('ensure')
        return {'actions': actions, 'state': state, 'job_ref': job['job_ref'] if job else None,
            'owner_gate': 'candidates' if state == 'waiting_confirmation' else None,
            'binding_digest': digest({'scope': asdict(RetrievalScope.from_task(task)),
                                      'execution': proposal['query_spec'].get('retrieval_execution')})}

    def retrieval_invoke(actor, task, action, key, fresh):
        if action == 'inspect': return {'status': fresh['state'], 'job_ref': fresh['job_ref']}
        if fresh['job_ref'] and fresh['state'] != 'not_dispatched':
            return {'status': fresh['state'], 'job_ref': fresh['job_ref'], 'replayed': True}
        result = deployment.confirmed_terms.enqueue(actor, task.task_id, key=key)
        deployment.worker.notify()
        return result

    registry.register(CoordinationCapability('retrieval', 'confirmed-terms-executor@1', 'agent',
        retrieval_context, retrieval_invoke))

    def cleaning_context(actor, task):
        service.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
        manifest = engine.repository.get_current_candidate_manifest(actor, task.task_id)
        if manifest is not None and (manifest.proposal.scope.revision, manifest.proposal.scope.generation) != (
                task.confirmed_revision, task.run_generation):
            manifest = None
        # A provider that launches another model needs a separately admitted
        # child budget; this first slice routes it to the original owner entry.
        model_plan = callable(getattr(getattr(cleaning, 'registry_provider', None), 'generate', None))
        operation = task.active_cleaning_operation_id
        state = ('unavailable' if cleaning is None else
                 cleaning.repository.cleaning_dispatch_state(actor, task.task_id, operation)
                 if operation else 'not_prepared')
        actions = ['inspect']
        if (cleaning is not None and manifest is not None and not model_plan and writable(actor, task)
                and state == 'not_prepared' and manifest.proposal.scope.task_version == task.task_version):
            actions.append('prepare')
        return {'actions': actions, 'available': cleaning is not None, 'state': state, 'operation_id': operation,
            'manifest_ref': manifest.manifest_ref if manifest else None,
            'owner_gate': 'candidates' if manifest is None else 'prepare_cleaning' if model_plan else 'cleaning',
            'planning_model_requires_owner_entry': model_plan,
            'binding_digest': digest({'scope': asdict(RetrievalScope.from_task(task)),
                'manifest': manifest.manifest_ref if manifest else None, 'model_plan': model_plan,
                'operation_id': operation, 'state': state})}

    def cleaning_invoke(actor, task, action, key, fresh):
        if action == 'inspect':
            return {'status': ('waiting_confirmation' if fresh['manifest_ref'] else 'waiting_input')
                    if fresh['state'] == 'not_prepared' else fresh['state'],
                    'owner_gate': fresh['owner_gate'],
                    'available': fresh['available']}
        return {**cleaning.prepare(actor, task.task_id, fresh['manifest_ref']), 'status': 'waiting_confirmation'}

    registry.register(CoordinationCapability('cleaning', 'confirmed-cleaning-module@1', 'business_module',
        cleaning_context, cleaning_invoke))
    runtime = deployment.runtime
    def profile(actor, ref):
        with runtime.settings.scope(actor.tenant_id, actor.user_id) if runtime.settings else nullcontext():
            return runtime.select(ref, 'llm')
    def model_binding(actor, ref):
        p = profile(actor, ref)
        return digest({'profile': p.profile_id, 'model': p.config.model, 'url': p.config.base_url,
                       'credential_binding': digest(p.config.api_key)})
    def environment(actor, ref):
        p = profile(actor, ref)
        return {'RETRIEVAL_MODEL_KEY': p.config.api_key, 'RETRIEVAL_MODEL_URL': p.config.base_url,
                'RETRIEVAL_MODEL_ID': p.config.model, 'RETRIEVAL_MAX_TOKENS': str(getattr(p.config, 'max_tokens', None) or 8000)}
    routes = {'schema_version': 'harness-routes@2',
              'releases': deployment.configuration['harness']['releases'],
              'roles': {'coordinator': config['release_ref']}}
    def runner(actor, domain, journal):
        return build_planning_runner(routes, authority=None,
            model_environment=lambda ref: environment(actor, ref), model_binding=lambda ref: model_binding(actor, ref),
            domain=domain, journal=journal, actor=actor,
            installed_adapters=adapters_provider(actor) if adapters_provider is not None else None)
    coordinator = PlatformCoordinatorJobs(service, registry, runner_factory=runner,
                                           model_binding=model_binding, policy=policy)
    deployment.jobs.registry.register(coordinator)
    deployment.jobs.links.register_workflow(coordinator.contract_ref, coordinator.links)
    deployment.coordinator = coordinator
    deployment.window.coordinator = coordinator
    return coordinator
