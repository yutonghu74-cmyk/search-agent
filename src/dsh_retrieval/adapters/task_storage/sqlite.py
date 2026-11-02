"""Small local SQLite snapshot ledger with a confirmation dispatch outbox.

Every call loads a fresh state inside a DB transaction. Business transitions are
reused from InMemoryTaskRepository, but no in-memory state survives a call.
BEGIN IMMEDIATE serializes writers across processes; external work stays outside
the transaction. Confirmations and their search-dispatch events commit together.
Full snapshots trade throughput for a simple recovery baseline.
"""
from contextlib import closing
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
import json
import os
from pathlib import Path
import sqlite3

from ..memory import InMemoryTaskRepository
from ...cleaning_models import Candidate, CleaningOperation, CleaningProposal
from ...errors import ServiceError
from ...models import Task, TaskLifecycle, RunStatus
from ...proposals import Draft
from ...integration_models import IndexBinding, QueryVector, SearchRequest, SearchHit, SearchPage, MediaRecord
from ...candidate_contracts import CandidateManifest, CandidateManifestProposal, ManifestItem
from ...multi_rule_contracts import (CleaningApproval, CleaningApprovalProposal, CleaningBinding,
                                     CleaningCandidateRef, CleaningCandidateVerdict, CleaningJobResult, CleaningRecipe,
                                     CleaningRule, CleaningRuleVerdict, DetectionStep)
from ...retrieval_contracts import RetrievalPlan, RetrievalScope
from ...result_contracts import (ExactRepresentative, ExactResultSetPreview,
                                 FusedSourceEvidence)
from ...near_duplicates import (NearDuplicateDecision, NearDuplicatePair,
                                PerceptualFingerprint)
from ...conversation_models import ConversationConfirmation, ConversationMessage
from ...physical_query_preparation import PreparedQuery
from ...multi_branch_contracts import RetrievalPlanSet


TYPES = {t.__name__: t for t in (Candidate, CleaningOperation, CleaningProposal, Task, Draft,
                              IndexBinding, QueryVector, SearchRequest, SearchHit, SearchPage,
                              RetrievalPlan, RetrievalScope, MediaRecord, CandidateManifest,
                              CandidateManifestProposal, ManifestItem, CleaningApproval,
                              CleaningApprovalProposal, CleaningBinding, CleaningCandidateRef, CleaningRecipe,
                              CleaningRule, DetectionStep, CleaningRuleVerdict,
                              CleaningCandidateVerdict, CleaningJobResult,
                              ExactRepresentative, ExactResultSetPreview, FusedSourceEvidence,
                              NearDuplicateDecision, NearDuplicatePair, PerceptualFingerprint,
                              ConversationConfirmation, ConversationMessage, PreparedQuery, RetrievalPlanSet)}
ENUMS = {t.__name__: t for t in (TaskLifecycle, RunStatus)}
FORMAT = 1
MAX_BYTES = 16 * 1024 * 1024


