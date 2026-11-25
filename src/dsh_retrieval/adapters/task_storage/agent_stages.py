"""Stage review drafts and action receipts, sharing the Harness task transaction."""
import json
import uuid

from ...errors import ServiceError
from ...harness.codec import stage_from, load_invocation
from ...query_spec import digest


class AgentStageStore:
    def __init__(self, journal):
        self.journal = journal
        with journal._tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS harness_stage_drafts(
                draft_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, owner_id TEXT NOT NULL,
                task_id TEXT NOT NULL, prepare_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                payload TEXT NOT NULL, proposal_digest TEXT NOT NULL, status TEXT NOT NULL,
                run_id TEXT, attempt_id TEXT, deadline REAL,
                UNIQUE(tenant_id,owner_id,task_id,prepare_key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS harness_stage_actions(
                tenant_id TEXT NOT NULL, owner_id TEXT NOT NULL, task_id TEXT NOT NULL,
                operation TEXT NOT NULL, action_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                draft_id TEXT NOT NULL, PRIMARY KEY(tenant_id,owner_id,task_id,operation,action_key))''')

    @property
    def storage_identity(self):
        return self.journal.storage_identity

    @staticmethod
    def _decode(row):
        try:
            review=json.loads(row['payload'])
            if type(review) is not dict: raise ValueError()
            if review.get('schema_version')!='agent-stage-review@1':
                raise ServiceError('STORAGE_FORMAT_UNSUPPORTED','Unsupported stage-review format',500)
            if (set(review)!={'schema_version','stage','role','runtime','context_json','model_profile_ref',
                              'task_kind','model_calls','max_seconds'} or digest(review)!=row['proposal_digest']):
                raise ValueError()
            stage_from(review['stage'])
            return review
        except ServiceError:
            raise
        except (ValueError,TypeError,KeyError,RecursionError):
            raise ServiceError('STORAGE_CORRUPT','Stage review is corrupt; refusing to rebuild it',500) from None

    def _row(self, db, actor, task_id, draft_id, *, cleanup=False):
        cursor = db.execute('SELECT * FROM harness_stage_drafts WHERE draft_id=? AND tenant_id=? AND owner_id=? AND task_id=?',
                            (draft_id, actor.tenant_id, actor.user_id, task_id))
        row = cursor.fetchone()
        if row is None: raise ServiceError('NOT_FOUND', 'Stage draft not found', 404)
        row = dict(zip((c[0] for c in cursor.description), row))
        review = self._decode(row)
        stage = stage_from(review['stage'])
        if cleanup: self.journal._task(db, actor, stage)
        else: self.journal._guard(db, actor, stage, approved=False)
        return dict(row), review

    def prepared(self, actor, task_id, key, fingerprint):
        with self.journal._tx() as db:
            row = db.execute('SELECT draft_id,fingerprint FROM harness_stage_drafts WHERE tenant_id=? AND owner_id=? AND task_id=? AND prepare_key=?',
                             (actor.tenant_id, actor.user_id, task_id, key)).fetchone()
            if row is None: return None
            if row[1] != fingerprint: raise ServiceError('IDEMPOTENCY_CONFLICT', 'Stage preparation changed', 409)
            return self._row(db, actor, task_id, row[0])

    def save(self, actor, task_id, key, fingerprint, review):
        with self.journal._tx() as db:
            self.journal._guard(db, actor, stage_from(review['stage']), approved=False)
            old = db.execute('SELECT draft_id,fingerprint FROM harness_stage_drafts WHERE tenant_id=? AND owner_id=? AND task_id=? AND prepare_key=?',
                             (actor.tenant_id, actor.user_id, task_id, key)).fetchone()
            if old:
                if old[1] != fingerprint: raise ServiceError('IDEMPOTENCY_CONFLICT', 'Stage preparation changed', 409)
                return (*self._row(db, actor, task_id, old[0]), True)
            draft_id = 'agentdraft_' + uuid.uuid4().hex
            db.execute('INSERT INTO harness_stage_drafts VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)',
                (draft_id, actor.tenant_id, actor.user_id, task_id, key, fingerprint,
                 json.dumps(review, ensure_ascii=False, sort_keys=True, allow_nan=False), digest(review), 'prepared'))
            return (*self._row(db, actor, task_id, draft_id), False)

    def get(self, actor, task_id, draft_id, *, cleanup=False):
        with self.journal._tx() as db: return self._row(db, actor, task_id, draft_id, cleanup=cleanup)

    def list(self, actor, task, limit):
        with self.journal._tx() as db:
            task = self.journal.task_in_transaction(db,actor,task.task_id)
            # Metadata for this current task version only, never raw contexts/prompts.
            rows = db.execute("""SELECT * FROM harness_stage_drafts WHERE tenant_id=? AND owner_id=? AND task_id=?
                AND json_extract(payload,'$.stage.scope.revision')=?
                AND json_extract(payload,'$.stage.scope.generation')=?
                AND json_extract(payload,'$.stage.scope.task_version')=? ORDER BY rowid DESC LIMIT ?""",
                (actor.tenant_id,actor.user_id,task.task_id,task.confirmed_revision,
                 task.run_generation,task.task_version,limit)).fetchall()
            result=[]
            for item in rows:
                row=dict(item); review=self._decode(row)
                stage=stage_from(review['stage'])
                self.journal._task(db,actor,stage)
                if row['status'] in ('confirmed','started'):
                    head=db.execute('SELECT stage_digest FROM harness_stage_heads WHERE head_key=?',
                                    (self.journal._head(stage),)).fetchone()
                    if head is None or head[0]!=stage.stage_digest: row['status']='superseded'
                    elif row['run_id']:
                        state=db.execute('SELECT status FROM harness_attempts WHERE run_id=? AND attempt_id=?',
                                         (row['run_id'],row['attempt_id'])).fetchone()
                        if state: row['status']=state[0]
                result.append((row,review))
            return result

    @staticmethod
    def _action(db, actor, task_id, operation, key, draft_id, body):
        fp = digest({'draft_id': draft_id, 'body': body})
        scope = (actor.tenant_id, actor.user_id, task_id, operation, key)
        old = db.execute('SELECT fingerprint FROM harness_stage_actions WHERE tenant_id=? AND owner_id=? AND task_id=? AND operation=? AND action_key=?', scope).fetchone()
        if old:
            if old[0] != fp: raise ServiceError('IDEMPOTENCY_CONFLICT', 'Stage action changed', 409)
            return True
        db.execute('INSERT INTO harness_stage_actions VALUES(?,?,?,?,?,?,?)', (*scope, fp, draft_id))
        return False

    @staticmethod
    def invocation(row, review):
        payload = {k: review[k] for k in ('stage', 'role', 'runtime', 'model_profile_ref', 'task_kind', 'context_json')}
        payload.update(run_id=row['run_id'], attempt_id=row['attempt_id'], deadline=row['deadline'])
        return load_invocation(json.dumps(payload, ensure_ascii=False))

    def act(self, actor, task_id, draft_id, operation, key, body):
        with self.journal._tx() as db:
            row, review = self._row(db, actor, task_id, draft_id, cleanup=operation == 'cancel')
            stage = stage_from(review['stage'])
            if body['expected_task_version'] != stage.scope.task_version:
                raise ServiceError('REVISION_CONFLICT', 'Stage task version changed', 409)
            if body['proposal_digest'] != row['proposal_digest']:
                raise ServiceError('PLAN_DIGEST_MISMATCH', 'Displayed stage review changed', 409)
            replayed = self._action(db, actor, task_id, operation, key, draft_id, body)
            if operation == 'confirm':
                if row['status'] == 'cancelled': raise ServiceError('INVALID_STATE_TRANSITION', 'Stage cancelled', 409)
                self.journal._approve(db, actor, stage, action=body['action'], proposal_digest=stage.stage_digest)
                if row['status'] == 'prepared':
                    db.execute("UPDATE harness_stage_drafts SET status='confirmed' WHERE draft_id=?", (draft_id,))
            elif operation == 'start':
                if row['status'] not in ('confirmed', 'started'):
                    raise ServiceError('AGENT_CONFIRMATION_REQUIRED', 'Confirm the displayed stage before starting', 409)
                if row['status'] == 'confirmed':
                    row.update(run_id='agentrun_' + uuid.uuid4().hex, attempt_id='attempt_' + uuid.uuid4().hex,
                               deadline=self.journal.clock() + review['max_seconds'])
                    self.journal._enqueue(db, actor, self.invocation(row, review), model_calls=review['model_calls'])
                    db.execute("UPDATE harness_stage_drafts SET status='started',run_id=?,attempt_id=?,deadline=? WHERE draft_id=?",
                               (row['run_id'], row['attempt_id'], row['deadline'], draft_id))
            elif operation == 'cancel':
                # The durable runtime is fenced by the service immediately after commit.
                db.execute("UPDATE harness_stage_drafts SET status='cancelled' WHERE draft_id=?", (draft_id,))
                if row['run_id']:
                    changed = db.execute("UPDATE harness_attempts SET status='cancelled',lease_token=NULL,lease_until=NULL "
                               "WHERE run_id=? AND attempt_id=? AND status IN ('queued','running')", (row['run_id'], row['attempt_id'])).rowcount
                    if changed:
                        self.journal._event(db, self.journal.handle(self.invocation(row, review)), 'cancelled')
                db.execute('DELETE FROM harness_stage_heads WHERE head_key=? AND stage_digest=?',
                           (self.journal._head(stage), stage.stage_digest))
            else: raise ValueError('unregistered stage action')
            return (*self._row(db, actor, task_id, draft_id, cleanup=operation == 'cancel'), replayed)


def purge_for_tasks(db, task_ids):
    if not task_ids: return
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    marks = ','.join('?' for _ in task_ids)
    for table in ('agent_job_links', 'harness_stage_drafts', 'harness_stage_actions'):
        if table in tables:
            db.execute('DELETE FROM ' + table + ' WHERE task_id IN (' + marks + ')', tuple(task_ids))
