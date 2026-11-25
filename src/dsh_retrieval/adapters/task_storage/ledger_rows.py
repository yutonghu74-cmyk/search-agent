"""One row store with legacy SQL views; no persisted aggregate JSON mirror."""
import json
import re
from ...errors import ServiceError

LEDGERS = frozenset(('local_ledger', 'plan_set_ledger', 'retrieval_ledger',
                     'candidate_ledger', 'multi_rule_cleaning_ledger'))


def kind(db, name):
    row = db.execute('SELECT type FROM sqlite_master WHERE name=?', (name,)).fetchone()
    return row[0] if row else None


def watch(db, name, trigger, event, body):
    ref = 'OLD' if event == 'DELETE' else 'NEW'
    target = 'ledger_registry' if kind(db, name) == 'view' and name in LEDGERS else name
    condition = f"WHEN {ref}.ledger='{name}'" if target == 'ledger_registry' else ''
    db.execute(f'CREATE TRIGGER IF NOT EXISTS {trigger} AFTER {event} ON {target} {condition} BEGIN {body} END')


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def unpack(payload):
    root = json.loads(payload)
    if type(root) is not dict or set(root) != {'type', 'value'} or root['type'] != 'dict':
        raise ValueError('invalid ledger root')
    sections, entries = {}, {}
    for position, (section, value) in enumerate(root['value']):
        if (type(section) is not str or section in sections or type(value) is not dict
                or set(value) != {'type','value'} or type(value['value']) is not list
                or value['type'] not in ('dict', 'list', 'tuple')):
            raise ValueError('invalid ledger section')
        sections[section] = (value['type'], position)
        for index, entry in enumerate(value['value']):
            key = _json(entry[0]) if value['type'] == 'dict' else str(index)
            identity = (section, key)
            if identity in entries:
                raise ValueError('duplicate ledger key')
            entries[identity] = (index, _json(entry))
    return sections, entries


def prepare(db, name, format, payload):
    """Compute record changes in the caller's read snapshot, before taking a write lock."""
    sections, entries = unpack(payload)
    prior_sections = {row[0]: row[1:] for row in db.execute(
        'SELECT section,kind,position FROM ledger_sections WHERE ledger=?', (name,))}
    prior_entries = {(row[0], row[1]): row[2:] for row in db.execute(
        'SELECT section,item_key,position,payload FROM ledger_items WHERE ledger=?', (name,))}
    current = db.execute('SELECT format,error_payload FROM ledger_registry WHERE ledger=?', (name,)).fetchone()
    return (name, format,
        [key for key in prior_sections if key not in sections],
        [(key, *value) for key, value in sections.items() if prior_sections.get(key) != value],
        [key for key in prior_entries if key not in entries],
        [(*key, *value) for key, value in entries.items() if prior_entries.get(key) != value],
        current != (format, None))


def apply(db, prepared):
    name, format, removed_sections, sections, removed, entries, metadata_changed = prepared
    if not (removed_sections or sections or removed or entries or metadata_changed):
        return False
    db.executemany('DELETE FROM ledger_items WHERE ledger=? AND section=? AND item_key=?',
                   ((name, *key) for key in removed))
    db.executemany('DELETE FROM ledger_sections WHERE ledger=? AND section=?',
                   ((name, section) for section in removed_sections))
    db.executemany('''INSERT INTO ledger_sections VALUES(?,?,?,?) ON CONFLICT(ledger,section)
        DO UPDATE SET kind=excluded.kind,position=excluded.position''',
        ((name, *row) for row in sections))
    db.executemany('''INSERT INTO ledger_items VALUES(?,?,?,?,?) ON CONFLICT(ledger,section,item_key)
        DO UPDATE SET position=excluded.position,payload=excluded.payload''',
        ((name, *row) for row in entries))
    db.execute('UPDATE ledger_registry SET format=?,error_payload=NULL,revision=revision+1 WHERE ledger=?', (format, name))
    return True


def write(db, name, format, payload):
    return apply(db, prepare(db, name, format, payload))


def migrate(db, name, validate):
    """Atomic standalone or nested migration; never leave a half-created layout."""
    owns = not db.in_transaction
    if owns:
        db.execute('BEGIN IMMEDIATE')
    try:
        _migrate(db, name, validate)
        if owns:
            db.commit()
    except BaseException:
        if owns:
            db.rollback()
        raise


