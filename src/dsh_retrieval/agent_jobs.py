"""Harness-neutral job facade over installed domain workflows and business records."""
import threading

from .errors import ServiceError, invalid
from .processing_requirements import identifier
from .service import require_key


class AgentJobRegistry:
    def __init__(self): self._workflows = {}; self._capabilities = {}

    def register(self, workflow, *, default=True):
        if (not identifier(workflow.contract_ref) or not identifier(workflow.capability)
                or workflow.contract_ref in self._workflows or default and workflow.capability in self._capabilities
                or any(not callable(getattr(workflow, name, None)) for name in
                       ('prepare', 'get', 'act', 'advance', 'artifacts'))):
            raise invalid('unique installed Agent job workflow required')
        self._workflows[workflow.contract_ref] = workflow
        if default or workflow.capability not in self._capabilities:
            self._capabilities[workflow.capability] = workflow

    def capability(self, name):
        value = self._capabilities.get(name) if isinstance(name, str) else None
        if value is None: raise ServiceError('AGENT_CAPABILITY_UNAVAILABLE', 'Agent job capability is not installed', 503)
        return value

    def resolve(self, ref):
        value = self._workflows.get(ref)
        if value is None: raise ServiceError('AGENT_WORKFLOW_UNAVAILABLE', 'Stored workflow release is not installed', 503)
        return value


class AgentJobService:
    def __init__(self, *, registry, links):
        self.registry, self.links = registry, links
        self._notify = lambda: None

    def _job(self, actor, task_id, job_ref, allowed=None):
        snapshot = self.links.get(actor, task_id, job_ref)
        workflow = self.registry.resolve(snapshot['workflow_ref'])
        if allowed is not None and workflow.capability not in allowed:
            raise ServiceError('SHARED_CAPABILITY_DENIED', 'Agent capability is outside this delegation', 403)
        return workflow

    def prepare(self, actor, task_id, key, body, *, allowed=None):
        require_key(key)
        if (type(body) is not dict or not {'capability', 'planning'} <= set(body)
                or set(body) - {'capability', 'planning', 'workflow_ref'} or type(body['planning']) is not dict):
            raise invalid('capability and planning request required')
        workflow = (self.registry.resolve(body['workflow_ref']) if 'workflow_ref' in body else
                    self.registry.capability(body['capability']))
        if workflow.capability != body['capability']:
            raise invalid('installed workflow does not provide requested capability')
        if allowed is not None and workflow.capability not in allowed:
            raise ServiceError('SHARED_CAPABILITY_DENIED', 'Agent capability is outside this delegation', 403)
        return workflow.prepare(actor, task_id, key, body['planning'])

    def get(self, actor, task_id, job_ref, *, allowed=None):
        return self._job(actor, task_id, job_ref, allowed).get(actor, task_id, job_ref)

    def act(self, actor, task_id, job_ref, operation, key, body, *, allowed=None):
        require_key(key)
        if operation not in ('confirm', 'start', 'cancel'): raise invalid('unsupported Agent job action')
        workflow = self._job(actor, task_id, job_ref, allowed)
        value, replayed = workflow.act(actor, task_id, job_ref, operation, key, body)
        if operation in ('start', 'confirm'): self._notify()
        return value, replayed

    def artifacts(self, actor, task_id, job_ref, *, allowed=None):
        return self._job(actor, task_id, job_ref, allowed).artifacts(actor, task_id, job_ref)

    def list(self, actor, task_id, *, limit=32, allowed=None):
        if type(limit) is not int or not 1 <= limit <= 100: raise invalid('bounded Agent job list required')
        items = []
        for ref in self.links.list(actor, task_id, limit):
            try: items.append(self.get(actor, task_id, ref, allowed=allowed))
            except ServiceError as error:
                if error.code not in ('REVISION_CONFLICT', 'PLAN_NOT_CURRENT', 'INVALID_STATE_TRANSITION',
                                      'AGENT_CONFIRMATION_REQUIRED', 'SHARED_CAPABILITY_DENIED'): raise
        return {'schema_version': 'agent-job-list@1', 'task_id': task_id, 'items': items}

    def advance(self, actor, task_id, job_ref):
        return self._job(actor, task_id, job_ref).advance(actor, task_id, job_ref)


class AgentJobDispatcher:
    """Recoverable business links are the queue; reads never invoke this dispatcher."""
    def __init__(self, jobs, *, start=True, poll_seconds=1):
        if type(poll_seconds) not in (int, float) or not .05 <= poll_seconds <= 30:
            raise ValueError('bounded job polling interval required')
        self.jobs, self.poll_seconds = jobs, poll_seconds
        self._closed, self._wake = threading.Event(), threading.Event()
        self._drain_lock = threading.Lock()
        self._thread = None
        jobs._notify = self.notify
        if start:
            self._thread = threading.Thread(target=self._work, name='agent-jobs', daemon=True)
            self._thread.start()

    def notify(self): self._wake.set()

    def drain(self):
        # A local manual drain and the background loop share one dispatcher.
        # Do not overlap their network work or observe its reservation halfway through.
        with self._drain_lock:
            for actor, task_id, job_ref in self.jobs.links.pending():
                try: self.jobs.advance(actor, task_id, job_ref)
                except Exception as error:
                    code = error.code if isinstance(error, ServiceError) else 'AGENT_JOB_WORKER_FAILED'
                    try: self.jobs.links.record_error(actor, task_id, job_ref, code)
                    except ServiceError: pass  # revoked/stale tasks cannot publish even an error

    def _work(self):
        while not self._closed.is_set():
            try: self.drain()
            except ServiceError: pass  # ledger/dependency unavailable; retain persisted intent
            self._wake.wait(self.poll_seconds); self._wake.clear()

    def close(self):
        self._closed.set(); self._wake.set()
        self.jobs._notify = lambda: None
        if self._thread is not None: self._thread.join(timeout=2)
