"""Atomic durable-strategy to single-plan ledger handoff (development SQLite)."""
import json

from ...agent_contracts import validate_retrieval_strategy
from ...agent_proposals import retrieval_strategy
from ...agent_retrieval import RetrievalHandoff, agent_plan_id
from ...errors import ServiceError
from ...integration_models import AccessContext
from ...query_spec import digest
from ...retrieval_contracts import validate_plan
from . import ledger_rows
from .retrieval import load_state


class SQLiteAgentRetrievalHandoffs:
    def __init__(self, *, journal, retrieval, authority, context_provider):
        if journal.path.resolve() != retrieval.tasks.path.resolve():
            raise ValueError('Agent journal and retrieval must share the task ledger')
        self.journal, self.retrieval = journal, retrieval
        self.authority, self.context_provider = authority, context_provider
        with journal._tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS agent_retrieval_handoffs(
                task_id TEXT NOT NULL, plan_id TEXT NOT NULL, plan_digest TEXT NOT NULL,
                run_id TEXT NOT NULL, attempt_id TEXT NOT NULL, stage_digest TEXT NOT NULL,
                receipt_digest TEXT NOT NULL, PRIMARY KEY(task_id,plan_id))''')

    def _load(self, db, actor, handle):
        row, invocation = self.journal._attempt(db, actor, handle)
        if row['status'] != 'finished' or invocation.stage.role != 'retrieval':
            raise ServiceError('AGENT_STRATEGY_NOT_READY', 'Finished retrieval strategy required', 409)
        stage = invocation.stage
        task = self.journal._task(db, actor, stage)
        spec = json.loads(task.confirmed_proposal_json)['query_spec']
        # The single-plan ledger has no branch/manifest provenance for quotas.
        if (task.media_type != 'image' or spec.get('grouped_expansion')
                or spec.get('source_constraints')
                or spec.get('query_input', {}).get('type') == 'reference_image'):
            raise ServiceError('AGENT_RETRIEVAL_UNSUPPORTED', 'This task needs a different retrieval compiler', 422)
        # Refuse before encoding, and repeat on save through this same method.
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='plan_set_ledger'").fetchone():
            from .plan_sets import restore
            state = restore(db.execute('SELECT format,payload FROM plan_set_ledger WHERE id=1').fetchone())
            if task.task_id in state['current']:
                raise ServiceError('AGENT_RETRIEVAL_UNSUPPORTED', 'Active multi-branch plan cannot be replaced by a single plan', 409)
        context = self.context_provider(actor, task, stage.role)
        if type(context) is not dict or digest(context) != stage.input_digest:
            raise ServiceError('AGENT_INPUT_CHANGED', 'Planning input changed before handoff', 409)
        authority = self.authority(invocation)
        if (authority.actor != actor or authority.task != task or authority.current != stage
                or authority.spec != spec):
            raise ServiceError('AGENT_STAGE_CONFLICT', 'Current backend authority required', 409)
        outputs = [o for o in self.journal._outputs(db, handle, stage.stage_digest)
                   if o['tool_id'] == 'submit_retrieval_strategy']
        if len(outputs) != 1:
            raise ServiceError('AGENT_STRATEGY_NOT_READY', 'One validated strategy receipt required', 409)
        output = outputs[0]
        strategy = retrieval_strategy(stage, output['arguments'], authority.authorized_bindings)
        requirements = spec['agent_requirements']
        validate_retrieval_strategy(strategy, stage, requirements,
            authorized_bindings=authority.authorized_bindings, evidence_refs=authority.evidence_refs)
        self.retrieval.authorization.require_access(
            AccessContext(actor, task.task_id, 'search', 'current'),
            'library:' + strategy.binding.library_id, 'search')
        if output['receipt'] != {'status': 'proposal_validated',
                'proposal_digest': strategy.proposal_digest, 'execution_allowed': False}:
            raise ServiceError('STORAGE_CORRUPT', 'Strategy receipt does not match its payload', 500)
        if strategy.action not in ('rewrite_query', 'switch_source') or strategy.method not in ('text', 'vector'):
            raise ServiceError('AGENT_RETRIEVAL_UNSUPPORTED', 'Strategy requires a different execution adapter', 422)
        return RetrievalHandoff(stage, strategy, digest(output), requirements['autonomy']['max_rounds']), requirements

    def load(self, actor, handle):
        with self.journal._tx() as db:
            return self._load(db, actor, handle)[0]

    def save(self, actor, handle, snapshot, plan):
        with self.journal._tx() as db:
            current, requirements = self._load(db, actor, handle)
            if current != snapshot:
                raise ServiceError('AGENT_INPUT_CHANGED', 'Strategy changed during encoding', 409)
            validate_plan(plan)
            if (plan.scope != current.stage.scope or plan.request.binding != current.strategy.binding
                    or plan.epoch > requirements['autonomy']['max_rounds']
                    or plan.request.plan_version != plan.epoch
                    or plan.request.plan_id != agent_plan_id(current, plan.epoch,
                        plan.request.batch_size, plan.request.filter_template_ref)):
                raise ServiceError('AGENT_STAGE_CONFLICT', 'Plan exceeds the strategy scope or round limit', 409)
            memory = self.journal.repository._load(db.execute(
                'SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            state = load_state(db.execute('SELECT format,payload FROM retrieval_ledger WHERE id=1').fetchone())
            self.retrieval._put_proposal(memory, state, actor, plan)
            # Recheck after authorization callbacks, before publishing the plan.
            if self._load(db, actor, handle)[0] != current:
                raise ServiceError('AGENT_INPUT_CHANGED', 'Strategy changed before publication', 409)
            ledger_rows.write_state(db, 'retrieval_ledger', 1, state)
            provenance = (plan.scope.task_id, plan.request.plan_id, plan.proposal_digest,
                handle.run_id, handle.attempt_id, current.stage.stage_digest, current.receipt_digest)
            old = db.execute('SELECT * FROM agent_retrieval_handoffs WHERE task_id=? AND plan_id=?', provenance[:2]).fetchone()
            if old is not None and tuple(old) != provenance:
                raise ServiceError('PLAN_ID_CONFLICT', 'Agent plan provenance is immutable', 409)
            db.execute('INSERT OR IGNORE INTO agent_retrieval_handoffs VALUES(?,?,?,?,?,?,?)', provenance)
            return plan


def require_current_handoff(db, proposal):
    """Called by ordinary business confirmation, before it increments task version.

    Once explicitly confirmed, business-plan cancellation owns execution control;
    cancelling a completed planning session does not undo business confirmation.
    """
    if not proposal.request.plan_id.startswith('agentplan_'):
        return
    from .harness import SQLiteHarnessJournal
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_retrieval_handoffs'").fetchone()
    row = db.execute('SELECT * FROM agent_retrieval_handoffs WHERE task_id=? AND plan_id=?',
        (proposal.scope.task_id, proposal.request.plan_id)).fetchone() if exists else None
    if row is None or row[2] != proposal.proposal_digest:
        raise ServiceError('AGENT_CONFIRMATION_REQUIRED', 'Current Agent plan provenance required', 409)
    stage = db.execute('SELECT head_key FROM harness_stages WHERE stage_digest=?', (row[5],)).fetchone()
    head = db.execute('SELECT stage_digest FROM harness_stage_heads WHERE head_key=?', (stage[0],)).fetchone() if stage else None
    attempt = db.execute('SELECT status,payload FROM harness_attempts WHERE run_id=? AND attempt_id=? AND stage_digest=?',
        (row[3], row[4], row[5])).fetchone()
    if attempt is None:
        raise ServiceError('AGENT_CONFIRMATION_REQUIRED', 'Strategy attempt unavailable', 409)
    from ...harness.codec import load_invocation
    invocation = load_invocation(attempt[1])
    if (invocation.stage.scope != proposal.scope or invocation.stage.stage_digest != row[5]
            or invocation.run_id != row[3] or invocation.attempt_id != row[4]):
        raise ServiceError('STORAGE_CORRUPT', 'Agent plan origin binding changed', 500)
    outputs = SQLiteHarnessJournal._outputs(db, SQLiteHarnessJournal.handle(invocation), row[5])
    strategies = [o for o in outputs if o['tool_id'] == 'submit_retrieval_strategy']
    if (head is None or head[0] != row[5] or attempt[0] != 'finished'
            or len(strategies) != 1 or digest(strategies[0]) != row[6]):
        raise ServiceError('AGENT_CONFIRMATION_REQUIRED', 'Strategy was revoked or changed before confirmation', 409)


def purge_for_tasks(db, task_ids):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_retrieval_handoffs'").fetchone():
        db.executemany('DELETE FROM agent_retrieval_handoffs WHERE task_id=?', [(tid,) for tid in task_ids])