def _migrate(db, name, validate):
    """Source table is dropped only after validation, inside the caller's transaction."""
    if name not in LEDGERS:
        raise ValueError('unknown ledger')
    if kind(db, name) == 'view':
        version = db.execute('SELECT version FROM ledger_storage_meta WHERE id=1').fetchone()
        if version != (1,):
            raise ServiceError('STORAGE_FORMAT_UNSUPPORTED', 'Unsupported row storage version', 500)
        validate(db.execute(f'SELECT format,payload FROM {name} WHERE id=1').fetchone())
        _install_compatibility(db,name)
        return
    row = db.execute(f'SELECT format,payload FROM {name} WHERE id=1').fetchone()
    validate(row)
    sections, entries = unpack(row[1])
    old_triggers = list(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (name,)))
    db.execute('CREATE TABLE IF NOT EXISTS ledger_storage_meta (id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL)')
    db.execute('INSERT OR IGNORE INTO ledger_storage_meta VALUES(1,1)')
    if db.execute('SELECT version FROM ledger_storage_meta WHERE id=1').fetchone() != (1,):
        raise ServiceError('STORAGE_FORMAT_UNSUPPORTED', 'Unsupported row storage version', 500)
    db.execute('''CREATE TABLE IF NOT EXISTS ledger_registry (
        ledger TEXT PRIMARY KEY,format INTEGER NOT NULL,revision INTEGER NOT NULL,error_payload TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS ledger_sections (
        ledger TEXT NOT NULL,section TEXT NOT NULL,kind TEXT NOT NULL,position INTEGER NOT NULL,
        PRIMARY KEY(ledger,section))''')
    db.execute('''CREATE TABLE IF NOT EXISTS ledger_items (
        ledger TEXT NOT NULL,section TEXT NOT NULL,item_key TEXT NOT NULL,position INTEGER NOT NULL,payload TEXT NOT NULL,
        PRIMARY KEY(ledger,section,item_key))''')
    db.execute('CREATE INDEX IF NOT EXISTS ledger_item_order ON ledger_items(ledger,section,position)')
    db.execute('INSERT INTO ledger_registry VALUES(?,?,0,NULL)', (name, row[0]))
    db.executemany('INSERT INTO ledger_sections VALUES(?,?,?,?)', ((name,key,*value) for key,value in sections.items()))
    db.executemany('INSERT INTO ledger_items VALUES(?,?,?,?,?)', ((name,*key,*value) for key,value in entries.items()))
    db.execute(f'DROP TABLE {name}')
    db.execute(f'''CREATE VIEW {name} AS SELECT 1 AS id,format,
        COALESCE(error_payload, json_object('type','dict','value',json(COALESCE((
          SELECT json_group_array(json_array(section,json_object('type',kind,'value',json(COALESCE((
            SELECT json_group_array(json(payload)) FROM (
              SELECT payload FROM ledger_items i WHERE i.ledger='{name}' AND i.section=s.section ORDER BY position
            )), '[]'))))) FROM (
            SELECT section,kind FROM ledger_sections WHERE ledger='{name}' ORDER BY position
          ) s
        ),'[]')))) AS payload FROM ledger_registry WHERE ledger='{name}' ''')
    _install_compatibility(db,name)
    for trigger, sql in old_triggers:
        match = re.search(r'AFTER\s+(INSERT|UPDATE|DELETE)\s+ON\s+[\w"`]+\s+BEGIN\s+(.*)\s+END\s*$', sql, re.S | re.I)
        if not match or re.search(r'\b(?:NEW|OLD)\.', match[2], re.I):
            raise ServiceError('STORAGE_FORMAT_UNSUPPORTED', 'Cannot migrate an unknown ledger trigger', 500)
        watch(db, name, trigger, match[1].upper(), match[2])
    validate(db.execute(f'SELECT format,payload FROM {name} WHERE id=1').fetchone())


