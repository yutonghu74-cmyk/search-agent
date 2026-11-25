"""Rebuildable task headers and plan-scoped page projections of the row ledger."""
import hashlib
import json
from . import ledger_rows
from ...errors import ServiceError


def create(db):
    db.execute('CREATE TABLE IF NOT EXISTS plan_source_meta (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, dirty INTEGER NOT NULL)')
    db.execute('INSERT OR IGNORE INTO plan_source_meta VALUES(1,1,1)')
    if db.execute('SELECT version FROM plan_source_meta WHERE id=1').fetchone() != (1,):
        raise ServiceError('STORAGE_FORMAT_UNSUPPORTED', 'Unsupported plan source version', 500)
    db.execute('CREATE TABLE IF NOT EXISTS plan_task_sources (task_id TEXT PRIMARY KEY,payload TEXT NOT NULL,checksum TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS plan_page_sources (task_id TEXT NOT NULL,plan_digest TEXT NOT NULL,payload TEXT NOT NULL,checksum TEXT NOT NULL,PRIMARY KEY(task_id,plan_digest))')
    for event in ('INSERT', 'UPDATE', 'DELETE'):
        ledger_rows.watch(db, 'plan_set_ledger', 'plan_source_dirty_' + event.lower(), event,
                          'UPDATE plan_source_meta SET dirty=1 WHERE id=1;')


def _check(row):
    if row is None or type(row[0]) is not str or hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
        raise ServiceError('STORAGE_CORRUPT', 'Partitioned plan source invalid', 500)
    from .plan_sets import restore
    return restore((3, row[0]))


def _put(db, table, keys, payload):
    checksum = hashlib.sha256(payload.encode()).hexdigest()
    where = 'task_id=?' if table == 'plan_task_sources' else 'task_id=? AND plan_digest=?'
    if db.execute(f'SELECT payload,checksum FROM {table} WHERE {where}', keys).fetchone() == (payload,checksum):
        return
    if table == 'plan_task_sources':
        db.execute('INSERT OR REPLACE INTO plan_task_sources VALUES(?,?,?)', (*keys, payload, checksum))
    else:
        db.execute('INSERT OR REPLACE INTO plan_page_sources VALUES(?,?,?,?)', (*keys, payload, checksum))


def prepare(state):
    from .plan_sets import empty_state, serialize
    header = {**state, 'operations': {}, 'requests': {}, 'cursors': {}}
    pages, operation_sets = {}, {}
    for task_id, approved in state['current'].items():
        pages.setdefault(approved.proposal_digest, empty_state())
    for operation_id, record in state['operations'].items():
        key = record[0].proposal_digest
        pages.setdefault(key, empty_state())['operations'][operation_id] = record
        operation_sets[operation_id] = key
    for key, operation_id in state['requests'].items():
        target = pages.get(operation_sets.get(operation_id))
        (header if target is None else target)['requests'][key] = operation_id
    for key, operation_id in state['cursors'].items():
        target = pages.get(key[0])
        (header if target is None else target)['cursors'][key] = operation_id
    return serialize(header), {key: serialize(value) for key, value in pages.items()}


def sync(db, state, *, task_ids=None):
    """Rebuild all sources, or only targets observed clean before this mutation.

    The caller must capture cleanliness before changing the authoritative rows:
    their triggers deliberately mark this projection dirty within the transaction.
    """
    from .plan_sets import empty_state
    targets = None if task_ids is None else frozenset(task_ids)
    partitions = {}
    def target(task_id):
        if task_id not in partitions:
            partitions[task_id] = empty_state()
        return partitions[task_id]
    operation_tasks = {key: record[0].scope.task_id for key, record in state['operations'].items()}
    plan_tasks = {approved.proposal_digest: approved.scope.task_id for approved in state['approved'].values()
                  if targets is None or approved.scope.task_id in targets}
    plan_tasks.update({record[0].proposal_digest: record[0].scope.task_id for record in state['operations'].values()
                       if targets is None or record[0].scope.task_id in targets})
    proposal_tasks = {key: proposal.scope.task_id for key, proposal in state['candidate_proposals'].items()}
    for name, section in state.items():
        entries = section.items() if isinstance(section, dict) else enumerate(section)
        for key, value in entries:
            if name in ('current', 'epochs'): task_id = key
            elif name in ('proposals', 'approved'): task_id = key[0]
            elif name in ('keys', 'reserve_keys', 'candidate_keys'): task_id = key[2]
            elif name == 'operations': task_id = value[0].scope.task_id
            elif name == 'requests': task_id = operation_tasks.get(value, '')
            elif name == 'cursors': task_id = plan_tasks.get(key[0], '')
            elif name == 'candidate_proposals': task_id = value.scope.task_id
            elif name == 'candidate_manifests': task_id = value.proposal.scope.task_id
            elif name == 'candidate_published': task_id = proposal_tasks[key]
            elif name == 'candidate_events': task_id = value['task_id']
            elif name == 'events':
                task_id = (value['plan_set'].scope.task_id if value.get('kind') == 'plan_set_approved'
                           else operation_tasks.get(value.get('operation_id'), ''))
            else: raise ValueError('unknown plan source section')
            if targets is not None and task_id not in targets:
                continue
            if isinstance(section, dict): target(task_id)[name][key] = value
            else: target(task_id)[name].append(value)
    existing = (targets if targets is not None else
                {row[0] for row in db.execute('SELECT task_id FROM plan_task_sources')})
    for task_id in existing - partitions.keys():
        db.execute('DELETE FROM plan_task_sources WHERE task_id=?', (task_id,))
        db.execute('DELETE FROM plan_page_sources WHERE task_id=?', (task_id,))
    for task_id, partition in partitions.items():
        prepared = prepare(partition)
        previous = {row[0] for row in db.execute('SELECT plan_digest FROM plan_page_sources WHERE task_id=?', (task_id,))}
        for key in previous - prepared[1].keys():
            db.execute('DELETE FROM plan_page_sources WHERE task_id=? AND plan_digest=?', (task_id,key))
        save(db, task_id, prepared)
    db.execute('UPDATE plan_source_meta SET dirty=0 WHERE id=1')


