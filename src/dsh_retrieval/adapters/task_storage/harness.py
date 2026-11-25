"""Durable planning journal in the SAME SQLite ledger as the business task.

No SDK sessions, credentials, business executions or automatic unknown retries.
All state transitions reauthorize under BEGIN IMMEDIATE and retain reservations.
"""
from contextlib import closing, contextmanager
from dataclasses import asdict
import json
import math
import sqlite3
import time
import uuid

from ...agent_contracts import validate_stage_authority
from ...errors import ServiceError, invalid
from ...harness.codec import dump_invocation, load_invocation
from ...harness.contracts import RuntimeHandle, RuntimeEvent
from ...models import Actor
from ...processing_requirements import identifier, positive
from ...query_spec import digest
from .sqlite import SQLiteTaskRepository


def _error(code, message, status=409):
    raise ServiceError(code, message, status)


class SQLiteHarnessJournal:
    task_contract_ref = 'retrieval-cleaning-stage@1'

    def __init__(self, repository, projects, *, clock=time.time, stage_codecs=None,
                 task_contract_ref=None, roles=frozenset({'retrieval', 'cleaning'})):
        if not isinstance(repository, SQLiteTaskRepository):
            raise ValueError('journal must share a SQLite business task repository')
        self.repository, self.path, self.projects, self.clock = repository, repository.path, projects, clock
        if type(roles) is not frozenset or not roles or any(not identifier(role) for role in roles):
            raise ValueError('installed journal roles required')
        self.stage_codecs, self.roles = stage_codecs, roles
        if task_contract_ref is not None:
            if not identifier(task_contract_ref): raise ValueError('installed task contract required')
            self.task_contract_ref = task_contract_ref
        with self._tx() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='harness_journal_meta'").fetchone()
            format_row = db.execute('SELECT format FROM harness_journal_meta WHERE id=1').fetchone() if exists else None
            if exists and (format_row is None or format_row[0] != 1):
                _error('STORAGE_FORMAT_UNSUPPORTED', 'Unsupported Harness journal format', 500)
            schema = '''
                CREATE TABLE IF NOT EXISTS harness_journal_meta(id INTEGER PRIMARY KEY, format INTEGER NOT NULL);
                INSERT OR IGNORE INTO harness_journal_meta VALUES(1,1);
                CREATE TABLE IF NOT EXISTS harness_stages(
                    stage_digest TEXT PRIMARY KEY, head_key TEXT NOT NULL, operation_id TEXT NOT NULL,
                    payload TEXT NOT NULL, budget_key TEXT NOT NULL, max_calls INTEGER NOT NULL,
                    UNIQUE(head_key,operation_id));
                CREATE TABLE IF NOT EXISTS harness_stage_heads(head_key TEXT PRIMARY KEY, stage_digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS harness_model_budgets(
                    budget_key TEXT PRIMARY KEY, max_calls INTEGER NOT NULL CHECK(max_calls>0),
                    reserved INTEGER NOT NULL CHECK(reserved>=0 AND reserved<=max_calls));
                CREATE TABLE IF NOT EXISTS harness_attempts(
                    run_id TEXT NOT NULL, attempt_id TEXT NOT NULL, invocation_digest TEXT NOT NULL,
                    stage_digest TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
                    queued_at REAL NOT NULL, allocation INTEGER NOT NULL CHECK(allocation>0),
                    admitted INTEGER NOT NULL DEFAULT 0 CHECK(admitted>=0 AND admitted<=allocation),
                    lease_token TEXT, lease_until REAL, PRIMARY KEY(run_id,attempt_id));
                CREATE INDEX IF NOT EXISTS harness_attempt_stage ON harness_attempts(stage_digest,status);
                CREATE TABLE IF NOT EXISTS harness_tool_receipts(
                    stage_digest TEXT NOT NULL, tool_id TEXT NOT NULL, action_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, kind TEXT NOT NULL, arguments TEXT NOT NULL, result TEXT NOT NULL,
                    run_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                    PRIMARY KEY(stage_digest,tool_id,action_key));
                CREATE TABLE IF NOT EXISTS harness_attempt_tools(
                    run_id TEXT NOT NULL, attempt_id TEXT NOT NULL, tool_id TEXT NOT NULL, action_key TEXT NOT NULL,
                    PRIMARY KEY(run_id,attempt_id,tool_id,action_key));
                CREATE TABLE IF NOT EXISTS harness_run_events(
                    run_id TEXT NOT NULL, attempt_id TEXT NOT NULL, cursor INTEGER NOT NULL,
                    kind TEXT NOT NULL, code TEXT, PRIMARY KEY(run_id,attempt_id,cursor));
            '''
            for statement in schema.split(';'):
                if statement.strip(): db.execute(statement)

    @property
    def storage_identity(self):
        return 'sqlite:'+str(self.path.resolve())

    @contextmanager
    def _tx(self):
        try:
            with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA synchronous=FULL')
                db.execute('BEGIN IMMEDIATE')
                yield db
        except sqlite3.Error:
            raise ServiceError('STORAGE_UNAVAILABLE', 'Harness journal unavailable', 503, True) from None

    @staticmethod
    def _head(stage):
        s = stage.scope
        return digest({'tenant': s.tenant_id, 'owner': s.owner_user_id, 'project': s.project_id,
                       'task': s.task_id, 'role': stage.role})

    @staticmethod
    def _budget(stage):
        # Different roles/operation IDs/budget refs cannot reset the task-generation cap.
        s = stage.scope
        return digest({'tenant': s.tenant_id, 'owner': s.owner_user_id, 'project': s.project_id,
                       'task': s.task_id, 'revision': s.revision, 'generation': s.generation})

    def task_in_transaction(self, db, actor, task_id):
        task = self.repository._read_task_binding(db, actor, task_id)
        if task is None:
            task = self.repository._load(db.execute(
                'SELECT format,payload FROM local_ledger WHERE id=1').fetchone()).get(actor, task_id)
        if (task.tenant_id, task.owner_user_id) != (actor.tenant_id, actor.user_id):
            _error('NOT_FOUND', 'Task not found', 404)
        self.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
        return task

    def _task(self, db, actor, stage):
        task = self.task_in_transaction(db, actor, stage.scope.task_id)
        if task.project_id != stage.scope.project_id:
            _error('NOT_FOUND', 'Task not found', 404)
        return task

    def _guard(self, db, actor, stage, *, approved=True):
        task = self._task(db, actor, stage)
        spec = json.loads(task.confirmed_proposal_json)
        spec = spec['query_spec']
        normalized = self.validate_authority(actor, task, stage, spec)
        if approved:
            row = db.execute('SELECT h.stage_digest,s.payload FROM harness_stage_heads h '
                             'JOIN harness_stages s ON s.stage_digest=h.stage_digest WHERE h.head_key=?',
                             (self._head(stage),)).fetchone()
            if row is None or row[0] != stage.stage_digest or json.loads(row[1]) != asdict(stage):
                _error('AGENT_CONFIRMATION_REQUIRED', 'Stage approval is not current')
        return normalized

    def validate_authority(self, actor, task, stage, spec):
        return validate_stage_authority(actor, task, stage, current=stage,
            spec=spec, requirements=spec.get('agent_requirements'))

    def model_limit(self, task, stage):
        return json.loads(task.confirmed_proposal_json)['query_spec']['agent_requirements']['autonomy']['max_calls']

    def decode_invocation(self, payload):
        return load_invocation(payload, stage_codecs=self.stage_codecs)

    def approve(self, actor, stage, *, action, proposal_digest):
        with self._tx() as db:
            return self._approve(db, actor, stage, action=action, proposal_digest=proposal_digest)

    def _approve(self, db, actor, stage, *, action, proposal_digest):
        self._guard(db, actor, stage, approved=False)
        if action != 'confirm' or proposal_digest != stage.stage_digest:
            _error('CONFIRMATION_REQUIRED', 'Explicit confirmation of the displayed stage required', 422)
        task = self._task(db, actor, stage)
        self.projects.require_access(actor, task.project_id, task.dimension_id, 'approve')
        limit = self.model_limit(task, stage)
        payload = json.dumps(asdict(stage), sort_keys=True, ensure_ascii=False)
        prior = db.execute('SELECT stage_digest FROM harness_stages WHERE head_key=? AND operation_id=?',
                           (self._head(stage), stage.operation_id)).fetchone()
        if prior and prior[0] != stage.stage_digest:
            _error('IDEMPOTENCY_CONFLICT', 'Stage operation changed')
        if prior:
            head = db.execute('SELECT stage_digest FROM harness_stage_heads WHERE head_key=?',
                              (self._head(stage),)).fetchone()
            if head is None or head[0] != stage.stage_digest:
                _error('AGENT_CONFIRMATION_CONFLICT', 'Old confirmation cannot restore a superseded stage')
        db.execute('INSERT OR IGNORE INTO harness_stages VALUES(?,?,?,?,?,?)',
                   (stage.stage_digest, self._head(stage), stage.operation_id, payload, self._budget(stage), limit))
        budget = db.execute('SELECT max_calls FROM harness_model_budgets WHERE budget_key=?',
                            (self._budget(stage),)).fetchone()
        if budget and budget[0] != limit:
            _error('AGENT_BUDGET_CONFLICT', 'Confirmed generation budget changed')
        db.execute('INSERT OR IGNORE INTO harness_model_budgets VALUES(?,?,0)', (self._budget(stage), limit))
        db.execute('INSERT INTO harness_stage_heads VALUES(?,?) ON CONFLICT(head_key) DO UPDATE '
                   'SET stage_digest=excluded.stage_digest', (self._head(stage), stage.stage_digest))


    @staticmethod
    def _event(db, handle, kind, code=None):
        cursor = db.execute('SELECT COALESCE(MAX(cursor),0)+1 FROM harness_run_events '
                            'WHERE run_id=? AND attempt_id=?', (handle.run_id, handle.attempt_id)).fetchone()[0]
        db.execute('INSERT INTO harness_run_events VALUES(?,?,?,?,?)',
                   (handle.run_id, handle.attempt_id, cursor, kind, code))

    @staticmethod
    def handle(inv):
        return RuntimeHandle(inv.run_id, inv.attempt_id, inv.runtime.release_digest, inv.invocation_digest)

    def _attempt(self, db, actor, handle, *, token=None, live=False, cleanup=False):
        cursor = db.execute('SELECT * FROM harness_attempts WHERE run_id=? AND attempt_id=?',
                            (handle.run_id, handle.attempt_id))
        row = cursor.fetchone()
        if row is None: _error('NOT_FOUND', 'Attempt not found', 404)
        if not isinstance(row, sqlite3.Row):
            row = dict(zip((column[0] for column in cursor.description), row))
        inv = self.decode_invocation(row['payload'])
        if self.handle(inv) != handle: _error('HARNESS_HANDLE_NOT_FOUND', 'Attempt binding mismatch', 404)
        if cleanup: self._task(db, actor, inv.stage)
        else: self._guard(db, actor, inv.stage)
        if live and (row['status'] != 'running' or row['lease_token'] != token
                     or row['lease_until'] <= self.clock() or inv.deadline <= self.clock()):
            _error('HARNESS_LEASE_LOST', 'Attempt lease expired or was revoked')
        return row, inv

    def enqueue(self, actor, invocation, *, model_calls):
        with self._tx() as db:
            return self._enqueue(db, actor, invocation, model_calls=model_calls)

    def _enqueue(self, db, actor, invocation, *, model_calls):
        if not positive(model_calls): raise invalid('positive bounded model allocation required')
        handle = self.handle(invocation)
        self._guard(db, actor, invocation.stage)
        row = db.execute('SELECT invocation_digest,allocation FROM harness_attempts WHERE run_id=? AND attempt_id=?',
                         (handle.run_id, handle.attempt_id)).fetchone()
        if row:
            if (row[0], row[1]) != (handle.invocation_digest, model_calls):
                _error('IDEMPOTENCY_CONFLICT', 'Attempt input or allocation changed')
            return handle
        if invocation.deadline <= self.clock(): _error('HARNESS_RUN_STOPPED', 'Attempt deadline reached')
        db.execute('INSERT INTO harness_attempts(run_id,attempt_id,invocation_digest,stage_digest,payload,status,queued_at,allocation) '
                   'VALUES(?,?,?,?,?,?,?,?)', (handle.run_id, handle.attempt_id, handle.invocation_digest,
                   invocation.stage.stage_digest, dump_invocation(invocation, stage_codecs=self.stage_codecs), 'queued', self.clock(), model_calls))
        self._event(db, handle, 'checkpoint', 'QUEUED')
        return handle

    def claim(self, actor, handle, *, lease_seconds=30):
        if type(lease_seconds) not in (int, float) or not math.isfinite(lease_seconds) or not 0 < lease_seconds <= 300:
            raise invalid('lease duration must be 0–300 seconds')
        with self._tx() as db:
            row, inv = self._attempt(db, actor, handle)
            if row['status'] != 'queued': return None
            if inv.deadline <= self.clock(): _error('HARNESS_RUN_STOPPED', 'Attempt deadline reached')
            if db.execute('SELECT 1 FROM harness_attempts WHERE stage_digest=? AND status=?',
                          (inv.stage.stage_digest, 'running')).fetchone():
                return None
            budget = db.execute('SELECT max_calls,reserved FROM harness_model_budgets WHERE budget_key=?',
                                (self._budget(inv.stage),)).fetchone()
            if row['allocation'] + budget[1] > budget[0]:
                _error('AGENT_BUDGET_EXCEEDED', 'Insufficient durable model-call budget')
            db.execute('UPDATE harness_model_budgets SET reserved=reserved+? WHERE budget_key=?',
                       (row['allocation'], self._budget(inv.stage)))
            token = uuid.uuid4().hex
            db.execute('UPDATE harness_attempts SET status=?,lease_token=?,lease_until=? WHERE run_id=? AND attempt_id=?',
                       ('running', token, min(inv.deadline, self.clock() + lease_seconds), handle.run_id, handle.attempt_id))
            self._event(db, handle, 'started')
            return token

    def renew(self, actor, handle, token, *, lease_seconds=30):
        if type(lease_seconds) not in (int, float) or not math.isfinite(lease_seconds) or not 0 < lease_seconds <= 300:
            raise invalid('invalid lease duration')
        with self._tx() as db:
            _, inv = self._attempt(db, actor, handle, token=token, live=True)
            db.execute('UPDATE harness_attempts SET lease_until=? WHERE run_id=? AND attempt_id=?',
                       (min(inv.deadline, self.clock() + lease_seconds), handle.run_id, handle.attempt_id))

    def model_admit(self, actor, handle, token, sequence):
        with self._tx() as db:
            row, _ = self._attempt(db, actor, handle, token=token, live=True)
            if type(sequence) is not int or sequence != row['admitted'] + 1:
                _error('MODEL_ADMISSION_CONFLICT', 'Model request already admitted or out of order')
            if row['admitted'] >= row['allocation']:
                _error('AGENT_BUDGET_EXCEEDED', 'Reserved model-call allowance exhausted')
            db.execute('UPDATE harness_attempts SET admitted=admitted+1 WHERE run_id=? AND attempt_id=?',
                       (handle.run_id, handle.attempt_id))
            self._event(db, handle, 'usage', 'MODEL_CALL_ADMITTED')

    def guard(self, actor, handle, token):
        with self._tx() as db: self._attempt(db, actor, handle, token=token, live=True)

    def tool_call(self, actor, handle, token, definition, arguments, fingerprint, handler):
        if definition.kind not in ('read', 'proposal', 'report'):
            _error('HARNESS_EXECUTION_UNAVAILABLE', 'Planning journal cannot execute business actions')
        action = fingerprint if definition.kind == 'read' else 'submission'
        with self._tx() as db:
            _, inv = self._attempt(db, actor, handle, token=token, live=True)
            key = (inv.stage.stage_digest, definition.tool_id, action)
            old = db.execute('SELECT fingerprint,result FROM harness_tool_receipts WHERE stage_digest=? AND tool_id=? AND action_key=?', key).fetchone()
            if old:
                if old[0] != fingerprint: _error('IDEMPOTENCY_CONFLICT', 'Stage proposal changed')
                result = json.loads(old[1])
            else:
                # Planning handlers MUST be pure, bounded reads/validation, never external work.
                result = handler(inv, arguments)
                self._attempt(db, actor, handle, token=token, live=True)
                encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
                if len(encoded.encode()) > 100000: raise invalid('tool result too large')
                db.execute('INSERT INTO harness_tool_receipts VALUES(?,?,?,?,?,?,?,?,?)',
                           (*key, fingerprint, definition.kind, json.dumps(arguments, ensure_ascii=False, allow_nan=False),
                            encoded, handle.run_id, handle.attempt_id))
            db.execute('INSERT OR IGNORE INTO harness_attempt_tools VALUES(?,?,?,?)',
                       (handle.run_id, handle.attempt_id, definition.tool_id, action))
            self._event(db, handle, 'tool_completed')
            return result

    @staticmethod
    def _outputs(db, handle, stage_digest):
        return tuple({'tool_id': row[0], 'arguments': json.loads(row[1]), 'receipt': json.loads(row[2])}
            for row in db.execute('''SELECT r.tool_id,r.arguments,r.result FROM harness_tool_receipts r
                JOIN harness_attempt_tools a ON r.tool_id=a.tool_id AND r.action_key=a.action_key
                WHERE a.run_id=? AND a.attempt_id=? AND r.stage_digest=? AND r.kind IN ('proposal','report')
                ORDER BY r.tool_id''', (handle.run_id, handle.attempt_id, stage_digest)))

    def finish(self, actor, handle, token, *, code=None):
        with self._tx() as db:
            _, inv = self._attempt(db, actor, handle, token=token, live=True)
            ready = code is None and bool(self._outputs(db, handle, inv.stage.stage_digest))
            status = 'finished' if ready else 'failed'
            db.execute('UPDATE harness_attempts SET status=?,lease_token=NULL,lease_until=NULL WHERE run_id=? AND attempt_id=?',
                       (status, handle.run_id, handle.attempt_id))
            self._event(db, handle, status, code or (None if ready else 'HARNESS_OUTPUT_MISSING'))

    def cancel(self, actor, handle):
        with self._tx() as db:
            row, _ = self._attempt(db, actor, handle, cleanup=True)
            if row['status'] in ('finished', 'failed', 'cancelled', 'unknown'): return
            db.execute('UPDATE harness_attempts SET status=?,lease_token=NULL,lease_until=NULL WHERE run_id=? AND attempt_id=?',
                       ('cancelled', handle.run_id, handle.attempt_id))
            self._event(db, handle, 'cancelled')

    def recover(self, actor, handle):
        with self._tx() as db:
            row, inv = self._attempt(db, actor, handle)
            if row['status'] != 'running' or row['lease_until'] > self.clock(): return row['status']
            status = 'finished' if self._outputs(db, handle, inv.stage.stage_digest) else 'unknown'
            db.execute('UPDATE harness_attempts SET status=?,lease_token=NULL,lease_until=NULL WHERE run_id=? AND attempt_id=?',
                       (status, handle.run_id, handle.attempt_id))
            self._event(db, handle, 'finished' if status == 'finished' else 'failed',
                        'RECOVERED_RECEIPT' if status == 'finished' else 'OUTCOME_UNKNOWN')
            return status

    def read(self, actor, handle, *, after_cursor=0):
        if type(after_cursor) is not int or after_cursor < 0: raise invalid('invalid journal cursor')
        with self._tx() as db:
            row, inv = self._attempt(db, actor, handle)
            budget = db.execute('SELECT max_calls,reserved FROM harness_model_budgets WHERE budget_key=?',
                                (self._budget(inv.stage),)).fetchone()
            events = tuple(RuntimeEvent(r[0], r[1], r[2]) for r in db.execute(
                'SELECT cursor,kind,code FROM harness_run_events WHERE run_id=? AND attempt_id=? AND cursor>? '
                'ORDER BY cursor LIMIT 100', (handle.run_id, handle.attempt_id, after_cursor)))
            return {'status': row['status'], 'invocation': inv, 'allocation': row['allocation'],
                    'admitted': row['admitted'], 'budget_reserved': budget[1], 'budget_limit': budget[0],
                    'events': events, 'outputs': self._outputs(db, handle, inv.stage.stage_digest)
                    if row['status'] == 'finished' else ()}

    def pending_outputs(self, actor, handle, token):
        with self._tx() as db:
            _, inv = self._attempt(db, actor, handle, token=token, live=True)
            return self._outputs(db, handle, inv.stage.stage_digest)

    def find(self, actor, run_id, attempt_id):
        with self._tx() as db:
            row = db.execute('SELECT payload FROM harness_attempts WHERE run_id=? AND attempt_id=?',
                             (run_id, attempt_id)).fetchone()
            if row is None: return None
            inv = self.decode_invocation(row[0])
            self._guard(db, actor, inv.stage)
            return inv


    def pending(self, actor, project_id, *, limit=32):
        if type(limit) is not int or not 1 <= limit <= 100: raise invalid('invalid pending-page limit')
        with self._tx() as db:
            placeholders = ','.join('?' for _ in self.roles)
            rows = db.execute("""SELECT payload FROM harness_attempts WHERE status='queued'
                AND json_extract(payload,'$.stage.scope.tenant_id')=?
                AND json_extract(payload,'$.stage.scope.owner_user_id')=?
                AND json_extract(payload,'$.stage.scope.project_id')=?
                AND json_extract(payload,'$.stage.role') IN (""" + placeholders + """ )
                ORDER BY queued_at,run_id,attempt_id LIMIT ?""",
                (actor.tenant_id, actor.user_id, project_id, *sorted(self.roles), limit)).fetchall()
            handles = []
            for row in rows:
                inv = self.decode_invocation(row[0]); handle = self.handle(inv)
                self._task(db, actor, inv.stage)
                try:
                    self._guard(db, actor, inv.stage)
                except ServiceError as error:
                    if error.code not in ('REVISION_CONFLICT', 'AGENT_CONFIRMATION_REQUIRED', 'INVALID_STATE_TRANSITION'):
                        raise
                    db.execute("UPDATE harness_attempts SET status='cancelled' WHERE run_id=? AND attempt_id=?",
                               (handle.run_id, handle.attempt_id))
                    self._event(db, handle, 'cancelled', 'STALE_STAGE')
                    continue
                handles.append(handle)
            return tuple(handles)