def _install_compatibility(db,name):
    def install(sql):
        trigger = sql.split()[2]
        prior = db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",(trigger,)).fetchone()
        if prior and prior[0].strip().rstrip(';') == sql.strip().rstrip(';'):
            return
        db.execute('DROP TRIGGER IF EXISTS ' + trigger)
        db.execute(sql)
    # Raw SQL compatibility is used by historical maintenance tools and fault
    # injection tests. Normal application writes use prepare/apply record deltas.
    valid = """CASE WHEN json_valid(NEW.payload) THEN
        json_extract(NEW.payload,'$.type') IS 'dict' AND json_type(NEW.payload,'$.value') IS 'array'
        AND (SELECT COUNT(*) FROM json_each(NEW.payload))=2
        AND NOT EXISTS (SELECT 1 FROM json_each(NEW.payload,'$.value') s WHERE
          json_type(NEW.payload,'$.value['||s.key||']') IS NOT 'array'
          OR json_array_length(NEW.payload,'$.value['||s.key||']')!=2
          OR json_type(NEW.payload,'$.value['||s.key||'][0]') IS NOT 'text'
          OR json_type(NEW.payload,'$.value['||s.key||'][1]') IS NOT 'object'
          OR (SELECT COUNT(*) FROM json_each(NEW.payload,'$.value['||s.key||'][1]'))!=2
          OR COALESCE(json_extract(NEW.payload,'$.value['||s.key||'][1].type'),'') NOT IN ('dict','list','tuple')
          OR json_type(NEW.payload,'$.value['||s.key||'][1].value') IS NOT 'array')
        AND NOT EXISTS (SELECT 1 FROM json_each(NEW.payload,'$.value') s,
          json_each(CASE WHEN json_type(NEW.payload,'$.value['||s.key||'][1].value')='array'
                    THEN json_extract(NEW.payload,'$.value['||s.key||'][1].value') ELSE '[]' END) e
          WHERE json_extract(NEW.payload,'$.value['||s.key||'][1].type')='dict'
          AND CASE WHEN e.type='array' THEN json_array_length(e.value) ELSE 0 END != 2)
        ELSE 0 END"""
    install(f'''CREATE TRIGGER ledger_compat_{name}_update INSTEAD OF UPDATE ON {name} BEGIN
        DELETE FROM ledger_items WHERE ledger='{name}' AND ({valid});
        DELETE FROM ledger_sections WHERE ledger='{name}' AND ({valid});
        INSERT INTO ledger_sections SELECT '{name}', json_extract(s.value,'$[0]'),
            json_extract(s.value,'$[1].type'),s.key FROM json_each(CASE WHEN {valid} THEN NEW.payload ELSE '{{}}' END,'$.value') s;
        INSERT INTO ledger_items SELECT '{name}',json_extract(s.value,'$[0]'),
            CASE WHEN json_extract(s.value,'$[1].type')='dict' THEN (e.value -> '$[0]') ELSE CAST(e.key AS TEXT) END,
            e.key,(s.value -> ('$[1].value[' || e.key || ']')) FROM json_each(CASE WHEN {valid} THEN NEW.payload ELSE '{{}}' END,'$.value') s,
            json_each(s.value,'$[1].value') e;
        UPDATE ledger_registry SET format=NEW.format,revision=revision+1,
            error_payload=CASE WHEN {valid} THEN NULL ELSE NEW.payload END WHERE ledger='{name}';
        END''')
    install(f'''CREATE TRIGGER ledger_compat_{name}_delete INSTEAD OF DELETE ON {name} BEGIN
        DELETE FROM ledger_items WHERE ledger='{name}';
        DELETE FROM ledger_sections WHERE ledger='{name}';
        DELETE FROM ledger_registry WHERE ledger='{name}'; END''')


