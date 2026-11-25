"""Public planning-stage application service. No client-selected SDK or media paths."""
from dataclasses import asdict, replace
import json
import math
import threading
import uuid

from ..agent_contracts import AgentStageTask, _hash
from ..errors import ServiceError, invalid
from ..processing_requirements import identifier, normalize_agent_requirements, positive
from ..query_spec import digest
from ..retrieval_contracts import RetrievalScope
from ..service import require_key
from .codec import stage_from
from .contracts import RoleDefinition, RuntimeRelease
from .durable import DurableHarnessRunner
from .runner import load_role_routes


def _version(value):
    return type(value) is int and 0 < value <= 9223372036854775807


def review_bindings(review):
    role = dict(review['role']); role['tool_ids'] = tuple(role['tool_ids'])
    runtime = dict(review['runtime']); runtime['required_capabilities'] = frozenset(runtime['required_capabilities'])
    return RoleDefinition(**role), RuntimeRelease(**runtime)


class AgentPlanningService:
    def __init__(self, task_service, *, journal, review_store, runner_factory, context_provider, lease_seconds=30,
                 project_routes=None):
        if type(lease_seconds) not in (int,float) or not math.isfinite(lease_seconds) or not 0 < lease_seconds <= 300:
            raise ValueError('invalid planning lease duration')
        if review_store.storage_identity != journal.storage_identity:
            raise ValueError('stage reviews and runtime journal must share an atomic ledger')
        if project_routes is not None and not callable(project_routes):
            raise ValueError('project routes must be a backend resolver')
        self.lease_seconds = lease_seconds
        self.journal, self.repository, self.projects = journal, task_service.repository, task_service.projects
        self.store = review_store
        self.runner_factory, self.context_provider = runner_factory, context_provider
        self.project_routes = project_routes
        self._runners, self._lock = {}, threading.RLock()

    def _runner(self, actor):
        key = (actor.tenant_id, actor.user_id)
        with self._lock:
            if key not in self._runners:
                runner = self.runner_factory(actor)
                if (not isinstance(runner, DurableHarnessRunner) or runner.actor != actor
                        or runner.journal.storage_identity != self.journal.storage_identity):
                    raise ServiceError('HARNESS_DURABILITY_REQUIRED', 'Stage API requires the same durable task journal', 503)
                self._runners[key] = runner
            return self._runners[key]

    def _routes(self, actor, task, runner):
        if self.project_routes is None:
            return dict(runner.routes)
        # This callback uses verified backend identity/project, never HTTP route claims.
        selected = self.project_routes(actor, task)
        if type(selected) is not dict or not set(selected) <= (set(runner.roles) & set(runner.routes)):
            raise ServiceError('AGENT_ROUTE_CONFIGURATION_INVALID', 'Project routes select an unavailable role', 503)
        if not selected:
            return {}
        return load_role_routes(selected, runner.releases, roles=selected)

    def capabilities(self, actor, task_id):
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
        runner = self._runner(actor)
        routes = self._routes(actor, task, runner)
        requirements = json.loads(task.confirmed_proposal_json)['query_spec'].get('agent_requirements')
        if requirements is not None and not requirements['processing']:
            routes.pop('cleaning', None)
        return {'schema_version': 'agent-capabilities@1', 'task_id': task_id,
            'requirements_confirmed': requirements is not None,
            'roles': [{'role': name, 'role_release_ref': runner.roles[name].release_ref,
                       'runtime_release_ref': release.release_ref,
                       'output_contract': runner.roles[name].output_contract,
                       'task_kind': 'plan', 'availability': 'configured_not_probed',
                       'execution_allowed': False} for name, release in sorted(routes.items())]}

    def _runtime(self, actor, task, role, runner):
        runtime = self._routes(actor, task, runner).get(role)
        if runtime is None or role not in runner.roles:
            raise ServiceError('AGENT_ROLE_UNAVAILABLE', 'Agent role is not enabled for this project', 409)
        return replace(runtime, required_capabilities=runtime.required_capabilities | {'model_call_admission'})

    def _context(self, actor, task, role):
        value = self.context_provider(actor, task, role)
        try:
            if type(value) is not dict: raise ValueError()
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
            if len(encoded.encode()) > 200000: raise ValueError()
        except (ValueError, TypeError, RecursionError):
            raise invalid('invalid authorized planning snapshot') from None
        return encoded

    @staticmethod
    def _view(row, review):
        scope = review['stage']['scope']
        return {'schema_version': 'agent-stage@1', 'stage_draft_id': row['draft_id'],
            'task_id': row['task_id'], 'role': review['stage']['role'], 'status': row['status'],
            'revision': scope['revision'], 'generation': scope['generation'],
            'expected_task_version': scope['task_version'], 'proposal_digest': row['proposal_digest'],
            'input_digest': review['stage']['input_digest'], 'model_profile_ref': review['model_profile_ref'],
            'runtime_release_ref': review['runtime']['release_ref'], 'role_release_ref': review['role']['release_ref'],
            'model_calls': review['model_calls'], 'max_seconds': review['max_seconds'],
            'execution_allowed': False}

    def prepare(self, actor, task_id, key, body):
        require_key(key)
        required = {'role', 'model_profile_ref', 'model_calls', 'expected_revision', 'expected_generation', 'expected_task_version'}
        if (type(body) is not dict or set(body) != required or body['role'] not in ('retrieval', 'cleaning')
                or not identifier(body['model_profile_ref'])
                or not positive(body['model_calls'])
                or any(not _version(body[k]) for k in ('expected_revision', 'expected_generation', 'expected_task_version'))):
            raise invalid('invalid planning-stage preparation')
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
        if (task.confirmed_revision, task.run_generation, task.task_version) != (
                body['expected_revision'], body['expected_generation'], body['expected_task_version']):
            raise ServiceError('REVISION_CONFLICT', 'Task changed before stage preparation', 409)
        fingerprint = digest(body)
        previous = self.store.prepared(actor, task_id, key, fingerprint)
        if previous:
            return self._view(*previous), True
        spec = json.loads(task.confirmed_proposal_json)['query_spec']
        if 'agent_requirements' not in spec:
            raise ServiceError('AGENT_REQUIREMENTS_REQUIRED', 'Confirmed agent requirements required; legacy tasks are not converted automatically', 409)
        requirements = normalize_agent_requirements(spec['agent_requirements'], spec)
        if body['role'] == 'cleaning' and not requirements['processing']:
            raise ServiceError('AGENT_ROLE_UNAVAILABLE', 'Task has no confirmed processing requirements', 409)
        if body['model_calls'] > requirements['autonomy']['max_calls']:
            raise ServiceError('AGENT_BUDGET_EXCEEDED', 'Planning allocation exceeds confirmed limit', 409)
        runner = self._runner(actor)
        runtime = self._runtime(actor, task, body['role'], runner)
        role = runner.roles[body['role']]
        context = self._context(actor, task, body['role'])
        scope = RetrievalScope.from_task(task)
        stage = AgentStageTask(scope, 'stageop_' + uuid.uuid4().hex, body['role'], digest(json.loads(context)),
            digest(requirements), scope.requirement_digest, role.release_ref,
            runner.model_binding(body['model_profile_ref']), 'model-planning@1')
        runtime_data = asdict(runtime); runtime_data['required_capabilities'] = sorted(runtime_data['required_capabilities'])
        review = {'schema_version': 'agent-stage-review@1', 'stage': asdict(stage), 'role': asdict(role),
                  'runtime': runtime_data, 'context_json': context, 'model_profile_ref': body['model_profile_ref'],
                  'task_kind': 'plan', 'model_calls': body['model_calls'],
                  'max_seconds': min(300, requirements['autonomy']['max_seconds'])}
        row, stored, replayed = self.store.save(actor, task_id, key, fingerprint, review)
        return self._view(row, stored), replayed

    def _preflight(self, actor, row, review):
        runner = self._runner(actor)
        role, runtime = review_bindings(review)
        stage = stage_from(review['stage'])
        task = self.repository.get(actor, row['task_id'])
        current_runtime = self._runtime(actor, task, stage.role, runner)
        if (runner.roles[stage.role].release_digest != role.release_digest
                or current_runtime.release_digest != runtime.release_digest
                or runner.model_binding(review['model_profile_ref']) != stage.model_binding_digest):
            raise ServiceError('AGENT_STAGE_CONFIGURATION_CHANGED', 'Reload the stage review after a role/runtime/model change', 409)
        if digest(json.loads(self._context(actor, task, stage.role))) != stage.input_digest:
            raise ServiceError('AGENT_INPUT_CHANGED', 'Authorized planning input changed; prepare a new review', 409)
        # Installation/capability probe is read-only, not an SDK planning run.
        runner.registry.resolve(runtime)
        return runner

    def act(self, actor, task_id, draft_id, operation, key, body):
        require_key(key)
        if (operation not in ('confirm', 'start', 'cancel') or type(body) is not dict
                or set(body) != {'action', 'proposal_digest', 'expected_task_version'}
                or body['action'] != operation or not _version(body['expected_task_version'])
                or not _hash(body['proposal_digest'])):
            raise invalid('explicit stage action, review digest and task version required')
        row, review = self.store.get(actor, task_id, draft_id, cleanup=operation == 'cancel')
        if body['proposal_digest'] != row['proposal_digest']:
            raise ServiceError('PLAN_DIGEST_MISMATCH', 'Displayed stage review changed', 409)
        if body['expected_task_version'] != review['stage']['scope']['task_version']:
            raise ServiceError('REVISION_CONFLICT', 'Stage task version changed', 409)
        runner = None
        if operation in ('confirm', 'start') and row['status'] != 'started':
            runner = self._preflight(actor, row, review)
        row, review, replayed = self.store.act(actor, task_id, draft_id, operation, key, body)
        if row['run_id']:
            handle = self.journal.handle(self.store.invocation(row, review))
            if operation == 'start':
                (runner or self._runner(actor)).dispatch(handle, lease_seconds=self.lease_seconds)
            elif operation == 'cancel':
                with self._lock:
                    existing = self._runners.get((actor.tenant_id, actor.user_id))
                if existing is not None: existing.cancel(handle)
        return self.get(actor, task_id, draft_id, cancelled=operation == 'cancel'), replayed

    def get(self, actor, task_id, draft_id, *, after_cursor=0, cancelled=False):
        row, review = self.store.get(actor, task_id, draft_id, cleanup=cancelled)
        view = self._view(row, review)
        if row['run_id'] and row['status'] != 'cancelled':
            handle = self.journal.handle(self.store.invocation(row, review))
            snapshot = self.journal.read(actor, handle, after_cursor=after_cursor)
            view.update(status=snapshot['status'], run_id=handle.run_id, attempt_id=handle.attempt_id,
                events=[asdict(e) for e in snapshot['events']], outputs=list(snapshot['outputs']),
                model_calls_reserved=snapshot['allocation'], model_calls_admitted=snapshot['admitted'])
        return view

    def list(self, actor, task_id, *, limit=32):
        if type(limit) is not int or not 1<=limit<=100: raise invalid('invalid stage-list limit')
        task=self.repository.get(actor,task_id)
        self.projects.require_access(actor,task.project_id,task.dimension_id,'read')
        return {'schema_version':'agent-stage-list@1','task_id':task_id,
                'items':[self._view(row,review) for row,review in self.store.list(actor,task,limit)]}

    def close(self):
        for runner in self._runners.values(): runner.close()
