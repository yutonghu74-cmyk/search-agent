"""Optimistic preparation for pure ledger callbacks; SQL-mutating callbacks stay locked."""
from contextlib import closing, contextmanager
from contextvars import ContextVar
from ...errors import ServiceError

_checks = ContextVar('ledger_permission_checks', default=None)


def checked_access(provider, *args):
    result = provider.require_access(*args)
    checks = _checks.get()
    if checks is not None and not any(owner is provider and parameters == args for owner, parameters in checks):
        checks.append((provider, args))
    return result


@contextmanager
def capture_access():
    calls = []
    token = _checks.set(calls)
    try:
        yield calls
    finally:
        _checks.reset(token)


# A private SQLite snapshot lets legacy SQL callbacks prepare derived rows and
# event payloads without holding the live writer. Only explicit writes are
# replayed after the original database's commit watermark is checked.
import functools
import re
import sqlite3
from pathlib import Path

_staging = ContextVar('ledger_staging_connections', default={})


def connection(path):
    recorder = _staging.get().get(str(Path(path).resolve()))
    return Lease(recorder) if recorder is not None else None


class Recorder:
    def __init__(self, db):
        self.db, self.writes, self.serial = db, [], 0


class Lease:
    def __init__(self, recorder):
        self.recorder, self.db = recorder, recorder.db
        self.scope, self.start = None, None

    def _begin(self, sql='BEGIN'):
        self.start = len(self.recorder.writes)
        if self.db.in_transaction:
            self.recorder.serial += 1
            self.scope = 'stage_' + str(self.recorder.serial)
            self.db.execute('SAVEPOINT ' + self.scope)
        else:
            self.scope = 'root'
            self.db.execute('BEGIN')

    def execute(self, sql, params=()):
        normalized = ' '.join(sql.split())
        command = normalized.split(' ', 1)[0].upper()
        if command == 'BEGIN':
            self._begin(sql)
            return self.db.execute('SELECT 1 WHERE 0')
        if command in ('COMMIT', 'END'):
            self.commit()
            return self.db.execute('SELECT 1 WHERE 0')
        if command == 'ROLLBACK':
            self.rollback()
            return self.db.execute('SELECT 1 WHERE 0')
        # Turn legacy aggregate updates into record deltas during preparation.
        match = re.fullmatch(r'UPDATE (\w+) SET (?:format=(\?|\d+),\s*)?payload=\?(?: WHERE id=1)?', normalized, re.I)
        from . import ledger_rows
        if match and match[1] in ledger_rows.LEDGERS:
            format = (params[0] if match[2] == '?' else int(match[2]) if match[2] else
                      self.db.execute('SELECT format FROM ledger_registry WHERE ledger=?', (match[1],)).fetchone()[0])
            ledger_rows.write(self, match[1], format, params[-1])
            return self.db.execute('SELECT 1 WHERE 0')
        mutating = command not in ('SELECT', 'PRAGMA', 'EXPLAIN')
        if mutating and not self.db.in_transaction:
            self._begin()
        before = self.db.total_changes
        schema = self.db.execute('PRAGMA schema_version').fetchone()[0] if command in ('CREATE','ALTER','DROP') else None
        cursor = self.db.execute(sql, params)
        changed = (self.db.total_changes != before or schema is not None and schema != self.db.execute('PRAGMA schema_version').fetchone()[0])
        if mutating and (changed or 'RETURNING' in normalized.upper()):
            self.recorder.writes.append((sql, tuple(params), False))
        return cursor

    def executemany(self, sql, params):
        params = [tuple(row) for row in params]
        if not params:
            return self.db.execute('SELECT 1 WHERE 0')
        if not self.db.in_transaction:
            self._begin()
        before = self.db.total_changes
        cursor = self.db.executemany(sql, params)
        if self.db.total_changes != before:
            self.recorder.writes.append((sql, params, True))
        return cursor

    def commit(self):
        if self.scope == 'root':
            self.db.commit()
        elif self.scope:
            self.db.execute('RELEASE ' + self.scope)
        self.scope = None

    def rollback(self):
        if self.scope == 'root':
            self.db.rollback()
        elif self.scope:
            self.db.execute('ROLLBACK TO ' + self.scope)
            self.db.execute('RELEASE ' + self.scope)
        if self.scope is not None:
            del self.recorder.writes[self.start:]
        self.scope = None

    def close(self):
        if self.scope:
            self.rollback()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.rollback() if kind else self.commit()

    def __getattr__(self, name):
        if name in ('in_transaction', 'total_changes', 'row_factory', 'create_function'):
            return getattr(self.db, name)
        raise AttributeError('Unsupported staged connection operation: ' + name)


