"""Durable planning dispatch; execution commands remain unavailable.

An expired attempt is fenced and reconciled, never automatically rerun. Model
allowances remain conservatively reserved across failures and process crashes.
"""
from dataclasses import replace
import threading

from ..errors import ServiceError
from .contracts import HarnessInvocation
from .runner import published_releases
from .tools import ToolBroker


class DurableToolBroker(ToolBroker):
    model_budget_enabled = True

    def __init__(self, invocation, catalog, *, guard, journal, actor, handle, token):
        self.journal, self.actor, self.handle, self.token = journal, actor, handle, token
        def fenced(inv):
            guard(inv)
            journal.guard(actor, handle, token)
        super().__init__(invocation, catalog, guard=fenced)

    def call(self, tool_id, arguments):
        self._check()
        definition, handler, arguments, _, fingerprint = self.prepare_call(tool_id, arguments)
        return self.journal.tool_call(self.actor, self.handle, self.token,
            definition, arguments, fingerprint, handler)

    def submitted(self):
        self._check()
        return bool(self.journal.pending_outputs(self.actor, self.handle, self.token))

    def admit_model_call(self, sequence):
        self._check()
        self.journal.model_admit(self.actor, self.handle, self.token, sequence)
        return {'admitted': True}


class DurableHarnessRunner:
    def __init__(self, registry, routes, roles, catalog, *, guard, model_binding, journal, actor, releases=None):
        self.registry, self.routes, self.roles, self.catalog = registry, dict(routes), dict(roles), catalog
        self.releases = published_releases(self.routes, releases)
        self.guard, self.model_binding, self.journal, self.actor = guard, model_binding, journal, actor
        self._live, self._lock = {}, threading.RLock()

    def enqueue(self, stage, *, run_id, attempt_id, model_profile_ref, task_kind,
                deadline, context_json, model_calls):
        # Restore immutable persisted routing before consulting current configuration.
        previous = self.journal.find(self.actor, run_id, attempt_id)
        role = previous.role if previous else self.roles.get(stage.role)
        runtime = previous.runtime if previous else self.routes.get(stage.role)
        if role is None or runtime is None:
            raise ServiceError('HARNESS_ROUTE_UNAVAILABLE', 'No explicit runtime route for role', 503)
        runtime = replace(runtime, required_capabilities=runtime.required_capabilities | {'model_call_admission'})
        inv = HarnessInvocation(stage, run_id, attempt_id, role, runtime,
                                model_profile_ref, task_kind, deadline, context_json)
        self.guard(inv)
        if self.model_binding(model_profile_ref) != stage.model_binding_digest:
            raise ServiceError('HARNESS_MODEL_BINDING_CONFLICT', 'Model profile binding changed', 409)
        return self.journal.enqueue(self.actor, inv, model_calls=model_calls)

    def dispatch(self, handle, *, lease_seconds=30):
        with self._lock:
            if handle in self._live: return False
            snapshot = self.journal.read(self.actor, handle)
            if snapshot['status'] != 'queued': return False
            inv = snapshot['invocation']
            self.guard(inv)
            if self.model_binding(inv.model_profile_ref) != inv.stage.model_binding_digest:
                raise ServiceError('HARNESS_MODEL_BINDING_CONFLICT', 'Queued model binding changed', 409)
            release = replace(inv.runtime, required_capabilities=inv.runtime.required_capabilities | {'model_call_admission'})
            adapter = self.registry.resolve(release)
            token = self.journal.claim(self.actor, handle, lease_seconds=lease_seconds)
            if token is None: return False
            broker = DurableToolBroker(inv, self.catalog, guard=self.guard, journal=self.journal,
                                       actor=self.actor, handle=handle, token=token)
            try:
                live_handle = adapter.start(inv, broker)
                if live_handle != handle: raise ValueError('adapter handle mismatch')
            except Exception:
                self.journal.finish(self.actor, handle, token, code='HARNESS_START_FAILED')
                raise ServiceError('HARNESS_START_FAILED', 'Planning adapter failed to start', 502) from None
            stopped = threading.Event()
            self._live[handle] = (adapter, token, broker, stopped, lease_seconds)
            def heartbeat():
                while not stopped.wait(max(.001, min(10, lease_seconds / 3))):
                    try:
                        self.poll(handle)
                    except ServiceError:
                        if not stopped.is_set():
                            stopped.set(); adapter.cancel(handle); adapter.close(handle)
                        return
            threading.Thread(target=heartbeat, daemon=True).start()
            return True

    def poll(self, handle, after_cursor=0):
        with self._lock:
            live = self._live.get(handle)
            if live is not None:
                adapter, token, _, stopped, lease_seconds = live
                try:
                    self.journal.renew(self.actor, handle, token, lease_seconds=lease_seconds)
                except ServiceError:
                    stopped.set(); adapter.cancel(handle); adapter.close(handle)
                    del self._live[handle]
                    raise
                cursor, last = 0, None
                while True:
                    events = adapter.poll(handle, cursor)
                    if not events: break
                    last = events[-1]; cursor = last.cursor
                if last is not None and last.kind in ('finished', 'failed', 'cancelled'):
                    stopped.set()
                    if last.kind == 'cancelled': self.journal.cancel(self.actor, handle)
                    else: self.journal.finish(self.actor, handle, token,
                        code=last.code or (None if last.kind == 'finished' else 'HARNESS_DRIVER_FAILED'))
                    adapter.close(handle)
                    del self._live[handle]
            return self.journal.read(self.actor, handle, after_cursor=after_cursor)

    def outputs(self, handle):
        row = self.poll(handle)
        if row['status'] != 'finished':
            raise ServiceError('HARNESS_OUTPUT_NOT_READY', 'No completed durable output', 409)
        return row['outputs']

    def cancel(self, handle):
        try:
            self.journal.cancel(self.actor, handle)  # Fence first, even if task generation already changed.
        finally:
            with self._lock:
                live = self._live.pop(handle, None)
                if live is not None:
                    live[3].set(); live[0].cancel(handle); live[0].close(handle)

    def close(self):
        for handle in list(self._live): self.cancel(handle)
