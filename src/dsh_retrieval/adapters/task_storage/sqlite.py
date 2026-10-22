"""Small local SQLite snapshot ledger; not a production schema or an outbox.

Every call loads a fresh state inside a DB transaction. Business transitions are
reused from InMemoryTaskRepository, but no in-memory state survives a call.
BEGIN IMMEDIATE serializes writers across processes; external work stays outside
the transaction. Full snapshots trade throughput for a simple recovery baseline.
"""
from contextlib import closing
from dataclasses import fields, is_dataclass
from datetime import datetime
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
from ...result_contracts import ExactRepresentative, ExactResultSetPreview


TYPES = {t.__name__: t for t in (Candidate, CleaningOperation, CleaningProposal, Task, Draft,
                              IndexBinding, QueryVector, SearchRequest, SearchHit, SearchPage,
                              RetrievalPlan, RetrievalScope, MediaRecord, CandidateManifest,
                              CandidateManifestProposal, ManifestItem, CleaningApproval,
                              CleaningApprovalProposal, CleaningBinding, CleaningCandidateRef, CleaningRecipe,
                              CleaningRule, DetectionStep, CleaningRuleVerdict,
                              CleaningCandidateVerdict, CleaningJobResult,
                              ExactRepresentative, ExactResultSetPreview)}
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
        if set(data) != {f.name for f in fields(cls)}:
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
                if tables and ("local_ledger" not in tables or tables - {"local_ledger", "retrieval_ledger", "candidate_ledger", "multi_rule_cleaning_ledger"}):
                    raise ServiceError("STORAGE_FORMAT_UNSUPPORTED", "not a local demo ledger", 500)
                db.execute("CREATE TABLE IF NOT EXISTS local_ledger (id INTEGER PRIMARY KEY CHECK(id=1), format INTEGER NOT NULL, payload TEXT NOT NULL)")
                row = db.execute("SELECT format, payload FROM local_ledger WHERE id=1").fetchone()
                if row is None:
                    if tables:
                        raise ServiceError("STORAGE_CORRUPT", "ledger record missing", 500)
                    db.execute("INSERT INTO local_ledger VALUES (1, ?, ?)", (FORMAT, dump(InMemoryTaskRepository())))
                else:
                    self._load(row)
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

    def get(self, *args, **kwargs): return self._call("get", False, *args, **kwargs)
    def create(self, *args, **kwargs): return self._call("create", True, *args, **kwargs)
    def get_draft(self, *args, **kwargs): return self._call("get_draft", False, *args, **kwargs)
    def save_draft(self, *args, **kwargs): return self._call("save_draft", True, *args, **kwargs)
    def confirm_draft(self, *args, **kwargs): return self._call("confirm_draft", True, *args, **kwargs)
    def put_cleaning_proposal(self, *args, **kwargs): return self._call("put_cleaning_proposal", True, *args, **kwargs)
    def get_cleaning_proposal(self, *args, **kwargs): return self._call("get_cleaning_proposal", False, *args, **kwargs)
    def get_cleaning_operation(self, *args, **kwargs): return self._call("get_cleaning_operation", False, *args, **kwargs)
    def reserve_cleaning(self, *args, **kwargs): return self._call("reserve_cleaning", True, *args, **kwargs)
    def attach_cleaning_backend(self, *args, **kwargs): return self._call("attach_cleaning_backend", True, *args, **kwargs)
    def publish_cleaning(self, *args, **kwargs): return self._call("publish_cleaning", True, *args, **kwargs)
    def cancel_cleaning(self, *args, **kwargs): return self._call("cancel_cleaning", True, *args, **kwargs)
    def cleaning_results(self, *args, **kwargs): return self._call("cleaning_results", False, *args, **kwargs)
