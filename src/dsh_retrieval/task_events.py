"""Transactional task change events with a legacy snapshot compatibility path.

Events are change notifications, never an alternate business ledger.  The
client always reloads the current progress/result views after a notification.
"""

from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock

from .errors import ServiceError


EVENT_SCHEMA_VERSION = "task-event@1"
MUTATION_TABLES = (
    "local_ledger", "retrieval_ledger", "candidate_ledger",
    "multi_rule_cleaning_ledger", "plan_set_ledger",
    "search_dispatch_outbox", "candidate_browse_hits",
    "image_deliveries", "image_delivery_events",
    "cleaning_dispatch_attempts", "cleaning_checkpoints",
    "refill_reservations", "refill_dispatch_outbox",
    "auto_refill_outbox", "auto_refill_intake_outbox",
    "refill_iteration_metrics", "refill_execution_stops",
    "near_duplicate_review_keys", "result_exclusion_keys", "manual_review_keys",
)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value):
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


def initialize_event_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS task_event_mutation_clock (
        id INTEGER PRIMARY KEY CHECK(id=1),
        sequence INTEGER NOT NULL,
        updated_at TEXT NOT NULL)""")
    db.execute("""INSERT OR IGNORE INTO task_event_mutation_clock
        VALUES (1,0,STRFTIME('%Y-%m-%dT%H:%M:%fZ','now'))""")
    db.execute("""CREATE TABLE IF NOT EXISTS task_event_state (
        task_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
        owner_user_id TEXT NOT NULL, as_of_sequence INTEGER NOT NULL,
        snapshot_digest TEXT NOT NULL, snapshot_json TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS task_event_stream (
        task_id TEXT NOT NULL, sequence INTEGER NOT NULL,
        tenant_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
        event_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
        payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(task_id, sequence))""")
    db.execute("CREATE INDEX IF NOT EXISTS task_event_stream_owner "
               "ON task_event_stream(tenant_id,owner_user_id,task_id,sequence)")
    existing = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    db.execute('''CREATE TABLE IF NOT EXISTS task_event_task_mutations (
        task_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS task_event_observed_mutations (
        task_id TEXT PRIMARY KEY, sequence INTEGER)''')
    for table in ('task_read_summaries', 'task_step_read_runs',
                  'task_asset_read_heads'):
        if table not in existing:
            continue
        for action in ('INSERT', 'UPDATE', 'DELETE'):
            ref = 'OLD' if action == 'DELETE' else 'NEW'
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS
                task_event_scoped_{table}_{action.lower()}
                AFTER {action} ON {table}
                BEGIN
                  INSERT INTO task_event_task_mutations VALUES ({ref}.task_id,1)
                  ON CONFLICT(task_id) DO UPDATE SET sequence=sequence+1;
                END''')
    for table in MUTATION_TABLES:
        if table not in existing:
            continue
        for action in ("INSERT", "UPDATE", "DELETE"):
            trigger = f"task_event_mutation_{table}_{action.lower()}"
            from .adapters.task_storage import ledger_rows
            ledger_rows.watch(db, table, trigger, action,
                "UPDATE task_event_mutation_clock SET sequence=sequence+1, "
                "updated_at=STRFTIME('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=1;")
    db.execute("""CREATE TABLE IF NOT EXISTS task_event_write_heads (
        task_id TEXT PRIMARY KEY, digest TEXT NOT NULL)""")


def publish_retrieval_budget_stop(db, scope, reason):
    """Notify an existing task stream in the same transaction as its budget stop.

    A stale generation never emits an event for the current stream. A task with
    no stream yet is observed normally on its first authorized read.
    """
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='task_event_state' AND type='table'").fetchone():
        return
    row = db.execute('SELECT tenant_id,owner_user_id,as_of_sequence,snapshot_json '
                     'FROM task_event_state WHERE task_id=?', (scope.task_id,)).fetchone()
    if row is None or row[:2] != (scope.tenant_id, scope.owner_user_id):
        return
    try:
        binding = json.loads(row[3]).get('binding', {})
    except (ValueError, TypeError, AttributeError):
        raise ServiceError('STORAGE_CORRUPT', 'Task event binding is invalid', 500) from None
    if (binding.get('revision'), binding.get('generation')) != (scope.revision, scope.generation):
        return
    previous_mutation = db.execute('SELECT sequence FROM task_event_task_mutations WHERE task_id=?', (scope.task_id,)).fetchone()
    observed = db.execute('SELECT sequence FROM task_event_observed_mutations WHERE task_id=?', (scope.task_id,)).fetchone()
    sequence, now = row[2] + 1, datetime.now(UTC).isoformat()
    payload = {**binding, 'event_schema_version': EVENT_SCHEMA_VERSION,
               'node': 'retrieval', 'code': 'SEARCH_BUDGET_EXHAUSTED', 'reason': reason}
    db.execute('INSERT INTO task_event_stream VALUES (?,?,?,?,?,?,?,?)',
        (scope.task_id, sequence, scope.tenant_id, scope.owner_user_id,
         f'{scope.task_id}:{sequence}', 'node.progress', _canonical(payload), now))
    db.execute('UPDATE task_event_state SET as_of_sequence=?,updated_at=? WHERE task_id=?',
               (sequence, now, scope.task_id))
    db.execute('INSERT INTO task_event_task_mutations VALUES (?,1) '
               'ON CONFLICT(task_id) DO UPDATE SET sequence=sequence+1', (scope.task_id,))
    if previous_mutation is not None and observed == previous_mutation:
        db.execute('INSERT OR REPLACE INTO task_event_observed_mutations '
                   'SELECT task_id,sequence FROM task_event_task_mutations WHERE task_id=?', (scope.task_id,))
    db.execute('DELETE FROM task_event_stream WHERE task_id=? AND sequence<=?',
               (scope.task_id, max(0, sequence - 512)))


def publish_task_change(db, task, summary, tables):
    """Append notifications inside the caller's business transaction."""
    task_id = task.task_id
    refs = {'nodes': dict(db.execute('SELECT node,version FROM task_node_read_heads WHERE task_id=?', (task_id,)))}
    if 'task_step_read_runs' in tables:
        refs['steps'] = list(db.execute("""SELECT operation_id,output_version,summary_json
            FROM task_step_read_runs WHERE task_id=? AND revision=? AND generation=?
            ORDER BY operation_id""", (task_id,task.confirmed_revision,task.run_generation)))
    if 'task_asset_read_heads' in tables:
        refs['result'] = db.execute('SELECT result_set_id,source_digest FROM task_asset_read_heads WHERE task_id=?', (task_id,)).fetchone()
    fingerprint = _digest([summary, refs])
    old = db.execute('SELECT digest FROM task_event_write_heads WHERE task_id=?', (task_id,)).fetchone()
    saved = db.execute('SELECT snapshot_json FROM task_event_state WHERE task_id=?',
                       (task_id,)).fetchone()
    if old == (fingerprint,) and saved and 'node_versions' in json.loads(saved[0]):
        # Rebuilding identical projections can advance SQL mutation triggers.
        # The verified fingerprint is unchanged, so acknowledge that watermark
        # in this same transaction without creating a duplicate notification.
        db.execute('''INSERT OR REPLACE INTO task_event_observed_mutations
            SELECT task_id,sequence FROM task_event_task_mutations WHERE task_id=?''',
            (task_id,))
        return
    binding = {'task_id':task_id, 'revision':task.confirmed_revision,
               'generation':task.run_generation, 'task_version':task.task_version}
    row = db.execute('SELECT as_of_sequence FROM task_event_state WHERE task_id=?', (task_id,)).fetchone()
    sequence = (row[0] if row else 0) + 1
    node_versions = {**refs['nodes'],
        'expansion':_digest([binding,summary.get('raw_request')]),
        'retrieval':_digest(summary.get('progress')),
        'cleaning':_digest([refs.get('steps'),refs.get('result')])}
    previous = db.execute('SELECT snapshot_json FROM task_event_state WHERE task_id=?', (task_id,)).fetchone()
    previous = json.loads(previous[0]) if previous else {}
    changed = [node for node,version in node_versions.items() if previous.get('node_versions',{}).get(node) != version]
    kind = ('task.snapshot' if not row else 'task.revision_changed'
        if previous.get('binding') != binding else 'output.available'
        if any(node in changed for node in ('cleaning','packaging')) else 'node.progress')
    payload = {**binding, 'event_schema_version':EVENT_SCHEMA_VERSION, 'node':None,
               'output_versions':node_versions, 'changed_nodes':changed, 'run_status':summary['run_status'],
               'stage':summary['stage']}
    now = datetime.now(UTC).isoformat()
    db.execute('INSERT INTO task_event_stream VALUES (?,?,?,?,?,?,?,?)',
        (task_id,sequence,task.tenant_id,task.owner_user_id,f'{task_id}:{sequence}',kind,_canonical(payload),now))
    db.execute("INSERT OR REPLACE INTO task_event_state VALUES (?,?,?,?,?,?,?)",
        (task_id,task.tenant_id,task.owner_user_id,sequence,fingerprint,_canonical({'binding':binding,'node_versions':node_versions}),now))
    db.execute('INSERT OR REPLACE INTO task_event_write_heads VALUES (?,?)', (task_id,fingerprint))
    db.execute("""INSERT INTO task_event_task_mutations VALUES (?,1)
        ON CONFLICT(task_id) DO UPDATE SET sequence=sequence+1""", (task_id,))
    db.execute("""INSERT OR REPLACE INTO task_event_observed_mutations
        SELECT task_id,sequence FROM task_event_task_mutations WHERE task_id=?""", (task_id,))
    db.execute('DELETE FROM task_event_stream WHERE task_id=? AND sequence<=?', (task_id,max(0,sequence-512)))


class TaskEventFeed:
    """Persist and replay bounded, tenant-scoped task change notifications."""

    def __init__(self, path=None, *, retention=512):
        if type(retention) is not int or retention < 8:
            raise ValueError("event retention must be at least 8")
        self.path = Path(path).absolute() if path is not None else None
        self.retention = retention
        self._lock = RLock()
        self._states = {}
        self._events = {}
        if self.path is not None:
            try:
                with closing(self._connect()) as db, db:
                    initialize_event_tables(db)
            except sqlite3.Error as exc:
                raise ServiceError("STORAGE_UNAVAILABLE", "task event ledger unavailable",
                                   503, retryable=True) from exc

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def mutation_sequence(self):
        """Return the commit-scoped business-write watermark, if persistent."""
        if self.path is None:
            return None
        try:
            with closing(self._connect()) as db:
                row = db.execute(
                    "SELECT sequence FROM task_event_mutation_clock WHERE id=1").fetchone()
                if row is None or type(row[0]) is not int or row[0] < 0:
                    raise ServiceError("STORAGE_CORRUPT",
                                       "task event mutation clock is invalid", 500)
                return row[0]
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE",
                               "task event mutation clock unavailable", 503,
                               retryable=True) from exc

    def task_mutation_sequence(self, task_id):
        """Commit-scoped watermark for a task with a migrated progress projection."""
        if self.path is None:
            return None
        try:
            with closing(self._connect()) as db:
                db.execute('BEGIN')
                ready = db.execute('SELECT dirty FROM task_read_summaries_meta WHERE id=1').fetchone()
                if ready is None or ready[0]:
                    return None
                row = db.execute('''SELECT json_type(summary_json,'$.progress')
                    FROM task_read_summaries WHERE task_id=?''', (task_id,)).fetchone()
                sequence = db.execute('SELECT sequence FROM task_event_task_mutations '
                    'WHERE task_id=?', (task_id,)).fetchone()
                # Legacy tasks retain snapshot polling until migration. A
                # deleted task's tombstone still wakes existing connections.
                if row is not None and row[0] != 'object':
                    return None
                if row is None and sequence is None:
                    return None
                return sequence[0] if sequence else 0
        except sqlite3.Error as exc:
            raise ServiceError('STORAGE_UNAVAILABLE',
                'task mutation watermark unavailable', 503, retryable=True) from exc

    @staticmethod
    def _snapshot(task, progress, result_set, runs, step_outputs):
        binding = {
            "task_id": task["task_id"], "revision": task["confirmed_revision"],
            "generation": task["run_generation"], "task_version": task["task_version"],
        }
        result = None if result_set is None else {
            "result_set_id": result_set.get("result_set_id"),
            "result_set_digest": result_set.get("result_set_digest"),
            "kind": result_set.get("kind"),
            "accepted_count": result_set.get("accepted_count"),
            "failed_count": result_set.get("failed_count"),
            "pending_review_count": result_set.get("pending_review_count"),
            "user_excluded_count": result_set.get("user_excluded_count", 0),
            "gap": result_set.get("gap"),
        }
        executions = [{key: row.get(key) for key in (
            "run_id", "revision", "generation", "task_version", "route",
            "model_profile", "status", "error_code", "started_at", "ended_at")}
            for row in runs]
        return {"binding": binding, "run_status": task.get("run_status"),
                "stage": task.get("stage"), "nodes": deepcopy(progress["nodes"]),
                "counts": deepcopy(progress["counts"]), "result": result,
                "executions": executions, "step_outputs": deepcopy(step_outputs)}

    @staticmethod
    def _changes(previous, current):
        binding = current["binding"]
        common = {**binding, "event_schema_version": EVENT_SCHEMA_VERSION}
        if previous is None:
            return [("task.snapshot", {**common, "run_status": current["run_status"],
                                        "stage": current["stage"]})]
        changes = []
        if previous["binding"] != binding:
            changes.append(("task.revision_changed", {
                **common, "previous_revision": previous["binding"]["revision"],
                "previous_generation": previous["binding"]["generation"],
                "previous_task_version": previous["binding"]["task_version"],
            }))
        before_nodes = {row["node"]: row for row in previous["nodes"]}
        for node in current["nodes"]:
            before = before_nodes.get(node["node"])
            if before is None or any(before.get(key) != node.get(key)
                                     for key in ("node_status", "mode", "detail")):
                changes.append(("node.progress", {**common, "node": node["node"],
                    "node_status": node["node_status"], "mode": node["mode"]}))
            if before is None or before.get("gate_status") != node.get("gate_status"):
                changes.append(("gate.changed", {**common, "node": node["node"],
                    "gate_status": node["gate_status"]}))
        if previous.get("result") != current.get("result"):
            result = current.get("result") or {}
            changes.append(("output.available", {**common,
                "result_set_id": result.get("result_set_id"),
                "result_set_digest": result.get("result_set_digest"),
                "counts": {key: result.get(key) for key in (
                    "accepted_count", "failed_count", "pending_review_count",
                    "user_excluded_count", "gap")}}))
        if previous.get("step_outputs") != current.get("step_outputs"):
            changes.append(("output.available", {**common, "node": "cleaning",
                "step_outputs": current.get("step_outputs", [])}))
        if previous.get("executions") != current.get("executions"):
            changes.append(("execution.updated", {**common,
                "run_count": len(current["executions"]),
                "statuses": sorted({row.get("status") for row in current["executions"]
                                    if row.get("status")})}))
        if not changes and (previous.get("run_status"), previous.get("stage"),
                            previous.get("counts")) != (
                                current.get("run_status"), current.get("stage"),
                                current.get("counts")):
            changes.append(("node.progress", {**common, "node": None,
                "node_status": current.get("run_status")}))
        return changes

    def can_replay(self, actor, task, mutation_sequence):
        if self.path is None or mutation_sequence is None:
            return False
        try:
            with closing(self._connect()) as db:
                row = db.execute('''SELECT s.tenant_id,s.owner_user_id,s.snapshot_json,m.sequence
                    FROM task_event_state s JOIN task_event_observed_mutations m
                    ON m.task_id=s.task_id WHERE s.task_id=?''',
                    (task['task_id'],)).fetchone()
                if (row is None or row[:2] != (actor.tenant_id, actor.user_id)
                        or row[3] != mutation_sequence):
                    return False
                return json.loads(row[2])['binding'] == {
                    'task_id': task['task_id'], 'revision': task['confirmed_revision'],
                    'generation': task['run_generation'], 'task_version': task['task_version']}
        except (ValueError, TypeError, KeyError) as exc:
            raise ServiceError('STORAGE_CORRUPT', 'task event snapshot is invalid', 500) from exc
        except sqlite3.Error as exc:
            raise ServiceError('STORAGE_UNAVAILABLE',
                'task event snapshot unavailable', 503, retryable=True) from exc

    def observe(self, actor, task, progress, result_set=None, runs=(), step_outputs=(),
                *, mutation_sequence=None):
        if self.path is not None:
            with closing(self._connect()) as db:
                db.execute('BEGIN')
                ready = db.execute('SELECT dirty FROM task_read_summaries_meta WHERE id=1').fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='task_read_summaries_meta'").fetchone() else None
                row = db.execute('''SELECT s.tenant_id,s.owner_user_id,s.as_of_sequence,s.snapshot_json,m.sequence FROM task_event_state s JOIN task_event_write_heads w ON w.task_id=s.task_id JOIN task_event_observed_mutations m ON m.task_id=s.task_id WHERE s.task_id=?''', (task['task_id'],)).fetchone()
                if ready == (0,) and row is not None:
                    if row[:2] != (actor.tenant_id,actor.user_id):
                        raise ServiceError('NOT_FOUND', 'task event stream was not found', 404)
                    binding = {'task_id':task['task_id'],'revision':task['confirmed_revision'],
                        'generation':task['run_generation'],'task_version':task['task_version']}
                    if row[4] != mutation_sequence or json.loads(row[3])['binding'] != binding:
                        raise ServiceError('REVISION_CONFLICT', 'task changed while reading snapshot; retry', 409)
                    return row[2]
        snapshot = self._snapshot(task, progress, result_set, runs, step_outputs)
        task_id = task["task_id"]
        if self.path is None:
            with self._lock:
                state = self._states.get(task_id)
                if state is not None and state[0:2] != (actor.tenant_id, actor.user_id):
                    raise ServiceError("NOT_FOUND", "task event stream was not found", 404)
                previous = state[3] if state else None
                if state and state[2] == _digest(snapshot):
                    return state[4]
                sequence = state[4] if state else 0
                rows = self._events.setdefault(task_id, [])
                for kind, payload in self._changes(previous, snapshot):
                    sequence += 1
                    rows.append(self._event(task_id, sequence, kind, payload))
                self._events[task_id] = rows[-self.retention:]
                self._states[task_id] = (actor.tenant_id, actor.user_id,
                                         _digest(snapshot), snapshot, sequence)
                return sequence
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT tenant_id,owner_user_id,as_of_sequence,"
                                 "snapshot_digest,snapshot_json FROM task_event_state "
                                 "WHERE task_id=?", (task_id,)).fetchone()
                if row and row[:2] != (actor.tenant_id, actor.user_id):
                    raise ServiceError("NOT_FOUND", "task event stream was not found", 404)
                db.execute('INSERT OR REPLACE INTO task_event_observed_mutations '
                           'VALUES (?,?)', (task_id, mutation_sequence))
                current_digest = _digest(snapshot)
                if row and row[3] == current_digest:
                    return row[2]
                previous = json.loads(row[4]) if row else None
                # An old writer may dirty a previously migrated ledger. Its
                # compatibility snapshot replaces the compact write-side state.
                if previous is not None and 'nodes' not in previous:
                    previous = None
                db.execute('DELETE FROM task_event_write_heads WHERE task_id=?', (task_id,))
                sequence = row[2] if row else 0
                now = datetime.now(UTC).isoformat()
                for kind, payload in self._changes(previous, snapshot):
                    sequence += 1
                    event = self._event(task_id, sequence, kind, payload)
                    db.execute("INSERT INTO task_event_stream VALUES (?,?,?,?,?,?,?,?)",
                               (task_id, sequence, actor.tenant_id, actor.user_id,
                                event["event_id"], kind, _canonical(payload), now))
                db.execute("""INSERT INTO task_event_state VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(task_id) DO UPDATE SET as_of_sequence=excluded.as_of_sequence,
                    snapshot_digest=excluded.snapshot_digest,snapshot_json=excluded.snapshot_json,
                    updated_at=excluded.updated_at""",
                    (task_id, actor.tenant_id, actor.user_id, sequence, current_digest,
                     _canonical(snapshot), now))
                db.execute("DELETE FROM task_event_stream WHERE task_id=? AND sequence<=?",
                           (task_id, max(0, sequence - self.retention)))
                return sequence
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "task event ledger unavailable",
                               503, retryable=True) from exc

    @staticmethod
    def _event(task_id, sequence, kind, payload):
        return {"event_id": f"{task_id}:{sequence}", "sequence": sequence,
                "kind": kind, "payload": payload}

    def read(self, actor, task_id, *, after_sequence=0, limit=100):
        if type(after_sequence) is not int or after_sequence < 0:
            raise ServiceError("INVALID_EVENT_CURSOR", "after_sequence must be non-negative", 422)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ServiceError("INVALID_EVENT_CURSOR", "limit must be between 1 and 200", 422)
        if self.path is None:
            with self._lock:
                state = self._states.get(task_id)
                if state is None or state[:2] != (actor.tenant_id, actor.user_id):
                    raise ServiceError("NOT_FOUND", "task event stream was not found", 404)
                rows = self._events.get(task_id, [])
                return self._page(rows, after_sequence, limit, state[4])
        try:
            with closing(self._connect()) as db:
                db.execute("BEGIN")
                state = db.execute("SELECT tenant_id,owner_user_id,as_of_sequence "
                                   "FROM task_event_state WHERE task_id=?", (task_id,)).fetchone()
                if state is None or state[:2] != (actor.tenant_id, actor.user_id):
                    raise ServiceError("NOT_FOUND", "task event stream was not found", 404)
                rows = db.execute("SELECT sequence,event_id,kind,payload_json FROM task_event_stream "
                                  "WHERE task_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                                  (task_id, after_sequence, limit)).fetchall()
                events = [{"sequence": row[0], "event_id": row[1], "kind": row[2],
                           "payload": json.loads(row[3])} for row in rows]
                first = db.execute("SELECT MIN(sequence) FROM task_event_stream WHERE task_id=?",
                                   (task_id,)).fetchone()[0]
                return self._page(events, after_sequence, limit, state[2], first=first)
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "task event ledger unavailable",
                               503, retryable=True) from exc

    @staticmethod
    def _page(rows, after, limit, as_of, *, first=None):
        if after > as_of:
            raise ServiceError("INVALID_EVENT_CURSOR", "event cursor is ahead of task state", 422)
        first = first if first is not None else (rows[0]["sequence"] if rows else None)
        if after and first is not None and after < first - 1:
            raise ServiceError("EVENT_HISTORY_EXPIRED",
                               "event history expired; reload the task progress snapshot", 410)
        selected = [row for row in rows if row["sequence"] > after][:limit]
        next_sequence = selected[-1]["sequence"] if selected else after
        return {"events": deepcopy(selected), "next_sequence": next_sequence,
                "as_of_sequence": as_of, "has_more": next_sequence < as_of}
