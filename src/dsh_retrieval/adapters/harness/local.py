"""Threaded driver lifecycle shared by local and process adapters.

Driver cancellation may be cooperative; broker publication is always fenced.
Run state here is process-local until T05 persistence is installed.
"""
from dataclasses import dataclass, field
import threading
import time

from ...errors import ServiceError, invalid
from ...harness.contracts import PROTOCOL, ProbeResult, RuntimeHandle, RuntimeEvent


@dataclass
class _Attempt:
    handle: RuntimeHandle
    broker: object
    events: list = field(default_factory=list)
    stop: threading.Event = field(default_factory=threading.Event)
    lock: object = field(default_factory=threading.RLock)
    terminal: bool = False
    timer: object = None
    thread: object = None

    def emit(self, kind, code=None):
        with self.lock:
            if self.terminal: return
            self.events.append(RuntimeEvent(len(self.events) + 1, kind, code))
            if kind in ('finished', 'failed', 'cancelled'):
                self.terminal = True
                if self.timer is not None: self.timer.cancel()


class _Tools:
    def __init__(self, state): self.state = state
    @property
    def model_budget_enabled(self): return getattr(self.state.broker, 'model_budget_enabled', False)
    def admit_model_call(self, sequence): return self.state.broker.admit_model_call(sequence)
    def list_tools(self): return self.state.broker.list_tools()
    def call(self, name, arguments):
        self.state.emit('tool_requested')
        result = self.state.broker.call(name, arguments)
        self.state.emit('tool_completed')
        return result


class LocalHarnessAdapter:
    """SDK callback adapter. Register a trusted driver, not model-generated code."""
    def __init__(self, adapter_id, driver, *, artifact_digest, version='local@1'):
        self.adapter_id, self.driver, self.version = adapter_id, driver, version
        self.artifact_digest = artifact_digest
        self._attempts, self._lock = {}, threading.RLock()

    def describe(self):
        return ProbeResult(self.adapter_id, PROTOCOL, self.version,
            frozenset({'controlled_tools', 'deadline', 'attempt_tracking', 'planning_only'}), True, artifact_digest=self.artifact_digest)

    def probe(self): return self.describe()

    def _get(self, handle):
        with self._lock:
            state = self._attempts.get((handle.run_id, handle.attempt_id))
            if state is None or state.handle != handle:
                raise ServiceError('HARNESS_HANDLE_NOT_FOUND', 'Attempt handle unavailable', 404)
            return state

    def start(self, invocation, tools):
        handle = RuntimeHandle(invocation.run_id, invocation.attempt_id,
                               invocation.runtime.release_digest, invocation.invocation_digest)
        with self._lock:
            key = (handle.run_id, handle.attempt_id)
            if key in self._attempts:
                if self._attempts[key].handle != handle:
                    raise ServiceError('IDEMPOTENCY_CONFLICT', 'Attempt input changed', 409)
                return handle
            if invocation.deadline <= time.time():
                raise ServiceError('HARNESS_RUN_STOPPED', 'Deadline reached', 409)
            tools.list_tools()  # Check authority before a model/driver can start.
            state = _Attempt(handle, tools)
            self._attempts[key] = state
            state.emit('started')
            def expired():
                state.stop.set(); state.broker.cancel()
                state.emit('failed', 'HARNESS_DEADLINE')
            state.timer = threading.Timer(invocation.deadline - time.time(), expired)
            state.timer.daemon = True
            def run():
                try:
                    self.driver(invocation, _Tools(state), state.stop)
                    if state.stop.is_set(): return
                    if state.broker.submitted():
                        state.emit('proposal_submitted')
                        state.emit('finished')
                    else:
                        state.emit('failed', 'HARNESS_OUTPUT_MISSING')
                except ServiceError as error:
                    state.emit('failed', error.code)
                except Exception:
                    # SDK exceptions may contain input/credentials; never surface them.
                    state.emit('failed', 'HARNESS_DRIVER_FAILED')
            state.thread = threading.Thread(target=run, daemon=True)
            state.timer.start(); state.thread.start()
        return handle

    def poll(self, handle, after_cursor=0):
        state = self._get(handle)
        with state.lock:
            if type(after_cursor) is not int or not 0 <= after_cursor <= len(state.events):
                raise invalid('invalid event cursor')
            return tuple(state.events[after_cursor:after_cursor + 100])

    def cancel(self, handle):
        state = self._get(handle)
        state.stop.set(); state.broker.cancel(); state.emit('cancelled')
        # Publication is fenced immediately; thread/remote termination not assumed.
        return 'acknowledged'

    def close(self, handle):
        state = self._get(handle)
        if not state.terminal:
            self.cancel(handle)
        state.thread.join(timeout=1)
        # Retain terminal receipt/handle to prevent accidental duplicate start.
