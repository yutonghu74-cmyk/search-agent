"""Local immutable input manifests; refs only, not a media cache/result set."""
from contextlib import closing
from dataclasses import replace
import json
import sqlite3
import uuid

from .sqlite import MAX_BYTES, encode, decode
from .retrieval import SQLiteRetrievalPlanRepository, load_state as load_retrieval
from ...candidate_contracts import CandidateManifest, CandidateManifestProposal, validate_manifest
from ...errors import ServiceError
from ...integration_models import AccessContext
from ...query_spec import digest
from ...retrieval_contracts import RetrievalOperation, validate_execution, validate_scope
from ...service import require_key, require_version


def empty_state():
    return {"proposals": {}, "manifests": {}, "published": {}, "keys": {}, "events": []}


def dump_state(state):
    data = json.dumps(encode(state), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(data.encode()) > MAX_BYTES:
        raise ServiceError("STORAGE_LIMIT", "Candidate ledger exceeds local limit", 507)
    return data


def load_state(row):
    if row is None:
        raise ServiceError("STORAGE_CORRUPT", "Candidate ledger missing", 500)
    if row[0] != 1:
        raise ServiceError("STORAGE_FORMAT_UNSUPPORTED", "Unsupported candidate format", 500)
    try:
        if not isinstance(row[1], str) or len(row[1].encode()) > MAX_BYTES:
            raise ValueError("size")
        state = decode(json.loads(row[1]))
        template = empty_state()
        if not isinstance(state, dict) or set(state) != set(template):
            raise ValueError("schema")
        if any(type(state[k]) is not type(template[k]) for k in template):
            raise ValueError("containers")
        for key, proposal in state["proposals"].items():
            if not isinstance(proposal, CandidateManifestProposal) or key != proposal.proposal_digest:
                raise ValueError("proposal")
        for ref, manifest in state["manifests"].items():
            if (not isinstance(manifest, CandidateManifest) or manifest.manifest_ref != ref
                    or state["proposals"].get(manifest.proposal.proposal_digest) != manifest.proposal
                    or state["published"].get(manifest.proposal.proposal_digest) != ref):
                raise ValueError("manifest")
        for proposal_digest, ref in state["published"].items():
            if ref not in state["manifests"] or state["manifests"][ref].proposal.proposal_digest != proposal_digest:
                raise ValueError("publication")
        for key, value in state["keys"].items():
            if (not isinstance(key, tuple) or len(key) != 4 or not isinstance(value, tuple)
                    or len(value) != 2 or not isinstance(value[0], str) or value[1] not in state["manifests"]):
                raise ValueError("idempotency")
            scope = state["manifests"][value[1]].proposal.scope
            if key[:3] != (scope.tenant_id, scope.owner_user_id, scope.task_id):
                raise ValueError("key scope")
        expected_events = [{"event_id": "candidate-published:" + ref,
                            "kind": "candidate_manifest_published", "manifest_ref": ref,
                            "task_id": m.proposal.scope.task_id,
                            "proposal_digest": m.proposal.proposal_digest}
                           for ref, m in state["manifests"].items()]
        if state["events"] != expected_events:
            raise ValueError("events")
        return state
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        raise ServiceError("STORAGE_CORRUPT", "Invalid candidate ledger; refusing reset", 500) from exc


def purge_for_tasks(state, task_ids):
    """Drop every candidate manifest scoped to the given task ids, in place.

    ``load_state`` strictly revalidates ``events`` against the surviving
    manifests, so events are rebuilt from the filtered manifests rather than
    just filtered. A no-op for an empty task set. Returns the mutated state.
    """
    if not task_ids:
        return state
    surviving_manifests = {ref: m for ref, m in state["manifests"].items()
                           if m.proposal.scope.task_id not in task_ids}
    surviving_digests = {m.proposal.proposal_digest for m in surviving_manifests.values()}
    state["manifests"] = surviving_manifests
    state["published"] = {digest: ref for digest, ref in state["published"].items()
                          if digest in surviving_digests}
    state["proposals"] = {digest: p for digest, p in state["proposals"].items()
                          if digest in surviving_digests}
    state["keys"] = {key: v for key, v in state["keys"].items()
                     if key[2] not in task_ids}
    state["events"] = [{"event_id": "candidate-published:" + ref,
                        "kind": "candidate_manifest_published", "manifest_ref": ref,
                        "task_id": m.proposal.scope.task_id,
                        "proposal_digest": m.proposal.proposal_digest}
                       for ref, m in surviving_manifests.items()]
    return state


class SQLiteCandidateManifestRepository:
    def __init__(self, path, *, projects, authorization):
        self.retrieval = SQLiteRetrievalPlanRepository(path, projects=projects, authorization=authorization)
        self.tasks = self.retrieval.tasks
        self.authorization = authorization
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_ledger'").fetchone()
                if exists:
                    load_state(db.execute("SELECT format,payload FROM candidate_ledger WHERE id=1").fetchone())
                else:
                    db.execute("CREATE TABLE candidate_ledger (id INTEGER PRIMARY KEY CHECK(id=1), format INTEGER NOT NULL, payload TEXT NOT NULL)")
                    db.execute("INSERT INTO candidate_ledger VALUES (1,1,?)", (dump_state(empty_state()),))
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "Candidate ledger unavailable", 503, retryable=True) from exc

    def _transaction(self, write, operation):
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                memory = self.tasks._load(db.execute("SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                retrieval = load_retrieval(db.execute("SELECT format,payload FROM retrieval_ledger WHERE id=1").fetchone())
                state = load_state(db.execute("SELECT format,payload FROM candidate_ledger WHERE id=1").fetchone())
                result = operation(memory, retrieval, state)
                if write:
                    db.execute("UPDATE candidate_ledger SET payload=? WHERE id=1", (dump_state(state),))
                return result
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "Candidate transaction failed", 503, retryable=True) from exc

    def _check(self, memory, retrieval, actor, proposal, *, reading=False, delivery_read=False):
        task = self.retrieval._authorize(memory, actor, proposal.scope.task_id,
                                         action="read" if reading else "approve")
        record = retrieval["operations"].get(proposal.operation_id)
        if record is None or record[0].scope.task_id != task.task_id:
            raise ServiceError("NOT_FOUND", "Candidate source operation not found", 404)
        operation = RetrievalOperation(proposal.operation_id, *record)
        validate_manifest(proposal, operation)
        current = retrieval["current"].get(task.task_id)
        if reading:
            # Explicit manifest-only exception: cleaning increments task_version,
            # but does not change the approved input's stable identity/lifetime.
            if task.task_version < proposal.scope.task_version:
                raise ServiceError("REVISION_CONFLICT", "Task version regressed", 409)
            validate_scope(actor, replace(task, task_version=proposal.scope.task_version), proposal.scope, reading=delivery_read)
            if current != operation.plan:
                raise ServiceError("PLAN_NOT_CURRENT", "Manifest plan superseded", 409)
        else:
            validate_execution(actor, task, operation.plan, current, operation.request)
        self.retrieval._authorize(memory, actor, task.task_id, operation.plan,
                                  "read" if reading else "approve")
        context = AccessContext(actor, task.task_id, "candidate_manifest", "current")
        for item in proposal.items:
            self.authorization.require_access(context, item.media.authorized_media_ref, "read")

    def put_proposal(self, actor, proposal):
        def put(memory, retrieval, state):
            self._check(memory, retrieval, actor, proposal)
            old = state["proposals"].get(proposal.proposal_digest)
            if old is not None and old != proposal:
                raise ServiceError("INPUT_SNAPSHOT_CHANGED", "Candidate preview differs", 409)
            self._check(memory, retrieval, actor, proposal)
            state["proposals"][proposal.proposal_digest] = proposal
        return self._transaction(True, put)

    def confirm(self, actor, proposal, *, key, expected_task_version, action):
        require_key(key)
        require_version(expected_task_version)
        fingerprint = digest([proposal.proposal_digest, expected_task_version, action])
        def confirm(memory, retrieval, state):
            self._check(memory, retrieval, actor, proposal)
            scoped_key = (actor.tenant_id, actor.user_id, proposal.scope.task_id, key)
            old = state["keys"].get(scoped_key)
            if old is not None and old[0] != fingerprint:
                raise ServiceError("IDEMPOTENCY_CONFLICT", "Candidate confirmation key changed", 409)
            if action != "confirm":
                raise ServiceError("CONFIRMATION_REQUIRED", "Explicit owner confirmation required", 422)
            if expected_task_version != proposal.scope.task_version:
                raise ServiceError("REVISION_CONFLICT", "Confirmation task version changed", 409)
            if state["proposals"].get(proposal.proposal_digest) != proposal:
                raise ServiceError("MANIFEST_NOT_PREVIEWED", "Preview required before confirmation", 409)
            self._check(memory, retrieval, actor, proposal)
            ref = state["published"].get(proposal.proposal_digest)
            replayed = ref is not None
            if ref is None:
                ref = "manifest_" + uuid.uuid4().hex
                state["manifests"][ref] = CandidateManifest(ref, proposal)
                state["published"][proposal.proposal_digest] = ref
                state["events"].append({"event_id": "candidate-published:" + ref,
                                         "kind": "candidate_manifest_published", "manifest_ref": ref,
                                         "task_id": proposal.scope.task_id,
                                         "proposal_digest": proposal.proposal_digest})
            state["keys"][scoped_key] = (fingerprint, ref)
            return state["manifests"][ref], replayed
        return self._transaction(True, confirm)

    def get(self, actor, task_id, manifest_ref):
        def read(memory, retrieval, state):
            self.retrieval._authorize(memory, actor, task_id)
            manifest = state["manifests"].get(manifest_ref)
            if manifest is None or manifest.proposal.scope.task_id != task_id:
                raise ServiceError("NOT_FOUND", "Candidate manifest not found", 404)
            self._check(memory, retrieval, actor, manifest.proposal, reading=True)
            self._check(memory, retrieval, actor, manifest.proposal, reading=True)
            return manifest
        return self._transaction(False, read)
