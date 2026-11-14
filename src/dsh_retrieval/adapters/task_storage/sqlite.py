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
                                 FusedSourceEvidence, GroupQuotaProgress,
                                 ManualReviewDecision, ResultExclusionDecision)
from ...near_duplicates import (NearDuplicateDecision, NearDuplicatePair,
                                PerceptualFingerprint, NearDuplicateCluster)
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
                              GroupQuotaProgress, ResultExclusionDecision,
                              ManualReviewDecision,
                              NearDuplicateDecision, NearDuplicatePair, PerceptualFingerprint,
                              NearDuplicateCluster,
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
        if kind == "CleaningRuleVerdict" and "group_id" in missing:
            data = {**data, "group_id": None}
            missing = missing - {"group_id"}
        if kind == "CleaningRule" and missing == {"group_id"}:
            data = {**data, "group_id": None}
        if kind == "CleaningCandidateRef" and missing == {"group_ids"}:
            data = {**data, "group_ids": encode(())}
        if kind == "ManifestItem" and missing == {"group_ids"}:
            data = {**data, "group_ids": encode(())}
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
                      "near_duplicate_decisions", "group_quotas",
                      "exclusion_decisions", "publication_mutation_count"}
            legacy.add("manual_review_decisions")
            if missing <= legacy:
                defaults = {"parent_result_set_id": None,
                            "parent_result_set_digest": None,
                            "source_operation_ids": encode(()),
                            "near_duplicate_pairs": encode(()),
                            "near_duplicate_decisions": encode(()),
                            "group_quotas": encode(()),
                            "exclusion_decisions": encode(()),
                            "manual_review_decisions": encode(()),
                            "publication_mutation_count": None}
                data = {**data, **{key: value for key, value in defaults.items()
                                   if key in missing}}
        if kind == "ExactRepresentative":
            legacy = {"candidate_ids", "sources", "fingerprint", "group_ids",
                      "assigned_group_id"}
            if missing <= legacy:
                defaults = {"candidate_ids": encode(()), "sources": encode(()),
                            "fingerprint": None, "group_ids": encode(()),
                            "assigned_group_id": None}
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
                           "plan_task_read_views", "plan_task_read_meta",
                           "plan_source_meta", "plan_task_sources", "plan_page_sources",
                           "search_dispatch_outbox", "candidate_browse_hits", "sqlite_sequence",
                           "image_deliveries", "image_delivery_events", "cleaning_dispatch_attempts", "cleaning_checkpoints",
                           "refill_reservations", "refill_dispatch_outbox",
                           "auto_refill_outbox", "auto_refill_intake_outbox",
                           "near_duplicate_review_keys", "result_exclusion_keys",
                           "manual_review_keys",
                           "refill_iteration_metrics", "refill_execution_stops",
                           "task_event_state", "task_event_stream",
                           "task_event_mutation_clock", "task_event_task_mutations",
                           "task_event_observed_mutations", "query_images",
                           "task_asset_read_heads", "task_asset_read_bindings",
                           "task_result_read_snapshots", "task_result_read_views",
                           "task_result_read_items", "task_step_read_runs",
                           "task_step_read_items", "task_read_summaries",
                           "task_read_summaries_meta", "task_node_read_heads", "task_node_read_items",
                           "task_event_write_heads", "chat_read_summaries", "chat_read_meta"}
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
                db.execute("""CREATE TABLE IF NOT EXISTS task_read_summaries (
                    task_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    dimension_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    task_version INTEGER NOT NULL, revision INTEGER,
                    generation INTEGER NOT NULL, summary_json TEXT NOT NULL,
                    task_json TEXT)""")
                from ...node_read_views import initialize as initialize_node_views
                initialize_node_views(db)
                from ...chat_history import initialize as initialize_chat_history
                initialize_chat_history(db)
                from ...task_events import initialize_event_tables
                initialize_event_tables(db)
                summary_columns = {info[1] for info in db.execute(
                    'PRAGMA table_info(task_read_summaries)')}
                if 'task_json' not in summary_columns:
                    db.execute('ALTER TABLE task_read_summaries ADD COLUMN task_json TEXT')
                db.execute("""CREATE INDEX IF NOT EXISTS task_read_summaries_page
                    ON task_read_summaries(tenant_id,owner_user_id,project_id,
                                            created_at DESC,task_id DESC)""")
                db.execute("""CREATE TABLE IF NOT EXISTS task_read_summaries_meta (
                    id INTEGER PRIMARY KEY CHECK(id=1), format INTEGER NOT NULL,
                    task_count INTEGER NOT NULL, dirty INTEGER NOT NULL DEFAULT 1)""")
                columns = {info[1] for info in db.execute(
                    'PRAGMA table_info(task_read_summaries_meta)')}
                if 'task_count' not in columns:
                    db.execute('ALTER TABLE task_read_summaries_meta '
                               'ADD COLUMN task_count INTEGER NOT NULL DEFAULT -1')
                if 'dirty' not in columns:
                    db.execute('ALTER TABLE task_read_summaries_meta '
                               'ADD COLUMN dirty INTEGER NOT NULL DEFAULT 1')
                if row is None:
                    db.execute('INSERT OR IGNORE INTO task_read_summaries_meta '
                               '(id,format,task_count,dirty) VALUES (1,1,0,0)')
                db.execute('''CREATE TRIGGER IF NOT EXISTS task_read_summaries_invalidate
                    AFTER UPDATE ON local_ledger BEGIN
                      UPDATE task_read_summaries_meta SET dirty=1 WHERE id=1;
                    END''')
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

    @staticmethod
    def _task_progress_summary(db, task, proposal, tables, search=None):
        from ...progress_models import build_task_progress
        summary = task.to_summary()
        page = None
        if {'task_asset_read_heads', 'task_result_read_views'} <= tables:
            head = db.execute('''SELECT tenant_id,owner_user_id,project_id,
                revision,generation,result_set_id,source_version,source_digest
                FROM task_asset_read_heads WHERE task_id=?''', (task.task_id,)).fetchone()
            if head is not None and head[3:5] == (
                    task.confirmed_revision, task.run_generation):
                if head[:3] != (task.tenant_id, task.owner_user_id, task.project_id):
                    raise ServiceError('STORAGE_CORRUPT', 'task progress scope is invalid', 500)
                result_id, version, source_digest = head[5:]
                current = version == task.task_version
                delivery = None
                if not current and 'image_deliveries' in tables:
                    from ...delivery import current_delivery
                    records = {}
                    for (payload,) in db.execute('''SELECT metadata FROM image_deliveries
                        WHERE json_extract(metadata,'$.source_id')=?''', (result_id,)):
                        records[result_id] = json.loads(payload)
                    delivery = current_delivery(task, {'_deliveries': records}, result_id)
                    current = (delivery is not None
                        and delivery['source_version'] == version
                        and delivery['source_digest'] == source_digest)
                if current:
                    row = db.execute('''SELECT result_set_id,item_count,metadata_json
                        FROM task_result_read_views WHERE task_id=? AND view_type='accepted' ''',
                        (task.task_id,)).fetchone()
                    if row is None or row[0] != result_id or row[1] < 0:
                        raise ServiceError('STORAGE_CORRUPT',
                            'task progress result metadata is missing', 500)
                    page = json.loads(row[2])
                    if (page.get('task_version') != version
                            or page.get('result_set_digest') != source_digest):
                        raise ServiceError('STORAGE_CORRUPT',
                            'task progress result metadata is stale', 500)
                    page['task_version'] = task.task_version
                    if delivery is not None and delivery['status'] == 'published':
                        page['publication_kind'] = 'delivery'
                    if 'refill_execution_stops' in tables and page['counts']['gap'] > 0:
                        stop = db.execute('''SELECT stop_reason FROM refill_execution_stops
                            WHERE task_id=? AND revision=? AND generation=?''',
                            (task.task_id, task.confirmed_revision,
                             task.run_generation)).fetchone()
                        if stop:
                            page['stop_reason'] = stop[0]
        result = None if page is None else {
            'kind': page.get('publication_kind'), 'stop_reason': page.get('stop_reason')}
        returned = None
        if (search is not None and 'candidate_browse_hits' in tables
                and (search.scope.tenant_id, search.scope.owner_user_id,
                     search.scope.project_id, search.scope.task_id,
                     search.scope.revision, search.scope.generation)
                == (task.tenant_id, task.owner_user_id, task.project_id,
                    task.task_id, task.confirmed_revision, task.run_generation)):
            returned = db.execute('SELECT COUNT(*) FROM candidate_browse_hits '
                'WHERE source=?', (search.proposal_digest,)).fetchone()[0]
        return build_task_progress(summary, proposal, result, raw_returned=returned,
                                   _published_page=page).to_view()

    @staticmethod
    def _sync_task_read_summaries(db, memory, *, task_ids=None, searches=None):
        """Keep the bounded list projection in the business write transaction."""
        from ...chat_history import sync as sync_chat_history
        sync_chat_history(db, memory, task_ids=task_ids)
        live = set(memory._tasks)
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        supplied_searches = searches is not None
        searches = searches or {}
        if not supplied_searches and 'plan_set_ledger' in tables:
            from .plan_sets import restore as restore_plan_sets
            searches = restore_plan_sets(db.execute(
                'SELECT format,payload FROM plan_set_ledger WHERE id=1').fetchone())['current']
        pending = {}
        for draft in memory._feedback_drafts.values():
            if draft['status'] == 'ready' and (task_ids is None or draft['task_id'] in task_ids):
                task_id = draft['task_id']
                if task_id in pending:
                    raise ServiceError('STORAGE_CORRUPT',
                        'multiple pending feedback drafts', 500)
                pending[task_id] = memory._feedback_view(draft)
        for (task_id,) in db.execute('SELECT task_id FROM task_read_summaries'):
            if task_id not in live and (task_ids is None or task_id in task_ids):
                for table in ('task_node_read_heads', 'task_node_read_items'):
                    db.execute(f'DELETE FROM {table} WHERE task_id=?', (task_id,))
                db.execute('DELETE FROM task_read_summaries WHERE task_id=?',
                           (task_id,))
        for task in memory._tasks.values():
            if task_ids is not None and task.task_id not in task_ids:
                continue
            if task.confirmed_proposal_json is not None:
                try:
                    proposal = json.loads(task.confirmed_proposal_json)
                    raw_request = proposal['query_spec']['raw_request']
                except (ValueError, KeyError, TypeError) as exc:
                    raise ServiceError('STORAGE_CORRUPT',
                        'confirmed proposal is invalid', 500) from exc
            else:
                raw_request = task.raw_request
            summary = task.to_summary()
            summary.update(raw_request=raw_request,
                created_at=task.created_at.isoformat(),
                pending_draft=pending.get(task.task_id))
            if task.confirmed_proposal_json is not None:
                try:
                    summary['progress'] = SQLiteTaskRepository._task_progress_summary(
                        db, task, proposal, tables, searches.get(task.task_id))
                except (ValueError, TypeError, KeyError) as exc:
                    raise ServiceError('STORAGE_CORRUPT',
                        'task progress summary is invalid', 500) from exc
            payload = json.dumps(summary, ensure_ascii=False,
                                 separators=(',', ':'), allow_nan=False)
            task_payload = json.dumps(encode(task), ensure_ascii=False, separators=(',', ':'))
            db.execute('''INSERT INTO task_read_summaries
                (task_id,tenant_id,owner_user_id,project_id,dimension_id,created_at,
                 task_version,revision,generation,summary_json,task_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET
                tenant_id=excluded.tenant_id,
                owner_user_id=excluded.owner_user_id,
                project_id=excluded.project_id,
                dimension_id=excluded.dimension_id,
                created_at=excluded.created_at,
                task_version=excluded.task_version,
                revision=excluded.revision,
                generation=excluded.generation,
                summary_json=excluded.summary_json,task_json=excluded.task_json
                WHERE task_read_summaries.summary_json != excluded.summary_json
                   OR task_read_summaries.task_json IS NOT excluded.task_json''',
                (task.task_id, task.tenant_id, task.owner_user_id,
                 task.project_id, task.dimension_id,
                 task.created_at.isoformat(), task.task_version,
                 task.confirmed_revision, task.run_generation, payload, task_payload))
            from ...node_read_views import sync as sync_node_views
            sync_node_views(db, task, tables)
            from ...task_events import publish_task_change
            publish_task_change(db, task, summary, tables)
        db.execute('INSERT OR REPLACE INTO task_read_summaries_meta '
                   '(id,format,task_count,dirty) VALUES (1,1,?,0)',
                   (len(live),))

    def list_task_read_page(self, actor, project_id, *, limit, after=None):
        try:
            with closing(self._connect()) as db:
                db.execute('BEGIN')
                where = ('tenant_id=? AND owner_user_id=? AND project_id=?')
                args = [actor.tenant_id, actor.user_id, project_id]
                if after is not None:
                    where += ' AND (created_at,task_id)<(?,?)'
                    args.extend((after[0].isoformat(), after[1]))
                rows = db.execute('SELECT task_id,dimension_id,created_at,'
                    'task_version,revision,generation,summary_json '
                    'FROM task_read_summaries WHERE ' + where +
                    ' ORDER BY created_at DESC,task_id DESC LIMIT ?',
                    (*args, limit)).fetchall()
                # An old ledger needs an explicit migration. The service
                # may use its legacy path until the projection is built.
                marker = db.execute('SELECT format,task_count,dirty '
                    'FROM task_read_summaries_meta WHERE id=1').fetchone()
                if marker is None or marker[2]:
                    return None
                if marker[0] != 1:
                    raise ServiceError('STORAGE_FORMAT_UNSUPPORTED',
                        'task read summary format is unsupported', 500)
                count = db.execute('SELECT COUNT(*) FROM task_read_summaries').fetchone()[0]
                if marker[1] != count:
                    raise ServiceError('STORAGE_CORRUPT',
                        'task read summary row count is inconsistent', 500)
                result = []
                for task_id, dimension, created_at, version, revision, generation, payload in rows:
                    try:
                        item = json.loads(payload)
                    except (ValueError, TypeError) as exc:
                        raise ServiceError('STORAGE_CORRUPT',
                            'task read summary is invalid', 500) from exc
                    if (not isinstance(item, dict) or item.get('task_id') != task_id
                            or item.get('project_id') != project_id
                            or item.get('dimension_id') != dimension
                            or item.get('created_at') != created_at
                            or item.get('task_version') != version
                            or item.get('confirmed_revision') != revision
                            or item.get('run_generation') != generation):
                        raise ServiceError('STORAGE_CORRUPT',
                            'task read summary binding is invalid', 500)
                    progress = item.get('progress')
                    if progress is not None and (
                            not isinstance(progress, dict)
                            or (progress.get('task_id'), progress.get('revision'),
                                progress.get('task_version'), progress.get('run_generation'))
                            != (task_id, revision, version, generation)):
                        raise ServiceError('STORAGE_CORRUPT',
                            'task progress summary binding is invalid', 500)
                    result.append(item)
                return tuple(result)
        except sqlite3.Error as exc:
            raise ServiceError('STORAGE_UNAVAILABLE',
                'task read summary unavailable', 503, retryable=True) from exc

    def get_task_read_summary(self, actor, task_id):
        try:
            with closing(self._connect()) as db:
                db.execute('BEGIN')
                ready = db.execute('SELECT dirty FROM task_read_summaries_meta WHERE id=1').fetchone()
                if ready is None or ready[0]:
                    return None
                row = db.execute('''SELECT project_id,dimension_id,task_version,
                    revision,generation,summary_json FROM task_read_summaries
                    WHERE task_id=? AND tenant_id=? AND owner_user_id=?''',
                    (task_id, actor.tenant_id, actor.user_id)).fetchone()
                if row is None:
                    return None
                item = json.loads(row[5])
                if (not isinstance(item, dict) or item.get('task_id') != task_id
                        or (item.get('project_id'), item.get('dimension_id'),
                            item.get('task_version'), item.get('confirmed_revision'),
                            item.get('run_generation')) != row[:5]):
                    raise ServiceError('STORAGE_CORRUPT',
                        'task read summary binding is invalid', 500)
                return item
        except (ValueError, TypeError) as exc:
            raise ServiceError('STORAGE_CORRUPT', 'task read summary is invalid', 500) from exc
        except sqlite3.Error as exc:
            raise ServiceError('STORAGE_UNAVAILABLE',
                'task read summary unavailable', 503, retryable=True) from exc

    @staticmethod
    def _read_task_binding(db, actor, task_id):
        ready = db.execute('SELECT dirty FROM task_read_summaries_meta WHERE id=1').fetchone()
        if ready is None or ready[0]:
            return None
        row = db.execute('''SELECT project_id,dimension_id,task_version,revision,
            generation,task_json FROM task_read_summaries
            WHERE task_id=? AND tenant_id=? AND owner_user_id=?''',
            (task_id, actor.tenant_id, actor.user_id)).fetchone()
        if row is None or row[5] is None:
            return None
        try:
            task = decode(json.loads(row[5]))
        except (ValueError, TypeError, KeyError) as exc:
            raise ServiceError('STORAGE_CORRUPT', 'task read binding is invalid', 500) from exc
        if (type(task) is not Task
                or (task.task_id, task.tenant_id, task.owner_user_id)
                != (task_id, actor.tenant_id, actor.user_id)
                or (task.project_id, task.dimension_id, task.task_version,
                    task.confirmed_revision, task.run_generation) != row[:5]):
            raise ServiceError('STORAGE_CORRUPT', 'task read binding is stale', 500)
        return task

    def node_read_page(self, actor, task_id, node, *, limit=24, cursor=None, projects):
        from ...node_read_views import read
        try:
            with closing(self._connect()) as db:
                db.execute('BEGIN')
                task = self._read_task_binding(db, actor, task_id)
                if task is None:
                    # Authorize even when migration is required; never expose another owner's task.
                    task = self._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone()).get(actor, task_id)
                    projects.require_access(actor, task.project_id, task.dimension_id, 'read')
                    raise ServiceError('READ_PROJECTION_REQUIRED', 'node projection requires migration', 503)
                projects.require_access(actor, task.project_id, task.dimension_id, 'read')
                return read(db, task, node, limit, cursor)
        except (ValueError, TypeError, KeyError) as exc:
            raise ServiceError('STORAGE_CORRUPT', 'node projection is invalid', 500) from exc
        except sqlite3.Error as exc:
            raise ServiceError('STORAGE_UNAVAILABLE', 'node page unavailable', 503, retryable=True) from exc

    def get_task_read_binding(self, actor, task_id):
        try:
            with closing(self._connect()) as db:
                db.execute('BEGIN')
                task = self._read_task_binding(db, actor, task_id)
                if task is not None:
                    return task
                return self._load(db.execute('SELECT format,payload FROM local_ledger '
                    'WHERE id=1').fetchone()).get(actor, task_id)
        except sqlite3.Error as exc:
            raise ServiceError('STORAGE_UNAVAILABLE',
                'task read binding unavailable', 503, retryable=True) from exc

    def rebuild_task_read_summaries(self):
        try:
            with closing(self._connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                memory = self._load(db.execute(
                    'SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
                self._sync_task_read_summaries(db, memory)
                return len(memory._tasks)
        except sqlite3.Error as exc:
            raise ServiceError('STORAGE_UNAVAILABLE',
                'task read summary rebuild failed', 503, retryable=True) from exc

    def _call(self, name, write, *args, **kwargs):
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                memory = self._load(db.execute("SELECT format, payload FROM local_ledger WHERE id=1").fetchone())
                result = getattr(memory, name)(*args, **kwargs)
                if write:
                    db.execute("UPDATE local_ledger SET payload=? WHERE id=1", (dump(memory),))
                    self._sync_task_read_summaries(db, memory)
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
                self._sync_task_read_summaries(db, memory)
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
                self._sync_task_read_summaries(db, memory)
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
    def list_tasks_with_feedback(self, *args, **kwargs):
        return self._call("list_tasks_with_feedback", False, *args, **kwargs)
    def list_tasks_page_with_feedback(self, *args, **kwargs):
        return self._call("list_tasks_page_with_feedback", False, *args, **kwargs)
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
                self._sync_task_read_summaries(db, memory)
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
                    if 'result_exclusion_keys' in existing:
                        db.execute('DELETE FROM result_exclusion_keys WHERE task_id IN (' + placeholders + ')', tuple(task_ids))
                    for table in ('manual_review_keys', 'task_asset_read_heads',
                                  'task_asset_read_bindings',
                                  'task_result_read_snapshots',
                                  'task_result_read_views',
                                  'task_result_read_items',
                                  'task_step_read_runs', 'task_step_read_items'):
                        if table in existing:
                            db.execute('DELETE FROM ' + table + ' WHERE task_id IN ('
                                       + placeholders + ')', tuple(task_ids))
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
                    for table in ('refill_iteration_metrics', 'refill_execution_stops',
                                  'task_event_stream', 'task_event_state',
                                  'task_event_observed_mutations', 'task_event_write_heads',
                                  'task_node_read_heads', 'task_node_read_items'):
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
                self._sync_task_read_summaries(db, memory)
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
                    if 'result_exclusion_keys' in existing:
                        db.execute('DELETE FROM result_exclusion_keys WHERE task_id IN (' + placeholders + ')', tuple(task_ids))
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
                    for table in ('refill_iteration_metrics', 'refill_execution_stops',
                                  'task_event_stream', 'task_event_state',
                                  'task_event_observed_mutations', 'task_event_write_heads',
                                  'task_node_read_heads', 'task_node_read_items'):
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
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='plan_task_read_views'").fetchone():
            from .plan_read_views import sync
            sync(db, state)
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='plan_task_sources'").fetchone():
            from . import plan_sources
            plan_sources.sync(db, state)
        return purged_digests

    def put_cleaning_proposal(self, *args, **kwargs): return self._call("put_cleaning_proposal", True, *args, **kwargs)
    def get_cleaning_proposal(self, *args, **kwargs): return self._call("get_cleaning_proposal", False, *args, **kwargs)
    def get_cleaning_operation(self, *args, **kwargs): return self._call("get_cleaning_operation", False, *args, **kwargs)
    def reserve_cleaning(self, *args, **kwargs): return self._call("reserve_cleaning", True, *args, **kwargs)
    def attach_cleaning_backend(self, *args, **kwargs): return self._call("attach_cleaning_backend", True, *args, **kwargs)
    def publish_cleaning(self, *args, **kwargs): return self._call("publish_cleaning", True, *args, **kwargs)
    def cancel_cleaning(self, *args, **kwargs): return self._call("cancel_cleaning", True, *args, **kwargs)
    def cleaning_results(self, *args, **kwargs): return self._call("cleaning_results", False, *args, **kwargs)
