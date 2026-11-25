"""Numeric workflow diagnostics; no prompts, replies, paths, keys or media refs.

Nested stage durations are inclusive and must not be added to obtain wall time.
Each worker activation gets its own trace; task/job IDs link separate activations.
"""
from functools import wraps
import inspect
import json
import logging
import re
from urllib.parse import urlsplit
from uuid import uuid4
from threading import get_ident

from .conversation_timing import ConversationTiming, _active, timed_call

_log = logging.getLogger('dsh_retrieval.pipeline_timing')
if not _log.handlers:
    _log.addHandler(logging.StreamHandler())
_log.setLevel(logging.INFO)
_log.propagate = False
_identifier = re.compile(r'[A-Za-z0-9_:-]{1,96}')


def _emit(record):
    # Observability must never change a business result or exception.
    try:
        _log.info('pipeline_timing %s', json.dumps(record, separators=(',', ':'), allow_nan=False))
    except Exception:
        pass


class PipelineTiming(ConversationTiming):
    def __init__(self, operation, **identity):
        super().__init__()
        self.parent_trace_id = self.trace_id
        self.trace_id = uuid4().hex
        self.operation = operation
        self.identity = {}
        self.status = None
        self.bind(**identity)

    def bind(self, **identity):
        for key, value in identity.items():
            if key in ('task_id', 'job_id') and isinstance(value, str) and _identifier.fullmatch(value):
                self.identity[key] = value
            elif key in ('revision', 'generation', 'task_version') and type(value) is int and value >= 0:
                self.identity[key] = value

    def snapshot(self, outcome='succeeded'):
        result = super().snapshot(outcome)
        result.update(schema_version='pipeline-timing@1', scope='activation',
                      operation=self.operation, **self.identity)
        if self.status is not None:
            result['http_status'] = self.status
        if self.parent_trace_id:
            result['parent_trace_id'] = self.parent_trace_id
        return result

    def stage_event(self, stage, event, elapsed=None):
        record = dict(schema_version='pipeline-timing@1', event=event,
                      trace_id=self.trace_id, operation=self.operation, stage=stage, **self.identity)
        if elapsed is not None:
            record['ms'] = round(elapsed, 3)
        _emit(record)

    def absorb(self, child):
        for name, values in child.stages.items():
            row = self.stages.setdefault(name, {'ms': 0, 'calls': 0, 'failures': 0})
            for key, value in values.items():
                row[key] += value
        for name, value in child.counters.items():
            self.counters[name] = self.counters.get(name, 0) + value


def bind(**identity):
    trace = _active.get()
    if isinstance(trace, PipelineTiming) and trace.owner == get_ident():
        trace.bind(**identity)


def measured(stage):
    """Start a worker trace if needed; otherwise contribute to its caller's trace."""
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def invoke(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs).arguments
            identity = {key: arguments.get(key) for key in ('task_id', 'job_id')}
            scope = arguments.get('expected_scope') or getattr(arguments.get('approved'), 'scope', None)
            from .retrieval_contracts import RetrievalScope
            if isinstance(scope, RetrievalScope):
                identity.update(task_id=scope.task_id, revision=scope.revision,
                                generation=scope.generation, task_version=scope.task_version)
            active = _active.get()
            if active is not None and active.owner == get_ident():
                bind(**identity)
                return timed_call(stage, function, *args, **kwargs)
            trace = PipelineTiming(stage, **identity)
            outcome = 'failed'
            try:
                with trace.activate():
                    result = timed_call(stage, function, *args, **kwargs)
                outcome = 'succeeded'
                return result
            finally:
                _emit(dict(trace.snapshot(outcome), event='completed'))
        return invoke
    return decorate


def http_measured(function):
    """Trace JSON workflow requests, excluding assets, streams and SSE."""
    @wraps(function)
    def invoke(handler):
        path = urlsplit(handler.path).path
        if not path.startswith('/v1/') or (function.__name__ == 'do_GET' and not re.search(
                r'/(workspace|progress|results|candidates|status|manifest|expansion-jobs/expjob_[a-f0-9]+)$', path)):
            return function(handler)
        task = re.search(r'/tasks/(rt_[a-f0-9]{32})(?:/|$)', path)
        trace = PipelineTiming('http_' + function.__name__[3:].lower(), task_id=task[1] if task else None)
        outcome = 'failed'
        try:
            with trace.activate():
                result = function(handler)
            outcome = 'failed' if trace.status is not None and trace.status >= 400 else 'succeeded'
            return result
        finally:
            _emit(dict(trace.snapshot(outcome), event='completed'))
    return invoke


def response_headers(status):
    trace = _active.get()
    if not isinstance(trace, PipelineTiming) or trace.owner != get_ident():
        return {}
    trace.status = status
    return {'X-Performance-Trace-Id': trace.trace_id,
            'Server-Timing': 'pipeline;dur=%.3f' % trace.snapshot()['total_ms']}
