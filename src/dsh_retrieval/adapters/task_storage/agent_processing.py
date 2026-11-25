"""Atomic cleaning-Agent handoff to the existing multi-rule proposal ledger."""
import json

from ...agent_contracts import validate_processing_plan
from ...agent_processing import ProcessingHandoff, compile_agent_filter_recipe
from ...agent_proposals import processing_plan
from ...errors import ServiceError
from ...query_spec import digest


class SQLiteAgentProcessingHandoffs:
    def __init__(self, *, journal, cleaning, authority, context_provider):
        if journal.path.resolve() != cleaning.tasks.path.resolve():
            raise ValueError('Agent journal and cleaning must share the task ledger')
        self.journal, self.cleaning = journal, cleaning
        self.authority, self.context_provider = authority, context_provider
        with journal._tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS agent_processing_handoffs(
                proposal_digest TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                run_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                stage_digest TEXT NOT NULL, receipt_digest TEXT NOT NULL)''')

    def _load(self, db, actor, handle):
        row, invocation = self.journal._attempt(db, actor, handle)
        if row['status'] != 'finished' or invocation.stage.role != 'cleaning':
            raise ServiceError('AGENT_PROCESSING_NOT_READY', 'Finished processing plan required', 409)
        stage = invocation.stage
        task = self.journal._task(db, actor, stage)
        spec = json.loads(task.confirmed_proposal_json)['query_spec']
        context = self.context_provider(actor, task, stage.role)
        if type(context) is not dict or digest(context) != stage.input_digest:
            raise ServiceError('AGENT_INPUT_CHANGED', 'Processing input changed before handoff', 409)
        if not isinstance(context.get('manifest_ref'), str) or not context['manifest_ref']:
            raise ServiceError('AGENT_INPUT_CHANGED', 'Processing input must bind a candidate manifest', 409)
        authority = self.authority(invocation)
        if (authority.actor != actor or authority.task != task or authority.current != stage
                or authority.spec != spec):
            raise ServiceError('AGENT_STAGE_CONFLICT', 'Current processing authority required', 409)
        outputs = [o for o in self.journal._outputs(db, handle, stage.stage_digest)
                   if o['tool_id'] == 'submit_processing_plan']
        if len(outputs) != 1:
            raise ServiceError('AGENT_PROCESSING_NOT_READY', 'One validated processing receipt required', 409)
        output = outputs[0]
        proposal = processing_plan(stage, output['arguments'], authority.capabilities)
        validate_processing_plan(proposal, stage, spec['agent_requirements'],
            media_type=task.media_type, capabilities=authority.capabilities)
        if output['receipt'] != {'status': 'proposal_validated',
                'proposal_digest': proposal.proposal_digest, 'execution_allowed': False}:
            raise ServiceError('STORAGE_CORRUPT', 'Processing receipt changed', 500)
        return ProcessingHandoff(stage, task, proposal, digest(output), context['manifest_ref'])

    def load(self, actor, handle):
        with self.journal._tx() as db:
            return self._load(db, actor, handle)

    def save(self, actor, handle, snapshot, manifest_ref, recipe, registry):
        def save(memory, retrieval, candidates, state, db):
            current = self._load(db, actor, handle)
            if (current != snapshot or current.manifest_ref != manifest_ref
                    or compile_agent_filter_recipe(current, registry) != recipe):
                raise ServiceError('AGENT_INPUT_CHANGED', 'Processing release/input changed', 409)
            proposal = self.cleaning._prepare(memory, retrieval, candidates, state, actor,
                current.task.task_id, manifest_ref, recipe, registry.release_digest)
            provenance = (proposal.proposal_digest, current.task.task_id, handle.run_id,
                handle.attempt_id, current.stage.stage_digest, current.receipt_digest)
            old = db.execute('SELECT * FROM agent_processing_handoffs WHERE proposal_digest=?', provenance[:1]).fetchone()
            if old is not None and tuple(old) != provenance:
                raise ServiceError('AGENT_HANDOFF_CONFLICT', 'Processing proposal origin is immutable', 409)
            db.execute('INSERT OR IGNORE INTO agent_processing_handoffs VALUES(?,?,?,?,?,?)', provenance)
            if self._load(db, actor, handle) != current:
                raise ServiceError('AGENT_INPUT_CHANGED', 'Processing authority changed before publication', 409)
            return proposal
        return self.cleaning._transaction(True, save, with_connection=True)


def require_processing_handoff(db, task, proposal):
    if 'agent_requirements' not in json.loads(task.confirmed_proposal_json)['query_spec']:
        return
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_processing_handoffs'").fetchone()
    row = db.execute('SELECT * FROM agent_processing_handoffs WHERE proposal_digest=? AND task_id=?',
        (proposal.proposal_digest, task.task_id)).fetchone() if exists else None
    if row is None:
        raise ServiceError('AGENT_CONFIRMATION_REQUIRED', 'Durable processing plan origin required', 409)
    from .harness import SQLiteHarnessJournal
    from ...harness.codec import load_invocation
    from ...retrieval_contracts import RetrievalScope
    attempt = db.execute('SELECT status,payload FROM harness_attempts WHERE run_id=? AND attempt_id=? AND stage_digest=?',
        (row[2], row[3], row[4])).fetchone()
    stage = db.execute('SELECT head_key FROM harness_stages WHERE stage_digest=?', (row[4],)).fetchone()
    head = db.execute('SELECT stage_digest FROM harness_stage_heads WHERE head_key=?', (stage[0],)).fetchone() if stage else None
    if attempt is None or attempt[0] != 'finished' or head is None or head[0] != row[4]:
        raise ServiceError('AGENT_CONFIRMATION_REQUIRED', 'Processing stage was revoked', 409)
    invocation = load_invocation(attempt[1])
    if (invocation.stage.scope != RetrievalScope.from_task(task)
            or invocation.stage.stage_digest != row[4]
            or invocation.run_id != row[2] or invocation.attempt_id != row[3]):
        raise ServiceError('STORAGE_CORRUPT', 'Processing origin binding changed', 500)
    outputs = SQLiteHarnessJournal._outputs(db, SQLiteHarnessJournal.handle(invocation), row[4])
    plans = [o for o in outputs if o['tool_id'] == 'submit_processing_plan']
    if len(plans) != 1 or digest(plans[0]) != row[5]:
        raise ServiceError('AGENT_CONFIRMATION_REQUIRED', 'Processing receipt changed', 409)


def purge_for_tasks(db, task_ids):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_processing_handoffs'").fetchone():
        db.executemany('DELETE FROM agent_processing_handoffs WHERE task_id=?', [(tid,) for tid in task_ids])