def prepare_subset(db, name, format, before, after):
    """Prepare only changed record keys and appended events from a task partition."""
    from .sqlite import encode, MAX_BYTES
    old = {key:value for key,value in encode(before)['value']}
    new = {key:value for key,value in encode(after)['value']}
    removed, entries = [], []
    stats = {row[0]: [row[1],row[2]] for row in db.execute(
        'SELECT section,COUNT(*),SUM(length(CAST(payload AS BLOB))) FROM ledger_items WHERE ledger=? GROUP BY section', (name,))}
    sections = list(db.execute('SELECT section,kind FROM ledger_sections WHERE ledger=?', (name,)))
    def lookup(section, key, expected):
        row = db.execute('SELECT item_key,position,length(CAST(payload AS BLOB)) FROM ledger_items WHERE ledger=? AND section=? AND item_key=?', (name,section,key)).fetchone()
        if row is None and expected:
            # Legacy raw SQL can retain escaped Unicode in encoded tuple keys.
            for candidate in db.execute('SELECT item_key,position,length(CAST(payload AS BLOB)) FROM ledger_items WHERE ledger=? AND section=?', (name,section)):
                if _json(json.loads(candidate[0])) == key:
                    row = candidate
                    break
        if expected != (row is not None):
            raise ServiceError('STORAGE_CORRUPT', 'Partition source key differs from record ledger', 500)
        return row
    for section, value in new.items():
        prior = old[section]
        if value == prior:
            continue
        count, size = stats.setdefault(section,[0,0])
        if value['type'] == 'dict':
            left = {_json(pair[0]):_json(pair) for pair in prior['value']}
            right = {_json(pair[0]):_json(pair) for pair in value['value']}
            for key in left.keys() - right.keys():
                row = lookup(section,key,True)
                removed.append((section,row[0])); count -= 1; size -= row[2]
            last = db.execute('SELECT COALESCE(MAX(position),-1) FROM ledger_items WHERE ledger=? AND section=?', (name,section)).fetchone()[0]
            for key,payload in right.items():
                if left.get(key) == payload:
                    continue
                row = lookup(section,key,key in left)
                if row is None:
                    last += 1; count += 1
                else:
                    size -= row[2]
                size += len(payload.encode())
                entries.append((section,row[0] if row else key,row[1] if row else last,payload))
        else:
            if value['value'][:len(prior['value'])] != prior['value']:
                raise ServiceError('STORAGE_CORRUPT', 'Page events are not append-only', 500)
            last = db.execute('SELECT COALESCE(MAX(position),-1) FROM ledger_items WHERE ledger=? AND section=?', (name,section)).fetchone()[0]
            for entry in value['value'][len(prior['value']):]:
                last += 1; count += 1
                payload = _json(entry); size += len(payload.encode())
                entries.append((section,str(last),last,payload))
        stats[section] = [count,size]
    total = len(_json({'type':'dict','value':[]}).encode()) + max(len(sections)-1,0)
    for section,kind in sections:
        count,size = stats.get(section,[0,0])
        total += len(_json([section,{'type':kind,'value':[]}]).encode()) + size + max(count-1,0)
    if total > MAX_BYTES:
        raise ServiceError('STORAGE_LIMIT', 'Compatibility ledger exceeds local limit', 507)
    return name,format,[],[],removed,entries,False


def prepare_state(db, name, format, state):
    """Encode records directly; avoid constructing and parsing an aggregate JSON."""
    from .sqlite import encode, MAX_BYTES
    sections, entries = {}, {}
    total = len(_json({'type':'dict','value':[]}).encode()) + max(len(state)-1,0)
    for position,(section,values) in enumerate(state.items()):
        kind = type(values).__name__
        if kind not in ('dict','list','tuple'):
            raise ValueError('invalid state container')
        sections[section] = (kind,position)
        items = values.items() if kind == 'dict' else enumerate(values)
        count = 0
        for index,(key,value) in enumerate(items):
            entry = [encode(key),encode(value)] if kind == 'dict' else encode(value)
            payload = _json(entry)
            entries[(section,_json(entry[0]) if kind == 'dict' else str(index))] = (index,payload)
            total += len(payload.encode()); count += 1
        total += len(_json([section,{'type':kind,'value':[]}]).encode()) + max(count-1,0)
    if total > MAX_BYTES:
        raise ServiceError('STORAGE_LIMIT','Ledger exceeds local compatibility limit',507)
    previous = {row[0]:row[1:] for row in db.execute('SELECT section,kind,position FROM ledger_sections WHERE ledger=?',(name,))}
    prior = {(row[0],row[1]):row[2:] for row in db.execute('SELECT section,item_key,position,payload FROM ledger_items WHERE ledger=?',(name,))}
    meta = db.execute('SELECT format,error_payload FROM ledger_registry WHERE ledger=?',(name,)).fetchone()
    return (name,format,[key for key in previous if key not in sections],
            [(key,*value) for key,value in sections.items() if previous.get(key)!=value],
            [key for key in prior if key not in entries],
            [(*key,*value) for key,value in entries.items() if prior.get(key)!=value],meta!=(format,None))


def write_state(db, name, format, state):
    return apply(db, prepare_state(db,name,format,state))


def write_memory(db, memory):
    return write_state(db,'local_ledger',1,{key:value for key,value in vars(memory).items() if key!='_lock'})
