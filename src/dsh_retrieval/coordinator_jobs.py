"""Durable platform coordination workflow over the common Harness/job lifecycle."""
from dataclasses import asdict
import json
import threading
import time

from .adapters.task_storage.coordinator import SQLiteCoordinationJournal, SQLiteCoordinationLinks
from .errors import ServiceError, invalid
from .harness.agents import AgentDomain
from .harness.codec import load_invocation
from .harness.contracts import HarnessInvocation, RuntimeHandle
from .platform_coordination import (CoordinationStage, CoordinationPolicy, ROLE, WORKFLOW,
    TASK_CONTRACT, current_scope, coordination_catalog, validate_plan)
from .query_spec import digest
from .retrieval_contracts import RetrievalScope
from .processing_requirements import identifier
from .service import require_key


class PlatformCoordinatorJobs:
    contract_ref, capability = WORKFLOW, 'coordination'

    def __init__(self, service, registry, *, runner_factory, model_binding, policy=None):
        self.service, self.registry = service, registry
        registry.seal()
        self.policy = policy or CoordinationPolicy()
        self.policy_digest = digest({'policy': asdict(self.policy), 'capabilities': registry.release_digest,
                                     'role': ROLE.release_digest})
        self.journal = SQLiteCoordinationJournal(service.repository, service.projects,
            policy=self.policy, policy_digest=self.policy_digest)
        self.links = SQLiteCoordinationLinks(self.journal)
        self.model_binding, self.runner_factory = model_binding, runner_factory
        self.catalog = coordination_catalog(self.policy)
        self.domain = AgentDomain('platform-coordination@1', TASK_CONTRACT, stage_type=CoordinationStage,
            roles=(ROLE,), catalog=self.catalog, guard=self.guard)
        self._runners, self._lock = {}, threading.RLock()

    def guard(self, inv):
        actor = self._actor(inv.stage.scope)
        task = self.service.repository.get(actor, inv.stage.scope.task_id)
        self.service.projects.require_access(actor, task.project_id, task.dimension_id, 'create')
        self.journal.validate_authority(actor, task, inv.stage, {})
        if self.model_binding(actor, inv.model_profile_ref) != inv.stage.model_binding_digest:
            raise ServiceError('HARNESS_MODEL_BINDING_CONFLICT', 'Coordinator model profile changed', 409)

    @staticmethod
    def _actor(scope):
        from .models import Actor
        return Actor(scope.tenant_id, scope.owner_user_id)

    def runner(self, actor):
        key = (actor.tenant_id, actor.user_id)
        with self._lock:
            if key not in self._runners: self._runners[key] = self.runner_factory(actor, self.domain, self.journal)
            return self._runners[key]

    def prepare(self, actor, task_id, key, body):
        require_key(key)
        fields = {'message', 'model_profile_ref', 'expected_revision', 'expected_generation', 'expected_task_version'}
        if (type(body) is not dict or set(body) != fields or not isinstance(body['message'], str)
                or not 1 <= len(body['message'].strip()) <= 2000
                or not identifier(body['model_profile_ref'])
                or any(type(body[field]) is not int or body[field] < 1 for field in fields if field.startswith('expected_'))):
            raise invalid('bounded owner coordination request required')
        task = self.service.repository.get(actor, task_id)
        self.service.projects.require_access(actor, task.project_id, task.dimension_id, 'create')
        fingerprint = digest(body)
        ref = 'agentdraft_' + digest((actor.tenant_id, actor.user_id, task_id, WORKFLOW, key))[7:39]
        try:
            old = self.links.get(actor, task_id, ref)
        except ServiceError as error:
            if error.code != 'NOT_FOUND': raise
        else:
            if old['links']['fingerprint'] != fingerprint:
                raise ServiceError('IDEMPOTENCY_CONFLICT', 'Coordination key belongs to a different message', 409)
            return self.get(actor, task_id, ref), True
        scope = RetrievalScope.from_task(task)
        if (scope.revision, scope.generation, scope.task_version) != (
                body['expected_revision'], body['expected_generation'], body['expected_task_version']):
            raise ServiceError('REVISION_CONFLICT', 'Task changed before coordination', 409)
        current_scope(actor, task, scope)
        context = {'schema_version': 'platform-coordination-context@1', 'message': body['message'],
            'scope': asdict(scope), 'requirement': json.loads(task.confirmed_proposal_json)['query_spec'],
            'capabilities': self.registry.context(actor, task), 'policy': asdict(self.policy),
            'owner_confirmation_authority': 'original_business_service'}
        encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, allow_nan=False)
        stage = CoordinationStage(scope, ref, digest(context), self.model_binding(actor, body['model_profile_ref']), self.policy_digest)
        runner = self.runner(actor)
        runtime = runner.routes['coordinator']
        from dataclasses import replace
        runtime = replace(runtime, required_capabilities=runtime.required_capabilities | {'model_call_admission'})
        inv = HarnessInvocation(stage, 'coordrun_' + ref[11:], 'coordattempt_' + ref[11:], ROLE,
            runtime, body['model_profile_ref'], 'plan', time.time() + self.policy.max_seconds, encoded)
        created = self.links.save(actor, task_id, ref, fingerprint, inv, context)
        return self.get(actor, task_id, ref), not created

    def invocation(self, value):
        return load_invocation(value['invocation'], stage_codecs=self.journal.stage_codecs)

    @staticmethod
    def handle(inv): return RuntimeHandle(inv.run_id, inv.attempt_id, inv.runtime.release_digest, inv.invocation_digest)

    def get(self, actor, task_id, ref):
        row = self.links.get(actor, task_id, ref); task, value = row['task'], row['links']
        same_root = self.links.current(task, value, exact=False)
        fenced = not self.links.current(task, value)
        state = ('cancelled' if not same_root else 'unknown'
                 if fenced and value['status'] not in ('completed', 'unknown') else value['status'])
        return {'schema_version': 'agent-job@1', 'job_ref': ref, 'task_id': task_id,
            'capability': 'coordination', 'workflow_ref': WORKFLOW, 'status': state,
            'phase': 'coordination', 'revision': value['scope']['revision'],
            'generation': value['scope']['generation'], 'task_version': task.task_version,
            'source_task_version': value['scope']['task_version'],
            'confirmation': None, 'artifacts_ready': False, 'accepted_count': None,
            'model_profile_ref': self.invocation(value).model_profile_ref,
            'max_model_calls': value['context']['policy']['model_calls'],
            'max_seconds': value['context']['policy']['max_seconds'],
            'proposal_digest': value['stage_digest'], 'publication_fenced': fenced,
            'code': 'REVISION_CONFLICT' if fenced else value['code'],
            'coordination': {'steps': value['steps'] if same_root else [],
                'suggestion': value['plan']['reply'] if value.get('plan') and same_root else None}}

    def act(self, actor, task_id, ref, operation, key, body):
        require_key(key)
        row = self.links.get(actor, task_id, ref); value = row['links']
        if operation != 'start' or type(body) is not dict or set(body) != {'proposal_digest', 'expected_task_version'}:
            raise invalid('coordinator accepts explicit start only; owner gates remain in original APIs')
        if type(body['expected_task_version']) is not int or body['expected_task_version'] != value['scope']['task_version']:
            raise ServiceError('REVISION_CONFLICT', 'Coordination source version changed', 409)
        if value['stage_digest'] != body['proposal_digest']:
            raise ServiceError('PLAN_DIGEST_MISMATCH', 'Displayed coordination scope changed', 409)
        if value.get('start_key') == key and value.get('start_body') != body:
            raise ServiceError('IDEMPOTENCY_CONFLICT', 'Coordinator start request changed', 409)
        if value['status'] != 'ready': return self.get(actor, task_id, ref), True
        if body['expected_task_version'] != row['task'].task_version:
            raise ServiceError('REVISION_CONFLICT', 'Task changed before coordinator start', 409)
        inv = self.invocation(value); self.guard(inv)
        # Approval is for this bounded model/planning request, never for a child
        # business gate. Stage and queue commit together in the original ledger.
        replayed = False
        with self.journal._tx() as db:
            fresh = self.links._read(db, actor, task_id, ref)['links']
            if fresh['status'] != 'ready': replayed = True
            else:
                self.journal._approve(db, actor, inv.stage, action='confirm', proposal_digest=inv.stage.stage_digest)
                self.journal._enqueue(db, actor, inv, model_calls=self.policy.model_calls)
                fresh.update(status='queued', start_key=key, start_body=body)
                from .adapters.task_storage.agent_jobs import SQLiteAgentJobLinks
                SQLiteAgentJobLinks._write(db, ref, fresh)
        return self.get(actor, task_id, ref), replayed

    def advance(self, actor, task_id, ref):
        row = self.links.get(actor, task_id, ref); value = row['links']
        if value['status'] not in ('queued', 'running', 'dispatching'): return
        if not self.links.current(row['task'], value):
            if self.links.current(row['task'], value, exact=False):
                self.links.update(actor, task_id, ref, exact=False, status='unknown', code='REVISION_CONFLICT')
            return
        inv = self.invocation(value); runner = self.runner(actor); handle = self.handle(inv)
        if value['status'] == 'queued':
            runner.dispatch(handle); self.links.update(actor, task_id, ref, status='running'); return
        if value['status'] == 'running':
            status = runner.poll(handle)
            if status['status'] in ('queued', 'running'): return
            if status['status'] != 'finished':
                self.links.update(actor, task_id, ref, status='unknown', code='COORDINATOR_OUTCOME_UNKNOWN'); return
            outputs = [o['arguments'] for o in status['outputs'] if o['tool_id'] == 'submit_coordination_plan']
            if len(outputs) != 1: raise ServiceError('COORDINATOR_OUTPUT_INVALID', 'One bounded plan required', 502)
            plan = validate_plan(outputs[0], value['context'], self.policy)
            self.links.update(actor, task_id, ref, status='dispatching', plan=plan)
            value = self.links.get(actor, task_id, ref)['links']
        plan = value['plan']
        completed = {r['step_id'] for r in value['steps'] if r['state'] == 'delivered'}
        for step in plan['steps']:
            if step['step_id'] in completed: continue
            if not set(step['depends_on']) <= completed: return
            if not self.links.claim_step(actor, task_id, ref, step):
                self.links.update(actor, task_id, ref, status='unknown', code='COORDINATOR_DELIVERY_UNKNOWN'); return
            # The intent is durable before invoking the original business port.
            # A crash or ambiguous reply is queried/reconciled, never resent.
            current = self.service.repository.get(actor, task_id)
            current_scope(actor, current, inv.stage.scope)
            result = self.registry.invoke(actor, current, step,
                key=ref+':'+step['step_id'], expected=value['context']['capabilities'][step['capability']])
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
            if type(result) is not dict or len(encoded.encode()) > 100000:
                raise ServiceError('COORDINATOR_RECEIPT_INVALID', 'Invalid bounded capability receipt', 502)
            records = self.links.get(actor, task_id, ref)['links']['steps']
            records[-1].update(state='delivered', result=result)
            self.links.update(actor, task_id, ref, exact=False, steps=records)
            completed.add(step['step_id'])
        self.links.update(actor, task_id, ref, exact=False, status='completed', code=None)

    def artifacts(self, actor, task_id, ref):
        view = self.get(actor, task_id, ref)
        return {'schema_version': 'agent-artifacts@1', 'task_id': task_id, 'job_ref': ref,
                'items': [], 'status': view['status'], 'note': 'Read actual child jobs through their original authorized APIs.'}

    def close(self):
        for runner in self._runners.values(): runner.close()