def load_task(db, task_id):
    if db.execute('SELECT version,dirty FROM plan_source_meta WHERE id=1').fetchone() != (1, 0):
        return None
    row = db.execute('SELECT payload,checksum FROM plan_task_sources WHERE task_id=?', (task_id,)).fetchone()
    if row is None:
        return None
    state = _check(row)
    current = state['current'].get(task_id)
    if current is None:
        return None
    pages = _check(db.execute('SELECT payload,checksum FROM plan_page_sources WHERE task_id=? AND plan_digest=?',
                             (task_id, current.proposal_digest)).fetchone())
    for name in ('operations', 'requests', 'cursors'):
        state[name].update(pages[name])
    return state


def load_current(db, task_id, *, pages=False):
    """Decode only the current pointer and its page partition in a read snapshot.

    Dirty/missing projections use the caller's legacy fallback. A present but
    damaged current partition fails closed; old plan partitions stay unread.
    """
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='plan_source_meta'").fetchone():
        return None
    meta = db.execute('SELECT version,dirty FROM plan_source_meta WHERE id=1').fetchone()
    if meta is not None and meta[0] != 1:
        raise ServiceError('STORAGE_FORMAT_UNSUPPORTED', 'Unsupported plan source version', 500)
    if meta != (1, 0):
        return None
    row = db.execute('SELECT payload,checksum FROM plan_task_sources WHERE task_id=?', (task_id,)).fetchone()
    if row is None:
        return None
    try:
        from .sqlite import decode, MAX_BYTES
        from .plan_sets import empty_state
        from ...multi_branch_contracts import RetrievalPlanSet
        if (type(row[0]) is not str or len(row[0].encode()) > MAX_BYTES
                or hashlib.sha256(row[0].encode()).hexdigest() != row[1]):
            raise ValueError('header checksum')
        tagged = json.loads(row[0])
        if (type(tagged) is not dict or set(tagged) != {'type', 'value'}
                or tagged['type'] != 'dict' or type(tagged['value']) is not list):
            raise ValueError('header root')
        sections = dict(tagged['value'])
        template = empty_state()
        if len(sections) != len(tagged['value']) or set(sections) != set(template):
            raise ValueError('header sections')
        for name, value in sections.items():
            if (type(value) is not dict or set(value) != {'type', 'value'}
                    or value['type'] != type(template[name]).__name__ or type(value['value']) is not list):
                raise ValueError('header container')
        current = decode(sections['current'])
        if (type(current) is not dict or set(current) - {task_id}
                or any(type(value) is not RetrievalPlanSet or value.scope.task_id != task_id
                       for value in current.values())):
            raise ValueError('current task binding')
        state = {'current': current, 'operations': {}, 'requests': {}, 'cursors': {}}
        approved = current.get(task_id)
        if pages and approved is not None:
            partition = _check(db.execute('SELECT payload,checksum FROM plan_page_sources WHERE task_id=? AND plan_digest=?',
                                          (task_id, approved.proposal_digest)).fetchone())
            page_names = ('operations', 'requests', 'cursors')
            if (any(value for name, value in partition.items() if name not in page_names)
                    or any(type(record) is not tuple or len(record) != 4 or record[0] != approved
                           for record in partition['operations'].values())):
                raise ValueError('page plan binding')
            state.update({name: partition[name] for name in page_names})
        return state
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError) as error:
        raise ServiceError('STORAGE_CORRUPT', 'Invalid current plan source', 500) from error


def save(db, task_id, prepared):
    header, pages = prepared
    _put(db, 'plan_task_sources', (task_id,), header)
    for key, payload in pages.items():
        _put(db, 'plan_page_sources', (task_id, key), payload)
    db.execute('UPDATE plan_source_meta SET dirty=0 WHERE id=1')


def patch_shadow(payload, before, after):
    """Merge only a page mutation into the compatibility JSON, without object restoration."""
    from .sqlite import encode
    source = json.loads(payload)
    old, new = encode(before), encode(after)
    sections = {name: value for name, value in source['value']}
    for (name, prior), (_, current) in zip(old['value'], new['value']):
        if prior == current:
            continue
        section = sections[name]
        if prior['type'] == 'list':
            if current['value'][:len(prior['value'])] != prior['value']:
                raise ServiceError('STORAGE_CORRUPT', 'Page events are not append-only', 500)
            section['value'].extend(current['value'][len(prior['value']):])
        else:
            key_of = lambda key: json.dumps(key, sort_keys=True, separators=(',', ':'))
            prior_keys = {key_of(pair[0]) for pair in prior['value']}
            updates = {key_of(pair[0]): pair for pair in current['value']}
            merged = []
            for pair in section['value']:
                key = key_of(pair[0])
                if key in prior_keys:
                    if key in updates: merged.append(updates.pop(key))
                else:
                    merged.append(pair)
            merged.extend(updates[key] for key in updates if key not in prior_keys)
            section['value'] = merged
    return json.dumps(source, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