def encode(value):
    if isinstance(value, Enum):
        return {"type": type(value).__name__, "value": value.value}
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if is_dataclass(value) and type(value).__name__ in TYPES:
        return {"type": type(value).__name__, "value": {f.name: encode(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, (dict, tuple, list)):
        data = [[encode(k), encode(v)] for k, v in value.items()] if isinstance(value, dict) else [encode(v) for v in value]
        return {"type": type(value).__name__, "value": data}
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise TypeError("unsupported ledger type")


def decode(value):
    if not isinstance(value, dict):
        if value is None or type(value) in (str, int, float, bool):
            return value
        raise ValueError("invalid encoded value")
    if set(value) != {"type", "value"}:
        raise ValueError("invalid tagged value")
    kind, data = value["type"], value["value"]
    if kind in ENUMS:
        return ENUMS[kind](data)
    if kind == "datetime":
        return datetime.fromisoformat(data)
    if kind in TYPES:
        cls = TYPES[kind]
        expected = {f.name for f in fields(cls)}
        missing = expected - set(data) if isinstance(data, dict) else set()
        if kind == "CleaningRuleVerdict" and missing == {"reason_summary", "reason_truncated"}:
            data = {**data, "reason_summary": None, "reason_truncated": False}
        if (kind in {"CleaningApprovalProposal", "CleaningBinding"}
                and missing == {"detector_release_digest"}):
            data = {**data, "detector_release_digest": None}
        if kind == "CleaningRecipe" and missing == {"instructions"}:
            data = {**data, "instructions": encode(())}
        if kind == "CleaningJobResult" and missing == {"fingerprints"}:
            data = {**data, "fingerprints": encode(())}
        if kind == "ExactResultSetPreview":
            legacy = {"parent_result_set_id", "parent_result_set_digest",
                      "source_operation_ids", "near_duplicate_pairs",
                      "near_duplicate_decisions"}
            if missing <= legacy:
                defaults = {"parent_result_set_id": None,
                            "parent_result_set_digest": None,
                            "source_operation_ids": encode(()),
                            "near_duplicate_pairs": encode(()),
                            "near_duplicate_decisions": encode(())}
                data = {**data, **{key: value for key, value in defaults.items()
                                   if key in missing}}
        if kind == "ExactRepresentative":
            legacy = {"candidate_ids", "sources", "fingerprint"}
            if missing <= legacy:
                defaults = {"candidate_ids": encode(()), "sources": encode(()),
                            "fingerprint": None}
                data = {**data, **{key: value for key, value in defaults.items()
                                   if key in missing}}
        if kind == "PerceptualFingerprint":
            legacy = {"width", "height", "sharpness"}
            if missing <= legacy:
                defaults = {"width": None, "height": None, "sharpness": None}
                data = {**data, **{key: value for key, value in defaults.items()
                                   if key in missing}}
        if kind in {"Task", "Draft"} and missing == {"conversation_id"}:
            data = {**data, "conversation_id": None}
        if set(data) != expected:
            raise ValueError("incompatible model fields")
        return cls(**{k: decode(v) for k, v in data.items()})
    if kind == "tuple":
        return tuple(decode(v) for v in data)
    if kind == "list":
        return [decode(v) for v in data]
    if kind == "dict":
        result = {decode(k): decode(v) for k, v in data}
        if len(result) != len(data):
            raise ValueError("duplicate ledger keys")
        return result
    raise ValueError("unknown encoded type")


def dump(memory):
    state = {k: v for k, v in vars(memory).items() if k != "_lock"}
    result = json.dumps(encode(state), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(result.encode()) > MAX_BYTES:
        raise ServiceError("STORAGE_LIMIT", "local snapshot exceeds 16 MB; use a scalable adapter", 507)
    return result


def restore(payload):
    memory = InMemoryTaskRepository()
    if not isinstance(payload, str) or len(payload.encode()) > MAX_BYTES:
        raise ValueError("invalid snapshot length")
    state = decode(json.loads(payload))
    expected = set(vars(memory)) - {"_lock"}
    for key in {"_feedback_drafts", "_feedback_requests", "_revisions",
                "_conversation_messages", "_conversation_confirmations"} - set(state if isinstance(state, dict) else ()):
        state[key] = {}
    if not isinstance(state, dict) or set(state) != expected:
        raise ValueError("incompatible ledger state")
    if any(type(state[k]) is not type(getattr(memory, k)) for k in expected):
        raise ValueError("invalid state container")
    for key, value in state.items():
        setattr(memory, key, value)
    return memory


class SQLiteTaskRepository:
    def __init__(self, path):
        if str(path) == ":memory:":
            raise ValueError("a local file path is required")
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            pass
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                allowed = {"local_ledger", "retrieval_ledger", "candidate_ledger",
                           "multi_rule_cleaning_ledger", "plan_set_ledger",
                           "search_dispatch_outbox", "candidate_browse_hits", "sqlite_sequence",
                           "image_deliveries", "image_delivery_events", "cleaning_dispatch_attempts", "cleaning_checkpoints",
                           "refill_reservations", "refill_dispatch_outbox",
                           "auto_refill_outbox", "auto_refill_intake_outbox",
                           "near_duplicate_review_keys",
                           "refill_iteration_metrics", "refill_execution_stops"}
                if tables and ("local_ledger" not in tables or tables - allowed):
                    raise ServiceError("STORAGE_FORMAT_UNSUPPORTED", "not a local demo ledger", 500)
                db.execute("CREATE TABLE IF NOT EXISTS local_ledger (id INTEGER PRIMARY KEY CHECK(id=1), format INTEGER NOT NULL, payload TEXT NOT NULL)")
                row = db.execute("SELECT format, payload FROM local_ledger WHERE id=1").fetchone()
                if row is None:
                    if tables:
                        raise ServiceError("STORAGE_CORRUPT", "ledger record missing", 500)
                    db.execute("INSERT INTO local_ledger VALUES (1, ?, ?)", (FORMAT, dump(InMemoryTaskRepository())))
                else:
                    self._load(row)
                db.execute("""CREATE TABLE IF NOT EXISTS search_dispatch_outbox (
                    event_id TEXT PRIMARY KEY,
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
                db.execute("CREATE INDEX IF NOT EXISTS search_dispatch_pending_idx "
                           "ON search_dispatch_outbox(status, created_at)")
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "local ledger could not be opened", 503, retryable=True) from exc

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _load(row):
        if row is None:
            raise ServiceError("STORAGE_CORRUPT", "ledger record missing", 500)
        if row[0] != FORMAT:
            raise ServiceError("STORAGE_FORMAT_UNSUPPORTED", "unsupported ledger format; migration required", 500)
        try:
            return restore(row[1])
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise ServiceError("STORAGE_CORRUPT", "local ledger is invalid; refusing to reset it", 500) from exc

    def _call(self, name, write, *args, **kwargs):
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                memory = self._load(db.execute("SELECT format, payload FROM local_ledger WHERE id=1").fetchone())
                result = getattr(memory, name)(*args, **kwargs)
                if write:
                    db.execute("UPDATE local_ledger SET payload=? WHERE id=1", (dump(memory),))
                return result
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "local ledger transaction failed", 503, retryable=True) from exc

    @staticmethod
    def _queue_search_dispatch(db, task, dispatch_key):
        event_id = (f"search:{task.task_id}:{task.confirmed_revision}:"
                    f"{task.run_generation}:{task.task_version}")
        db.execute("""INSERT OR IGNORE INTO search_dispatch_outbox
            (event_id, tenant_id, owner_user_id, task_id, revision, generation,
             task_version, dispatch_key, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (event_id, task.tenant_id, task.owner_user_id, task.task_id,
             task.confirmed_revision, task.run_generation, task.task_version,
             dispatch_key, datetime.now(UTC).isoformat()))

    def confirm_draft(self, *args, **kwargs):
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                memory = self._load(db.execute(
                    "SELECT format, payload FROM local_ledger WHERE id=1").fetchone())
                task, replayed = memory.confirm_draft(*args, **kwargs)
                db.execute("UPDATE local_ledger SET payload=? WHERE id=1", (dump(memory),))
                self._queue_search_dispatch(
                    db, task, f"terms:{task.task_id}:{task.confirmed_revision}")
                return task, replayed
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "local ledger transaction failed", 503,
                               retryable=True) from exc

    def confirm_feedback_draft(self, *args, **kwargs):
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                memory = self._load(db.execute(
                    "SELECT format, payload FROM local_ledger WHERE id=1").fetchone())
                actor, _, task_id = args[:3]
                before = memory.get(actor, task_id)
                before_spec = json.loads(before.confirmed_proposal_json)["query_spec"]
                result, replayed = memory.confirm_feedback_draft(*args, **kwargs)
                db.execute("UPDATE local_ledger SET payload=? WHERE id=1", (dump(memory),))
                if kwargs.get("action") == "confirm" and not replayed:
                    after = memory.get(actor, task_id)
                    after_spec = json.loads(after.confirmed_proposal_json)["query_spec"]
                    if (after_spec.get("grouped_expansion") != before_spec.get("grouped_expansion")
                            or after_spec.get("target_count") != before_spec.get("target_count")):
                        self._queue_search_dispatch(db, after, "terms:" + after.request_digest)
                return result, replayed
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "local ledger transaction failed", 503,
                               retryable=True) from exc

    def pending_search_dispatches(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        try:
            with closing(self._connect()) as db:
                rows = db.execute("""SELECT event_id, tenant_id, owner_user_id, task_id,
                    revision, generation, task_version, dispatch_key
                    FROM search_dispatch_outbox WHERE status='pending'
                    ORDER BY created_at, event_id LIMIT ?""", (limit,)).fetchall()
            keys = ("event_id", "tenant_id", "owner_user_id", "task_id", "revision",
                    "generation", "task_version", "dispatch_key")
            return tuple(dict(zip(keys, row)) for row in rows)
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "search dispatch outbox unavailable", 503,
                               retryable=True) from exc

    def mark_search_dispatched(self, event_id, result):
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id is required")
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                updated = db.execute("""UPDATE search_dispatch_outbox
                    SET status='delivered', result_json=?, delivered_at=?
                    WHERE event_id=? AND status='pending'""",
                    (payload, datetime.now(UTC).isoformat(), event_id)).rowcount
                if not updated:
                    row = db.execute("SELECT result_json FROM search_dispatch_outbox "
                                     "WHERE event_id=? AND status='delivered'", (event_id,)).fetchone()
                    if row is None:
                        raise ServiceError("NOT_FOUND", "search dispatch event was not found", 404)
                    return json.loads(row[0])
            return result
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "search dispatch outbox unavailable", 503,
                               retryable=True) from exc

    def search_dispatch_result(self, task_id):
        try:
            with closing(self._connect()) as db:
                row = db.execute("""SELECT status, result_json FROM search_dispatch_outbox
                    WHERE task_id=? ORDER BY revision DESC, generation DESC LIMIT 1""",
                    (task_id,)).fetchone()
            if row is None:
                return None
            return {"status": row[0], "result": json.loads(row[1]) if row[1] else None}
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "search dispatch outbox unavailable", 503,
                               retryable=True) from exc

    def get(self, *args, **kwargs): return self._call("get", False, *args, **kwargs)
    def create(self, *args, **kwargs): return self._call("create", True, *args, **kwargs)
    def get_draft(self, *args, **kwargs): return self._call("get_draft", False, *args, **kwargs)
    def save_draft(self, *args, **kwargs): return self._call("save_draft", True, *args, **kwargs)
    def save_feedback_draft(self, *args, **kwargs): return self._call("save_feedback_draft", True, *args, **kwargs)
    def get_feedback_draft(self, *args, **kwargs): return self._call("get_feedback_draft", False, *args, **kwargs)
    def get_pending_feedback(self, *args, **kwargs): return self._call("get_pending_feedback", False, *args, **kwargs)
    def list_revisions(self, *args, **kwargs): return self._call("list_revisions", False, *args, **kwargs)
    def list_tasks(self, *args, **kwargs): return self._call("list_tasks", False, *args, **kwargs)
    def get_conversation(self, *args, **kwargs): return self._call("get_conversation", False, *args, **kwargs)
    def list_conversations(self, *args, **kwargs): return self._call("list_conversations", False, *args, **kwargs)
    def delete_conversation(self, *args, **kwargs): return self._call("delete_conversation", True, *args, **kwargs)

    def hard_delete_conversation(self, actor, conversation_id):
        """Force-cascade delete a conversation and its task tree in one transaction.

        The memory cascade (tasks/drafts/feedback/cleaning inline state) commits
        with the task ledger. Every sibling SQLite ledger that shares this file
        is then filtered by the purged task ids, each rebuilding the event
        invariant its ``load_state``/``restore`` enforces. ``search_dispatch_outbox``
        rows and ``candidate_browse_hits`` projection rows for the purged tasks
        are deleted by their task/source key. Sibling ledgers that were never
        created against this file are skipped.
        """
        from .retrieval import load_state as load_retrieval, purge_for_tasks as purge_retrieval, dump_state as dump_retrieval
        from .candidates import load_state as load_candidates, purge_for_tasks as purge_candidates, dump_state as dump_candidates
        from .plan_sets import restore as load_plan_sets, purge_for_tasks as purge_plan_sets, serialize as dump_plan_sets
        from .multi_rule import load_state as load_multi_rule, purge_for_tasks as purge_multi_rule, dump_state as dump_multi_rule
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                memory = self._load(db.execute(
                    "SELECT format, payload FROM local_ledger WHERE id=1").fetchone())
                cascade = memory.hard_delete_conversation(actor, conversation_id)
                task_ids = cascade['tasks']
                db.execute("UPDATE local_ledger SET payload=? WHERE id=1", (dump(memory),))
                existing = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if task_ids:
                    placeholders = ",".join("?" for _ in task_ids)
                    if 'cleaning_checkpoints' in existing:
                        db.execute('DELETE FROM cleaning_checkpoints WHERE task_id IN (' + placeholders + ')', tuple(task_ids))
                    if 'cleaning_dispatch_attempts' in existing:
                        db.execute('DELETE FROM cleaning_dispatch_attempts WHERE task_id IN (' + placeholders + ')', tuple(task_ids))
                    if 'near_duplicate_review_keys' in existing:
                        db.execute('DELETE FROM near_duplicate_review_keys WHERE task_id IN (' + placeholders + ')', tuple(task_ids))
                    if 'image_deliveries' in existing:
                        from ...delivery import load_delivery_records
                        records = load_delivery_records(db)
                        for record in records.values():
                            if record['task_id'] in task_ids:
                                db.execute('DELETE FROM image_deliveries WHERE package_id=?', (record['package_id'],))
                    if 'image_delivery_events' in existing:
                        db.execute('DELETE FROM image_delivery_events WHERE task_id IN (' + placeholders + ')', tuple(task_ids))
                    if 'search_dispatch_outbox' in existing:
                        db.execute("DELETE FROM search_dispatch_outbox WHERE task_id IN ("
                                   + placeholders + ")", tuple(task_ids))
                    if 'refill_reservations' in existing:
                        db.execute("DELETE FROM refill_reservations WHERE task_id IN ("
                                   + placeholders + ")", tuple(task_ids))
                    if 'refill_dispatch_outbox' in existing:
                        db.execute("DELETE FROM refill_dispatch_outbox WHERE task_id IN ("
                                   + placeholders + ")", tuple(task_ids))
                    if 'auto_refill_outbox' in existing:
                        db.execute("DELETE FROM auto_refill_outbox WHERE task_id IN ("
                                   + placeholders + ")", tuple(task_ids))
                    if 'auto_refill_intake_outbox' in existing:
                        db.execute("DELETE FROM auto_refill_intake_outbox WHERE task_id IN ("
                                   + placeholders + ")", tuple(task_ids))
                    for table in ('refill_iteration_metrics', 'refill_execution_stops'):
                        if table in existing:
                            db.execute("DELETE FROM " + table + " WHERE task_id IN ("
                                       + placeholders + ")", tuple(task_ids))
                purged_digests = ()
                if 'retrieval_ledger' in existing:
                    self._purge_ledger(db, 'retrieval_ledger', task_ids,
                                       load_retrieval, purge_retrieval, dump_retrieval)
                if 'candidate_ledger' in existing:
                    self._purge_ledger(db, 'candidate_ledger', task_ids,
                                       load_candidates, purge_candidates, dump_candidates)
                if 'plan_set_ledger' in existing:
                    purged_digests = self._purge_plan_sets(db, task_ids, load_plan_sets, purge_plan_sets, dump_plan_sets)
                if 'multi_rule_cleaning_ledger' in existing:
                    self._purge_ledger(db, 'multi_rule_cleaning_ledger', task_ids,
                                       load_multi_rule, purge_multi_rule, dump_multi_rule)
                if 'candidate_browse_hits' in existing and purged_digests:
                    placeholders = ",".join("?" for _ in purged_digests)
                    db.execute("DELETE FROM candidate_browse_hits WHERE source IN ("
                               + placeholders + ")", tuple(purged_digests))
                return cascade
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "local ledger transaction failed", 503,
                               retryable=True) from exc

    def hard_delete_task(self, actor, task_id):
        """Force-cascade delete a legacy task tree by task id in one transaction.

        Same single-transaction cascade as ``hard_delete_conversation`` but keyed
        by a single task id (no conversation lookup), for pre-conversation legacy
        tasks. The memory cascade refuses a conversation-linked task
        (``TASK_HAS_CONVERSATION``) before any sibling ledger is touched, so no
        confirmation is left dangling. Sibling ledgers that were never created
        against this file are skipped, exactly as on the conversation path.
        """
        from .retrieval import load_state as load_retrieval, purge_for_tasks as purge_retrieval, dump_state as dump_retrieval
        from .candidates import load_state as load_candidates, purge_for_tasks as purge_candidates, dump_state as dump_candidates
        from .plan_sets import restore as load_plan_sets, purge_for_tasks as purge_plan_sets, serialize as dump_plan_sets
        from .multi_rule import load_state as load_multi_rule, purge_for_tasks as purge_multi_rule, dump_state as dump_multi_rule
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                memory = self._load(db.execute(
                    "SELECT format, payload FROM local_ledger WHERE id=1").fetchone())
                cascade = memory.hard_delete_task(actor, task_id)
                task_ids = cascade['tasks']
                db.execute("UPDATE local_ledger SET payload=? WHERE id=1", (dump(memory),))
                existing = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if task_ids:
                    placeholders = ",".join("?" for _ in task_ids)
                    if 'cleaning_checkpoints' in existing:
                        db.execute('DELETE FROM cleaning_checkpoints WHERE task_id IN (' + placeholders + ')', tuple(task_ids))
                    if 'cleaning_dispatch_attempts' in existing:
                        db.execute('DELETE FROM cleaning_dispatch_attempts WHERE task_id IN (' + placeholders + ')', tuple(task_ids))
                    if 'near_duplicate_review_keys' in existing:
                        db.execute('DELETE FROM near_duplicate_review_keys WHERE task_id IN (' + placeholders + ')', tuple(task_ids))
                    if 'image_deliveries' in existing:
                        from ...delivery import load_delivery_records
                        records = load_delivery_records(db)
                        for record in records.values():
                            if record['task_id'] in task_ids:
                                db.execute('DELETE FROM image_deliveries WHERE package_id=?', (record['package_id'],))
                    if 'image_delivery_events' in existing:
                        db.execute('DELETE FROM image_delivery_events WHERE task_id IN (' + placeholders + ')', tuple(task_ids))
                    if 'search_dispatch_outbox' in existing:
                        db.execute("DELETE FROM search_dispatch_outbox WHERE task_id IN ("
                                   + placeholders + ")", tuple(task_ids))
                    if 'refill_reservations' in existing:
                        db.execute("DELETE FROM refill_reservations WHERE task_id IN ("
                                   + placeholders + ")", tuple(task_ids))
                    if 'refill_dispatch_outbox' in existing:
                        db.execute("DELETE FROM refill_dispatch_outbox WHERE task_id IN ("
                                   + placeholders + ")", tuple(task_ids))
                    if 'auto_refill_outbox' in existing:
                        db.execute("DELETE FROM auto_refill_outbox WHERE task_id IN ("
                                   + placeholders + ")", tuple(task_ids))
                    if 'auto_refill_intake_outbox' in existing:
                        db.execute("DELETE FROM auto_refill_intake_outbox WHERE task_id IN ("
                                   + placeholders + ")", tuple(task_ids))
                    for table in ('refill_iteration_metrics', 'refill_execution_stops'):
                        if table in existing:
                            db.execute("DELETE FROM " + table + " WHERE task_id IN ("
                                       + placeholders + ")", tuple(task_ids))
                purged_digests = ()
                if 'retrieval_ledger' in existing:
                    self._purge_ledger(db, 'retrieval_ledger', task_ids,
                                       load_retrieval, purge_retrieval, dump_retrieval)
                if 'candidate_ledger' in existing:
                    self._purge_ledger(db, 'candidate_ledger', task_ids,
                                       load_candidates, purge_candidates, dump_candidates)
                if 'plan_set_ledger' in existing:
                    purged_digests = self._purge_plan_sets(db, task_ids, load_plan_sets, purge_plan_sets, dump_plan_sets)
                if 'multi_rule_cleaning_ledger' in existing:
                    self._purge_ledger(db, 'multi_rule_cleaning_ledger', task_ids,
                                       load_multi_rule, purge_multi_rule, dump_multi_rule)
                if 'candidate_browse_hits' in existing and purged_digests:
                    placeholders = ",".join("?" for _ in purged_digests)
                    db.execute("DELETE FROM candidate_browse_hits WHERE source IN ("
                               + placeholders + ")", tuple(purged_digests))
                return cascade
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "local ledger transaction failed", 503,
                               retryable=True) from exc

    @staticmethod
    def _purge_ledger(db, table, task_ids, loader, purger, dumper):
        row = db.execute(f"SELECT format, payload FROM {table} WHERE id=1").fetchone()
        state = purger(loader(row), task_ids)
        db.execute(f"UPDATE {table} SET payload=? WHERE id=1", (dumper(state),))

    @staticmethod
    def _purge_plan_sets(db, task_ids, load, purge, dump):
        row = db.execute("SELECT format, payload FROM plan_set_ledger WHERE id=1").fetchone()
        state, purged_digests = purge(load(row), task_ids)
        db.execute("UPDATE plan_set_ledger SET format=3, payload=? WHERE id=1", (dump(state),))
        return purged_digests

    def put_cleaning_proposal(self, *args, **kwargs): return self._call("put_cleaning_proposal", True, *args, **kwargs)
    def get_cleaning_proposal(self, *args, **kwargs): return self._call("get_cleaning_proposal", False, *args, **kwargs)
    def get_cleaning_operation(self, *args, **kwargs): return self._call("get_cleaning_operation", False, *args, **kwargs)
    def reserve_cleaning(self, *args, **kwargs): return self._call("reserve_cleaning", True, *args, **kwargs)
    def attach_cleaning_backend(self, *args, **kwargs): return self._call("attach_cleaning_backend", True, *args, **kwargs)
    def publish_cleaning(self, *args, **kwargs): return self._call("publish_cleaning", True, *args, **kwargs)
    def cancel_cleaning(self, *args, **kwargs): return self._call("cancel_cleaning", True, *args, **kwargs)
    def cleaning_results(self, *args, **kwargs): return self._call("cleaning_results", False, *args, **kwargs)