def _snapshot(live, scratch, path):
    """Copy metadata rows consistently; immutable uploaded bytes stay lazy/read-only."""
    images = live.execute("SELECT 1 FROM sqlite_master WHERE name='query_images'").fetchone()
    if images is None or live.execute('SELECT 1 FROM query_images LIMIT 1').fetchone() is None:
        if live.in_transaction:
            with closing(sqlite3.connect(path)) as reader:
                reader.backup(scratch)
        else:
            live.backup(scratch)
        return
    scratch.execute('ATTACH DATABASE ? AS source', (Path(path).as_uri() + '?mode=ro',))
    scratch.execute('BEGIN')
    schema = scratch.execute("SELECT type,name,sql FROM source.sqlite_master WHERE sql IS NOT NULL ORDER BY rowid").fetchall()
    for kind, name, sql in schema:
        if kind == 'table' and not name.startswith('sqlite_'):
            scratch.execute(sql)
            if name != 'query_images':
                quoted = '"' + name.replace('"', '""') + '"'
                scratch.execute(f'INSERT INTO main.{quoted} SELECT * FROM source.{quoted}')
    if scratch.execute("SELECT 1 FROM main.sqlite_master WHERE name='sqlite_sequence'").fetchone():
        scratch.execute('DELETE FROM main.sqlite_sequence')
        scratch.execute('INSERT INTO main.sqlite_sequence SELECT * FROM source.sqlite_sequence')
    for kind, name, sql in schema:
        if kind in ('index', 'view', 'trigger'):
            scratch.execute(sql)
    if scratch.execute("SELECT 1 FROM main.sqlite_master WHERE name='query_images'").fetchone():
        scratch.execute('CREATE TEMP VIEW query_images AS SELECT * FROM source.query_images')
    scratch.commit()


def _stage(repo, path, function):
    for attempt in range(4):
        with closing(repo._connect()) as live, closing(sqlite3.connect(':memory:', uri=True)) as scratch:
            serialized = attempt == 3
            if serialized:
                live.execute('BEGIN IMMEDIATE')
            version = live.execute('PRAGMA data_version').fetchone()[0]
            _snapshot(live, scratch, path)
            if live.execute('PRAGMA data_version').fetchone()[0] != version:
                continue
            recorder = Recorder(scratch)
            token = _staging.set({**_staging.get(), path: recorder})
            try:
                with capture_access() as checks:
                    result = function()
            except ServiceError:
                if not serialized and live.execute('PRAGMA data_version').fetchone()[0] != version:
                    continue
                raise
            finally:
                _staging.reset(token)
            if scratch.in_transaction:
                raise ServiceError('STORAGE_UNAVAILABLE', 'Staged callback left an open transaction', 503, True)
            if not serialized:
                live.execute('BEGIN IMMEDIATE')
            if live.execute('PRAGMA data_version').fetchone()[0] != version:
                live.rollback()
                continue
            try:
                for provider, parameters in checks:
                    provider.require_access(*parameters)
                for sql, parameters, many in recorder.writes:
                    (live.executemany if many else live.execute)(sql, parameters)
                live.commit()
                return result
            except BaseException:
                live.rollback()
                raise
    raise ServiceError('REVISION_CONFLICT', '状态正在变化，请用原请求重试。', 409, True)


def staged(*, write_arg=0, always=False):
    def decorate(function):
        @functools.wraps(function)
        def invoke(self, *args, **kwargs):
            write = always or (args[write_arg] if len(args) > write_arg else kwargs.get('write', False))
            repo = getattr(self, 'tasks', self)
            path = str(Path(repo.path).resolve())
            if not write or path in _staging.get():
                return function(self, *args, **kwargs)
            try:
                return _stage(repo, path, lambda: function(self, *args, **kwargs))
            except sqlite3.Error as error:
                raise ServiceError('STORAGE_UNAVAILABLE', 'Prepared ledger transaction failed', 503, True) from error
        return invoke
    return decorate
