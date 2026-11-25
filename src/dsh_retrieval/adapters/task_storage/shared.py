"""External task associations, not a parallel task/job or approval model."""
from contextlib import closing, contextmanager
import json
import sqlite3

from ...errors import ServiceError, invalid
from ...models import RunStatus, TaskLifecycle
from ...query_spec import digest
from ...service import require_key
from ...shared_access import external_ref
from . import ledger_rows
from .sqlite import SQLiteTaskRepository


class SQLiteExternalBindings:
    def __init__(self, repository, projects, *, max_bindings=1000):
        if not isinstance(repository, SQLiteTaskRepository) or type(max_bindings) is not int or not 1 <= max_bindings <= 10000:
            raise ValueError('persistent task repository and bounded association capacity required')
        self.repository, self.projects, self.max_bindings = repository, projects, max_bindings
        with self._tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS external_task_bindings(
                tenant TEXT, owner TEXT, project TEXT, integration TEXT, external_ref TEXT,
                task_id TEXT, revision INTEGER, generation INTEGER, requirement_digest TEXT,
                PRIMARY KEY(tenant,owner,project,integration,external_ref))''')
            db.execute('''CREATE TABLE IF NOT EXISTS external_task_actions(
                tenant TEXT, owner TEXT, project TEXT, integration TEXT, task_id TEXT,
                action TEXT, action_key TEXT, fingerprint TEXT, result_json TEXT,
                PRIMARY KEY(tenant,owner,project,integration,task_id,action,action_key))''')

    @contextmanager
    def _tx(self):
        try:
            with closing(self.repository._connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                yield db
        except sqlite3.Error:
            raise ServiceError('STORAGE_UNAVAILABLE','Shared associations are unavailable',503,True) from None

    @staticmethod
    def _scope(principal):
        if principal.kind != 'integration': raise ServiceError('SHARED_CAPABILITY_DENIED', 'Integration identity required', 403)
        return principal.actor.tenant_id, principal.actor.user_id, principal.project_id, principal.integration_ref

    def _task(self, db, principal, task_id, action='read'):
        if task_id not in principal.task_ids: raise ServiceError('NOT_FOUND', 'Task not found', 404)
        memory = self.repository._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
        task = memory.get(principal.actor, task_id)
        if task.project_id != principal.project_id: raise ServiceError('NOT_FOUND', 'Task not found', 404)
        self.projects.require_access(principal.actor, task.project_id, task.dimension_id, action)
        return memory, task

    @staticmethod
    def _view(row, task):
        stale = ((row[6], row[7], row[8]) != (task.confirmed_revision, task.run_generation, digest(task.confirmed_proposal_json)))
        return {'schema_version':'external-task-binding@1', 'external_task_ref':row[4],
            'integration_ref':row[3], 'project_id':row[2], 'task_id':row[5],
            'revision':row[6], 'generation':row[7], 'stale':stale,
            'task_version':task.task_version, 'run_status':task.run_status.value}

    def _binding(self, db, principal, task_id, ref, *, current=True):
        if not external_ref(ref): raise invalid('bounded external task reference required')
        row = db.execute('SELECT * FROM external_task_bindings WHERE tenant=? AND owner=? AND project=? AND integration=? AND external_ref=?',
            (*self._scope(principal), ref)).fetchone()
        if row is None or row[5] != task_id: raise ServiceError('NOT_FOUND', 'External task binding not found', 404)
        memory, task = self._task(db, principal, task_id)
        view = self._view(row, task)
        if current and (view['stale'] or task.lifecycle != TaskLifecycle.ACTIVE or task.run_status == RunStatus.CANCELLED):
            raise ServiceError('EXTERNAL_BINDING_STALE', 'External task revision/generation is no longer current', 409)
        return memory, task, view

    def get(self, principal, task_id, ref, *, current=True):
        with self._tx() as db: return self._binding(db, principal, task_id, ref, current=current)[2]

    def bind(self, principal, task_id, key, body):
        require_key(key)
        required={'external_task_ref','expected_revision','expected_generation','expected_task_version'}
        if (type(body) is not dict or set(body)!=required or not external_ref(body['external_task_ref'])
                or any(type(body[k]) is not int or body[k]<=0 for k in required-{'external_task_ref'})):
            raise invalid('external reference and current task versions required')
        scope=self._scope(principal); fingerprint=digest(body)
        with self._tx() as db:
            _,task=self._task(db,principal,task_id)
            action=(*scope,task_id,'bind',key)
            old=db.execute('SELECT fingerprint FROM external_task_actions WHERE tenant=? AND owner=? AND project=? AND integration=? AND task_id=? AND action=? AND action_key=?',action).fetchone()
            if old:
                if old[0]!=fingerprint: raise ServiceError('IDEMPOTENCY_CONFLICT','Binding request changed',409)
                return self._binding(db,principal,task_id,body['external_task_ref'],current=False)[2],True
            if (task.confirmed_revision,task.run_generation,task.task_version)!=(body['expected_revision'],body['expected_generation'],body['expected_task_version']):
                raise ServiceError('REVISION_CONFLICT','Task changed before binding',409)
            if task.lifecycle!=TaskLifecycle.ACTIVE or task.run_status==RunStatus.CANCELLED:
                raise ServiceError('INVALID_STATE_TRANSITION','Active confirmed task required',409)
            record=(*scope,body['external_task_ref'],task_id,task.confirmed_revision,task.run_generation,digest(task.confirmed_proposal_json))
            prior=db.execute('SELECT * FROM external_task_bindings WHERE tenant=? AND owner=? AND project=? AND integration=? AND external_ref=?',record[:5]).fetchone()
            if prior is not None and tuple(prior)!=record:
                raise ServiceError('EXTERNAL_BINDING_CONFLICT','External reference is already bound',409)
            if prior is None and db.execute('SELECT COUNT(*) FROM external_task_bindings WHERE tenant=? AND owner=? AND project=? AND integration=?',scope).fetchone()[0]>=self.max_bindings:
                raise ServiceError('SHARED_BINDING_LIMIT','Integration association capacity reached',409)
            self.projects.require_access(principal.actor,task.project_id,task.dimension_id,'read')
            db.execute('INSERT OR IGNORE INTO external_task_bindings VALUES(?,?,?,?,?,?,?,?,?)',record)
            result=self._view(record,task)
            db.execute('INSERT INTO external_task_actions VALUES(?,?,?,?,?,?,?,?,?)',(*action,fingerprint,json.dumps(result)))
            return result,prior is not None

    def cancel(self, principal, task_id, ref, key, body):
        require_key(key)
        if 'task.cancel' not in principal.capabilities:
            raise ServiceError('SHARED_CAPABILITY_DENIED','Task cancellation not granted',403)
        if (type(body) is not dict or set(body)!={'action','expected_task_version'} or body['action']!='cancel'
                or type(body['expected_task_version']) is not int or body['expected_task_version']<=0):
            raise invalid('explicit cancel and displayed task version required')
        with self._tx() as db:
            memory,task,view=self._binding(db,principal,task_id,ref,current=False)
            self.projects.require_access(principal.actor,task.project_id,task.dimension_id,'write')
            action=(*self._scope(principal),task_id,'cancel',key); fingerprint=digest({'ref':ref,**body})
            old=db.execute('SELECT fingerprint,result_json FROM external_task_actions WHERE tenant=? AND owner=? AND project=? AND integration=? AND task_id=? AND action=? AND action_key=?',action).fetchone()
            if old:
                if old[0]!=fingerprint: raise ServiceError('IDEMPOTENCY_CONFLICT','Cancellation changed',409)
                return json.loads(old[1]),True
            if view['stale'] or task.task_version!=body['expected_task_version']:
                raise ServiceError('REVISION_CONFLICT','Task changed before cancellation',409)
            from .task_controls import cancel_task
            result=cancel_task(db,self.repository,self.projects,principal.actor,memory,task)
            db.execute('INSERT INTO external_task_actions VALUES(?,?,?,?,?,?,?,?,?)',(*action,fingerprint,json.dumps(result)))
            return result,False

    def invoke_once(self, principal, task_id, ref, key, operation, body, callback):
        require_key(key)
        if operation not in ('retrieval.prepare','cleaning.prepare','cleaning.execute'): raise invalid('invalid shared preparation')
        if operation not in principal.capabilities:
            raise ServiceError('SHARED_CAPABILITY_DENIED','Shared operation not granted',403)
        action=(*self._scope(principal),task_id,operation,key)
        fingerprint=digest({'external_task_ref':ref,'body':body})
        with self._tx() as db:
            self._binding(db,principal,task_id,ref)
            old=db.execute('SELECT fingerprint,result_json FROM external_task_actions WHERE tenant=? AND owner=? AND project=? AND integration=? AND task_id=? AND action=? AND action_key=?',action).fetchone()
            if old:
                if old[0]!=fingerprint: raise ServiceError('IDEMPOTENCY_CONFLICT','Shared preparation changed',409)
                if old[1] is None: raise ServiceError('SHARED_OUTCOME_UNKNOWN','Operation is running or interrupted; query its receipt before retrying',409,True)
                value=json.loads(old[1])
                if 'error' in value: raise ServiceError(**value['error'])
                return value['result'],True
            db.execute('INSERT INTO external_task_actions VALUES(?,?,?,?,?,?,?,?,NULL)',(*action,fingerprint))
        try:
            result=callback()
            encoded=json.dumps({'result':result},ensure_ascii=False,allow_nan=False)
            if len(encoded.encode())>1_000_000: raise ServiceError('SHARED_RESPONSE_LIMIT','Preparation response too large',502)
        except ServiceError as error:
            encoded=json.dumps({'error':{'code':error.code,'message':'Shared preparation failed',
                'status':error.status,'retryable':error.retryable}})
            with self._tx() as db:
                db.execute('UPDATE external_task_actions SET result_json=? WHERE tenant=? AND owner=? AND project=? AND integration=? AND task_id=? AND action=? AND action_key=?',(encoded,*action))
            raise
        with self._tx() as db:
            self._binding(db,principal,task_id,ref)
            db.execute('UPDATE external_task_actions SET result_json=? WHERE tenant=? AND owner=? AND project=? AND integration=? AND task_id=? AND action=? AND action_key=?',(encoded,*action))
        return result,False

    def receipt(self, principal, task_id, ref, key, operation):
        require_key(key)
        if operation not in ('retrieval.prepare','cleaning.prepare','cleaning.execute'): raise invalid('invalid shared preparation')
        with self._tx() as db:
            self._binding(db,principal,task_id,ref,current=False)
            row=db.execute('SELECT result_json FROM external_task_actions WHERE tenant=? AND owner=? AND project=? AND integration=? AND task_id=? AND action=? AND action_key=?',
                (*self._scope(principal),task_id,operation,key)).fetchone()
            if row is None: raise ServiceError('NOT_FOUND','Preparation receipt not found',404)
            return {'operation':operation,'key':key,'status':'recorded' if row[0] is not None else 'pending_or_unknown',
                **(json.loads(row[0]) if row[0] is not None else {})}


def purge_for_tasks(db, task_ids):
    for table in ('external_task_bindings','external_task_actions'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():
            db.executemany('DELETE FROM '+table+' WHERE task_id=?',[(tid,) for tid in task_ids])
