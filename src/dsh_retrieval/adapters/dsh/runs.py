"""Durable index from business task/revision to isolated DSH runs, and restart recovery.

Only identifiers and status are stored; prompts, messages and proposals stay in
the per-run directory. The business ledger remains authoritative: a recovered
run is never resumed or accepted, only closed as interrupted.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time

BINDING = ('project_id', 'task_id', 'revision', 'generation', 'task_version',
           'dialogue_session_id', 'expected_version')
WORKER = 'dsh_retrieval.adapters.dsh.worker'


def tenant_digest(tenant_id):
    return hashlib.sha256(json.dumps(['tenant', tenant_id]).encode()).hexdigest()


class RunIndex:
    def __init__(self, path):
        self.path = Path(path)
        with self._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS dsh_runs (
                run_id TEXT PRIMARY KEY, principal_digest TEXT NOT NULL, tenant_digest TEXT NOT NULL,
                project_id TEXT, task_id TEXT, revision INTEGER, generation INTEGER, task_version INTEGER,
                dialogue_session_id TEXT, expected_version INTEGER, route TEXT, request_id TEXT,
                model_profile TEXT NOT NULL, status TEXT NOT NULL, error_code TEXT, pid INTEGER,
                started_at REAL NOT NULL, ended_at REAL)''')
            db.execute('CREATE INDEX IF NOT EXISTS dsh_runs_task ON dsh_runs(tenant_digest, task_id, started_at)')
        os.chmod(self.path, 0o600)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def start(self, run_id, *, principal, tenant, scope, model_profile):
        row = {key: scope.get(key) for key in BINDING}
        with self._connect() as db:
            db.execute(f'''INSERT INTO dsh_runs (run_id, principal_digest, tenant_digest, {", ".join(BINDING)},
                           route, request_id, model_profile, status, started_at)
                           VALUES (?, ?, ?, {", ".join("?" * len(BINDING))}, ?, ?, ?, 'running', ?)''',
                       (run_id, principal, tenant_digest(tenant), *row.values(),
                        scope.get('route'), scope.get('request_id'), model_profile, time.time()))

    def attach_process(self, run_id, pid):
        with self._connect() as db:
            db.execute("UPDATE dsh_runs SET pid = ? WHERE run_id = ? AND status = 'running'", (pid, run_id))

    def finish(self, run_id, status, error_code=None):
        with self._connect() as db:
            db.execute("UPDATE dsh_runs SET status = ?, error_code = ?, ended_at = ?, pid = NULL "
                       "WHERE run_id = ? AND status = 'running'", (status, error_code, time.time(), run_id))

    def for_task(self, tenant_id, task_id, limit=200):
        with self._connect() as db:
            rows = db.execute('''SELECT run_id, task_id, revision, generation, task_version, route,
                                        model_profile, status, error_code, started_at, ended_at
                                 FROM dsh_runs WHERE tenant_digest = ? AND task_id = ?
                                 ORDER BY started_at DESC LIMIT ?''',
                              (tenant_digest(tenant_id), task_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def recover(self, home):
        """Close runs left 'running' by a crashed service; stop their orphaned process trees."""
        home = Path(home)
        with self._connect() as db:
            stale = db.execute("SELECT run_id, principal_digest, pid FROM dsh_runs WHERE status = 'running'").fetchall()
            for row in stale:
                if row['pid'] and _is_worker(row['pid']):
                    try:
                        os.killpg(row['pid'], signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                db.execute("UPDATE dsh_runs SET status = 'interrupted', error_code = 'SERVICE_RESTARTED', "
                           "ended_at = ?, pid = NULL WHERE run_id = ?", (time.time(), row['run_id']))
        # Includes runs written before this index existed.
        audits = 0
        for audit in home.glob('*/retrieval-*/audit.json'):
            try:
                metadata = json.loads(audit.read_text())
            except (OSError, ValueError):
                continue
            if metadata.get('status') == 'running':
                metadata.update(status='interrupted', error_code='SERVICE_RESTARTED')
                audit.write_text(json.dumps(metadata))
                audits += 1
        return {'runs': len(stale), 'audits': audits}


def _is_worker(pid):
    # A recorded pid may have been reused; only stop our own worker module.
    try:
        command = subprocess.run(['ps', '-o', 'command=', '-p', str(pid)], capture_output=True,
                                 text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return WORKER in command
