"""Durable conversation receipts with a pre-write checkpoint and fenced replay.

The journal shares the dialogue connection. Pre-task dialogue state and the
receipt commit atomically. Formal task writes remain in their own repository:
the checkpoint replays its exact idempotent write after a cross-database crash,
rather than reinterpreting the message. Async acceptance uses the same journal.
"""
from dataclasses import asdict
from datetime import UTC, datetime
import json
import uuid
import threading
from time import perf_counter
from concurrent.futures import ThreadPoolExecutor

from . import model_budget
from .errors import ServiceError
from .query_spec import digest
from .service import require_key


class ConversationCommandStore:
    def __init__(self, dialogue_store, *, max_workers=2, max_pending=8):
        if type(max_workers) is not int or type(max_pending) is not int or not 1 <= max_workers <= max_pending:
            raise ValueError('Require 1 <= max_workers <= max_pending')
        self.store = dialogue_store
        self._executor = None
        self._max_workers = max_workers
        self._slots = threading.BoundedSemaphore(max_pending)
        with self.store.lock, self.store.connection:
            self.store.connection.execute('''CREATE TABLE IF NOT EXISTS task_conversation_commands (
                tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, task_id TEXT NOT NULL,
                command_key TEXT NOT NULL, fingerprint TEXT NOT NULL, request_json TEXT NOT NULL,
                status TEXT NOT NULL, owner TEXT, plan_json TEXT, result_json TEXT, error_json TEXT,
                attempts INTEGER NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id,user_id,task_id,command_key))''')

    def recover_interrupted(self):
        """Called once by service startup, after the previous service has stopped."""
        with self.store.lock, self.store.connection:
            return self.store.connection.execute('''UPDATE task_conversation_commands
                SET owner=NULL,status=CASE WHEN plan_json IS NULL THEN 'interrupted' ELSE 'prepared' END
                WHERE owner IS NOT NULL''').rowcount

    @staticmethod
    def dialogue_scope(project_id):
        # Historical column name is task_id; this namespace is an aggregate
        # address only. No formal task, revision, batch or run is created.
        return 'dialogue-project:' + digest(project_id)

    @staticmethod
    def _scope(actor, task_id, key):
        require_key(key)
        return actor.tenant_id, actor.user_id, task_id, key

    def _row(self, scope):
        cursor = self.store.connection.execute('''SELECT fingerprint,status,owner,plan_json,
            result_json,error_json,attempts,updated_at FROM task_conversation_commands
            WHERE tenant_id=? AND user_id=? AND task_id=? AND command_key=?''', scope)
        row = cursor.fetchone()
        return dict(zip(('fingerprint', 'status', 'owner', 'plan', 'result', 'error', 'attempts', 'updated_at'), row)) if row else None

    def read(self, actor, task_id, key):
        scope = self._scope(actor, task_id, key)
        with self.store.lock:
            row = self._row(scope)
        if row is None:
            raise ServiceError('NOT_FOUND', '这条消息尚未被服务接收。', 404)
        return {'schema_version': 'task-conversation-command@1', 'task_id': task_id, 'command_key': key,
                'status': row['status'], 'attempts': row['attempts'], 'updated_at': row['updated_at'],
                'result': json.loads(row['result']) if row['result'] else None,
                'error': json.loads(row['error']) if row['error'] else None}

    def _claim(self, actor, task_id, key, request, *, retry_failed=False):
        scope = self._scope(actor, task_id, key)
        owner = uuid.uuid4().hex
        fingerprint = digest(request)
        now = datetime.now(UTC).isoformat()
        with self.store.lock, self.store.connection:
            self.store.connection.execute('BEGIN IMMEDIATE')
            row = self._row(scope)
            if row:
                if row['fingerprint'] != fingerprint:
                    raise ServiceError('IDEMPOTENCY_CONFLICT', '同一消息标识不能用于另一条消息。', 409)
                if row['result']:
                    return None, json.loads(row['result'])
                if row['owner']:
                    raise ServiceError('COMMAND_RUNNING', '这条消息仍在处理，请查询处理结果。', 409, True)
                if row['status'] in ('failed', 'interrupted') and not retry_failed:
                    if row['error']:
                        raise ServiceError(**json.loads(row['error']))
                    raise ServiceError('COMMAND_INTERRUPTED', '处理因服务重启中断，可以明确重试。', 409, True)
                self.store.connection.execute('''UPDATE task_conversation_commands SET owner=?,
                    status=CASE WHEN plan_json IS NULL THEN 'understanding' ELSE 'prepared' END,
                    error_json=NULL,attempts=attempts+1,updated_at=?
                    WHERE tenant_id=? AND user_id=? AND task_id=? AND command_key=?''', (owner, now, *scope))
            else:
                self.store.connection.execute('''INSERT INTO task_conversation_commands
                    (tenant_id,user_id,task_id,command_key,fingerprint,request_json,status,owner,attempts,updated_at)
                    VALUES (?,?,?,?,?,?,'understanding',?,1,?)''',
                    (*scope, fingerprint, json.dumps(request, ensure_ascii=False), owner, now))
        return TaskCommandReceipt(self, scope, owner, json.loads(row['plan']) if row and row['plan'] else None), None

    def _finish(self, receipt, request, callback):
        try:
            if receipt.plan is None:
                model_budget.remaining()
            result = callback(receipt)
            receipt.complete(result, request.get('text', request.get('message')))
            return result
        except ServiceError as error:
            receipt.fail(error)
            raise
        except Exception:
            error = ServiceError('COMMAND_FAILED', '消息处理未完成，请查看处理结果后重试。', 500, True)
            receipt.fail(error)
            raise error from None


    @model_budget.bounded
    def execute(self, actor, task_id, key, request, callback, *, retry_failed=False):
        receipt, result = self._claim(actor, task_id, key, request, retry_failed=retry_failed)
        if receipt is None:
            return result, True
        return self._finish(receipt, request, callback), False

    @model_budget.bounded
    def submit(self, actor, task_id, key, request, callback, *, retry_failed=False):
        """Commit acceptance before starting the bounded in-process model work."""
        from contextvars import copy_context
        try:
            receipt, result = self._claim(actor, task_id, key, request, retry_failed=retry_failed)
        except ServiceError as error:
            if error.code == 'COMMAND_RUNNING':
                return self.read(actor, task_id, key), True
            raise
        if receipt is None:
            return self.read(actor, task_id, key), True
        if not self._slots.acquire(blocking=False):
            error = ServiceError('COMMAND_QUEUE_FULL', '后台处理已满，请稍后用原消息重试。', 503, True)
            receipt.fail(error)
            raise error
        with model_budget.budget(check=False):
            context = copy_context()
        queued_at = perf_counter()
        def run():
            try:
                receipt.queue_wait_ms = round((perf_counter() - queued_at) * 1000, 3)
                context.run(self._finish, receipt, request, callback)
            except ServiceError:
                # The durable safe error is already queryable; never log raw
                # model exceptions or request content from a background thread.
                pass
            finally:
                self._slots.release()
        try:
            with self.store.lock:
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix='conversation-command')
                self._executor.submit(run)
        except Exception:
            self._slots.release()
            error = ServiceError('COMMAND_START_FAILED', '后台处理未启动，可以重试。', 503, True)
            receipt.fail(error)
            raise error from None
        return self.read(actor, task_id, key), False

    def close(self):
        if self._executor is not None:
            self._executor.shutdown(wait=True)


