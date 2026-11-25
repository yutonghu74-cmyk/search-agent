"""Request-local numeric timing; never retain prompts, replies or credentials."""
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from threading import get_ident

_active = ContextVar('conversation_timing', default=None)


class ConversationTiming:
    def __init__(self):
        self.started = perf_counter()
        self.stages = {}
        self.counters = {}
        self.trace_id = getattr(_active.get(), 'trace_id', None)
        self.owner = get_ident()
        self.parent_timing = _active.get()

    @contextmanager
    def activate(self):
        token = _active.set(self)
        try:
            yield self
        finally:
            _active.reset(token)
            parent = self.parent_timing
            if parent is not None and parent.owner == get_ident() and hasattr(parent, 'absorb'):
                parent.absorb(self)

    def stage_event(self, name, event, elapsed=None):
        parent = self.parent_timing
        if parent is not None and parent.owner == get_ident():
            parent.stage_event(name, event, elapsed)

    def snapshot(self, outcome='succeeded'):
        result = {'schema_version': 'conversation-timing@1', 'scope': 'original_processing',
                'outcome': outcome, 'total_ms': round((perf_counter() - self.started) * 1000, 3),
                'stages': {name: {**row, 'ms': round(row['ms'], 3)} for name, row in self.stages.items()},
                'counters': dict(self.counters)}
        if self.trace_id:
            result['trace_id'] = self.trace_id
        return result


def increment(name, amount=1):
    trace = _active.get()
    if trace is not None and trace.owner == get_ident():
        trace.counters[name] = trace.counters.get(name, 0) + amount


def timed_call(name, function, *args, **kwargs):
    trace = _active.get()
    if trace is None or trace.owner != get_ident():
        return function(*args, **kwargs)
    started = perf_counter()
    notify = getattr(trace, 'stage_event', None)
    if notify:
        notify(name, 'started')
    failed = False
    try:
        return function(*args, **kwargs)
    except BaseException:
        failed = True
        raise
    finally:
        elapsed = (perf_counter() - started) * 1000
        row = trace.stages.setdefault(name, {'ms': 0, 'calls': 0, 'failures': 0})
        row['ms'] += elapsed
        row['calls'] += 1
        row['failures'] += int(failed)
        if notify:
            notify(name, 'failed' if failed else 'finished', elapsed)
