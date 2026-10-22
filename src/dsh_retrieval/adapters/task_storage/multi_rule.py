"""Local, transaction-bound approval ledger for multi-rule cleaning.

This records an approved *queued* job. It does not run detectors, publish
verdicts, or claim business-accepted counts.
"""

from contextlib import closing
import json
import sqlite3
import uuid

from .candidates import SQLiteCandidateManifestRepository, load_state as load_candidates
from .retrieval import load_state as load_retrieval
from .sqlite import MAX_BYTES, decode, dump, encode
from ...errors import ServiceError
from ...models import RunStatus
from ...multi_rule_contracts import (CleaningApproval, CleaningApprovalProposal,
                                     CleaningCandidateRef, CleaningRecipe)
from ...query_spec import digest
from ...recipe_bridge import require_confirmed_rules
from ...service import require_key, require_version


def empty_state():
    return {"proposals": {}, "approvals": {}, "keys": {}, "events": []}


def dump_state(state):
    payload = json.dumps(encode(state), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(payload.encode()) > MAX_BYTES:
        raise ServiceError("STORAGE_LIMIT", "Multi-rule approval ledger exceeds local limit", 507)
    return payload


def load_state(row):
    if row is None:
        raise ServiceError("STORAGE_CORRUPT", "Multi-rule approval ledger missing", 500)
    if row[0] != 1:
        raise ServiceError("STORAGE_FORMAT_UNSUPPORTED", "Unsupported multi-rule ledger format", 500)
    try:
        if type(row[1]) is not str or len(row[1].encode()) > MAX_BYTES:
            raise ValueError("size")
        state = decode(json.loads(row[1]))
        if (type(state) is not dict or set(state) != set(empty_state())
                or any(type(state[k]) is not type(empty_state()[k]) for k in state)):
            raise ValueError("shape")
        for key, proposal in state["proposals"].items():
            if type(proposal) is not CleaningApprovalProposal or key != proposal.proposal_digest:
                raise ValueError("proposal")
        for opid, approval in state["approvals"].items():
            if (type(approval) is not CleaningApproval or opid != approval.operation_id
                    or state["proposals"].get(approval.proposal.proposal_digest) != approval.proposal):
                raise ValueError("approval")
        for key, value in state["keys"].items():
            if (type(key) is not tuple or len(key) != 4 or type(value) is not tuple
                    or len(value) != 2 or type(value[0]) is not str
                    or value[1] not in state["approvals"]):
                raise ValueError("idempotency")
            p = state["approvals"][value[1]].proposal
            if key[:3] != (p.tenant_id, p.owner_user_id, p.task_id):
                raise ValueError("key scope")
        expected = [{"event_id": "multi-rule-approved:" + opid,
                     "kind": "multi_rule_cleaning_approved", "operation_id": opid}
                    for opid in state["approvals"]]
        if state["events"] != expected:
            raise ValueError("events")
        return state
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        raise ServiceError("STORAGE_CORRUPT", "Invalid multi-rule approval ledger; refusing reset", 500) from exc


class SQLiteMultiRuleApprovalRepository:
    def __init__(self, path, *, projects, authorization):
        self.manifests = SQLiteCandidateManifestRepository(path, projects=projects,
                                                            authorization=authorization)
        self.tasks = self.manifests.tasks
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='multi_rule_cleaning_ledger'").fetchone()
                if exists:
                    load_state(db.execute("SELECT format,payload FROM multi_rule_cleaning_ledger WHERE id=1").fetchone())
                else:
                    db.execute("CREATE TABLE multi_rule_cleaning_ledger (id INTEGER PRIMARY KEY CHECK(id=1), format INTEGER NOT NULL, payload TEXT NOT NULL)")
                    db.execute("INSERT INTO multi_rule_cleaning_ledger VALUES (1,1,?)", (dump_state(empty_state()),))
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "Multi-rule approval ledger unavailable", 503,
                               retryable=True) from exc

    def _transaction(self, write, operation):
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                memory = self.tasks._load(db.execute("SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                retrieval = load_retrieval(db.execute("SELECT format,payload FROM retrieval_ledger WHERE id=1").fetchone())
                candidates = load_candidates(db.execute("SELECT format,payload FROM candidate_ledger WHERE id=1").fetchone())
                state = load_state(db.execute("SELECT format,payload FROM multi_rule_cleaning_ledger WHERE id=1").fetchone())
                result = operation(memory, retrieval, candidates, state)
                if write:
                    db.execute("UPDATE local_ledger SET payload=? WHERE id=1", (dump(memory),))
                    db.execute("UPDATE multi_rule_cleaning_ledger SET payload=? WHERE id=1", (dump_state(state),))
                return result
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "Multi-rule approval transaction failed", 503,
                               retryable=True) from exc

    def _manifest(self, memory, retrieval, candidates, actor, task_id, manifest_ref, action):
        task = self.manifests.retrieval._authorize(memory, actor, task_id, action=action)
        manifest = candidates["manifests"].get(manifest_ref)
        if manifest is None or manifest.proposal.scope.task_id != task_id:
            raise ServiceError("NOT_FOUND", "Candidate manifest not found", 404)
        self.manifests._check(memory, retrieval, actor, manifest.proposal, reading=True)
        return task, manifest

    @staticmethod
    def _same_inputs(task, manifest, proposal):
        scope = manifest.proposal.scope
        refs = tuple(CleaningCandidateRef(item.candidate_id, item.media.asset_version,
                                          item.media.content_digest) for item in manifest.proposal.items)
        if ((task.tenant_id, task.owner_user_id, task.project_id, task.task_id,
             task.confirmed_revision, task.run_generation, task.request_digest)
                != (proposal.tenant_id, proposal.owner_user_id, proposal.project_id,
                    proposal.task_id, proposal.revision, proposal.generation,
                    proposal.confirmed_request_digest)
                or scope.revision != proposal.revision or scope.generation != proposal.generation
                or manifest.manifest_ref != proposal.manifest_ref or refs != proposal.candidates):
            raise ServiceError("INPUT_SNAPSHOT_CHANGED", "Approved cleaning inputs changed", 409)

    def prepare(self, actor, task_id, manifest_ref, recipe: CleaningRecipe):
        if type(recipe) is not CleaningRecipe:
            raise ServiceError("RECIPE_INVALID", "Trusted cleaning recipe required", 422)
        def prepare(memory, retrieval, candidates, state):
            task, manifest = self._manifest(memory, retrieval, candidates, actor, task_id,
                                            manifest_ref, "approve")
            if task.run_status not in {RunStatus.PLANNING, RunStatus.RUNNING} or task.active_cleaning_operation_id:
                raise ServiceError("INVALID_STATE_TRANSITION", "Task cannot prepare cleaning", 409)
            require_confirmed_rules(task, recipe)
            if not 1 <= len(manifest.proposal.items) <= 100:
                raise ServiceError("INPUT_LIMIT", "Cleaning requires 1..100 candidates", 422)
            proposal = CleaningApprovalProposal(task.tenant_id, task.owner_user_id,
                task.project_id, task.task_id, task.confirmed_revision, task.run_generation,
                task.task_version, task.request_digest, manifest_ref, recipe,
                tuple(CleaningCandidateRef(item.candidate_id, item.media.asset_version,
                                           item.media.content_digest) for item in manifest.proposal.items))
            self._same_inputs(task, manifest, proposal)
            self.manifests._check(memory, retrieval, actor, manifest.proposal, reading=True)
            old = state["proposals"].get(proposal.proposal_digest)
            if old is not None and old != proposal:
                raise ServiceError("PROPOSAL_CONFLICT", "Cleaning proposal digest collision", 409)
            state["proposals"][proposal.proposal_digest] = proposal
            return proposal
        return self._transaction(True, prepare)

    @staticmethod
    def _current(task, approval):
        p = approval.proposal
        if ((task.task_id, task.confirmed_revision, task.run_generation,
             task.task_version, task.active_cleaning_operation_id, task.active_plan_digest)
                != (p.task_id, p.revision, p.generation, approval.task_version,
                    approval.operation_id, p.proposal_digest)
                or task.run_status != RunStatus.RUNNING):
            raise ServiceError("STALE_GENERATION", "Cleaning approval no longer current", 409)

    def confirm(self, actor, task_id, proposal_digest, *, key, expected_task_version, action):
        require_key(key)
        require_version(expected_task_version)
        fingerprint = digest({"proposal_digest": proposal_digest,
                              "expected_task_version": expected_task_version, "action": action})
        def confirm(memory, retrieval, candidates, state):
            self.manifests.retrieval._authorize(memory, actor, task_id, action="approve")
            scoped = (actor.tenant_id, actor.user_id, task_id, key)
            old = state["keys"].get(scoped)
            if old is not None and old[0] != fingerprint:
                raise ServiceError("IDEMPOTENCY_CONFLICT", "Confirmation key changed", 409)
            proposal = state["proposals"].get(proposal_digest)
            if proposal is None or proposal.task_id != task_id:
                raise ServiceError("NOT_FOUND", "Cleaning proposal not found", 404)
            task, manifest = self._manifest(memory, retrieval, candidates, actor, task_id,
                                            proposal.manifest_ref, "approve")
            self._same_inputs(task, manifest, proposal)
            require_confirmed_rules(task, proposal.recipe)
            if old is not None:
                approval = state["approvals"][old[1]]
                self._current(task, approval)
                return approval, True
            if action != "confirm":
                raise ServiceError("CONFIRMATION_REQUIRED", "Explicit owner confirmation required", 422)
            if expected_task_version != proposal.expected_task_version:
                raise ServiceError("REVISION_CONFLICT", "Task version changed before cleaning approval", 409)
            existing = next((a for a in state["approvals"].values()
                             if a.proposal.proposal_digest == proposal_digest), None)
            if existing is not None:
                self._current(task, existing)
                state["keys"][scoped] = (fingerprint, existing.operation_id)
                return existing, True
            if task.task_version != expected_task_version:
                raise ServiceError("REVISION_CONFLICT", "Task version changed before cleaning approval", 409)
            if task.run_status not in {RunStatus.PLANNING, RunStatus.RUNNING} or task.active_cleaning_operation_id:
                raise ServiceError("CLEANING_IN_PROGRESS", "Another cleaning operation is active", 409)
            self.manifests._check(memory, retrieval, actor, manifest.proposal, reading=True)
            # _authorize returns a detached Task; mutate the transaction's
            # authoritative object so the task and approval commit together.
            task = memory._tasks[task_id]
            task.task_version += 1
            task.run_status = RunStatus.RUNNING
            task.stage = "multi_rule_cleaning"
            approval = CleaningApproval(proposal, "cleanmulti_" + uuid.uuid4().hex,
                                        task.task_version)
            task.active_cleaning_operation_id = approval.operation_id
            task.active_plan_digest = proposal.proposal_digest
            state["approvals"][approval.operation_id] = approval
            state["keys"][scoped] = (fingerprint, approval.operation_id)
            state["events"].append({"event_id": "multi-rule-approved:" + approval.operation_id,
                                    "kind": "multi_rule_cleaning_approved",
                                    "operation_id": approval.operation_id})
            return approval, False
        return self._transaction(True, confirm)

    def get_current(self, actor, task_id, operation_id):
        def get(memory, retrieval, candidates, state):
            approval = state["approvals"].get(operation_id)
            if approval is None or approval.proposal.task_id != task_id:
                raise ServiceError("NOT_FOUND", "Cleaning approval not found", 404)
            task, manifest = self._manifest(memory, retrieval, candidates, actor, task_id,
                                            approval.proposal.manifest_ref, "read")
            self._same_inputs(task, manifest, approval.proposal)
            self._current(task, approval)
            require_confirmed_rules(task, approval.proposal.recipe)
            self.manifests._check(memory, retrieval, actor, manifest.proposal, reading=True)
            return approval
        return self._transaction(False, get)
