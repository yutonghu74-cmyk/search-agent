"""One monotonic budget across a dialogue's queue, HTTP retries and repair."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import time
from .errors import ServiceError

_deadline = ContextVar('model_deadline', default=None)
CONVERSATION_SECONDS = 30.0


def remaining():
    deadline = _deadline.get()
    if deadline is None:
        return float('inf')
    value = deadline - time.monotonic()
    if value <= 0:
        raise ServiceError('MODEL_TIME_BUDGET_EXCEEDED',
                           '本条消息处理超时，尚未执行新的任务操作，请用原消息重试。', 504, True)
    return value


@contextmanager
def budget(seconds=CONVERSATION_SECONDS, *, check=True):
    # Nested interpretation/repair must inherit rather than reset the deadline.
    token = _deadline.set(time.monotonic() + seconds) if _deadline.get() is None else None
    try:
        if check:
            remaining()
        yield
    finally:
        if token is not None:
            _deadline.reset(token)


def bounded(function):
    @wraps(function)
    def run(*args, **kwargs):
        with budget():
            return function(*args, **kwargs)
    return run


def inference(function):
    @bounded
    @wraps(function)
    def run(*args, **kwargs):
        result = function(*args, **kwargs)
        remaining()  # Include proposal decoding/validation, before business writes.
        return result
    return run


def retry_wait(seconds, sleeper):
    if seconds >= remaining():
        raise ServiceError('MODEL_TIME_BUDGET_EXCEEDED',
                           '剩余处理时间不足以重试，尚未执行新的任务操作，请稍后重试。', 504, True)
    sleeper(seconds)
    remaining()


def read_response(response, limit, request_timeout):
    if _deadline.get() is None:
        return response.read(limit)
    chunks, total = [], 0
    reader = getattr(response, 'read1', response.read)
    while total < limit:
        left = remaining()
        # urllib's HTTPResponse exposes its socket through the buffered stream.
        # read1 performs at most one underlying read, allowing a trickling body
        # to be checked against the absolute deadline between reads.
        raw = getattr(getattr(response, 'fp', None), 'raw', None)
        sock = getattr(raw, '_sock', None)
        if sock is not None:
            try:
                sock.settimeout(min(request_timeout, left))
            except OSError:
                pass  # A completed response may already have closed its socket.
        chunk = reader(min(65536, limit - total))
        remaining()
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b''.join(chunks)
