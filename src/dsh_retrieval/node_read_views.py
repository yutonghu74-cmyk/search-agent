"""Transactional, version-bound pages for delivery members and refill evidence."""
import hashlib
import json

from .errors import ServiceError
from .result_views import _decode_cursor, _encode_cursor, ResultCursorError


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS task_node_read_heads (
        task_id TEXT NOT NULL, node TEXT NOT NULL, version TEXT NOT NULL,
        task_version INTEGER NOT NULL, item_count INTEGER NOT NULL,
        summary_json TEXT NOT NULL, PRIMARY KEY(task_id,node))''')
    db.execute('''CREATE TABLE IF NOT EXISTS task_node_read_items (
        task_id TEXT NOT NULL, node TEXT NOT NULL, ordinal INTEGER NOT NULL,
        item_json TEXT NOT NULL, PRIMARY KEY(task_id,node,ordinal))''')


def node_sources(db, task, tables):
    scope = (task.task_id, task.confirmed_revision, task.run_generation)
    delivery = {'available': False}
    members = []
    if 'image_deliveries' in tables:
        from .delivery import current_delivery, ImageDeliveryService
        for (payload,) in db.execute('''SELECT metadata FROM image_deliveries
                WHERE json_extract(metadata,'$.task_id')=?''', (task.task_id,)):
            record = json.loads(payload)
            if current_delivery(task, {'_deliveries': {record['source_id']: record}}, record['source_id']):
                delivery = {'available': True, **ImageDeliveryService.view(record)}
                manifest = dict(delivery['manifest'])
                members = manifest.pop('members', [])
                delivery['manifest'] = manifest
                break
    evidence = {'issued_rounds': 0, 'completed_rounds': 0, 'vlm_calls': 0,
                'stagnant_rounds': 0, 'stop_reason': None}
    rounds = []
    if 'refill_iteration_metrics' in tables:
        for row in db.execute('''SELECT iteration,baseline_accepted_count,accepted_after,
                outcome,candidate_count,vlm_calls FROM refill_iteration_metrics
                WHERE task_id=? AND revision=? AND generation=? ORDER BY iteration''', scope):
            iteration, baseline, after, outcome, count, calls = row
            rounds.append(dict(iteration=iteration, baseline_accepted=baseline,
                accepted_after=after, outcome=outcome, candidate_count=count, vlm_calls=calls,
                new_unique_accepted=None if after is None else max(0, after-baseline)))
        evidence['issued_rounds'] = db.execute('''SELECT COUNT(*) FROM refill_reservations
            WHERE task_id=? AND revision=? AND generation=?''', scope).fetchone()[0]
        evidence['completed_rounds'] = sum(r['outcome'] in {'empty', 'result'} for r in rounds)
        evidence['vlm_calls'] = sum(r['vlm_calls'] for r in rounds)
        for r in reversed(rounds):
            if r['outcome'] not in {'empty', 'result'}:
                continue
            if r['accepted_after'] is not None and r['accepted_after'] > r['baseline_accepted']:
                break
            evidence['stagnant_rounds'] += 1
        stop = db.execute('''SELECT stop_reason FROM refill_execution_stops
            WHERE task_id=? AND revision=? AND generation=?''', scope).fetchone()
        evidence['stop_reason'] = stop[0] if stop else None
    if task.confirmed_proposal_json:
        from .refill_planning import normalize_refill_limits
        spec = json.loads(task.confirmed_proposal_json)['query_spec']
        evidence.update(normalize_refill_limits(spec.get('refill_limits')))
    row = db.execute('SELECT summary_json FROM task_read_summaries WHERE task_id=?', (task.task_id,)).fetchone()
    counts = json.loads(row[0]).get('progress', {}).get('counts', {}) if row else {}
    return {'packaging': (delivery, members), 'refill': ({'execution': evidence,
        'target_count':task.target_count, 'accepted_count':counts.get('accepted'),
        'gap':counts.get('gap'), 'stop_reason': evidence['stop_reason']}, rounds)}


def sync(db, task, tables):
    for node, (summary, items) in node_sources(db, task, tables).items():
        binding = (task.task_id, task.confirmed_revision, task.run_generation, task.task_version)
        version = 'sha256:' + hashlib.sha256(canonical([binding, summary, items]).encode()).hexdigest()
        old = db.execute('SELECT version FROM task_node_read_heads WHERE task_id=? AND node=?',
                         (task.task_id, node)).fetchone()
        if old == (version,):
            continue
        db.execute('INSERT OR REPLACE INTO task_node_read_heads VALUES (?,?,?,?,?,?)',
                   (task.task_id, node, version, task.task_version, len(items), canonical(summary)))
        db.execute('DELETE FROM task_node_read_items WHERE task_id=? AND node=?', (task.task_id,node))
        db.executemany('INSERT INTO task_node_read_items VALUES (?,?,?,?)',
                       ((task.task_id,node,i,canonical(item)) for i,item in enumerate(items)))


def read(db, task, node, limit, cursor):
    if node not in {'packaging', 'refill'} or type(limit) is not int or not 1 <= limit <= 100:
        raise ServiceError('INVALID_SPEC', 'invalid node or page size', 422)
    row = db.execute('''SELECT version,task_version,item_count,summary_json
        FROM task_node_read_heads WHERE task_id=? AND node=?''', (task.task_id,node)).fetchone()
    if row is None or row[1] != task.task_version:
        raise ServiceError('READ_PROJECTION_REQUIRED', 'node projection requires migration', 503)
    version, _, count, payload = row
    try:
        offset = 0 if cursor is None else _decode_cursor(cursor, version, node)
        if offset > count:
            raise ResultCursorError('cursor outside node view')
    except ResultCursorError as exc:
        raise ServiceError('NODE_VERSION_CHANGED', str(exc), 409) from exc
    rows = db.execute('''SELECT item_json FROM task_node_read_items
        WHERE task_id=? AND node=? AND ordinal>=? ORDER BY ordinal LIMIT ?''',
        (task.task_id,node,offset,limit)).fetchall()
    if len(rows) != min(limit, count-offset):
        raise ServiceError('STORAGE_CORRUPT', 'node page has missing rows', 500)
    end = offset + len(rows)
    return {**json.loads(payload), 'task_id': task.task_id, 'revision': task.confirmed_revision,
        'generation': task.run_generation, 'task_version': task.task_version, 'output_version':version,
        'items':[json.loads(r[0]) for r in rows], 'item_count':count,
        'next_cursor':_encode_cursor(version,node,end) if end<count else None}
