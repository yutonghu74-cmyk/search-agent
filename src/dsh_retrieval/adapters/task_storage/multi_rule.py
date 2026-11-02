"""Local, transaction-bound approval ledger for multi-rule cleaning.

This records an approved *queued* job. It does not run detectors, publish
verdicts, or claim business-accepted counts.
"""

from contextlib import closing
from datetime import UTC, datetime
import json
import re
import sqlite3
import uuid
import time
import math

from .candidates import SQLiteCandidateManifestRepository, load_state as load_candidates
from .plan_sets import SQLitePlanSetApprovalRepository, restore as load_plan_sets
from .retrieval import load_state as load_retrieval
from .sqlite import MAX_BYTES, decode, dump, encode
from ...errors import ServiceError
from ...models import RunStatus
from ...multi_rule_contracts import (CleaningApproval, CleaningApprovalProposal,
                                     CleaningCandidateRef, CleaningCandidateVerdict, CleaningJobResult, CleaningRecipe,
                                     CleaningRuleVerdict,
                                     require_complete_cleaning_result)
from ...query_spec import digest
from ...recipe_bridge import require_confirmed_rules
from ...result_contracts import (ExactResultSetPreview, FusedSourceEvidence,
                                 apply_near_duplicate_decision,
                                 build_exact_result_set,
                                 merge_exact_result_sets,
                                 near_duplicate_clusters)
from ...near_duplicates import NearDuplicateDecision
from ...refill_planning import decide_refill
from ...service import require_key, require_version


FORMAT = 3
_RELEASE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def empty_state():
    return {"proposals": {}, "approvals": {}, "results": {}, "result_sets": {},
            "current_result_sets": {}, "published_results": {}, "keys": {}, "events": []}


