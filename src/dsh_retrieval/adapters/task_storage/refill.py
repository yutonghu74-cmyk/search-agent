"""Durable refill iteration reservations; this module never dispatches search."""

from contextlib import closing
from datetime import UTC, datetime
import json
import re
import sqlite3
import uuid

from .sqlite import SQLiteTaskRepository
from ...errors import ServiceError
from ...integration_models import AccessContext
from ...models import Actor, RunStatus
from ...query_spec import digest
from ...refill_planning import RefillPosition, RefillReservation, parse_refill_position


_ACTIVE_RUNS = {RunStatus.PLANNING, RunStatus.RUNNING, RunStatus.AWAITING_INPUT}
_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")


class SQLiteRefillReservationRepository:
    """Atomically assigns one iteration to each current-scope query position.

    Reservations are a commit-before-dispatch barrier. Replaying the same
    position returns its original iteration; a changed task generation cannot
    append to or publish through the old execution scope.
    """

    def __init__(self, path, *, projects, authorization):
        self.tasks = SQLiteTaskRepository(path)
        self.projects = projects
        self.authorization = authorization
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""CREATE TABLE IF NOT EXISTS refill_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    task_version INTEGER NOT NULL,
                    iteration INTEGER NOT NULL,
                    library_id TEXT NOT NULL,
                    position_fingerprint TEXT NOT NULL,
                    policy_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    position_json TEXT,
                    UNIQUE(task_id, revision, generation, iteration),
                    UNIQUE(task_id, revision, generation, position_fingerprint)
                )""")
                db.execute("CREATE INDEX IF NOT EXISTS refill_current_scope_idx "
                           "ON refill_reservations(task_id, revision, generation, iteration)")
                columns = {row[1] for row in db.execute("PRAGMA table_info(refill_reservations)")}
                if "position_json" not in columns:
                    db.execute("ALTER TABLE refill_reservations ADD COLUMN position_json TEXT")
                db.execute("""CREATE TABLE IF NOT EXISTS refill_dispatch_outbox (
                    event_id TEXT PRIMARY KEY,
                    reservation_id TEXT NOT NULL UNIQUE,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    task_version INTEGER NOT NULL,
                    dispatch_key TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','delivered')),
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT
                )""")
                db.execute("CREATE INDEX IF NOT EXISTS refill_dispatch_pending_idx "
                           "ON refill_dispatch_outbox(status, created_at, event_id)")
                db.execute("""CREATE TABLE IF NOT EXISTS auto_refill_intake_outbox (
                    event_id TEXT PRIMARY KEY,
                    reservation_id TEXT NOT NULL UNIQUE,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    task_version INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','delivered')),
                    stage TEXT NOT NULL CHECK(stage IN ('pending','manifest_confirmed','cleaning_prepared')),
                    manifest_ref TEXT,
                    cleaning_proposal_digest TEXT,
                    cleaning_expected_task_version INTEGER,
                    candidate_count INTEGER,
                    vlm_calls INTEGER,
                    cleaning_operation_id TEXT,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT
                )""")
                db.execute("CREATE INDEX IF NOT EXISTS auto_refill_intake_pending_idx "
                           "ON auto_refill_intake_outbox(status, created_at, event_id)")
                intake_columns = {row[1] for row in db.execute(
                    "PRAGMA table_info(auto_refill_intake_outbox)")}
                if "candidate_count" not in intake_columns:
                    db.execute("ALTER TABLE auto_refill_intake_outbox ADD COLUMN candidate_count INTEGER")
                if "vlm_calls" not in intake_columns:
                    db.execute("ALTER TABLE auto_refill_intake_outbox ADD COLUMN vlm_calls INTEGER")
                if "cleaning_operation_id" not in intake_columns:
                    db.execute("ALTER TABLE auto_refill_intake_outbox ADD COLUMN cleaning_operation_id TEXT")
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS auto_refill_intake_operation_idx "
                           "ON auto_refill_intake_outbox(cleaning_operation_id) "
                           "WHERE cleaning_operation_id IS NOT NULL")
                db.execute("""CREATE TABLE IF NOT EXISTS refill_iteration_metrics (
                    reservation_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    iteration INTEGER NOT NULL,
                    baseline_accepted_count INTEGER NOT NULL,
                    candidate_count INTEGER NOT NULL DEFAULT 0,
                    vlm_calls INTEGER NOT NULL DEFAULT 0,
                    outcome TEXT NOT NULL CHECK(outcome IN ('pending','empty','result','stopped')),
                    accepted_after INTEGER,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(task_id,revision,generation,iteration)
                )""")
                db.execute("""CREATE TABLE IF NOT EXISTS refill_execution_stops (
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    stop_reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id,revision,generation)
                )""")
                # Upgrade reservations created by the previous slice without
                # losing their commit-before-dispatch guarantee.
                db.execute("""INSERT OR IGNORE INTO refill_dispatch_outbox
                    (event_id,reservation_id,tenant_id,owner_user_id,task_id,revision,generation,
                     task_version,dispatch_key,status,created_at)
                    SELECT 'refill-dispatch:' || reservation_id,reservation_id,tenant_id,
                     owner_user_id,task_id,revision,generation,task_version,reservation_id,
                     'pending',created_at FROM refill_reservations""")
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill ledger unavailable", 503,
                               retryable=True) from exc

    def _authorize_task(self, memory, actor, task_id, action):
        task = memory.get(actor, task_id)
        if self.projects is None or self.authorization is None:
            raise ServiceError("ACCESS_DENIED", "refill authority not configured", 403)
        self.projects.require_access(actor, task.project_id, task.dimension_id, action)
        return task

    @staticmethod
    def _policy(task):
        try:
            proposal = json.loads(task.confirmed_proposal_json)
            policy = proposal["query_spec"]["refill_policy"]
            node_mode = proposal["node_modes"]["refill"]
        except (TypeError, KeyError, json.JSONDecodeError):
            raise ServiceError("STORAGE_CORRUPT", "confirmed refill policy is unavailable", 500) from None
        if (not isinstance(policy, dict)
                or set(policy) != {"mode", "target_count", "approved_library_ids", "progression"}
                or node_mode not in {"manual", "auto"}):
            raise ServiceError("STORAGE_CORRUPT", "confirmed refill policy is invalid", 500)
        return policy, node_mode

    @staticmethod
    def _record(row):
        try:
            created_at = datetime.fromisoformat(row[9])
        except (TypeError, ValueError):
            raise ServiceError("STORAGE_CORRUPT", "refill reservation timestamp is invalid", 500) from None
        position_json = row[10] if len(row) > 10 else None
        if position_json is not None and not isinstance(position_json, str):
            raise ServiceError("STORAGE_CORRUPT", "refill position binding is invalid", 500)
        return RefillReservation(*row[:9], created_at, position_json)

    def reserve(self, actor, task_id, position: RefillPosition, *, expected_revision: int,
                expected_generation: int, expected_task_version: int):
        try:
            parse_refill_position(position)
        except ValueError:
            raise ServiceError("INVALID_SPEC", "invalid refill position binding", 422) from None
        if (not _FINGERPRINT.fullmatch(position.fingerprint)
                or any(type(value) is not int or value < 1 for value in
                       (expected_revision, expected_generation, expected_task_version))):
            raise ServiceError("INVALID_SPEC", "invalid refill reservation binding", 422)
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                memory = self.tasks._load(db.execute(
                    "SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                task = self._authorize_task(memory, actor, task_id, "approve")
                if (task.confirmed_revision, task.run_generation, task.task_version) != (
                        expected_revision, expected_generation, expected_task_version):
                    raise ServiceError("REVISION_CONFLICT", "refill execution scope is stale", 409)
                if task.run_status not in _ACTIVE_RUNS:
                    raise ServiceError("RUN_NOT_WRITABLE", "current run cannot reserve refill work", 409)
                policy, node_mode = self._policy(task)
                if policy["mode"] != "maintain_target":
                    raise ServiceError("REFILL_NOT_APPROVED", "confirmed task has fixed input scope", 409)
                if position.library_id not in policy["approved_library_ids"]:
                    raise ServiceError("REFILL_SCOPE_EXCEEDED", "library is outside confirmed refill scope", 403)
                context = AccessContext(actor, task_id, "refill", "current")
                self.authorization.require_access(context, "library:" + position.library_id, "search")
                row = db.execute("""SELECT reservation_id,task_id,revision,generation,task_version,
                    iteration,library_id,position_fingerprint,policy_digest,created_at,position_json
                    FROM refill_reservations WHERE task_id=? AND revision=? AND generation=?
                    AND position_fingerprint=?""",
                    (task_id, expected_revision, expected_generation, position.fingerprint)).fetchone()
                if row is not None:
                    return self._record(row), True
                iteration = db.execute("""SELECT COALESCE(MAX(iteration),0)+1
                    FROM refill_reservations WHERE task_id=? AND revision=? AND generation=?""",
                    (task_id, expected_revision, expected_generation)).fetchone()[0]
                policy_digest = digest({"policy": policy, "node_mode": node_mode})
                reservation_id = "refill_" + uuid.uuid4().hex
                created_at = datetime.now(UTC).isoformat()
                db.execute("""INSERT INTO refill_reservations
                    (reservation_id,tenant_id,owner_user_id,task_id,revision,generation,
                     task_version,iteration,library_id,position_fingerprint,policy_digest,created_at,
                     position_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (reservation_id, actor.tenant_id, actor.user_id, task_id, expected_revision,
                     expected_generation, expected_task_version, iteration, position.library_id,
                     position.fingerprint, policy_digest, created_at, position.binding_json))
                db.execute("""INSERT INTO refill_dispatch_outbox
                    (event_id,reservation_id,tenant_id,owner_user_id,task_id,revision,generation,
                     task_version,dispatch_key,status,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,'pending',?)""",
                    ("refill-dispatch:" + reservation_id, reservation_id, actor.tenant_id,
                     actor.user_id, task_id, expected_revision, expected_generation,
                     expected_task_version, reservation_id, created_at))
                # Recheck external authority while the local write is still rollback-able.
                self.authorization.require_access(context, "library:" + position.library_id, "search")
                row = (reservation_id, task_id, expected_revision, expected_generation,
                       expected_task_version, iteration, position.library_id,
                       position.fingerprint, policy_digest, created_at, position.binding_json)
                return self._record(row), False
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill reservation failed", 503,
                               retryable=True) from exc

    def list_current(self, actor, task_id):
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN")
                memory = self.tasks._load(db.execute(
                    "SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                task = self._authorize_task(memory, actor, task_id, "read")
                rows = db.execute("""SELECT reservation_id,task_id,revision,generation,task_version,
                    iteration,library_id,position_fingerprint,policy_digest,created_at,position_json
                    FROM refill_reservations WHERE task_id=? AND revision=? AND generation=?
                    ORDER BY iteration""",
                    (task_id, task.confirmed_revision, task.run_generation)).fetchall()
                return tuple(self._record(row) for row in rows)
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill reservations unavailable", 503,
                               retryable=True) from exc

    def record_iteration_baseline(self, actor, reservation_id, accepted_count):
        if type(accepted_count) is not int or accepted_count < 0:
            raise ValueError("accepted count must be non-negative")
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                memory = self.tasks._load(db.execute(
                    "SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                row = db.execute("""SELECT task_id,revision,generation,iteration
                    FROM refill_reservations WHERE reservation_id=?""",
                    (reservation_id,)).fetchone()
                if row is None:
                    raise ServiceError("NOT_FOUND", "refill reservation was not found", 404)
                task = self._authorize_task(memory, actor, row[0], "approve")
                if (task.confirmed_revision, task.run_generation) != row[1:3]:
                    raise ServiceError("REVISION_CONFLICT", "refill execution scope is stale", 409)
                created_at = datetime.now(UTC).isoformat()
                db.execute("""INSERT OR IGNORE INTO refill_iteration_metrics
                    (reservation_id,task_id,revision,generation,iteration,
                     baseline_accepted_count,outcome,created_at)
                    VALUES (?,?,?,?,?,?,'pending',?)""",
                    (reservation_id, *row, accepted_count, created_at))
                saved = db.execute("""SELECT baseline_accepted_count FROM
                    refill_iteration_metrics WHERE reservation_id=?""",
                    (reservation_id,)).fetchone()[0]
                if saved != accepted_count:
                    raise ServiceError("RESULT_CONFLICT", "refill baseline changed", 409)
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill baseline unavailable", 503,
                               retryable=True) from exc

    def reserve_vlm_calls(self, actor, reservation_id, *, candidate_count,
                          vlm_calls, max_vlm_calls):
        if (type(candidate_count) is not int or candidate_count < 1
                or type(vlm_calls) is not int or vlm_calls < 0
                or max_vlm_calls is not None
                and (type(max_vlm_calls) is not int or max_vlm_calls < 1)):
            raise ValueError("invalid VLM budget reservation")
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                memory = self.tasks._load(db.execute(
                    "SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                row = db.execute("""SELECT task_id,revision,generation,candidate_count,
                    vlm_calls,outcome FROM refill_iteration_metrics WHERE reservation_id=?""",
                    (reservation_id,)).fetchone()
                if row is None:
                    raise ServiceError("STORAGE_CORRUPT", "refill baseline is missing", 500)
                task = self._authorize_task(memory, actor, row[0], "approve")
                if (task.confirmed_revision, task.run_generation) != row[1:3]:
                    raise ServiceError("REVISION_CONFLICT", "refill execution scope is stale", 409)
                if row[5] == "stopped":
                    if row[3] != candidate_count:
                        raise ServiceError("RESULT_CONFLICT", "refill VLM reservation changed", 409)
                    return False
                if row[3] or row[4]:
                    if (row[3], row[4]) != (candidate_count, vlm_calls):
                        raise ServiceError("RESULT_CONFLICT", "refill VLM reservation changed", 409)
                    return True
                used = db.execute("""SELECT COALESCE(SUM(vlm_calls),0)
                    FROM refill_iteration_metrics WHERE task_id=? AND revision=? AND generation=?""",
                    row[:3]).fetchone()[0]
                if max_vlm_calls is not None and used + vlm_calls > max_vlm_calls:
                    now = datetime.now(UTC).isoformat()
                    db.execute("""UPDATE refill_iteration_metrics SET candidate_count=?,
                        vlm_calls=?,outcome='stopped',completed_at=? WHERE reservation_id=?""",
                        (candidate_count, 0, now, reservation_id))
                    db.execute("""INSERT OR REPLACE INTO refill_execution_stops
                        (task_id,revision,generation,stop_reason,created_at)
                        VALUES (?,?,?,'VLM_CALL_LIMIT_REACHED',?)""", (*row[:3], now))
                    return False
                db.execute("""UPDATE refill_iteration_metrics SET candidate_count=?,vlm_calls=?
                    WHERE reservation_id=?""", (candidate_count, vlm_calls, reservation_id))
                return True
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill VLM budget unavailable", 503,
                               retryable=True) from exc

    def complete_empty_iteration(self, actor, reservation_id, accepted_count):
        return self._complete_iteration(actor, reservation_id, accepted_count, "empty")

    def complete_result_iteration(self, actor, operation_id, accepted_count):
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("operation id is required")
        try:
            with closing(self.tasks._connect()) as db:
                row = db.execute("""SELECT reservation_id FROM auto_refill_intake_outbox
                    WHERE cleaning_operation_id=?""", (operation_id,)).fetchone()
            reservation_id = row[0] if row else None
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill operation binding unavailable", 503,
                               retryable=True) from exc
        if reservation_id is None:
            return False
        self._complete_iteration(actor, reservation_id, accepted_count, "result")
        return True

    def _complete_iteration(self, actor, reservation_id, accepted_count, outcome):
        if type(accepted_count) is not int or accepted_count < 0 or outcome not in {"empty", "result"}:
            raise ValueError("invalid refill iteration outcome")
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                memory = self.tasks._load(db.execute(
                    "SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                row = db.execute("""SELECT task_id,revision,generation,outcome,accepted_after
                    FROM refill_iteration_metrics WHERE reservation_id=?""",
                    (reservation_id,)).fetchone()
                if row is None:
                    raise ServiceError("STORAGE_CORRUPT", "refill iteration is missing", 500)
                task = self._authorize_task(memory, actor, row[0], "approve")
                if (task.confirmed_revision, task.run_generation) != row[1:3]:
                    raise ServiceError("REVISION_CONFLICT", "refill execution scope is stale", 409)
                if row[3] in {"empty", "result"}:
                    if row[4] != accepted_count:
                        raise ServiceError("RESULT_CONFLICT", "refill outcome changed", 409)
                    return
                if row[3] == "stopped":
                    return
                db.execute("""UPDATE refill_iteration_metrics SET outcome=?,accepted_after=?,
                    completed_at=? WHERE reservation_id=?""",
                    (outcome, accepted_count, datetime.now(UTC).isoformat(), reservation_id))
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill outcome unavailable", 503,
                               retryable=True) from exc

    def execution_evidence(self, actor, task_id):
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN")
                memory = self.tasks._load(db.execute(
                    "SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                task = self._authorize_task(memory, actor, task_id, "read")
                scope = (task_id, task.confirmed_revision, task.run_generation)
                rows = db.execute("""SELECT iteration,baseline_accepted_count,accepted_after,outcome,
                    candidate_count,vlm_calls FROM refill_iteration_metrics WHERE task_id=? AND revision=?
                    AND generation=? ORDER BY iteration""", scope).fetchall()
                stagnant = 0
                for _, baseline, after, outcome, _, _ in reversed(rows):
                    if outcome not in {"empty", "result"}:
                        continue
                    if after is not None and after > baseline:
                        break
                    stagnant += 1
                stop = db.execute("""SELECT stop_reason FROM refill_execution_stops
                    WHERE task_id=? AND revision=? AND generation=?""", scope).fetchone()
                issued = db.execute("""SELECT COUNT(*) FROM refill_reservations
                    WHERE task_id=? AND revision=? AND generation=?""", scope).fetchone()[0]
                rounds = [{"iteration": row[0], "baseline_accepted": row[1],
                           "candidate_count": row[4], "vlm_calls": row[5],
                           "outcome": row[3], "accepted_after": row[2],
                           "new_unique_accepted": (None if row[2] is None else
                                                    max(0, row[2] - row[1]))}
                          for row in rows]
                return {"issued_rounds": issued,
                        "completed_rounds": sum(row[3] in {"empty", "result"} for row in rows),
                        "vlm_calls": sum(row[5] for row in rows),
                        "stagnant_rounds": stagnant,
                        "stop_reason": stop[0] if stop else None,
                        "rounds": rounds}
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill execution evidence unavailable", 503,
                               retryable=True) from exc

    def record_execution_stop(self, actor, task_id, reason):
        if reason not in {"EXECUTION_LIMIT_REACHED", "VLM_CALL_LIMIT_REACHED",
                          "NO_PROGRESS", "SEARCH_STRATEGIES_EXHAUSTED"}:
            raise ValueError("invalid refill stop reason")
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                memory = self.tasks._load(db.execute(
                    "SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                task = self._authorize_task(memory, actor, task_id, "approve")
                db.execute("""INSERT OR IGNORE INTO refill_execution_stops
                    (task_id,revision,generation,stop_reason,created_at)
                    VALUES (?,?,?,?,?)""", (task_id, task.confirmed_revision,
                    task.run_generation, reason, datetime.now(UTC).isoformat()))
                saved = db.execute("""SELECT stop_reason FROM refill_execution_stops
                    WHERE task_id=? AND revision=? AND generation=?""",
                    (task_id, task.confirmed_revision, task.run_generation)).fetchone()[0]
                if saved != reason:
                    raise ServiceError("RESULT_CONFLICT", "refill stop reason changed", 409)
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill stop unavailable", 503,
                               retryable=True) from exc

    def pending_stop_finalizations(self, limit=100):
        """Return durable stops whose task still needs the owner-decision gate."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        try:
            with closing(self.tasks._connect()) as db:
                memory = self.tasks._load(db.execute(
                    "SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                rows = db.execute("""SELECT task_id,revision,generation,stop_reason
                    FROM refill_execution_stops ORDER BY created_at,task_id LIMIT ?""",
                    (limit,)).fetchall()
            pending = []
            for task_id, revision, generation, reason in rows:
                task = memory._tasks.get(task_id)
                if (task is not None and task.confirmed_revision == revision
                        and task.run_generation == generation
                        and task.run_status == RunStatus.RUNNING):
                    pending.append({"tenant_id": task.tenant_id,
                                    "owner_user_id": task.owner_user_id,
                                    "task_id": task_id, "stop_reason": reason})
            return tuple(pending)
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill stops unavailable", 503,
                               retryable=True) from exc

    def pending_dispatches(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        try:
            with closing(self.tasks._connect()) as db:
                rows = db.execute("""SELECT event_id,reservation_id,tenant_id,owner_user_id,
                    task_id,revision,generation,task_version,dispatch_key
                    FROM refill_dispatch_outbox WHERE status='pending'
                    ORDER BY created_at,event_id LIMIT ?""", (limit,)).fetchall()
            keys = ("event_id", "reservation_id", "tenant_id", "owner_user_id", "task_id",
                    "revision", "generation", "task_version", "dispatch_key")
            return tuple(dict(zip(keys, row)) for row in rows)
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill dispatch outbox unavailable", 503,
                               retryable=True) from exc

    def validate_dispatch(self, event):
        if not isinstance(event, dict) or not {"event_id", "reservation_id", "tenant_id",
                "owner_user_id", "task_id", "revision", "generation", "task_version",
                "dispatch_key"} <= set(event):
            raise ValueError("invalid refill dispatch event")
        actor = Actor(event["tenant_id"], event["owner_user_id"])
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN")
                memory = self.tasks._load(db.execute(
                    "SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                task = self._authorize_task(memory, actor, event["task_id"], "approve")
                if (task.confirmed_revision, task.run_generation, task.task_version) != (
                        event["revision"], event["generation"], event["task_version"]):
                    return None
                if task.run_status not in _ACTIVE_RUNS:
                    return None
                row = db.execute("""SELECT reservation_id,task_id,revision,generation,task_version,
                    iteration,library_id,position_fingerprint,policy_digest,created_at,position_json
                    FROM refill_reservations WHERE reservation_id=?""",
                    (event["reservation_id"],)).fetchone()
                if row is None:
                    raise ServiceError("STORAGE_CORRUPT", "refill dispatch lost reservation", 500)
                reservation = self._record(row)
                if reservation.position_binding_json is None:
                    return None
                try:
                    parse_refill_position(RefillPosition(
                        reservation.library_id, reservation.position_fingerprint,
                        reservation.position_binding_json))
                except ValueError:
                    raise ServiceError("STORAGE_CORRUPT",
                                       "refill dispatch binding does not match fingerprint", 500) from None
                policy, node_mode = self._policy(task)
                if (policy["mode"] != "maintain_target"
                        or reservation.library_id not in policy["approved_library_ids"]
                        or reservation.policy_digest != digest({"policy": policy,
                                                                "node_mode": node_mode})):
                    return None
                context = AccessContext(actor, task.task_id, "refill", "current")
                self.authorization.require_access(
                    context, "library:" + reservation.library_id, "search")
                return actor, reservation
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill dispatch validation failed", 503,
                               retryable=True) from exc

    def mark_dispatched(self, event_id, result):
        if not isinstance(event_id, str) or not event_id or not isinstance(result, dict):
            raise ValueError("event id and result are required")
        try:
            payload = json.dumps(result, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("dispatch result must be canonical JSON") from None
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                event = db.execute("""SELECT reservation_id,tenant_id,owner_user_id,task_id,
                    revision,generation,task_version,created_at FROM refill_dispatch_outbox
                    WHERE event_id=?""", (event_id,)).fetchone()
                if event is None:
                    raise ServiceError("NOT_FOUND", "refill dispatch event was not found", 404)
                updated = db.execute("""UPDATE refill_dispatch_outbox
                    SET status='delivered',result_json=?,delivered_at=?
                    WHERE event_id=? AND status='pending'""",
                    (payload, datetime.now(UTC).isoformat(), event_id)).rowcount
                if not updated:
                    row = db.execute("SELECT result_json FROM refill_dispatch_outbox "
                                     "WHERE event_id=? AND status='delivered'", (event_id,)).fetchone()
                    if row is None:
                        raise ServiceError("NOT_FOUND", "refill dispatch event was not found", 404)
                    return json.loads(row[0])
                if result.get("status") in {"committed", "replayed"}:
                    db.execute("""INSERT OR IGNORE INTO auto_refill_intake_outbox
                        (event_id,reservation_id,tenant_id,owner_user_id,task_id,revision,
                         generation,task_version,status,stage,created_at)
                        VALUES (?,?,?,?,?,?,?,?,'pending','pending',?)""",
                        ("auto-refill-intake:" + event[0], *event))
            return result
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill dispatch update failed", 503,
                               retryable=True) from exc

    def pending_auto_intake_events(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        try:
            with closing(self.tasks._connect()) as db:
                rows = db.execute("""SELECT event_id,reservation_id,tenant_id,owner_user_id,
                    task_id,revision,generation,task_version,stage,manifest_ref,
                    cleaning_proposal_digest,cleaning_expected_task_version,
                    candidate_count,vlm_calls
                    FROM auto_refill_intake_outbox WHERE status='pending'
                    ORDER BY created_at,event_id LIMIT ?""", (limit,)).fetchall()
            keys = ("event_id", "reservation_id", "tenant_id", "owner_user_id", "task_id",
                    "revision", "generation", "task_version", "stage", "manifest_ref",
                    "cleaning_proposal_digest", "cleaning_expected_task_version",
                    "candidate_count", "vlm_calls")
            return tuple(dict(zip(keys, row)) for row in rows)
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "auto refill intake unavailable", 503,
                               retryable=True) from exc

    def advance_auto_intake(self, event_id, *, stage, manifest_ref=None,
                            cleaning_proposal_digest=None,
                            cleaning_expected_task_version=None,
                            candidate_count=None, vlm_calls=None):
        if stage not in {"manifest_confirmed", "cleaning_prepared"}:
            raise ValueError("invalid auto intake stage")
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                updated = db.execute("""UPDATE auto_refill_intake_outbox SET stage=?,
                    manifest_ref=COALESCE(?,manifest_ref),
                    cleaning_proposal_digest=COALESCE(?,cleaning_proposal_digest),
                    cleaning_expected_task_version=COALESCE(?,cleaning_expected_task_version),
                    candidate_count=COALESCE(?,candidate_count),
                    vlm_calls=COALESCE(?,vlm_calls)
                    WHERE event_id=? AND status='pending'""",
                    (stage, manifest_ref, cleaning_proposal_digest,
                     cleaning_expected_task_version, candidate_count, vlm_calls,
                     event_id)).rowcount
                if not updated:
                    raise ServiceError("NOT_FOUND", "auto refill intake event was not found", 404)
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "auto refill intake update failed", 503,
                               retryable=True) from exc

    def mark_auto_intake_delivered(self, event_id, result):
        if not isinstance(result, dict):
            raise ValueError("auto intake result is required")
        try:
            payload = json.dumps(result, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("auto intake result must be canonical JSON") from None
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                updated = db.execute("""UPDATE auto_refill_intake_outbox
                    SET status='delivered',result_json=?,delivered_at=?
                    WHERE event_id=? AND status='pending'""",
                    (payload, datetime.now(UTC).isoformat(), event_id)).rowcount
                if not updated:
                    row = db.execute("SELECT result_json FROM auto_refill_intake_outbox "
                                     "WHERE event_id=? AND status='delivered'", (event_id,)).fetchone()
                    if row is None:
                        raise ServiceError("NOT_FOUND", "auto refill intake event was not found", 404)
                    return json.loads(row[0])
            return result
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "auto refill intake delivery failed", 503,
                               retryable=True) from exc

    def bind_auto_intake_operation(self, event_id, operation_id):
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("cleaning operation id is required")
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("""SELECT cleaning_operation_id FROM
                    auto_refill_intake_outbox WHERE event_id=? AND status='pending'""",
                    (event_id,)).fetchone()
                if row is None:
                    raise ServiceError("NOT_FOUND", "auto refill intake event was not found", 404)
                if row[0] is not None and row[0] != operation_id:
                    raise ServiceError("RESULT_CONFLICT", "cleaning operation binding changed", 409)
                db.execute("""UPDATE auto_refill_intake_outbox SET cleaning_operation_id=?
                    WHERE event_id=?""", (operation_id, event_id))
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "cleaning operation binding unavailable", 503,
                               retryable=True) from exc

    def dispatch_result(self, reservation_id):
        try:
            with closing(self.tasks._connect()) as db:
                row = db.execute("""SELECT status,result_json FROM refill_dispatch_outbox
                    WHERE reservation_id=?""", (reservation_id,)).fetchone()
            if row is None:
                return None
            return {"status": row[0], "result": json.loads(row[1]) if row[1] else None}
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "refill dispatch result unavailable", 503,
                               retryable=True) from exc
