"""One retrieval owner for provisional planning and approved execution.

The existing term queue and search worker are internal phase adapters. Planning
creates no business task; only an explicit, owner-scoped confirmation resumes it.
"""
import json
from contextlib import nullcontext
from copy import deepcopy

from .errors import ServiceError
from .proposals import normalize_proposal
from .query_spec import digest
from .service import require_key


class RetrievalAgent:
    workflow_ref = 'retrieval-agent@1'

    def __init__(self, plans, service, execution=None, *, enabled_projects=None):
        self.plans, self.service, self.execution = plans, service, execution
        self.enabled_projects = enabled_projects
        with plans.lock:
            plans.db.execute('''CREATE TABLE IF NOT EXISTS retrieval_agent_confirmations (
                tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, project_id TEXT NOT NULL,
                session_id TEXT NOT NULL, job_id TEXT NOT NULL, request_digest TEXT NOT NULL,
                plan_digest TEXT NOT NULL, draft_confirmation TEXT, task_id TEXT,
                PRIMARY KEY (tenant_id,user_id,project_id,session_id,job_id))''')
            plans.db.execute('CREATE INDEX IF NOT EXISTS retrieval_agent_confirmation_task '
                             'ON retrieval_agent_confirmations (tenant_id,user_id,task_id)')
            plans.db.commit()
        if execution is not None:
            execution.owner = self

    @classmethod
    def descriptor(cls, phase):
        return {'capability': 'retrieval', 'workflow_ref': cls.workflow_ref, 'phase': phase}

    def _plan(self, value):
        value = deepcopy(value)
        value['phase_adapter'] = value['agent']
        value['agent'] = self.descriptor('planning')
        value['phase'] = ('waiting_confirmation' if value['status'] == 'completed'
                          else 'failed' if value['status'] == 'failed' else 'planning')
        value['planning_job_ref'] = value['job_id']
        # Legacy callers/servers can continue using their existing draft path.
        value['confirmation'] = ({'action': 'confirm', 'plan_digest': value['result']['plan']['plan_digest'],
                                  'dialogue_version': value['dialogue_version']}
                                 if self.execution is not None and value['status'] == 'completed'
                                 and not value.get('task_id') else None)
        return value

    def start(self, *args, **kwargs):
        return self._plan(self.plans.start(*args, **kwargs))

    def start_task(self, *args, **kwargs):
        return self._plan(self.plans.start_task(*args, **kwargs))

    def get(self, actor, project_id, session_id, job_id=None):
        self.plans.projects.require_access(actor, project_id, 'default', 'create')
        with self.plans.lock:
            scope = (actor.tenant_id, actor.user_id, project_id, session_id)
            if job_id is None:
                latest = self.plans.db.execute('''SELECT job_id FROM expansion_jobs
                    WHERE tenant_id=? AND user_id=? AND project_id=? AND session_id=?
                    ORDER BY rowid DESC LIMIT 1''', scope).fetchone()
                job_id = latest[0] if latest else None
            row = self.plans.db.execute('''SELECT task_id FROM retrieval_agent_confirmations
                WHERE tenant_id=? AND user_id=? AND project_id=? AND session_id=? AND job_id=?''',
                (*scope, job_id)).fetchone()
            if not row or not row[0]:
                return self._plan(self.plans.get(actor, project_id, session_id, job_id))
            self.service.get_task(actor, row[0])
            value = self._plan(self.plans._public(job_id))
            execution = self.current_job(actor, row[0])
            phase = execution.get('phase', 'retrieval') if execution else 'retrieval'
            value.update(task_id=row[0], plan_status=value['status'],
                         status=execution['status'] if execution else 'queued',
                         phase=phase, agent=self.descriptor(phase), confirmation=None, execution=execution,
                         task_version=execution.get('task_version') if execution else None,
                         revision=execution.get('revision') if execution else None,
                         generation=execution.get('generation') if execution else None)
            return value

    def retry(self, *args, **kwargs):
        return self._plan(self.plans.retry(*args, **kwargs))

    def _executor(self):
        if self.execution is None:
            raise ServiceError('AGENT_EXECUTION_UNAVAILABLE', 'Retrieval execution is unavailable', 503)
        return self.execution

    def policy(self, *args, **kwargs):
        return self._executor().policy(*args, **kwargs)

    def require(self, *args, **kwargs):
        return self._executor().require(*args, **kwargs)

    def enqueue(self, actor, task_id, *, key):
        value = self._executor().enqueue(actor, task_id, key=key)
        return {**value, **self.execution_context(actor, task_id, 'retrieval')}

    def current_job(self, actor, task_id):
        value = self._executor().current_job(actor, task_id)
        return ({**value, **self.execution_context(actor, task_id, value.get('phase', 'retrieval'))}
                if value is not None else None)

    def execution_context(self, actor, task_id, phase, *, plan_digest=None):
        if plan_digest is None:
            task = self.service.repository.get(actor, task_id)
            spec = json.loads(task.confirmed_proposal_json)['query_spec']
            plan_digest = (spec.get('grouped_expansion') or {}).get('plan_digest')
        with self.plans.lock:
            row = self.plans.db.execute('''SELECT job_id,plan_digest FROM retrieval_agent_confirmations
                WHERE tenant_id=? AND user_id=? AND task_id=? AND plan_digest=?''',
                (actor.tenant_id, actor.user_id, task_id, plan_digest)).fetchone()
            if row is None and plan_digest is not None:
                # A later reviewed revision must reference its own planning job,
                # never borrow the initial approval's provenance.
                later = self.plans.db.execute('''SELECT job_id FROM expansion_jobs
                    WHERE tenant_id=? AND user_id=? AND session_id=?
                    AND json_extract(state,'$.status')='completed'
                    AND json_extract(state,'$.result.plan.plan_digest')=?
                    ORDER BY rowid DESC LIMIT 1''',
                    (actor.tenant_id, actor.user_id, 'task:' + task_id, plan_digest)).fetchone()
                row = (later[0], plan_digest) if later else None
        return {'agent': self.descriptor(phase),
                'planning': {'job_ref': row[0], 'plan_digest': row[1]} if row else None}

    def confirm(self, actor, project_id, session_id, job_id, key, body):
        require_key(key)
        fields = {'action', 'dialogue_version', 'plan_digest', 'proposal'}
        if (not isinstance(body, dict) or set(body) != fields or body.get('action') != 'confirm'
                or type(body.get('dialogue_version')) is not int or body['dialogue_version'] < 1):
            raise ServiceError('CONFIRMATION_REQUIRED', 'Confirm the reviewed retrieval plan explicitly', 422)
        self._executor()
        if self.enabled_projects is not None and project_id not in self.enabled_projects:
            raise ServiceError('AGENT_EXECUTION_UNAVAILABLE', 'Retrieval Agent is not enabled for this project', 503)
        self.plans.projects.require_access(actor, project_id, 'default', 'approve')
        proposal = normalize_proposal(body['proposal'])
        fingerprint = digest({**body, 'proposal': proposal})
        scope = (actor.tenant_id, actor.user_id, project_id, session_id, job_id)
        store = getattr(self.plans.dialogue, 'store', None)
        # Keep a concurrent dialogue edit from changing the request during approval.
        with self.plans.lock, (store.lock if store is not None else nullcontext()):
            row = self.plans.db.execute('''SELECT request_digest,draft_confirmation,task_id
                FROM retrieval_agent_confirmations WHERE tenant_id=? AND user_id=? AND project_id=?
                AND session_id=? AND job_id=?''', scope).fetchone()
            if row and row[0] != fingerprint:
                raise ServiceError('IDEMPOTENCY_CONFLICT', 'This plan was already confirmed with different settings', 409)
            if row and row[2]:
                task = self.service.get_task(actor, row[2])
                self._archive(actor, project_id, session_id, row[2], body['dialogue_version'])
                return self._confirmed(actor, task, job_id, body['plan_digest']), True
            if row and row[1]:
                # The task ledger may have committed before our acknowledgement.
                # Recover that approval before checking a newer planning session.
                saved = json.loads(row[1])
                draft = self.service.get_draft(actor, project_id, saved['draft_id'])
                if draft.get('confirmed_task_id'):
                    task = self.service.get_task(actor, draft['confirmed_task_id'])
                    self._remember_task(scope, task['task_id'])
                    self._archive(actor, project_id, session_id, task['task_id'], body['dialogue_version'])
                    return self._confirmed(actor, task, job_id, body['plan_digest']), True
            job = self.plans.get(actor, project_id, session_id, job_id)
            latest = self.plans.get(actor, project_id, session_id)
            self.plans._current(actor, project_id, session_id, body['dialogue_version'], None)
            spec = proposal['query_spec']
            plan = (job.get('result') or {}).get('plan')
            if (latest['job_id'] != job_id or job['status'] != 'completed' or job['dialogue_version'] != body['dialogue_version']
                    or plan is None or body['plan_digest'] != plan['plan_digest']
                    or spec.get('grouped_expansion') != plan
                    or spec['raw_request'] != job['raw_request'] or spec['target_count'] != job['target_count']
                    or proposal['dimension_id'] != 'default'):
                raise ServiceError('RETRIEVAL_PLAN_CHANGED', 'Review the latest complete retrieval plan before confirming', 409)
            frozen_binding = self.plans._load(job_id)[1]['model_binding']
            if self.plans.runtime.binding(job['model_selection']['llm'], job['model_selection']['vlm']) != frozen_binding:
                raise ServiceError('MODEL_CONFIG_CHANGED', 'Model configuration changed; generate a new plan', 409)
            self.require(actor, project_id, proposal)
            if row is None:
                self.plans.db.execute('INSERT OR IGNORE INTO retrieval_agent_confirmations VALUES (?,?,?,?,?,?,?,?,?)',
                    (*scope, fingerprint, body['plan_digest'], None, None))
                self.plans.db.commit()
                row = self.plans.db.execute('''SELECT request_digest,draft_confirmation,task_id
                    FROM retrieval_agent_confirmations WHERE tenant_id=? AND user_id=? AND project_id=?
                    AND session_id=? AND job_id=?''', scope).fetchone()
                if row[0] != fingerprint:
                    raise ServiceError('IDEMPOTENCY_CONFLICT', 'This plan was already confirmed with different settings', 409)
            confirmation = json.loads(row[1]) if row and row[1] else None
            if confirmation is None:
                # Keys derive from the durable plan, not from the browser click.
                # A crash between ledgers can replay these existing idempotent writes.
                draft, _ = self.service.save_draft(actor, project_id, 'agent-plan-draft:' + job_id, proposal)
                confirmation = {'draft_id': draft['draft_id'], 'expected_draft_version': draft['draft_version'],
                                'proposal_digest': draft['proposal_digest'], 'action': 'confirm'}
                self.plans.db.execute('''UPDATE retrieval_agent_confirmations SET draft_confirmation=?
                    WHERE tenant_id=? AND user_id=? AND project_id=? AND session_id=? AND job_id=?''',
                    (json.dumps(confirmation), *scope))
                self.plans.db.commit()
            task, replayed = self.service.create_task(actor, 'agent-plan-task:' + job_id, confirmation)
            self._remember_task(scope, task['task_id'])
            self._archive(actor, project_id, session_id, task['task_id'], body['dialogue_version'])
            return self._confirmed(actor, task, job_id, plan['plan_digest']), replayed

    def _remember_task(self, scope, task_id):
        self.plans.db.execute('''UPDATE retrieval_agent_confirmations SET task_id=?
            WHERE tenant_id=? AND user_id=? AND project_id=? AND session_id=? AND job_id=?''',
            (task_id, *scope))
        self.plans.db.commit()

    def _archive(self, actor, project, session, task_id, version):
        store = getattr(self.plans.dialogue, 'store', None)
        if store is None:
            return
        try:
            current = self.plans.dialogue.snapshot(actor, project, session)
        except ServiceError as error:
            if error.code != 'DIALOGUE_NOT_FOUND':
                raise
            return
        # A lost acknowledgement must never retire a newer user request.
        if current['version'] == version:
            store.archive_to_task(actor, project, session, task_id)

    def _confirmed(self, actor, task, job_id, plan_digest):
        return {**task, 'agent': self.descriptor('retrieval'),
                'planning': {'job_ref': job_id, 'plan_digest': plan_digest}}