def dump_state(state):
    payload = json.dumps(encode(state), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(payload.encode()) > MAX_BYTES:
        raise ServiceError("STORAGE_LIMIT", "Multi-rule approval ledger exceeds local limit", 507)
    return payload


def load_state(row):
    if row is None:
        raise ServiceError("STORAGE_CORRUPT", "Multi-rule approval ledger missing", 500)
    if row[0] != FORMAT:
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
        for opid, result in state["results"].items():
            approval = state["approvals"].get(opid)
            if type(result) is not CleaningJobResult or approval is None:
                raise ValueError("result")
            require_complete_cleaning_result(approval.job, result)
        for result_set_id, record in state["result_sets"].items():
            if (type(result_set_id) is not str or type(record) is not tuple or len(record) != 2
                    or type(record[0]) is not ExactResultSetPreview or type(record[1]) is not int):
                raise ValueError("result set")
            preview, published_version = record
            approval = state["approvals"].get(preview.operation_id)
            result = state["results"].get(preview.operation_id)
            if (approval is None or result is None or preview.cleaning_result_digest != result.result_digest
                    or preview.task_version != approval.task_version
                    or published_version != preview.task_version + 1
                    + len(preview.near_duplicate_decisions)):
                raise ValueError("result set source")
            source_ids = preview.source_operation_ids or (preview.operation_id,)
            if (not source_ids or source_ids[-1] != preview.operation_id
                    or len(set(source_ids)) != len(source_ids)):
                raise ValueError("result set lineage")
            for source_operation_id in source_ids:
                source_approval = state["approvals"].get(source_operation_id)
                source_result = state["results"].get(source_operation_id)
                if (source_approval is None or source_result is None
                        or source_approval.proposal.task_id != approval.proposal.task_id
                        or source_approval.proposal.revision != approval.proposal.revision
                        or source_approval.proposal.generation != approval.proposal.generation):
                    raise ValueError("result set lineage source")
            if preview.parent_result_set_id is None:
                if preview.parent_result_set_digest is not None or len(source_ids) != 1:
                    raise ValueError("result set root lineage")
            else:
                parent_record = state["result_sets"].get(preview.parent_result_set_id)
                if (preview.parent_result_set_id == result_set_id or parent_record is None
                        or parent_record[1] >= published_version
                        or parent_record[0].result_set_digest != preview.parent_result_set_digest):
                    raise ValueError("result set parent")
                parent_sources = (parent_record[0].source_operation_ids
                                  or (parent_record[0].operation_id,))
                if source_ids != parent_sources + (preview.operation_id,):
                    raise ValueError("result set parent lineage")
        for task_id, result_set_id in state["current_result_sets"].items():
            record = state["result_sets"].get(result_set_id)
            if record is None:
                raise ValueError("current result set")
            approval = state["approvals"][record[0].operation_id]
            if approval.proposal.task_id != task_id:
                raise ValueError("current result task")
        for operation_id, result_set_id in state["published_results"].items():
            record = state["result_sets"].get(result_set_id)
            if record is None or record[0].operation_id != operation_id:
                raise ValueError("published result source")
        for key, value in state["keys"].items():
            if (type(key) is not tuple or len(key) != 4 or type(value) is not tuple
                    or len(value) != 2 or type(value[0]) is not str
                    or value[1] not in state["approvals"]):
                raise ValueError("idempotency")
            p = state["approvals"][value[1]].proposal
            if key[:3] != (p.tenant_id, p.owner_user_id, p.task_id):
                raise ValueError("key scope")
        expected = {"multi-rule-approved:" + opid:
                    {"event_id": "multi-rule-approved:" + opid,
                     "kind": "multi_rule_cleaning_approved", "operation_id": opid}
                    for opid in state["approvals"]}
        expected.update({"multi-rule-result:" + opid:
                         {"event_id": "multi-rule-result:" + opid,
                          "kind": "multi_rule_cleaning_result_published",
                          "operation_id": opid,
                          "task_id": result.binding.task_id,
                          "result_digest": result.result_digest}
                         for opid, result in state["results"].items()})
        expected.update({"result-set-published:" + result_set_id:
                         {"event_id": "result-set-published:" + result_set_id,
                          "kind": ("near_duplicate_review_applied"
                                   if preview.near_duplicate_decisions
                                   else "preview_result_set_published"),
                          "result_set_id": result_set_id,
                          "task_id": state["approvals"][preview.operation_id].proposal.task_id,
                          "accepted_count": preview.accepted_count,
                          "gap": preview.gap,
                          "stop_reason": preview.stop_reason}
                         for result_set_id, (preview, _) in state["result_sets"].items()})
        actual = {event.get("event_id"): event for event in state["events"]
                  if type(event) is dict and type(event.get("event_id")) is str}
        if len(actual) != len(state["events"]) or actual != expected:
            raise ValueError("events")
        return state
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        raise ServiceError("STORAGE_CORRUPT", "Invalid multi-rule approval ledger; refusing reset", 500) from exc


def purge_for_tasks(state, task_ids):
    """Drop every multi-rule cleaning record scoped to the given task ids, in place.

    ``load_state`` strictly revalidates ``events`` against the surviving
    approvals/results/result_sets, so events are rebuilt from those rather than
    just filtered. A no-op for an empty task set. Returns the mutated state.
    """
    if not task_ids:
        return state
    surviving_approvals = {opid: a for opid, a in state["approvals"].items()
                           if a.proposal.task_id not in task_ids}
    surviving_op_ids = set(surviving_approvals)
    state["approvals"] = surviving_approvals
    state["proposals"] = {d: p for d, p in state["proposals"].items()
                          if p.task_id not in task_ids}
    state["results"] = {opid: r for opid, r in state["results"].items()
                        if opid in surviving_op_ids}
    state["result_sets"] = {rsid: rec for rsid, rec in state["result_sets"].items()
                            if rec[0].operation_id in surviving_op_ids}
    state["current_result_sets"] = {tid: rsid for tid, rsid in state["current_result_sets"].items()
                                    if tid not in task_ids}
    state["published_results"] = {opid: rsid for opid, rsid in state["published_results"].items()
                                  if opid in surviving_op_ids}
    state["keys"] = {key: v for key, v in state["keys"].items()
                     if key[2] not in task_ids}
    expected = {"multi-rule-approved:" + opid:
                {"event_id": "multi-rule-approved:" + opid,
                 "kind": "multi_rule_cleaning_approved", "operation_id": opid}
                for opid in state["approvals"]}
    expected.update({"multi-rule-result:" + opid:
                     {"event_id": "multi-rule-result:" + opid,
                      "kind": "multi_rule_cleaning_result_published",
                      "operation_id": opid,
                      "task_id": result.binding.task_id,
                      "result_digest": result.result_digest}
                     for opid, result in state["results"].items()})
    expected.update({"result-set-published:" + result_set_id:
                     {"event_id": "result-set-published:" + result_set_id,
                      "kind": ("near_duplicate_review_applied"
                               if preview.near_duplicate_decisions
                               else "preview_result_set_published"),
                      "result_set_id": result_set_id,
                      "task_id": state["approvals"][preview.operation_id].proposal.task_id,
                      "accepted_count": preview.accepted_count,
                      "gap": preview.gap,
                      "stop_reason": preview.stop_reason}
                     for result_set_id, (preview, _) in state["result_sets"].items()})
    state["events"] = list(expected.values())
    return state


class SQLiteMultiRuleApprovalRepository:
    def __init__(self, path, *, projects, authorization):
        self.manifests = SQLiteCandidateManifestRepository(path, projects=projects,
                                                            authorization=authorization)
        self.plan_sets = SQLitePlanSetApprovalRepository(path, projects=projects,
                                                         authorization=authorization)
        self.tasks = self.manifests.tasks
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute('''CREATE TABLE IF NOT EXISTS cleaning_checkpoints (
                    operation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, payload TEXT NOT NULL)''')
                db.execute('''CREATE TABLE IF NOT EXISTS cleaning_dispatch_attempts (
                    operation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                    attempts INTEGER NOT NULL CHECK(attempts BETWEEN 1 AND 3),
                    status TEXT NOT NULL CHECK(status IN ('running','retry_wait','failed','completed')),
                    next_attempt_at REAL NOT NULL, token TEXT NOT NULL, code TEXT)''')
                db.execute('''CREATE TABLE IF NOT EXISTS auto_refill_outbox (
                    event_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL, task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, generation INTEGER NOT NULL,
                    task_version INTEGER NOT NULL, result_set_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(status IN ('pending','delivered')),
                    result_json TEXT, created_at TEXT NOT NULL, delivered_at TEXT)''')
                db.execute("CREATE INDEX IF NOT EXISTS auto_refill_pending_idx "
                           "ON auto_refill_outbox(status,created_at,event_id)")
                db.execute('''CREATE TABLE IF NOT EXISTS near_duplicate_review_keys (
                    tenant_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
                    task_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL, result_set_id TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,owner_user_id,task_id,idempotency_key))''')
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='multi_rule_cleaning_ledger'").fetchone()
                if exists:
                    load_state(db.execute("SELECT format,payload FROM multi_rule_cleaning_ledger WHERE id=1").fetchone())
                else:
                    db.execute("CREATE TABLE multi_rule_cleaning_ledger (id INTEGER PRIMARY KEY CHECK(id=1), format INTEGER NOT NULL, payload TEXT NOT NULL)")
                    db.execute("INSERT INTO multi_rule_cleaning_ledger VALUES (1,?,?)",
                               (FORMAT, dump_state(empty_state())))
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "Multi-rule approval ledger unavailable", 503,
                               retryable=True) from exc

    def _transaction(self, write, operation, *, with_connection=False):
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                memory = self.tasks._load(db.execute("SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                retrieval = load_retrieval(db.execute("SELECT format,payload FROM retrieval_ledger WHERE id=1").fetchone())
                candidates = load_candidates(db.execute("SELECT format,payload FROM candidate_ledger WHERE id=1").fetchone())
                candidates['_plan_sets'] = load_plan_sets(db.execute(
                    "SELECT format,payload FROM plan_set_ledger WHERE id=1").fetchone())
                state = load_state(db.execute("SELECT format,payload FROM multi_rule_cleaning_ledger WHERE id=1").fetchone())
                from ...delivery import load_delivery_records
                state['_deliveries'] = load_delivery_records(db)
                result = (operation(memory, retrieval, candidates, state, db) if with_connection
                          else operation(memory, retrieval, candidates, state))
                state.pop('_deliveries')
                if write:
                    db.execute("UPDATE local_ledger SET payload=? WHERE id=1", (dump(memory),))
                    db.execute("UPDATE multi_rule_cleaning_ledger SET payload=? WHERE id=1", (dump_state(state),))
                return result
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "Multi-rule approval transaction failed", 503,
                               retryable=True) from exc

    def _manifest(self, memory, retrieval, candidates, actor, task_id, manifest_ref, action, *, state=None):
        task = self.manifests.retrieval._authorize(memory, actor, task_id, action=action)
        manifest = candidates["manifests"].get(manifest_ref)
        if manifest is None:
            manifest = candidates['_plan_sets']['candidate_manifests'].get(manifest_ref)
        if manifest is None or manifest.proposal.scope.task_id != task_id:
            raise ServiceError("NOT_FOUND", "Candidate manifest not found", 404)
        self._check_manifest(memory, retrieval, candidates, actor, manifest, state=state)
        return task, manifest

    def _check_manifest(self, memory, retrieval, candidates, actor, manifest, *, state=None):
        delivery_read = False
        if state is not None:
            from ...delivery import current_delivery
            task = memory.get(actor, manifest.proposal.scope.task_id)
            source_id = state['current_result_sets'].get(task.task_id)
            delivery_read = current_delivery(task, state, source_id) is not None
            stopped = state['result_sets'].get(source_id)
            delivery_read = delivery_read or bool(
                stopped and task.stage == 'refill'
                and task.run_status == RunStatus.AWAITING_INPUT
                and stopped[0].gap > 0)
        if manifest.manifest_ref in candidates['manifests']:
            self.manifests._check(memory, retrieval, actor, manifest.proposal, reading=True, delivery_read=delivery_read)
        else:
            plan_state = candidates['_plan_sets']
            if (plan_state['candidate_manifests'].get(
                    manifest.manifest_ref) != manifest):
                raise ServiceError('NOT_FOUND', 'Candidate manifest not found', 404)
            self.plan_sets._validate_candidate_manifest(
                memory, plan_state, actor, manifest.proposal, reading=True, delivery_read=delivery_read)

    def get_manifest(self, actor, task_id, manifest_ref):
        def read(memory, retrieval, candidates, state):
            return self._manifest(memory, retrieval, candidates, actor,
                                  task_id, manifest_ref, 'read')[1]
        return self._transaction(False, read)

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

    def prepare(self, actor, task_id, manifest_ref, recipe: CleaningRecipe, *, detector_release_digest):
        if type(recipe) is not CleaningRecipe:
            raise ServiceError("RECIPE_INVALID", "Trusted cleaning recipe required", 422)
        if (not isinstance(detector_release_digest, str)
                or _RELEASE_DIGEST.fullmatch(detector_release_digest) is None):
            raise ServiceError("DETECTOR_RELEASE_INVALID",
                               "Trusted detector release digest required", 422)
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
                                           item.media.content_digest) for item in manifest.proposal.items),
                detector_release_digest)
            self._same_inputs(task, manifest, proposal)
            self._check_manifest(memory, retrieval, candidates, actor, manifest)
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
            self._check_manifest(memory, retrieval, candidates, actor, manifest)
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
            self._check_manifest(memory, retrieval, candidates, actor, manifest)
            return approval
        return self._transaction(False, get)

    def pending_approved_operations(self):
        """Return current approved work not yet published as a result set.

        The approval event and operation live in the same durable ledger, so
        this derived outbox cannot be lost between confirmation and worker
        notification.  A restarted dispatcher can safely call the idempotent
        pipeline again; saved results are reused and published previews replay.
        """

        def read(memory, retrieval, candidates, state):
            pending = []
            published = set(state['published_results'])
            for operation_id, approval in state['approvals'].items():
                if operation_id in published:
                    continue
                task = memory._tasks.get(approval.proposal.task_id)
                if task is None:
                    continue
                try:
                    self._current(task, approval)
                except ServiceError:
                    continue
                pending.append({
                    'tenant_id': approval.proposal.tenant_id,
                    'owner_user_id': approval.proposal.owner_user_id,
                    'task_id': approval.proposal.task_id,
                    'operation_id': operation_id,
                    'revision': approval.proposal.revision,
                    'generation': approval.proposal.generation,
                })
            return tuple(sorted(pending, key=lambda row: row['operation_id']))

        return self._transaction(False, read)

    def cleaning_dispatch_state(self, actor, task_id, operation_id, *, details=False):
        def read(memory, retrieval, candidates, state, db):
            task = self.manifests.retrieval._authorize(
                memory, actor, task_id, action='read')
            approval = state['approvals'].get(operation_id)
            if approval is None or approval.proposal.task_id != task_id:
                raise ServiceError('NOT_FOUND', 'Cleaning operation not found', 404)
            if operation_id in state['published_results']:
                source_id = state['current_result_sets'].get(task_id)
                publication = state['result_sets'].get(source_id)
                status = ('completed' if publication is not None
                    and operation_id in (publication[0].source_operation_ids
                                         or (publication[0].operation_id,))
                    and task.confirmed_revision == approval.proposal.revision
                    and task.run_generation == approval.proposal.generation
                    and self._publication_current(task, state, source_id, publication[1])
                    else 'superseded')
            else:
                try:
                    self._current(task, approval)
                    status = 'queued'
                except ServiceError:
                    status = 'superseded'
            row = db.execute('SELECT attempts,status,next_attempt_at,code FROM cleaning_dispatch_attempts WHERE operation_id=?',
                             (operation_id,)).fetchone()
            if status == 'queued' and row:
                status = row[1]
            if not details:
                return status
            result = {'status': status, 'operation_id': operation_id,
                      'attempts': row[0] if row else 0, 'max_attempts': 3}
            if status != 'superseded':
                current, manifest = self._manifest(memory, retrieval, candidates, actor,
                    task_id, approval.proposal.manifest_ref, 'read', state=state)
                self._same_inputs(current, manifest, approval.proposal)
                checkpoint = self._checkpoint_record(db, approval.job)
                saved = state['results'].get(operation_id)
                items = saved.candidates if saved is not None else checkpoint['completed'] if checkpoint else ()
                result['progress'] = {
                    'total': len(approval.job.candidates), 'completed': len(items),
                    'pending': len(approval.job.candidates) - len(items),
                    'passed': sum(item.verdict == 'pass' for item in items),
                    'rejected': sum(item.verdict == 'fail' for item in items),
                    'review': sum(item.verdict == 'unknown' for item in items),
                    'candidate_results': [{'candidate_id': item.candidate_id, 'verdict': item.verdict} for item in items],
                    'note': '逐图检测进度；通过数不是最终去重入选数。',
                }
                if checkpoint is not None:
                    result['execution'] = checkpoint['execution']
                    result['progress']['started_at'] = checkpoint['started_at']
                    result['progress']['updated_at'] = checkpoint['updated_at']
                    if checkpoint['partial_steps']:
                        result['progress']['partial_candidate'] = checkpoint['partial_candidate_id']
                        result['progress']['completed_nodes'] = [
                            {'step_id': step.step_id,
                             'verdict': ('fail' if any(rule.verdict == 'fail' for rule in verdicts)
                                         else 'unknown' if any(rule.verdict == 'unknown' for rule in verdicts)
                                         else 'pass'),
                             'reasons': [{'rule_id': rule.rule_id, 'reason_code': rule.reason_code,
                                          'reason_summary': rule.reason_summary}
                                         for rule in verdicts]}
                            for step, verdicts in zip(approval.job.recipe.steps,
                                                      checkpoint['partial_steps'])]
                        next_index = len(checkpoint['partial_steps'])
                        result['progress']['next_node'] = (
                            approval.job.recipe.steps[next_index].step_id
                            if next_index < len(approval.job.recipe.steps) else None)
            if row and status in ('retry_wait', 'running'):
                result['next_attempt_at'] = row[2]
            if row and status in ('retry_wait', 'failed'):
                result.update(code=row[3], retryable=status == 'retry_wait',
                              message='清洗执行失败；等待退避重试。' if status == 'retry_wait' else
                              '清洗已停止自动重试，请检查配置并重新确认新任务版本。')
            return result

        return self._transaction(False, read, with_connection=True)

    def claim_cleaning_attempt(self, actor, task_id, operation_id, now):
        """Reserve budget before external work; a crash cannot reset attempts.

        A one-hour recovery window prevents immediate restart replay of an
        in-flight call. This is a local recovery guard, not a renewable lease.
        """
        def claim(memory, retrieval, candidates, state, db):
            task = self.manifests.retrieval._authorize(memory, actor, task_id, action='write')
            approval = state['approvals'].get(operation_id)
            if approval is None or approval.proposal.task_id != task_id:
                raise ServiceError('NOT_FOUND', 'Cleaning operation not found', 404)
            if operation_id in state['published_results']:
                return None
            self._current(task, approval)
            row = db.execute('SELECT attempts,status,next_attempt_at FROM cleaning_dispatch_attempts WHERE operation_id=?',
                             (operation_id,)).fetchone()
            if row and (row[1] in ('failed', 'completed') or now < row[2]):
                return None
            attempts = row[0] if row else 0
            if attempts >= 3:
                db.execute("UPDATE cleaning_dispatch_attempts SET status='failed',code='RETRY_EXHAUSTED' WHERE operation_id=?",
                           (operation_id,))
                return None
            token = uuid.uuid4().hex
            db.execute('INSERT OR REPLACE INTO cleaning_dispatch_attempts VALUES (?,?,?,?,?,?,?)',
                       (operation_id, task_id, attempts + 1, 'running', now + 3600, token, None))
            return token
        return self._transaction(True, claim, with_connection=True)

    def finish_cleaning_attempt(self, actor, task_id, operation_id, token, now, *, code=None, retryable=False):
        def finish(memory, retrieval, candidates, state, db):
            task = self.manifests.retrieval._authorize(memory, actor, task_id, action='write')
            approval = state['approvals'].get(operation_id)
            if approval is None or approval.proposal.task_id != task_id:
                raise ServiceError('NOT_FOUND', 'Cleaning operation not found', 404)
            published = operation_id in state['published_results']
            if not published:
                self._current(task, approval)
            row = db.execute('SELECT attempts,token,status FROM cleaning_dispatch_attempts WHERE operation_id=?',
                             (operation_id,)).fetchone()
            if not row or row[1] != token or row[2] != 'running':
                return False
            # Never persist service exception text, which may contain secrets.
            safe_code = code if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code) else 'CLEANING_EXECUTION_ERROR'
            status = 'completed' if published else 'retry_wait' if retryable and row[0] < 3 else 'failed'
            db.execute('UPDATE cleaning_dispatch_attempts SET status=?,next_attempt_at=?,code=? WHERE operation_id=?',
                       (status, now + 5 * 2 ** (row[0] - 1) if status == 'retry_wait' else now,
                        None if published else 'RETRY_EXHAUSTED' if retryable and row[0] >= 3 else safe_code,
                        operation_id))
            return True
        return self._transaction(True, finish, with_connection=True)

    def _checkpoint_job(self, memory, retrieval, candidates, state, actor, task_id, operation_id):
        approval = state['approvals'].get(operation_id)
        if approval is None or approval.proposal.task_id != task_id:
            raise ServiceError('NOT_FOUND', 'Cleaning operation not found', 404)
        task, manifest = self._manifest(memory, retrieval, candidates, actor, task_id,
                                       approval.proposal.manifest_ref, 'write')
        self._current(task, approval)
        self._same_inputs(task, manifest, approval.proposal)
        require_confirmed_rules(task, approval.proposal.recipe)
        return approval.job

    @staticmethod
    def _checkpoint_record(db, job):
        row = db.execute('SELECT payload FROM cleaning_checkpoints WHERE operation_id=?',
                         (job.binding.operation_id,)).fetchone()
        if row is None:
            return None
        try:
            if len(row[0].encode()) > MAX_BYTES:
                raise ValueError('size')
            record = decode(json.loads(row[0]))
            if set(record) == {'binding', 'execution', 'completed', 'started_at', 'updated_at'}:
                record['partial_candidate_id'] = None
                record['partial_steps'] = ()
            if (set(record) != {'binding', 'execution', 'completed', 'partial_candidate_id',
                                'partial_steps', 'started_at', 'updated_at'}
                    or record['binding'] != job.binding
                    or type(record['completed']) is not tuple
                    or len(record['completed']) > len(job.candidates)
                    or type(record['partial_steps']) is not tuple
                    or len(record['partial_steps']) > len(job.recipe.steps)
                    or type(record['execution']) is not dict
                    or any(type(record[key]) not in (int, float) or not math.isfinite(record[key])
                           for key in ('started_at', 'updated_at'))):
                raise ValueError('binding')
            expected_rules = tuple(rule.rule_id for rule in job.recipe.rules)
            for candidate, verdict in zip(job.candidates, record['completed']):
                if (type(verdict) is not CleaningCandidateVerdict
                        or (candidate.candidate_id, candidate.asset_version) != (verdict.candidate_id, verdict.asset_version)
                        or tuple(rule.rule_id for rule in verdict.rules) != expected_rules):
                    raise ValueError('candidate')
            next_candidate = (job.candidates[len(record['completed'])]
                              if len(record['completed']) < len(job.candidates) else None)
            if (record['partial_candidate_id'] != (next_candidate.candidate_id if next_candidate
                    and record['partial_steps'] else None)):
                raise ValueError('partial candidate')
            for step, saved in zip(job.recipe.steps, record['partial_steps']):
                expected = tuple(rule.rule_id for rule in job.recipe.rules if rule.step_id == step.step_id)
                if (type(saved) is not tuple or tuple(item.rule_id for item in saved) != expected
                        or any(type(item) is not CleaningRuleVerdict for item in saved)):
                    raise ValueError('partial step')
            return record
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise ServiceError('STORAGE_CORRUPT', 'Invalid cleaning checkpoint', 500) from exc

    def start_cleaning_checkpoint(self, actor, task_id, operation_id, execution):
        # Only trusted application descriptors, never arbitrary model settings.
        if (type(execution) is not dict
                or set(execution) not in ({'backend_refs'},
                    {'backend_ref', 'instruction', 'prompt_version', 'config_digest', 'model'},
                    {'backend_ref', 'instruction', 'prompt_version', 'config_digest', 'model', 'nodes'})
                or len(json.dumps(execution, ensure_ascii=False)) > 12000):
            raise ValueError('Invalid execution snapshot')
        if 'model' in execution and (type(execution['model']) is not dict
                or set(execution['model']) != {'id', 'provider', 'name'}):
            raise ValueError('Invalid model snapshot')
        if 'nodes' in execution and (type(execution['nodes']) is not list
                or not 1 <= len(execution['nodes']) <= 8
                or any(type(node) is not dict or set(node) != {
                    'node_id', 'source', 'backend_ref', 'instruction'}
                    or any(not isinstance(node[key], str) or not node[key]
                           for key in ('node_id', 'source', 'backend_ref', 'instruction'))
                    for node in execution['nodes'])):
            raise ValueError('Invalid VLM node snapshot')
        def start(memory, retrieval, candidates, state, db):
            job = self._checkpoint_job(memory, retrieval, candidates, state, actor, task_id, operation_id)
            record = self._checkpoint_record(db, job)
            if record is not None:
                if record['execution'] != execution:
                    raise ServiceError('MODEL_BINDING_CHANGED', 'Execution snapshot changed', 409)
                return record['completed']
            now = time.time()
            record = dict(binding=job.binding, execution=execution, completed=(),
                          partial_candidate_id=None, partial_steps=(),
                          started_at=now, updated_at=now)
            db.execute('INSERT INTO cleaning_checkpoints VALUES (?,?,?)',
                       (operation_id, task_id, dump_state(record)))
            return ()
        return self._transaction(True, start, with_connection=True)

    def get_cleaning_step_checkpoint(self, actor, task_id, operation_id):
        def read(memory, retrieval, candidates, state, db):
            job = self._checkpoint_job(memory, retrieval, candidates, state,
                                       actor, task_id, operation_id)
            record = self._checkpoint_record(db, job)
            if record is None or not record['partial_steps']:
                return None
            return record['partial_candidate_id'], record['partial_steps']
        return self._transaction(False, read, with_connection=True)

    def save_cleaning_step_checkpoint(self, actor, task_id, operation_id,
                                      candidate, step, verdicts):
        def save(memory, retrieval, candidates, state, db):
            job = self._checkpoint_job(memory, retrieval, candidates, state,
                                       actor, task_id, operation_id)
            record = self._checkpoint_record(db, job)
            index = len(record['completed']) if record else -1
            position = len(record['partial_steps']) if record else -1
            if (record and index < len(job.candidates)
                    and candidate == job.candidates[index]
                    and step in job.recipe.steps):
                prior_index = job.recipe.steps.index(step)
                if prior_index < position:
                    if record['partial_steps'][prior_index] != verdicts:
                        raise ServiceError('RESULT_CONFLICT',
                                           'Step checkpoint changed', 409)
                    return False
            if (index >= len(job.candidates) or candidate != job.candidates[index]
                    or position >= len(job.recipe.steps)
                    or step != job.recipe.steps[position]
                    or type(verdicts) is not tuple
                    or tuple(item.rule_id for item in verdicts) != tuple(
                        rule.rule_id for rule in job.recipe.rules if rule.step_id == step.step_id)
                    or any(type(item) is not CleaningRuleVerdict for item in verdicts)):
                raise ServiceError('RESULT_INVALID', 'Step checkpoint out of order', 422)
            record['partial_candidate_id'] = candidate.candidate_id
            record['partial_steps'] += (verdicts,)
            record['updated_at'] = time.time()
            db.execute('UPDATE cleaning_checkpoints SET payload=? WHERE operation_id=?',
                       (dump_state(record), operation_id))
            return True
        return self._transaction(True, save, with_connection=True)

    def save_cleaning_checkpoint(self, actor, task_id, operation_id, verdict):
        def save(memory, retrieval, candidates, state, db):
            job = self._checkpoint_job(memory, retrieval, candidates, state, actor, task_id, operation_id)
            record = self._checkpoint_record(db, job)
            if record is None or type(verdict) is not CleaningCandidateVerdict:
                raise ServiceError('RESULT_INVALID', 'Complete candidate checkpoint required', 422)
            previous = next((item for item in record['completed'] if item.candidate_id == verdict.candidate_id), None)
            if previous is not None:
                if previous != verdict:
                    raise ServiceError('RESULT_CONFLICT', 'Candidate checkpoint changed', 409)
                return False
            index = len(record['completed'])
            if (index >= len(job.candidates)
                    or (verdict.candidate_id, verdict.asset_version) !=
                       (job.candidates[index].candidate_id, job.candidates[index].asset_version)
                    or tuple(rule.rule_id for rule in verdict.rules) != tuple(rule.rule_id for rule in job.recipe.rules)):
                raise ServiceError('RESULT_INVALID', 'Candidate checkpoint out of order or incomplete', 422)
            record['completed'] += (verdict,)
            record['partial_candidate_id'] = None
            record['partial_steps'] = ()
            record['updated_at'] = time.time()
            db.execute('UPDATE cleaning_checkpoints SET payload=? WHERE operation_id=?',
                       (dump_state(record), operation_id))
            return True
        return self._transaction(True, save, with_connection=True)

    def publish_result(self, actor, task_id, operation_id, result):
        if type(result) is not CleaningJobResult:
            raise ServiceError("RESULT_INVALID", "Complete cleaning result required", 422)

        def publish(memory, retrieval, candidates, state):
            approval = state["approvals"].get(operation_id)
            if approval is None or approval.proposal.task_id != task_id:
                raise ServiceError("NOT_FOUND", "Cleaning approval not found", 404)
            task, manifest = self._manifest(memory, retrieval, candidates, actor, task_id,
                                            approval.proposal.manifest_ref, "write")
            self._same_inputs(task, manifest, approval.proposal)
            self._current(task, approval)
            require_confirmed_rules(task, approval.proposal.recipe)
            self._check_manifest(memory, retrieval, candidates, actor, manifest)
            try:
                require_complete_cleaning_result(approval.job, result)
            except (TypeError, ValueError) as exc:
                raise ServiceError("RESULT_INVALID", "Cleaning result does not match approved job", 422) from exc
            previous = state["results"].get(operation_id)
            if previous is not None:
                if previous.result_digest != result.result_digest or previous != result:
                    raise ServiceError("RESULT_CONFLICT", "Cleaning operation already has another result", 409)
                return previous, True
            # Recheck all external authority and task fences immediately before
            # the local atomic result/event write.
            task, manifest = self._manifest(memory, retrieval, candidates, actor, task_id,
                                            approval.proposal.manifest_ref, "write")
            self._same_inputs(task, manifest, approval.proposal)
            self._current(task, approval)
            require_confirmed_rules(task, approval.proposal.recipe)
            self._check_manifest(memory, retrieval, candidates, actor, manifest)
            state["results"][operation_id] = result
            state["events"].append({"event_id": "multi-rule-result:" + operation_id,
                                    "kind": "multi_rule_cleaning_result_published",
                                    "operation_id": operation_id,
                                    "task_id": task_id,
                                    "result_digest": result.result_digest})
            return result, False

        return self._transaction(True, publish)

    def get_result(self, actor, task_id, operation_id):
        def get(memory, retrieval, candidates, state):
            approval = state["approvals"].get(operation_id)
            result = state["results"].get(operation_id)
            if approval is None or result is None or approval.proposal.task_id != task_id:
                raise ServiceError("NOT_FOUND", "Cleaning result not found", 404)
            task, manifest = self._manifest(memory, retrieval, candidates, actor, task_id,
                                            approval.proposal.manifest_ref, "read")
            self._same_inputs(task, manifest, approval.proposal)
            self._current(task, approval)
            require_confirmed_rules(task, approval.proposal.recipe)
            self._check_manifest(memory, retrieval, candidates, actor, manifest)
            require_complete_cleaning_result(approval.job, result)
            self._check_manifest(memory, retrieval, candidates, actor, manifest)
            return result

        return self._transaction(False, get)

    def step_results(self, actor, task_id):
        from ...cleaning_step_views import cleaning_step_view

        def read(memory, retrieval, candidates, state, db):
            task = self.manifests.retrieval._authorize(memory, actor, task_id, action='read')
            runs = []
            for operation_id, result in state['results'].items():
                approval = state['approvals'][operation_id]
                if (approval.proposal.task_id != task_id
                        or result.binding.revision != task.confirmed_revision
                        or result.binding.generation != task.run_generation):
                    continue
                current, manifest = self._manifest(memory, retrieval, candidates, actor,
                    task_id, approval.proposal.manifest_ref, 'read', state=state)
                self._same_inputs(current, manifest, approval.proposal)
                result_set_id = state['current_result_sets'].get(task_id)
                published = state['result_sets'].get(result_set_id)
                is_current_publication = (published is not None
                    and operation_id in (published[0].source_operation_ids
                                         or (published[0].operation_id,))
                    and self._publication_current(current, state, result_set_id, published[1])
                    and published[0].generation == current.run_generation
                    and approval.proposal.revision == current.confirmed_revision)
                if not is_current_publication:
                    self._current(current, approval)
                require_confirmed_rules(current, approval.proposal.recipe)
                self._check_manifest(memory, retrieval, candidates, actor, manifest, state=state)
                run = cleaning_step_view(approval.job, result)
                checkpoint = self._checkpoint_record(db, approval.job)
                if checkpoint is not None:
                    run['execution'] = checkpoint['execution']
                    run['started_at'] = checkpoint['started_at']
                    run['updated_at'] = checkpoint['updated_at']
                    run['elapsed_seconds'] = max(0, checkpoint['updated_at'] - checkpoint['started_at'])
                runs.append(run)
            return {'task_id': task_id, 'revision': task.confirmed_revision,
                    'generation': task.run_generation, 'runs': runs,
                    'available': bool(runs)}
        return self._transaction(False, read, with_connection=True)

    def get_published_candidate(self, actor, task_id, operation_id, candidate_id):
        """Resolve media only from the current, completely published result set."""
        def read(memory, retrieval, candidates, state):
            task = self.manifests.retrieval._authorize(memory, actor, task_id, action='read')
            result_set_id = state['current_result_sets'].get(task_id)
            published = state['result_sets'].get(result_set_id)
            if (published is None
                    or not self._publication_current(task, state, result_set_id, published[1])
                    or published[0].generation != task.run_generation
                    or operation_id not in (published[0].source_operation_ids
                                            or (published[0].operation_id,))):
                raise ServiceError('PREVIEW_UNAVAILABLE',
                                   'Cleaning candidate is not in the current result', 404)
            target, policy, node_mode = self._counting_binding(task)
            self._rebuild_result_set(memory, retrieval, candidates, state, actor, task_id,
                                     result_set_id, target, policy, node_mode)
            matches = []
            for source_operation_id in (published[0].source_operation_ids
                                        or (published[0].operation_id,)):
                approval = state['approvals'][source_operation_id]
                result = state['results'][source_operation_id]
                current, manifest = self._manifest(
                    memory, retrieval, candidates, actor, task_id,
                    approval.proposal.manifest_ref, 'read', state=state)
                self._same_inputs(current, manifest, approval.proposal)
                candidate = next((item for item in approval.job.candidates
                                  if item.candidate_id == candidate_id), None)
                verdict = next((item for item in result.candidates
                                if item.candidate_id == candidate_id), None)
                media = next((item.media for item in manifest.proposal.items
                              if item.candidate_id == candidate_id), None)
                if candidate is None and verdict is None and media is None:
                    continue
                if (candidate is None or verdict is None or media is None
                        or media.asset_version != candidate.asset_version
                        or media.content_digest != candidate.content_digest):
                    raise ServiceError('INPUT_SNAPSHOT_CHANGED',
                                       'Cleaning candidate binding changed', 409)
                matches.append((candidate, media.authorized_media_ref))
            if not matches or any(match != matches[0] for match in matches[1:]):
                raise ServiceError('PREVIEW_UNAVAILABLE',
                                   'Cleaning candidate is not in the current result', 404)
            return matches[0]
        return self._transaction(False, read)

    @staticmethod
    def _counting_binding(task):
        try:
            proposal = json.loads(task.confirmed_proposal_json)
            query = proposal["query_spec"]
            policy = query["refill_policy"]
            node_mode = proposal["node_modes"]["refill"]
        except (TypeError, ValueError, KeyError) as exc:
            raise ServiceError("TASK_CORRUPT", "Confirmed query is unavailable", 500) from exc
        if (query.get("media_type") != "image"
                or query.get("intra_batch_dedup") != {"policy_ref": "exact-content",
                                                       "config_version": "1"}
                or query.get("cross_batch_dedup") != {"scope": "none", "references": [],
                                                       "comparison_methods": [],
                                                       "reference_digest": None}
                or query.get("target_count") != task.target_count):
            raise ServiceError("FEATURE_NOT_SUPPORTED",
                               "Only image exact-content preview is supported", 422)
        try:
            decide_refill(target_count=task.target_count, accepted_count=0,
                          policy=policy, node_mode=node_mode)
        except ValueError as exc:
            raise ServiceError("FEATURE_NOT_SUPPORTED",
                               "Confirmed refill policy is not supported", 422) from exc
        return task.target_count, policy, node_mode

    @staticmethod
    def _publication_current(task, state, result_set_id, published_version):
        from ...delivery import publication_is_current
        return publication_is_current(task, state, result_set_id, published_version)

    @staticmethod
    def _source_evidence(manifest):
        evidence = {}
        for item in manifest.proposal.items:
            rows = {FusedSourceEvidence(
                item.candidate_id, hit.binding.binding_id,
                hit.binding.library_id, hit.binding.index_version,
                hit.vector_id, hit.score) for hit in item.hits}
            evidence[item.candidate_id] = tuple(sorted(rows, key=lambda row: (
                row.library_id, row.binding_id, row.index_version,
                type(row.vector_id).__name__, row.vector_id, row.score)))
        return evidence

    def _read_result_set(self, memory, retrieval, candidates, state, actor, task_id,
                         result_set_id):
        task = self.manifests.retrieval._authorize(memory, actor, task_id, action="read")
        record = state["result_sets"].get(result_set_id)
        if record is None or state["current_result_sets"].get(task_id) != result_set_id:
            raise ServiceError("NOT_FOUND", "Current result set not found", 404)
        preview, published_version = record
        approval = state["approvals"].get(preview.operation_id)
        result = state["results"].get(preview.operation_id)
        if (approval is None or result is None or approval.proposal.task_id != task_id
                or not self._publication_current(task, state, result_set_id, published_version)
                or task.confirmed_revision != approval.proposal.revision
                or task.run_generation != preview.generation):
            raise ServiceError("REVISION_CONFLICT", "Result set is no longer current", 409)
        target, policy, node_mode = self._counting_binding(task)
        self._rebuild_result_set(memory, retrieval, candidates, state, actor, task_id,
                                 result_set_id, target, policy, node_mode)
        view = self._result_set_view(result_set_id, preview, task.task_version)
        from ...delivery import current_delivery
        delivery = current_delivery(task, state, result_set_id)
        if delivery is not None:
            members = delivery['manifest']['members']
            if (delivery['source_digest'] != preview.result_set_digest
                    or delivery['source_version'] != published_version
                    or [(item['candidate_id'], item['asset_version'], item['content_digest']) for item in members]
                    != [(item.candidate_id, item.asset_version, item.content_digest) for item in preview.accepted]):
                raise ServiceError('STORAGE_CORRUPT', 'Delivery members differ from qualified result', 500)
            if delivery['status'] == 'published':
                view.update(kind='delivery', result_set_id=delivery['delivery_result_id'],
                            result_set_digest=delivery['manifest_digest'])
        return view

    def _rebuild_result_set(self, memory, retrieval, candidates, state, actor, task_id,
                            result_set_id, target, policy, node_mode, seen=None):
        """Reauthorize and deterministically rebuild one cumulative lineage."""
        seen = set() if seen is None else seen
        if result_set_id in seen:
            raise ServiceError("STORAGE_CORRUPT", "Result set lineage contains a cycle", 500)
        seen.add(result_set_id)
        record = state["result_sets"].get(result_set_id)
        if record is None:
            raise ServiceError("STORAGE_CORRUPT", "Result set lineage is missing", 500)
        preview = record[0]
        approval = state["approvals"].get(preview.operation_id)
        result = state["results"].get(preview.operation_id)
        if approval is None or result is None or approval.proposal.task_id != task_id:
            raise ServiceError("STORAGE_CORRUPT", "Result set source is missing", 500)
        current, manifest = self._manifest(memory, retrieval, candidates, actor, task_id,
                                            approval.proposal.manifest_ref, "read", state=state)
        self._same_inputs(current, manifest, approval.proposal)
        require_confirmed_rules(current, approval.proposal.recipe)
        self._check_manifest(memory, retrieval, candidates, actor, manifest, state=state)
        enriched = any(item.candidate_ids or item.sources
                       for item in preview.accepted + preview.reserve)
        rebuilt = build_exact_result_set(
            approval.job, result, target,
            legacy_input_only=(preview.parent_result_set_id is None and preview.gap > 0
                               and preview.stop_reason == "SEARCH_STRATEGIES_EXHAUSTED"),
            refill_policy=policy, node_mode=node_mode,
            legacy_lineage=not preview.source_operation_ids,
            source_evidence_by_candidate=(self._source_evidence(manifest)
                                          if enriched else None))
        if preview.parent_result_set_id is not None:
            parent = self._rebuild_result_set(
                memory, retrieval, candidates, state, actor, task_id,
                preview.parent_result_set_id, target, policy, node_mode, seen)
            if parent.result_set_digest != preview.parent_result_set_digest:
                raise ServiceError("STORAGE_CORRUPT", "Result set parent digest changed", 500)
            try:
                rebuilt = merge_exact_result_sets(
                    preview.parent_result_set_id, parent, rebuilt,
                    refill_policy=policy, node_mode=node_mode)
            except ValueError as exc:
                raise ServiceError("STORAGE_CORRUPT",
                                   "Result set lineage cannot be merged", 500) from exc
        try:
            for decision in preview.near_duplicate_decisions:
                rebuilt = apply_near_duplicate_decision(
                    rebuilt, decision, refill_policy=policy, node_mode=node_mode)
        except ValueError as exc:
            raise ServiceError("STORAGE_CORRUPT",
                               "Near-duplicate review cannot be rebuilt", 500) from exc
        if rebuilt != preview:
            raise ServiceError("STORAGE_CORRUPT", "Published result set cannot be rebuilt", 500)
        self._check_manifest(memory, retrieval, candidates, actor, manifest, state=state)
        seen.remove(result_set_id)
        return preview

    @staticmethod
    def _result_set_view(result_set_id, preview, published_version):
        clusters = near_duplicate_clusters(preview)
        merged_near_duplicates = any(
            decision.action == "merge" for decision in preview.near_duplicate_decisions)
        return {"result_set_id": result_set_id, "kind": "preview", "status": "published",
                "as_of_task_version": published_version, "operation_id": preview.operation_id,
                "generation": preview.generation, "result_set_digest": preview.result_set_digest,
                "policy_ref": ("exact-content+owner-near-duplicate@1"
                               if merged_near_duplicates else "exact-content@1"),
                "selection_ref": ("owner-near-duplicate-review@1"
                                  if merged_near_duplicates
                                  else "candidate-identity-order@1"),
                "target_count": preview.target_count,
                "parent_result_set_id": preview.parent_result_set_id,
                "source_operation_ids": (preview.source_operation_ids
                                         or (preview.operation_id,)),
                "eligible_unique_count": len(preview.accepted) + len(preview.reserve),
                "accepted_count": preview.accepted_count, "reserve_count": len(preview.reserve),
                "duplicate_count": preview.duplicate_count, "failed_count": preview.fail_count,
                "pending_review_count": preview.unknown_count,
                "near_duplicate_review_count": len(preview.near_duplicate_pairs),
                "near_duplicate_pairs": preview.near_duplicate_pairs,
                "near_duplicate_cluster_count": len(clusters),
                "near_duplicate_clusters": clusters,
                "near_duplicate_decisions": preview.near_duplicate_decisions,
                "currently_accessible_count": preview.accepted_count, "counts_status": "fresh",
                "gap": preview.gap, "stop_reason": preview.stop_reason,
                "accepted": tuple(preview.accepted), "reserve": tuple(preview.reserve),
                "refill_dispatched": False}

    def publish_preview(self, actor, task_id, operation_id):
        def publish(memory, retrieval, candidates, state, db):
            task = self.manifests.retrieval._authorize(memory, actor, task_id, action="write")
            existing_id = state["published_results"].get(operation_id)
            if existing_id is not None:
                return self._read_result_set(memory, retrieval, candidates, state, actor,
                                             task_id, existing_id), True
            approval = state["approvals"].get(operation_id)
            result = state["results"].get(operation_id)
            if approval is None or result is None or approval.proposal.task_id != task_id:
                raise ServiceError("NOT_FOUND", "Complete cleaning result not found", 404)
            task, manifest = self._manifest(memory, retrieval, candidates, actor, task_id,
                                            approval.proposal.manifest_ref, "write")
            self._same_inputs(task, manifest, approval.proposal)
            self._current(task, approval)
            require_confirmed_rules(task, approval.proposal.recipe)
            self._check_manifest(memory, retrieval, candidates, actor, manifest)
            target, policy, node_mode = self._counting_binding(task)
            preview = build_exact_result_set(
                approval.job, result, target, refill_policy=policy, node_mode=node_mode,
                source_evidence_by_candidate=self._source_evidence(manifest))
            parent_result_set_id = state["current_result_sets"].get(task_id)
            if parent_result_set_id is not None:
                parent_record = state["result_sets"].get(parent_result_set_id)
                if parent_record is None:
                    raise ServiceError("STORAGE_CORRUPT", "Current result lineage is missing", 500)
                parent_approval = state["approvals"].get(parent_record[0].operation_id)
                if (parent_approval is None
                        or parent_approval.proposal.revision != approval.proposal.revision
                        or parent_approval.proposal.generation != approval.proposal.generation
                        or parent_record[1] >= approval.task_version):
                    raise ServiceError("REVISION_CONFLICT",
                                       "Prior result cannot be extended by this cleaning run", 409)
                parent = self._rebuild_result_set(
                    memory, retrieval, candidates, state, actor, task_id,
                    parent_result_set_id, target, policy, node_mode)
                try:
                    preview = merge_exact_result_sets(
                        parent_result_set_id, parent, preview,
                        refill_policy=policy, node_mode=node_mode)
                except ValueError as exc:
                    raise ServiceError("REVISION_CONFLICT",
                                       "Prior result cannot be merged with this batch", 409) from exc
            # Final authority/fence check immediately before the atomic local write.
            task, manifest = self._manifest(memory, retrieval, candidates, actor, task_id,
                                            approval.proposal.manifest_ref, "write")
            self._same_inputs(task, manifest, approval.proposal)
            self._current(task, approval)
            self._check_manifest(memory, retrieval, candidates, actor, manifest)
            authoritative = memory._tasks[task_id]
            authoritative.task_version += 1
            authoritative.stage = "refill"
            authoritative.active_cleaning_operation_id = None
            authoritative.active_plan_digest = None
            result_set_id = "resultset_" + uuid.uuid4().hex
            state["result_sets"][result_set_id] = (preview, authoritative.task_version)
            state["current_result_sets"][task_id] = result_set_id
            state["published_results"][operation_id] = result_set_id
            state["events"].append({"event_id": "result-set-published:" + result_set_id,
                                    "kind": "preview_result_set_published",
                                    "result_set_id": result_set_id, "task_id": task_id,
                                    "accepted_count": preview.accepted_count, "gap": preview.gap,
                                    "stop_reason": preview.stop_reason})
            if (preview.gap > 0 and policy["mode"] == "maintain_target"
                    and policy["progression"] == "auto" and node_mode == "auto"):
                created_at = datetime.now(UTC).isoformat()
                db.execute('''INSERT OR IGNORE INTO auto_refill_outbox
                    (event_id,tenant_id,owner_user_id,task_id,revision,generation,
                     task_version,result_set_id,status,created_at)
                    VALUES (?,?,?,?,?,?,?,?,'pending',?)''',
                    ("auto-refill:" + result_set_id, actor.tenant_id, actor.user_id,
                     task_id, authoritative.confirmed_revision,
                     authoritative.run_generation, authoritative.task_version,
                     result_set_id, created_at))
            return self._result_set_view(result_set_id, preview, authoritative.task_version), False
        return self._transaction(True, publish, with_connection=True)

    def pending_auto_refill_events(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        try:
            with closing(self.tasks._connect()) as db:
                rows = db.execute('''SELECT event_id,tenant_id,owner_user_id,task_id,
                    revision,generation,task_version,result_set_id
                    FROM auto_refill_outbox WHERE status='pending'
                    ORDER BY created_at,event_id LIMIT ?''', (limit,)).fetchall()
            keys = ("event_id", "tenant_id", "owner_user_id", "task_id", "revision",
                    "generation", "task_version", "result_set_id")
            return tuple(dict(zip(keys, row)) for row in rows)
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "auto refill outbox unavailable", 503,
                               retryable=True) from exc

    def mark_auto_refill_delivered(self, event_id, result):
        if not isinstance(event_id, str) or not event_id or not isinstance(result, dict):
            raise ValueError("event id and result are required")
        try:
            payload = json.dumps(result, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("auto refill result must be canonical JSON") from None
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                updated = db.execute('''UPDATE auto_refill_outbox
                    SET status='delivered',result_json=?,delivered_at=?
                    WHERE event_id=? AND status='pending' ''',
                    (payload, datetime.now(UTC).isoformat(), event_id)).rowcount
                if not updated:
                    row = db.execute("SELECT result_json FROM auto_refill_outbox "
                                     "WHERE event_id=? AND status='delivered'",
                                     (event_id,)).fetchone()
                    if row is None:
                        raise ServiceError("NOT_FOUND", "auto refill event was not found", 404)
                    return json.loads(row[0])
            return result
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "auto refill update failed", 503,
                               retryable=True) from exc

    @staticmethod
    def _near_duplicate_review_view(result_set_id, preview, published_version):
        representatives = {item.candidate_id: item
                           for item in preview.accepted + preview.reserve}
        clusters = []
        for cluster in near_duplicate_clusters(preview):
            members = []
            for candidate_id in cluster.candidate_ids:
                ref = representatives[candidate_id]
                fingerprint = ref.fingerprint
                members.append({
                    "candidate_id": candidate_id,
                    "asset_version": ref.asset_version,
                    "width": fingerprint.width if fingerprint else None,
                    "height": fingerprint.height if fingerprint else None,
                    "sharpness": fingerprint.sharpness if fingerprint else None,
                    "recommended": candidate_id == cluster.recommended_candidate_id,
                })
            clusters.append({
                "cluster_id": cluster.cluster_id,
                "candidate_ids": list(cluster.candidate_ids),
                "recommended_candidate_id": cluster.recommended_candidate_id,
                "members": members,
                "pairs": [{"candidate_id": pair.candidate_id,
                           "other_candidate_id": pair.other_candidate_id,
                           "hamming_distance": pair.hamming_distance,
                           "review_threshold": pair.review_threshold,
                           "policy_ref": pair.policy_ref}
                          for pair in cluster.pairs],
            })
        return {
            "result_set_id": result_set_id,
            "result_set_digest": preview.result_set_digest,
            "task_version": published_version,
            "pending_cluster_count": len(clusters),
            "clusters": clusters,
            "decisions": [{"cluster_id": decision.cluster_id,
                           "candidate_ids": list(decision.candidate_ids),
                           "action": decision.action,
                           "representative_candidate_id":
                               decision.representative_candidate_id}
                          for decision in preview.near_duplicate_decisions],
            "counts": {"accepted": preview.accepted_count,
                       "reserve": len(preview.reserve),
                       "duplicate": preview.duplicate_count,
                       "gap": preview.gap},
        }

    def near_duplicate_reviews(self, actor, task_id):
        def read(memory, retrieval, candidates, state):
            result_set_id = state["current_result_sets"].get(task_id)
            if result_set_id is None:
                raise ServiceError("NOT_FOUND", "Current result set not found", 404)
            self._read_result_set(memory, retrieval, candidates, state, actor,
                                  task_id, result_set_id)
            preview, published_version = state["result_sets"][result_set_id]
            return self._near_duplicate_review_view(
                result_set_id, preview, published_version)
        return self._transaction(False, read)

    def review_near_duplicate_cluster(self, actor, task_id, *, key,
                                      expected_task_version, result_set_digest,
                                      cluster_id, action,
                                      representative_candidate_id=None):
        require_key(key)
        require_version(expected_task_version)
        if (not isinstance(result_set_digest, str)
                or not result_set_digest.startswith("sha256:")
                or not isinstance(cluster_id, str) or not cluster_id.startswith("ndc_")
                or action not in {"merge", "keep_separate"}
                or representative_candidate_id is not None
                and (not isinstance(representative_candidate_id, str)
                     or not representative_candidate_id)):
            raise ServiceError("INVALID_SPEC", "Invalid near-duplicate review", 422)
        request = {"expected_task_version": expected_task_version,
                   "result_set_digest": result_set_digest,
                   "cluster_id": cluster_id, "action": action,
                   "representative_candidate_id": representative_candidate_id}
        request_digest = digest(request)
        def review(memory, retrieval, candidates, state, db):
            replay = db.execute('''SELECT request_digest,result_set_id
                FROM near_duplicate_review_keys
                WHERE tenant_id=? AND owner_user_id=? AND task_id=? AND idempotency_key=?''',
                (actor.tenant_id, actor.user_id, task_id, key)).fetchone()
            if replay is not None:
                if replay[0] != request_digest:
                    raise ServiceError("IDEMPOTENCY_CONFLICT",
                                       "Idempotency key was used for another review", 409)
                record = state["result_sets"].get(replay[1])
                if record is None:
                    raise ServiceError("STORAGE_CORRUPT",
                                       "Reviewed result set is missing", 500)
                return self._near_duplicate_review_view(replay[1], *record), True
            task = self.manifests.retrieval._authorize(
                memory, actor, task_id, action="approve")
            if (task.stage != "refill"
                    or task.run_status not in (RunStatus.RUNNING,
                                               RunStatus.AWAITING_INPUT)):
                raise ServiceError("REVISION_CONFLICT",
                                   "Task is not awaiting result review", 409)
            result_set_id = state["current_result_sets"].get(task_id)
            record = state["result_sets"].get(result_set_id)
            if (record is None or task.task_version != expected_task_version
                    or record[0].result_set_digest != result_set_digest
                    or record[1] != expected_task_version):
                raise ServiceError("REVISION_CONFLICT",
                                   "Result changed before near-duplicate review", 409)
            if state.get("_deliveries", {}).get(result_set_id) is not None:
                raise ServiceError("REVISION_CONFLICT",
                                   "Packaged results cannot be reviewed", 409)
            preview = record[0]
            cluster = next((item for item in near_duplicate_clusters(preview)
                            if item.cluster_id == cluster_id), None)
            if cluster is None:
                raise ServiceError("REVISION_CONFLICT",
                                   "Near-duplicate cluster is no longer pending", 409)
            if action == "merge":
                representative = representative_candidate_id
                if representative not in cluster.candidate_ids:
                    raise ServiceError("INVALID_SPEC",
                                       "Representative must belong to the cluster", 422)
            elif representative_candidate_id is not None:
                raise ServiceError("INVALID_SPEC",
                                   "Keep-separate review has no representative", 422)
            else:
                representative = None
            target, policy, node_mode = self._counting_binding(task)
            decision = NearDuplicateDecision(
                cluster.cluster_id, cluster.candidate_ids, action, representative)
            reviewed = apply_near_duplicate_decision(
                preview, decision, refill_policy=policy, node_mode=node_mode)
            authoritative = memory._tasks[task_id]
            authoritative.task_version += 1
            new_result_set_id = "resultset_" + uuid.uuid4().hex
            state["result_sets"][new_result_set_id] = (
                reviewed, authoritative.task_version)
            state["current_result_sets"][task_id] = new_result_set_id
            state["published_results"][reviewed.operation_id] = new_result_set_id
            state["events"].append({
                "event_id": "result-set-published:" + new_result_set_id,
                "kind": "near_duplicate_review_applied",
                "result_set_id": new_result_set_id, "task_id": task_id,
                "accepted_count": reviewed.accepted_count, "gap": reviewed.gap,
                "stop_reason": reviewed.stop_reason})
            stop_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                    "AND name='refill_execution_stops'").fetchone()
            stop = (db.execute('''SELECT stop_reason FROM refill_execution_stops
                WHERE task_id=? AND revision=? AND generation=?''',
                (task_id, task.confirmed_revision, task.run_generation)).fetchone()
                    if stop_table else None)
            if (reviewed.gap > 0 and stop is None
                    and policy["mode"] == "maintain_target"
                    and policy["progression"] == "auto" and node_mode == "auto"):
                created_at = datetime.now(UTC).isoformat()
                db.execute('''INSERT OR IGNORE INTO auto_refill_outbox
                    (event_id,tenant_id,owner_user_id,task_id,revision,generation,
                     task_version,result_set_id,status,created_at)
                    VALUES (?,?,?,?,?,?,?,?,'pending',?)''',
                    ("auto-refill:" + new_result_set_id, actor.tenant_id,
                     actor.user_id, task_id, authoritative.confirmed_revision,
                     authoritative.run_generation, authoritative.task_version,
                     new_result_set_id, created_at))
            db.execute('''INSERT INTO near_duplicate_review_keys VALUES (?,?,?,?,?,?)''',
                       (actor.tenant_id, actor.user_id, task_id, key,
                        request_digest, new_result_set_id))
            return self._near_duplicate_review_view(
                new_result_set_id, reviewed, authoritative.task_version), False
        return self._transaction(True, review, with_connection=True)

    def get_current_result_set(self, actor, task_id):
        def get(memory, retrieval, candidates, state, db):
            result_set_id = state["current_result_sets"].get(task_id)
            if result_set_id is None:
                raise ServiceError("NOT_FOUND", "Current result set not found", 404)
            view = self._read_result_set(memory, retrieval, candidates, state, actor,
                                         task_id, result_set_id)
            task = memory.get(actor, task_id)
            exists = db.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                AND name='refill_execution_stops'""").fetchone()
            if exists:
                row = db.execute("""SELECT stop_reason FROM refill_execution_stops
                    WHERE task_id=? AND revision=? AND generation=?""",
                    (task_id, task.confirmed_revision, task.run_generation)).fetchone()
                if row is not None and view["gap"] > 0:
                    view = {**view, "stop_reason": row[0], "refill_dispatched": False}
            return view
        return self._transaction(False, get, with_connection=True)

    def finalize_refill_stop(self, actor, task_id, reason):
        """Move a durable automatic stop to the explicit owner-decision gate.

        The run-status gate fences late writers. The immutable preview keeps
        its publication version so it remains eligible for explicit delivery.
        """
        allowed = {"EXECUTION_LIMIT_REACHED", "VLM_CALL_LIMIT_REACHED",
                   "NO_PROGRESS", "SEARCH_STRATEGIES_EXHAUSTED"}
        if reason not in allowed:
            raise ValueError("invalid refill stop reason")
        def finalize(memory, retrieval, candidates, state, db):
            task = self.manifests.retrieval._authorize(
                memory, actor, task_id, action="approve")
            row = db.execute("""SELECT stop_reason FROM refill_execution_stops
                WHERE task_id=? AND revision=? AND generation=?""",
                (task_id, task.confirmed_revision, task.run_generation)).fetchone()
            if row is None or row[0] != reason:
                raise ServiceError("REVISION_CONFLICT", "Refill stop is not current", 409)
            result_set_id = state["current_result_sets"].get(task_id)
            record = state["result_sets"].get(result_set_id)
            if record is None:
                raise ServiceError("NOT_FOUND", "Current result set not found", 404)
            preview, published_version = record
            if preview.gap <= 0:
                raise ServiceError("REVISION_CONFLICT", "Refill target is already met", 409)
            if task.run_status == RunStatus.AWAITING_INPUT and task.stage == "refill":
                return False
            if task.run_status != RunStatus.RUNNING or task.stage != "refill":
                raise ServiceError("REVISION_CONFLICT", "Task is not awaiting refill finalization", 409)
            if not self._publication_current(task, state, result_set_id, published_version):
                raise ServiceError("REVISION_CONFLICT", "Qualified result is no longer current", 409)
            authoritative = memory._tasks[task_id]
            authoritative.run_status = RunStatus.AWAITING_INPUT
            return True
        return self._transaction(True, finalize, with_connection=True)