def purge_for_tasks(db, task_ids):
    """Called by the existing authorized hard-delete transaction, never standalone."""
    if not task_ids: return
    placeholders = ','.join('?' for _ in task_ids)
    stages = db.execute('SELECT stage_digest,head_key,budget_key FROM harness_stages WHERE '
                        "json_extract(payload,'$.scope.task_id') IN (" + placeholders + ')', tuple(task_ids)).fetchall()
    for stage, head, budget in stages:
        attempts = db.execute('SELECT run_id,attempt_id FROM harness_attempts WHERE stage_digest=?', (stage,)).fetchall()
        for run, attempt in attempts:
            for table in ('harness_run_events', 'harness_attempt_tools'):
                db.execute('DELETE FROM ' + table + ' WHERE run_id=? AND attempt_id=?', (run, attempt))
        db.execute('DELETE FROM harness_attempts WHERE stage_digest=?', (stage,))
        db.execute('DELETE FROM harness_tool_receipts WHERE stage_digest=?', (stage,))
        db.execute('DELETE FROM harness_stage_heads WHERE head_key=? AND stage_digest=?', (head, stage))
        db.execute('DELETE FROM harness_stages WHERE stage_digest=?', (stage,))
        if not db.execute('SELECT 1 FROM harness_stages WHERE budget_key=?', (budget,)).fetchone():
            db.execute('DELETE FROM harness_model_budgets WHERE budget_key=?', (budget,))
