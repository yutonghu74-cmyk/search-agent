"""Build result, step summary and preview projections in an isolated copy."""

import argparse
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import NamedTemporaryFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.result_views import VIEWS as RESULT_VIEWS


SOURCE_TABLES = ('local_ledger', 'retrieval_ledger', 'candidate_ledger',
    'cleaning_usage_steps', 'cleaning_usage_http', 'retrieval_execution_budgets',
    'multi_rule_cleaning_ledger', 'plan_set_ledger', 'image_deliveries',
    'cleaning_checkpoints', 'refill_reservations', 'refill_iteration_metrics',
    'refill_execution_stops')


def source_digest(path):
    digest = hashlib.sha256()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        tables = {r[0]: r[1] for r in db.execute("SELECT name,type FROM sqlite_master WHERE type IN ('table','view')")}
        for table in SOURCE_TABLES:
            if table not in tables:
                continue
            digest.update(table.encode())
            columns = 'package_id,metadata' if table == 'image_deliveries' else '*'
            order = 'id' if tables[table] == 'view' else 'rowid'
            for row in db.execute(f'SELECT {columns} FROM {table} ORDER BY {order}'):
                digest.update(json.dumps(row,ensure_ascii=False,separators=(',', ':')).encode())
    return digest.hexdigest()


def save_checkpoint(path, value):
    with NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as file:
        json.dump(value,file,ensure_ascii=False,sort_keys=True)
        file.flush();os.fsync(file.fileno())
        temp = Path(file.name)
    os.replace(temp,path)


def migrate(source: Path, target: Path, *, resume=False) -> int:
    source, target = source.resolve(), target.resolve()
    if source == target or not source.is_file() or target.exists():
        raise ValueError('source must exist and target must be a new, separate file')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.migrating')
    checkpoint = target.with_name(target.name + '.migration.json')
    if resume:
        if not temporary.is_file() or not checkpoint.is_file():
            raise ValueError('no resumable migration copy exists')
        state = json.loads(checkpoint.read_text())
        if state.get('format') != 1 or state.get('source') != str(source):
            raise ValueError('migration checkpoint does not match source')
        if source_digest(source) != state['source_digest']:
            raise ValueError('source changed since backup; use a new target')
    else:
        if temporary.exists() or checkpoint.exists():
            raise ValueError('migration checkpoint exists; use --resume or a new target')
        os.close(os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600))
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as copy:
                original.backup(copy)
        state = {'format':1,'source':str(source),'source_digest':source_digest(temporary),'phase':'backup'}
        save_checkpoint(checkpoint,state)
    repository = SQLiteMultiRuleApprovalRepository(temporary, projects=None, authorization=None)
    if state['phase'] == 'backup':
        state['result_count'] = repository.rebuild_asset_read_bindings()
        state['task_count'] = repository.tasks.rebuild_task_read_summaries()
        state['phase'] = 'projected'
        save_checkpoint(checkpoint,state)
    count,task_count = state['result_count'],state['task_count']
    with closing(sqlite3.connect(temporary)) as db:
        result = db.execute('PRAGMA integrity_check').fetchone()
        if result != ('ok',):
            raise ValueError('migrated database failed integrity check')
        heads = db.execute('SELECT COUNT(*) FROM task_asset_read_heads').fetchone()[0]
        summaries = db.execute(
            'SELECT COUNT(*) FROM task_read_summaries').fetchone()[0]
        summary_meta = db.execute('SELECT format,task_count,dirty '
            'FROM task_read_summaries_meta WHERE id=1').fetchone()
        missing_bindings = db.execute('SELECT COUNT(*) FROM task_read_summaries '
            'WHERE task_json IS NULL').fetchone()[0]
        snapshots = db.execute(
            'SELECT COUNT(*) FROM task_result_read_snapshots').fetchone()[0]
        views = db.execute(
            'SELECT COUNT(*),COALESCE(SUM(item_count),0) '
            'FROM task_result_read_views').fetchone()
        items = db.execute(
            'SELECT COUNT(*) FROM task_result_read_items').fetchone()[0]
        if (summaries != task_count or summary_meta != (1, task_count, 0)
                or missing_bindings
                or heads != count or snapshots != count
                or views != (count * len(RESULT_VIEWS), items)):
            raise ValueError('migrated read projection count is inconsistent')
        stored_views = {}
        for task_id, view_type in db.execute('SELECT task_id,view_type FROM task_result_read_views'):
            stored_views.setdefault(task_id, set()).add(view_type)
        if any(names != RESULT_VIEWS for names in stored_views.values()):
            raise ValueError('migrated result view types are inconsistent')
        for task_id, operation_id, summary_json in db.execute(
                'SELECT task_id,operation_id,summary_json FROM task_step_read_runs'):
            run = json.loads(summary_json)
            for step in run['steps']:
                stored = db.execute('''SELECT COUNT(*) FROM task_step_read_items
                    WHERE task_id=? AND operation_id=? AND step_execution_id=?''',
                    (task_id, operation_id,
                     step['step_execution_id'])).fetchone()[0]
                if stored != step['counts']['input']:
                    raise ValueError('migrated step item count is inconsistent')
    from tools.verify_task_read_projections import verify
    state['verified'] = verify(repository)
    if source_digest(source) != state['source_digest']:
        raise ValueError('source changed during migration; copy not published')
    state['phase'] = 'verified'
    save_checkpoint(checkpoint,state)
    os.chmod(temporary,0o600)
    os.link(temporary,target)
    temporary.unlink()
    state['phase'] = 'published'
    save_checkpoint(checkpoint,state)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('--resume', action='store_true', help='resume an interrupted isolated copy')
    args = parser.parse_args()
    try:
        count = migrate(args.source, args.target, resume=args.resume)
    except (ValueError, sqlite3.Error, OSError, ServiceError) as exc:
        parser.error(str(exc))
    print(f'migrated {count} current result bindings into {args.target}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
