"""Coordination records reuse the original Harness journal and job link table."""
from dataclasses import asdict
import json

from ...errors import ServiceError
from ...harness.codec import StageCodecRegistry
from ...models import Actor
from ...platform_coordination import CoordinationStage, TASK_CONTRACT, WORKFLOW, current_scope
from ...query_spec import digest
from ...retrieval_contracts import RetrievalScope
from .agent_jobs import ensure_job_links, SQLiteAgentJobLinks
from .harness import SQLiteHarnessJournal


def codecs():
    registry = StageCodecRegistry()
    def decode(value):
        value = dict(value); value['scope'] = RetrievalScope(**value['scope'])
        return CoordinationStage(**value)
    registry.register(TASK_CONTRACT, CoordinationStage, encode=asdict, decode=decode)
    return registry


class SQLiteCoordinationJournal(SQLiteHarnessJournal):
    def __init__(self, repository, projects, *, policy, policy_digest, clock=None):
        self.policy, self.policy_digest = policy, policy_digest
        super().__init__(repository, projects, stage_codecs=codecs(), task_contract_ref=TASK_CONTRACT,
                         roles=frozenset({'coordinator'}), **({'clock': clock} if clock else {}))

    def validate_authority(self, actor, task, stage, spec):
        if type(stage) is not CoordinationStage or stage.policy_digest != self.policy_digest:
            raise ServiceError('COORDINATOR_POLICY_CHANGED', 'Coordinator policy or installed capabilities changed', 409)
        current_scope(actor, task, stage.scope)
        return {}

    def model_limit(self, task, stage): return self.policy.generation_model_calls

    @staticmethod
    def _budget(stage):
        # Coordinator model budget is separate from physical search/cleaning
        # budgets and remains shared by all its turns in the root generation.
        return digest({'coordinator': SQLiteHarnessJournal._budget(stage)})


class SQLiteCoordinationLinks:
    def __init__(self, journal):
        self.journal, self.storage_identity = journal, journal.storage_identity
        self._pending_after = 0
        ensure_job_links(journal)

    def _read(self, db, actor, task_id, ref):
        task = self.journal.task_in_transaction(db, actor, task_id)
        row = db.execute('SELECT stage_digest,payload,payload_digest FROM agent_job_links '
            'WHERE job_ref=? AND task_id=? AND workflow_ref=?', (ref, task_id, WORKFLOW)).fetchone()
        if row is None: raise ServiceError('NOT_FOUND', 'Coordination job not found', 404)
        value = json.loads(row['payload'])
        if (value.get('schema_version') != 'coordination-links@1' or digest(value) != row['payload_digest']
                or row['stage_digest'] != value['stage_digest']):
            raise ServiceError('STORAGE_CORRUPT', 'Coordination record changed', 500)
        return {'task': task, 'links': value, 'workflow_ref': WORKFLOW}

    def get(self, actor, task_id, ref):
        with self.journal._tx() as db: return self._read(db, actor, task_id, ref)

    def list(self, actor, task_id, limit=32):
        return SQLiteAgentJobLinks.list(self, actor, task_id, limit)

    @staticmethod
    def current(task, value, *, exact=True):
        scope = RetrievalScope(**value['scope'])
        current = RetrievalScope.from_task(task)
        return (current == scope if exact else
                (current.tenant_id, current.owner_user_id, current.project_id, current.task_id,
                 current.revision, current.generation) ==
                (scope.tenant_id, scope.owner_user_id, scope.project_id, scope.task_id, scope.revision, scope.generation))

    def save(self, actor, task_id, ref, fingerprint, invocation, context):
        with self.journal._tx() as db:
            task = self.journal.task_in_transaction(db, actor, task_id)
            current_scope(actor, task, invocation.stage.scope)
            if db.execute('SELECT 1 FROM agent_job_links WHERE job_ref=?', (ref,)).fetchone():
                value = self._read(db, actor, task_id, ref)['links']
                if value['fingerprint'] != fingerprint:
                    raise ServiceError('IDEMPOTENCY_CONFLICT', 'Coordination request changed', 409)
                return False
            if db.execute("SELECT 1 FROM agent_job_links WHERE task_id=? AND workflow_ref=? "
                    "AND json_extract(payload,'$.scope.revision')=? AND json_extract(payload,'$.scope.generation')=? "
                    "AND json_extract(payload,'$.scope.task_version')=? "
                    "AND json_extract(payload,'$.status') IN ('queued','running','dispatching') LIMIT 1",
                    (task_id, WORKFLOW, invocation.stage.scope.revision, invocation.stage.scope.generation,
                     invocation.stage.scope.task_version)).fetchone():
                raise ServiceError('COORDINATOR_BUSY', 'Read the existing coordination job before starting another', 409)
            from ...harness.codec import dump_invocation
            value = {'schema_version': 'coordination-links@1', 'fingerprint': fingerprint,
                'stage_digest': invocation.stage.stage_digest, 'scope': asdict(invocation.stage.scope),
                'invocation': dump_invocation(invocation, stage_codecs=self.journal.stage_codecs),
                'status': 'ready', 'context': context, 'plan': None, 'steps': [], 'code': None}
            db.execute('INSERT INTO agent_job_links VALUES(?,?,?,?,?,?)',
                (ref, task_id, WORKFLOW, value['stage_digest'], json.dumps(value, sort_keys=True), digest(value)))
            return True

    def update(self, actor, task_id, ref, *, exact=True, **changes):
        with self.journal._tx() as db:
            value = self._read(db, actor, task_id, ref)
            if not self.current(value['task'], value['links'], exact=exact):
                raise ServiceError('REVISION_CONFLICT', 'Old coordinator cannot publish a new task result', 409)
            value['links'].update(changes)
            SQLiteAgentJobLinks._write(db, ref, value['links'])

    def claim_step(self, actor, task_id, ref, step):
        """Persist delivery intent before leaving the business transaction."""
        with self.journal._tx() as db:
            value = self._read(db, actor, task_id, ref)
            if not self.current(value['task'], value['links']):
                raise ServiceError('REVISION_CONFLICT', 'Coordination source changed', 409)
            records = value['links']['steps']
            prior = next((r for r in records if r['step_id'] == step['step_id']), None)
            if prior is not None: return False
            records.append({'step_id': step['step_id'], 'capability': step['capability'],
                            'action': step['action'], 'state': 'pending_or_unknown', 'result': None})
            SQLiteAgentJobLinks._write(db, ref, value['links'])
            return True

    def record_error(self, actor, task_id, ref, code):
        self.update(actor, task_id, ref, exact=False, status='unknown', code=code)

    def pending(self, limit=32):
        with self.journal._tx() as db:
            rows = db.execute("SELECT rowid,task_id,job_ref FROM agent_job_links WHERE workflow_ref=? AND rowid>? "
                "AND json_extract(payload,'$.status') IN ('queued','running','dispatching') ORDER BY rowid LIMIT ?",
                (WORKFLOW, self._pending_after, limit)).fetchall()
            self._pending_after = rows[-1]['rowid'] if len(rows) == limit else 0
            result = []
            for row in rows:
                owner = db.execute('SELECT tenant_id,owner_user_id FROM task_read_summaries WHERE task_id=?',
                                   (row['task_id'],)).fetchone()
                if owner is not None:
                    actor = Actor(*owner)
                    value = self._read(db, actor, row['task_id'], row['job_ref'])
                    if self.current(value['task'], value['links'], exact=value['links']['status'] != 'dispatching'):
                        result.append((actor, row['task_id'], row['job_ref']))
            return tuple(result)
