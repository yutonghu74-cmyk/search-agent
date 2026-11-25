"""Durable HTTP observations for approved cleaning; no prompts or credentials."""
from contextlib import contextmanager, nullcontext, closing
from contextvars import ContextVar
from dataclasses import asdict
import json
import logging
import sqlite3
import time
import uuid
import math
import os


_observer = ContextVar('cleaning_http_observer', default=None)


@contextmanager
def observed_http(opener, request, *, timeout, model_kind):
    observer = _observer.get()
    scope = observer.http() if observer is not None and model_kind == 'vlm' else nullcontext()
    with scope:
        with opener.open(request, timeout=timeout) as response:
            yield response


class CleaningUsageRecorder:
    def __init__(self, path, binding, require_current):
        self.path, self.binding, self.require_current = path, binding, require_current
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            pass

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            with db:
                yield db

    @contextmanager
    def step(self, candidate, step, *, supported):
        self.require_current()
        step_token = uuid.uuid4().hex
        binding_json = json.dumps(asdict(self.binding), sort_keys=True)
        with self._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS cleaning_usage_steps (
                token TEXT PRIMARY KEY, operation_id TEXT NOT NULL, binding_json TEXT NOT NULL,
                candidate_id TEXT NOT NULL, step_id TEXT NOT NULL, supported INTEGER NOT NULL,
                status TEXT NOT NULL, elapsed_ms REAL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS cleaning_usage_http (
                token TEXT PRIMARY KEY, step_token TEXT NOT NULL,
                status TEXT NOT NULL, elapsed_ms REAL)''')
            db.execute('CREATE INDEX IF NOT EXISTS cleaning_usage_operation ON cleaning_usage_steps(operation_id)')
            db.execute('CREATE INDEX IF NOT EXISTS cleaning_usage_http_step ON cleaning_usage_http(step_token)')
            db.execute('INSERT INTO cleaning_usage_steps VALUES(?,?,?,?,?,?,?,?)',
                       (step_token, self.binding.operation_id, binding_json,
                        candidate.candidate_id, step.step_id, int(supported), 'started', None))
        parent = self

        class Observer:
            @contextmanager
            def http(self):
                parent.require_current()
                token = uuid.uuid4().hex
                with parent._connect() as db:
                    db.execute('INSERT INTO cleaning_usage_http VALUES(?,?,?,?)',
                               (token, step_token, 'started', None))
                started = time.perf_counter()
                status = 'error'
                try:
                    yield
                    status = 'returned'
                finally:
                    parent._finish('cleaning_usage_http', token, status, started)

        token = _observer.set(Observer())
        started = time.perf_counter()
        status = 'error'
        try:
            yield
            status = 'completed'
        finally:
            _observer.reset(token)
            self._finish('cleaning_usage_steps', step_token, status, started)

    def _finish(self, table, token, status, started):
        elapsed = (time.perf_counter() - started) * 1000
        try:
            with self._connect() as db:
                db.execute(f'UPDATE {table} SET status=?,elapsed_ms=? WHERE token=? AND status=?',
                           (status, elapsed, token, 'started'))
        except sqlite3.Error:
            # A missing completion remains visibly pending. Never force another
            # paid request merely because telemetry completion could not persist.
            logging.getLogger(__name__).warning('Cleaning usage completion unavailable')


def read_usage(db, approvals):
    """Read only this result lineage; historical absence stays unknown."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='cleaning_usage_steps'").fetchone():
        return None
    output = []
    for operation_id, approval in approvals.items():
        rows = db.execute('SELECT token,binding_json,candidate_id,step_id,supported,status,elapsed_ms '
                          'FROM cleaning_usage_steps WHERE operation_id=?', (operation_id,)).fetchall()
        candidates = {c.candidate_id for c in approval.job.candidates}
        steps = {s.step_id for s in approval.job.recipe.steps}
        for token, binding, cid, step_id, supported, status, elapsed in rows:
            if (json.loads(binding) != asdict(approval.job.binding) or cid not in candidates
                    or step_id not in steps or supported not in (0, 1)
                    or status not in {'started', 'completed', 'error'}):
                raise ValueError('invalid cleaning usage binding')
            attempts = db.execute('SELECT status,elapsed_ms FROM cleaning_usage_http WHERE step_token=?',
                                  (token,)).fetchall()
            if any(s not in {'started', 'returned', 'error'} for s, _ in attempts):
                raise ValueError('invalid HTTP usage status')
            for observed_status, ms in [(status, elapsed)] + attempts:
                if ((observed_status == 'started') != (ms is None)
                        or ms is not None and (type(ms) not in (int, float)
                                               or not math.isfinite(ms) or ms < 0)):
                    raise ValueError('invalid cleaning usage duration')
            output.append(dict(measurement_id=token, operation_id=operation_id, candidate_id=cid, step_id=step_id,
                               supported=bool(supported), status=status, step_elapsed_ms=elapsed,
                               http_attempts=sum(s != 'started' for s, _ in attempts),
                               pending_http_attempts=sum(s == 'started' for s, _ in attempts),
                               http_errors=sum(s == 'error' for s, _ in attempts),
                               http_elapsed_ms=sum(ms or 0 for s, ms in attempts if s != 'started')))
    return output
