"""Task headers and plan-scoped page sources, transactionally mirrored for legacy readers."""
import hashlib
import json
from ...errors import ServiceError


def create(db):
    db.execute('CREATE TABLE IF NOT EXISTS plan_source_meta (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, dirty INTEGER NOT NULL)')
    db.execute('INSERT OR IGNORE INTO plan_source_meta VALUES(1,1,1)')
    if db.execute('SELECT version FROM plan_source_meta WHERE id=1').fetchone() != (1,):
        raise ServiceError('STORAGE_FORMAT_UNSUPPORTED', 'Unsupported plan source version', 500)
    db.execute('CREATE TABLE IF NOT EXISTS plan_task_sources (task_id TEXT PRIMARY KEY,payload TEXT NOT NULL,checksum TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS plan_page_sources (task_id TEXT NOT NULL,plan_digest TEXT NOT NULL,payload TEXT NOT NULL,checksum TEXT NOT NULL,PRIMARY KEY(task_id,plan_digest))')
    for event in ('INSERT', 'UPDATE', 'DELETE'):
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS plan_source_dirty_{event.lower()} AFTER {event} ON plan_set_ledger
            BEGIN UPDATE plan_source_meta SET dirty=1 WHERE id=1; END''')


def _check(row):
    if row is None or type(row[0]) is not str or hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
        raise ServiceError('STORAGE_CORRUPT', 'Partitioned plan source invalid', 500)
    from .plan_sets import restore
    return restore((3, row[0]))


def _put(db, table, keys, payload):
    checksum = hashlib.sha256(payload.encode()).hexdigest()
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


def sync(db, state):
    """Full migration/recovery; normal page writes update just one task/plan."""
    from .plan_sets import empty_state
    partitions = {}
    def target(task_id):
        if task_id not in partitions:
            partitions[task_id] = empty_state()
        return partitions[task_id]
    operation_tasks = {key: record[0].scope.task_id for key, record in state['operations'].items()}
    plan_tasks = {approved.proposal_digest: approved.scope.task_id for approved in state['approved'].values()}
    plan_tasks.update({record[0].proposal_digest: record[0].scope.task_id for record in state['operations'].values()})
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
            if isinstance(section, dict): target(task_id)[name][key] = value
            else: target(task_id)[name].append(value)
    db.execute('DELETE FROM plan_task_sources')
    db.execute('DELETE FROM plan_page_sources')
    for task_id, partition in partitions.items():
        save(db, task_id, prepare(partition))
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
