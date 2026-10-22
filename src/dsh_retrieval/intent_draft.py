"""Private, versioned interpretation drafts; no formal task or run is created."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .errors import ServiceError, invalid
from .intent_review import IntentReviewService
from .query_spec import digest
from .service import require_key, require_version


def _identity(actor, project_id, dimension_id=None):
    values = (actor.tenant_id, actor.user_id)
    if project_id is not None:
        values += (project_id,)
    if dimension_id is not None:
        values += (dimension_id,)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise invalid("tenant, user and project identifiers are required")


class IntentDraftStore:
    def __init__(self, database_path, projects, reviewer=None):
        self.path = Path(database_path)
        self.projects = projects
        self.reviewer = reviewer or IntentReviewService()
        if self.path.is_symlink() or not self.path.parent.is_dir():
            raise ValueError("private draft database path is invalid")
        if not self.path.exists():
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        if not self.path.is_file() or self.path.stat().st_mode & 0o077:
            raise ValueError("draft database must be a private regular file")
        with self._open() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS intent_drafts (
                  draft_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
                  project_id TEXT NOT NULL, dimension_id TEXT NOT NULL, draft_version INTEGER NOT NULL,
                  raw_request TEXT NOT NULL, proposal_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS intent_draft_ops (
                  tenant_id TEXT NOT NULL, owner_user_id TEXT NOT NULL, request_key TEXT NOT NULL,
                  payload_digest TEXT NOT NULL, draft_id TEXT NOT NULL, result_version INTEGER NOT NULL,
                  PRIMARY KEY (tenant_id, owner_user_id, request_key)
                );
            """)

    @contextmanager
    def _open(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
            if db.in_transaction:
                db.commit()
        except Exception:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def _view(self, row):
        raw = row["raw_request"]
        proposal = json.loads(row["proposal_json"])
        return {"draft_id": row["draft_id"], "draft_version": row["draft_version"],
                "project_id": row["project_id"], "dimension_id": row["dimension_id"],
                "status": "interpretation_draft", "review": self.reviewer.review(raw, proposal),
                "creates_task": False}

    def _load(self, db, actor, draft_id):
        row = db.execute("SELECT * FROM intent_drafts WHERE draft_id=? AND tenant_id=? AND owner_user_id=?",
                         (draft_id, actor.tenant_id, actor.user_id)).fetchone()
        if row is None:
            raise ServiceError("NOT_FOUND", "draft was not found", 404)
        self.projects.require_access(actor, row["project_id"], row["dimension_id"], "read")
        return row

    def get(self, actor, draft_id):
        _identity(actor, None)
        with self._open() as db:
            return self._view(self._load(db, actor, draft_id))

    def _replay(self, db, actor, key, payload_digest):
        op = db.execute("SELECT * FROM intent_draft_ops WHERE tenant_id=? AND owner_user_id=? AND request_key=?",
                        (actor.tenant_id, actor.user_id, key)).fetchone()
        if op is None:
            return None
        if op["payload_digest"] != payload_digest:
            raise ServiceError("IDEMPOTENCY_CONFLICT", "request key was used for different content", 409)
        row = self._load(db, actor, op["draft_id"])
        # A later version cannot be represented as the original operation result.
        return {"draft_id": op["draft_id"], "draft_version": op["result_version"],
                "current_draft_version": row["draft_version"], "replayed": True}

    def create(self, actor, project_id, dimension_id, key, raw_request, proposal):
        _identity(actor, project_id, dimension_id)
        require_key(key)
        self.projects.require_access(actor, project_id, dimension_id, "create")
        review = self.reviewer.review(raw_request, proposal)
        body = {"operation": "create", "project_id": project_id, "dimension_id": dimension_id,
                "raw_request": raw_request, "proposal": proposal}
        fingerprint = digest(body)
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            replay = self._replay(db, actor, key, fingerprint)
            if replay is not None:
                return replay
            draft_id = "intent_" + uuid.uuid4().hex
            db.execute("INSERT INTO intent_drafts VALUES (?,?,?,?,?,?,?,?)",
                       (draft_id, actor.tenant_id, actor.user_id, project_id, dimension_id, 1,
                        raw_request, json.dumps(proposal, ensure_ascii=False, sort_keys=True)))
            db.execute("INSERT INTO intent_draft_ops VALUES (?,?,?,?,?,?)",
                       (actor.tenant_id, actor.user_id, key, fingerprint, draft_id, 1))
            db.commit()
        return {"draft_id": draft_id, "draft_version": 1, "project_id": project_id,
                "dimension_id": dimension_id, "review": review, "creates_task": False, "replayed": False}

    def revise(self, actor, draft_id, key, expected_version, raw_request, proposal):
        _identity(actor, None)
        require_key(key)
        require_version(expected_version)
        review = self.reviewer.review(raw_request, proposal)
        fingerprint = digest({"operation": "revise", "draft_id": draft_id, "expected_version": expected_version,
                              "raw_request": raw_request, "proposal": proposal})
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._load(db, actor, draft_id)
            self.projects.require_access(actor, row["project_id"], row["dimension_id"], "create")
            replay = self._replay(db, actor, key, fingerprint)
            if replay is not None:
                return replay
            if row["draft_version"] != expected_version:
                raise ServiceError("DRAFT_VERSION_CONFLICT", "draft version has changed", 409)
            next_version = expected_version + 1
            db.execute("UPDATE intent_drafts SET draft_version=?, raw_request=?, proposal_json=? WHERE draft_id=?",
                       (next_version, raw_request, json.dumps(proposal, ensure_ascii=False, sort_keys=True), draft_id))
            db.execute("INSERT INTO intent_draft_ops VALUES (?,?,?,?,?,?)",
                       (actor.tenant_id, actor.user_id, key, fingerprint, draft_id, next_version))
            db.commit()
        return {"draft_id": draft_id, "draft_version": next_version, "project_id": row["project_id"],
                "dimension_id": row["dimension_id"], "review": review, "creates_task": False, "replayed": False}
