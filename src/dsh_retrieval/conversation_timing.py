"""Request-local numeric timing; never retain prompts, replies or credentials."""
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

_active = ContextVar('conversation_timing', default=None)


class ConversationTiming:
    def __init__(self):
        self.started = perf_counter()
        self.stages = {}
        self.counters = {}

    @contextmanager
    def activate(self):
        token = _active.set(self)
        try:
            yield self
        finally:
            _active.reset(token)

    def snapshot(self, outcome='succeeded'):
        return {'schema_version': 'conversation-timing@1', 'scope': 'original_processing',
                'outcome': outcome, 'total_ms': round((perf_counter() - self.started) * 1000, 3),
                'stages': {name: {**row, 'ms': round(row['ms'], 3)} for name, row in self.stages.items()},
                'counters': dict(self.counters)}


def increment(name, amount=1):
    trace = _active.get()
    if trace is not None:
        trace.counters[name] = trace.counters.get(name, 0) + amount


def timed_call(name, function, *args, **kwargs):
    trace = _active.get()
    if trace is None:
        return function(*args, **kwargs)
    started = perf_counter()
    failed = False
    try:
        return function(*args, **kwargs)
    except BaseException:
        failed = True
        raise
    finally:
        row = trace.stages.setdefault(name, {'ms': 0, 'calls': 0, 'failures': 0})
        row['ms'] += (perf_counter() - started) * 1000
        row['calls'] += 1
        row['failures'] += int(failed)
