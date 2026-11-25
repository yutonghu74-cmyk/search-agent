"""Transactional per-task search read views; the original ledger remains authoritative."""
import hashlib
from . import ledger_rows

from ...errors import ServiceError


def create(db):
    db.execute('''CREATE TABLE IF NOT EXISTS plan_task_read_views (
        task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS plan_task_read_meta (
        id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, dirty INTEGER NOT NULL)''')
    db.execute('INSERT OR IGNORE INTO plan_task_read_meta VALUES(1,1,1)')
    if db.execute('SELECT version FROM plan_task_read_meta WHERE id=1').fetchone() != (1,):
        raise ServiceError('STORAGE_FORMAT_UNSUPPORTED', 'Unsupported task search read view', 500)
    for event in ('INSERT', 'UPDATE', 'DELETE'):
        ledger_rows.watch(db, 'plan_set_ledger', 'plan_task_read_dirty_' + event.lower(), event,
                          'UPDATE plan_task_read_meta SET dirty=1 WHERE id=1;')


def sync(db, state, *, task_ids=None):
    # Import lazily: plan_sets owns serialization and source validation.
    from .plan_sets import empty_state, serialize
    # A targeted sync is safe only if the caller observed a clean view before
    # updating the source in this same write transaction. Legacy dirty sources
    # require a full rebuild, so unrelated stale rows cannot be marked clean.
    targets = None if task_ids is None else frozenset(task_ids)
    views = {}
    def view(task_id):
        if task_id not in views:
            views[task_id] = empty_state()
        return views[task_id]
    for task_id, approved in state['current'].items():
        if targets is None or task_id in targets:
            view(task_id)['current'][task_id] = approved
    operation_tasks, plan_tasks = {}, {}
    for operation_id, record in state['operations'].items():
        approved = record[0]
        task_id = approved.scope.task_id
        if targets is not None and task_id not in targets:
            continue
        operation_tasks[operation_id] = task_id
        plan_tasks[approved.proposal_digest] = task_id
        view(task_id)['operations'][operation_id] = record
    for task_id, approved in state['current'].items():
        if targets is None or task_id in targets:
            plan_tasks[approved.proposal_digest] = task_id
    for fingerprint, operation_id in state['requests'].items():
        if operation_id in operation_tasks:
            view(operation_tasks[operation_id])['requests'][fingerprint] = operation_id
    for key, operation_id in state['cursors'].items():
        if key[0] in plan_tasks:
            view(plan_tasks[key[0]])['cursors'][key] = operation_id
    proposal_tasks = {}
    for key, proposal in state['candidate_proposals'].items():
        task_id = proposal.scope.task_id
        if targets is not None and task_id not in targets:
            continue
        proposal_tasks[key] = task_id
        view(task_id)['candidate_proposals'][key] = proposal
    for ref, manifest in state['candidate_manifests'].items():
        task_id = manifest.proposal.scope.task_id
        if targets is None or task_id in targets:
            view(task_id)['candidate_manifests'][ref] = manifest
    for key, ref in state['candidate_published'].items():
        if key in proposal_tasks:
            view(proposal_tasks[key])['candidate_published'][key] = ref
    for event in state['candidate_events']:
        if targets is None or event['task_id'] in targets:
            view(event['task_id'])['candidate_events'].append(event)
    for task_id, task_state in views.items():
        payload = serialize(task_state)
        checksum = hashlib.sha256(payload.encode()).hexdigest()
        if db.execute('SELECT checksum FROM plan_task_read_views WHERE task_id=?', (task_id,)).fetchone() == (checksum,):
            continue
        db.execute('''INSERT INTO plan_task_read_views VALUES(?,?,?)
            ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload, checksum=excluded.checksum
            WHERE plan_task_read_views.checksum != excluded.checksum''', (task_id, payload, checksum))
    existing = (targets if targets is not None else
                {row[0] for row in db.execute('SELECT task_id FROM plan_task_read_views')})
    db.executemany('DELETE FROM plan_task_read_views WHERE task_id=?',
                   ((task_id,) for task_id in existing - views.keys()))
    db.execute('UPDATE plan_task_read_meta SET version=1,dirty=0 WHERE id=1')


def read(db, task_id):
    meta = db.execute('SELECT version,dirty FROM plan_task_read_meta WHERE id=1').fetchone()
    if meta != (1, 0):
        return None
    row = db.execute('SELECT payload,checksum FROM plan_task_read_views WHERE task_id=?', (task_id,)).fetchone()
    # Missing rows fall back to the source, including tasks without a search.
    if row is None:
        return None
    if (type(row[0]) is not str or type(row[1]) is not str
            or hashlib.sha256(row[0].encode()).hexdigest() != row[1]):
        raise ServiceError('STORAGE_CORRUPT', 'Task search read view checksum differs', 500)
    return 3, row[0]