class TaskCommandReceipt:
    def __init__(self, journal, scope, owner, plan):
        self.journal, self.scope, self.owner, self.plan = journal, scope, owner, plan

    def _require_owner(self):
        row = self.journal._row(self.scope)
        if not row or row['owner'] != self.owner:
            raise ServiceError('COMMAND_SUPERSEDED', '此处理轮次已失效，请读取最新结果。', 409)

    def checkpoint(self, plan):
        if self.plan is None:
            model_budget.remaining()
        store = self.journal.store
        with store.lock, store.connection:
            self._require_owner()
            if self.plan is None:
                model_budget.remaining()
            store.connection.execute('''UPDATE task_conversation_commands SET plan_json=?,status='prepared',updated_at=?
                WHERE tenant_id=? AND user_id=? AND task_id=? AND command_key=?''',
                (json.dumps(plan, ensure_ascii=False), datetime.now(UTC).isoformat(), *self.scope))
        self.plan = plan

    def complete(self, result, text):
        store = self.journal.store
        with store.lock, store.connection:
            store.connection.execute('BEGIN IMMEDIATE')
            self._require_owner()
            if self.plan.get('kind') == 'dialogue':
                from .models import Actor
                plan = self.plan
                session_id, version = store._save(Actor(*self.scope[:2]), plan['project_id'],
                    plan['session_id'], plan['expected_version'], plan['state'],
                    new_messages=plan['new_messages'], new_session_id=plan['result']['session_id'])
                if (session_id, version) != (result['session_id'], result['version']):
                    raise ServiceError('COMMAND_RESULT_CONFLICT', 'Conversation result differs from checkpoint', 409)
            else:
                task = self.plan['task']
                store.connection.execute('''INSERT INTO task_dialogue_turns
                (tenant_id,user_id,project_id,task_id,task_version,user_text,assistant_text) VALUES (?,?,?,?,?,?,?)''',
                (*self.scope[:2], task['project_id'], self.scope[2], task['task_version'], text,
                 result.get('reply') or '本轮理解：' + result['kind']))
            store.connection.execute('''UPDATE task_conversation_commands SET result_json=?,status='completed',
                owner=NULL,error_json=NULL,updated_at=? WHERE tenant_id=? AND user_id=? AND task_id=? AND command_key=?''',
                (json.dumps(result, ensure_ascii=False), datetime.now(UTC).isoformat(), *self.scope))

    def fail(self, error):
        store = self.journal.store
        with store.lock, store.connection:
            store.connection.execute('''UPDATE task_conversation_commands SET status='failed',error_json=?,
                owner=NULL,updated_at=? WHERE tenant_id=? AND user_id=? AND task_id=? AND command_key=? AND owner=?''',
                (json.dumps(asdict(error), ensure_ascii=False), datetime.now(UTC).isoformat(), *self.scope, self.owner))


# Existing callers and journal files remain compatible during the migration.
TaskCommandStore = ConversationCommandStore
