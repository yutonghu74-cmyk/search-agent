"""Process-local fair dispatch; durable approved plans remain the recovery queue."""
from collections import deque
from dataclasses import dataclass
import threading
import time

from .errors import ServiceError


@dataclass(frozen=True)
class SearchLimits:
    workers: int = 2
    max_pending: int = 128
    max_calls: int = 128
    max_hit_slots: int = 1000
    max_pending_per_user: int = 16

    def __post_init__(self):
        for name, upper in (('workers', 16), ('max_pending', 4096),
                            ('max_calls', 100000), ('max_hit_slots', 1000000), ('max_pending_per_user', 4096)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError('invalid search limit: ' + name)
        if self.max_pending < self.workers:
            raise ValueError('search queue must hold at least all workers')


@dataclass(frozen=True)
class SearchRetry:
    delay: float = 0.25


class FairSearchQueue:
    """FIFO per user, round-robin between users, one active task per user.

    Callback executes one page, returning true to continue at the tail. Duplicate
    notifications cannot run a task twice; a notification during execution gets
    one additional turn. No external call occurs while holding the queue lock.
    """
    def __init__(self, callback, limits, *, start=True, name='search'):
        self.callback, self.limits = callback, limits
        self.condition = threading.Condition()
        self.queues, self.users = {}, deque()
        self.known, self.active_users, self.again = set(), set(), set()
        self.closed = False
        self.not_before = {}
        self.errors = {}
        self.workers = [threading.Thread(target=self._work, daemon=True,
                        name=f'{name}-{index}') for index in range(limits.workers)]
        if start:
            for worker in self.workers:
                worker.start()

    def enqueue(self, actor, task_id, *, wait=False):
        item = (actor, task_id)
        with self.condition:
            if self.closed:
                raise ServiceError('SEARCH_EXECUTOR_STOPPED', '检索执行器已停止', 503)
            if item in self.known:
                if actor in self.active_users and not any(item in q for q in self.queues.values()):
                    self.again.add(item)
                return
            def full():
                return (len(self.known) >= self.limits.max_pending or
                        sum(known_actor == actor for known_actor, _ in self.known) >= self.limits.max_pending_per_user)
            while wait and not self.closed and full():
                self.condition.wait()
            if self.closed:
                raise ServiceError('SEARCH_EXECUTOR_STOPPED', '检索执行器已停止', 503)
            if item in self.known:
                return
            if sum(known_actor == actor for known_actor, _ in self.known) >= self.limits.max_pending_per_user:
                raise ServiceError('SEARCH_USER_QUEUE_FULL', '你的检索排队已满，请稍后重试', 503, True)
            if len(self.known) >= self.limits.max_pending:
                raise ServiceError('SEARCH_QUEUE_FULL', '检索排队已满，请稍后重试', 503, True)
            self.errors.pop(item, None)
            self.known.add(item)
            self._append(item)
            self.condition.notify_all()

    def recover(self, items):
        """Interleave durable recovery owners before waiting for bounded slots."""
        groups = {}
        for actor, identifier in items:
            groups.setdefault(actor, deque()).append(identifier)
        actors = deque(groups)
        while actors:
            actor = actors.popleft()
            self.enqueue(actor, groups[actor].popleft(), wait=True)
            if groups[actor]:
                actors.append(actor)

    def _append(self, item):
        actor, _ = item
        if actor not in self.queues:
            self.queues[actor] = deque()
            self.users.append(actor)
        self.queues[actor].append(item)

    def _take(self):
        for _ in range(len(self.users)):
            actor = self.users.popleft()
            if actor in self.active_users or self.not_before.get(self.queues[actor][0], 0) > time.monotonic():
                self.users.append(actor)
                continue
            item = self.queues[actor].popleft()
            self.not_before.pop(item, None)
            self.errors.pop(item, None)
            if self.queues[actor]:
                self.users.append(actor)
            else:
                del self.queues[actor]
            self.active_users.add(actor)
            return item
        return None

    def _work(self):
        while True:
            with self.condition:
                item = None
                while not self.closed and item is None:
                    item = self._take()
                    if item is None:
                        due = [value for value in self.not_before.values() if value > time.monotonic()]
                        self.condition.wait(timeout=max(0.001, min(due) - time.monotonic()) if due else None)
                if self.closed:
                    return
            more, error_code = False, None
            try:
                more = self.callback(*item)
            except ServiceError as error:
                error_code = error.code
            except Exception:
                import logging
                logging.getLogger(__name__).error('Search activation failed unexpectedly')
                error_code = 'SEARCH_EXECUTION_FAILED'
            finally:
                with self.condition:
                    self.active_users.remove(item[0])
                    if error_code:
                        self.errors[item] = error_code
                        while len(self.errors) > self.limits.max_pending:
                            self.errors.pop(next(iter(self.errors)))
                    if not self.closed and (more or item in self.again):
                        self._append(item)
                        if isinstance(more, SearchRetry):
                            self.not_before[item] = time.monotonic() + more.delay
                    else:
                        self.known.discard(item)
                    self.again.discard(item)
                    self.condition.notify_all()

    def status(self, actor, task_id):
        item = (actor, task_id)
        with self.condition:
            queued = any(item in q for q in self.queues.values())
            return {'state': 'queued' if queued else 'running' if item in self.known else 'failed' if item in self.errors else 'not_queued',
                    'error_code': self.errors.get(item), 'workers': self.limits.workers}

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        # Join before closing shared stores; approved checkpoints recover after restart.
        for worker in self.workers:
            if worker.is_alive():
                worker.join()
