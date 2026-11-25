"""Conservative durable retrieval reservations, separate from VLM/billing usage."""
from contextlib import closing, contextmanager
import sqlite3
import threading
from functools import wraps

from .errors import ServiceError

def storage_errors(operation):
    @wraps(operation)
    def call(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except sqlite3.Error:
            raise ServiceError('STORAGE_UNAVAILABLE', '检索预算账本暂不可用', 503, True) from None
    return call



class SearchBudget:
    @staticmethod
    def bind_limits(db, scope, *, max_calls, max_hit_slots):
        """Pin confirmed ceilings in the caller's original job-intent transaction."""
        if (type(max_calls) is not int or not 1 <= max_calls <= 100000
                or type(max_hit_slots) is not int or not 1 <= max_hit_slots <= 1000000):
            raise ServiceError('INVALID_RETRIEVAL_PLAN', 'Confirmed retrieval ceilings required', 422)
        key = SearchBudget._key(scope)
        db.execute('INSERT OR IGNORE INTO retrieval_execution_budgets VALUES (?,?,?,?,?,?,0,0,?,?,NULL)',
                   (*key, max_calls, max_hit_slots))
        row = db.execute('SELECT calls_reserved,hit_slots_reserved,max_calls,max_hit_slots,stop_reason '
            'FROM retrieval_execution_budgets WHERE tenant_id=? AND owner_user_id=? AND project_id=? '
            'AND task_id=? AND revision=? AND generation=?', key).fetchone()
        calls, hits, old_calls, old_hits, reason = SearchBudget._validate(row)
        if calls > max_calls or hits > max_hit_slots:
            raise ServiceError('AGENT_BUDGET_EXCEEDED', 'Existing reservations exceed the confirmed ceilings', 409)
        db.execute('UPDATE retrieval_execution_budgets SET max_calls=?,max_hit_slots=? WHERE tenant_id=? '
            'AND owner_user_id=? AND project_id=? AND task_id=? AND revision=? AND generation=?',
            (min(old_calls, max_calls), min(old_hits, max_hit_slots), *key))

    @storage_errors
    def __init__(self, path, limits):
        self.path, self.limits = str(path), limits
        self.slots = threading.BoundedSemaphore(limits.workers)
        self.lock, self.active = threading.Lock(), set()
        self.active_users = set()
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS retrieval_execution_budgets(
                tenant_id TEXT NOT NULL, owner_user_id TEXT NOT NULL, project_id TEXT NOT NULL,
                task_id TEXT NOT NULL, revision INTEGER NOT NULL, generation INTEGER NOT NULL,
                calls_reserved INTEGER NOT NULL, hit_slots_reserved INTEGER NOT NULL,
                max_calls INTEGER NOT NULL, max_hit_slots INTEGER NOT NULL,
                stop_reason TEXT, PRIMARY KEY(tenant_id,owner_user_id,project_id,task_id,revision,generation))''')
            db.execute('CREATE INDEX IF NOT EXISTS retrieval_budget_task ON retrieval_execution_budgets(task_id)')

    @staticmethod
    def _key(scope):
        return (scope.tenant_id, scope.owner_user_id, scope.project_id,
                scope.task_id, scope.revision, scope.generation)

    @staticmethod
    def _validate(row):
        calls, hits, max_calls, max_hits, reason = row
        if (any(type(value) is not int for value in row[:4])
                or not 1 <= max_calls <= 100000 or not 1 <= max_hits <= 1000000
                or not 0 <= calls <= max_calls or not 0 <= hits <= max_hits
                or reason not in (None, 'call_limit', 'hit_slot_limit')):
            raise ServiceError('STORAGE_CORRUPT', '检索预算记录无效', 500)
        return row

    @storage_errors
    def snapshot(self, scope):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            row = db.execute('SELECT calls_reserved,hit_slots_reserved,max_calls,max_hit_slots,stop_reason '
                             'FROM retrieval_execution_budgets WHERE tenant_id=? AND owner_user_id=? '
                             'AND project_id=? AND task_id=? AND revision=? AND generation=?', self._key(scope)).fetchone()
        calls, hits, max_calls, max_hits, reason = self._validate(row or (0, 0, self.limits.max_calls, self.limits.max_hit_slots, None))
        return {'accounting': 'reservations_since_enablement', 'calls_reserved': calls,
                'hit_slots_reserved': hits, 'max_calls': max_calls, 'max_hit_slots': max_hits,
                'remaining_calls': max(0, max_calls - calls),
                'remaining_hit_slots': max(0, max_hits - hits), 'stop_reason': reason}

    @storage_errors
    def _charge(self, scope, batch_size):
        if type(batch_size) is not int or batch_size < 1:
            raise ServiceError('INVALID_RETRIEVAL_PLAN', 'Invalid search batch size', 422)
        key = self._key(scope)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO retrieval_execution_budgets VALUES (?,?,?,?,?,?,0,0,?,?,NULL)',
                       (*key, self.limits.max_calls, self.limits.max_hit_slots))
            row = db.execute('SELECT calls_reserved,hit_slots_reserved,max_calls,max_hit_slots,stop_reason '
                             'FROM retrieval_execution_budgets WHERE tenant_id=? AND owner_user_id=? '
                             'AND project_id=? AND task_id=? AND revision=? AND generation=?', key).fetchone()
            calls, hits, max_calls, max_hits, reason = self._validate(row)
            max_calls, max_hits = max(calls, min(max_calls, self.limits.max_calls)), max(hits, min(max_hits, self.limits.max_hit_slots))
            reason = reason or ('call_limit' if calls >= max_calls else
                                'hit_slot_limit' if hits + batch_size > max_hits else None)
            db.execute('UPDATE retrieval_execution_budgets SET calls_reserved=?,hit_slots_reserved=?, '
                       'max_calls=?,max_hit_slots=?,stop_reason=? WHERE tenant_id=? AND owner_user_id=? '
                       'AND project_id=? AND task_id=? AND revision=? AND generation=?',
                       (calls + (reason is None), hits + (batch_size if reason is None else 0),
                        max_calls, max_hits, reason, *key))
            if reason and row[4] is None:
                from .task_events import publish_retrieval_budget_stop
                publish_retrieval_budget_stop(db, scope, reason)
        if reason:
            raise ServiceError('SEARCH_BUDGET_EXHAUSTED', '本任务检索预算已用尽，已停止继续召回', 409)

    @contextmanager
    def execution(self, scope):
        # Include manual and refill calls in the same service execution bound.
        key = self._key(scope)
        user = key[:2]
        with self.lock:
            if key in self.active or user in self.active_users or not self.slots.acquire(blocking=False):
                raise ServiceError('SEARCH_BUSY', '检索执行中，请稍后重试', 503, True)
            self.active.add(key)
            self.active_users.add(user)
        try:
            yield
        finally:
            with self.lock:
                self.active.remove(key)
                self.active_users.remove(user)
                self.slots.release()

    @contextmanager
    def attempt(self, scope, batch_size):
        with self.execution(scope):
            self._charge(scope, batch_size)
            yield
