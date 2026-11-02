"""Memory ledger methods sharing the task repository's lock/transaction boundary."""
from dataclasses import replace
import json
import uuid

from ..cleaning_models import CleaningOperation
from ..errors import ServiceError
from ..models import RunStatus
from ..query_spec import digest


class MemoryCleaningMixin:
    def _init_cleaning(self):
        self._cleaning_proposals = {}
        self._cleaning_operations = {}
        self._cleaning_keys = {}
        self._cleaning_proposal_ops = {}
        self._cleaning_results = {}
        self._cleaning_events = []
        self._cleaning_cancel_keys = {}

    def _purge_cleaning_for_tasks(self, task_ids):
        """Drop every cleaning record scoped to the given task ids, in place.

        Operation/proposal/cancel-key indexes are rebuilt from the surviving
        operations so the next read sees a consistent ledger. Returns a summary
        of how many records were removed; an empty task set is a no-op.
        """
        if not task_ids:
            return {'cleaning_proposals': 0, 'cleaning_operations': 0,
                    'cleaning_results': 0, 'cleaning_keys': 0,
                    'cleaning_proposal_ops': 0, 'cleaning_events': 0,
                    'cleaning_cancel_keys': 0}
        before_proposals = len(self._cleaning_proposals)
        before_ops = len(self._cleaning_operations)
        before_results = len(self._cleaning_results)
        before_keys = len(self._cleaning_keys)
        before_proposal_ops = len(self._cleaning_proposal_ops)
        before_events = len(self._cleaning_events)
        before_cancel_keys = len(self._cleaning_cancel_keys)

        surviving_ops = {op_id for op_id, op in self._cleaning_operations.items()
                         if op.proposal.task_id not in task_ids}
        self._cleaning_proposals = {pid: p for pid, p in self._cleaning_proposals.items()
                                    if p.task_id not in task_ids}
        self._cleaning_operations = {op_id: op for op_id, op in self._cleaning_operations.items()
                                     if op_id in surviving_ops}
        self._cleaning_proposal_ops = {pid: op_id for pid, op_id in self._cleaning_proposal_ops.items()
                                       if op_id in surviving_ops}
        for task_id in task_ids:
            self._cleaning_results.pop(task_id, None)
        self._cleaning_keys = {scope: value for scope, value in self._cleaning_keys.items()
                               if scope[2] not in task_ids}
        self._cleaning_events = [(op_id, status) for op_id, status in self._cleaning_events
                                 if op_id in surviving_ops]
        self._cleaning_cancel_keys = {scope: value for scope, value in self._cleaning_cancel_keys.items()
                                      if scope[2] in surviving_ops}
        return {
            'cleaning_proposals': before_proposals - len(self._cleaning_proposals),
            'cleaning_operations': before_ops - len(self._cleaning_operations),
            'cleaning_results': before_results - len(self._cleaning_results),
            'cleaning_keys': before_keys - len(self._cleaning_keys),
            'cleaning_proposal_ops': before_proposal_ops - len(self._cleaning_proposal_ops),
            'cleaning_events': before_events - len(self._cleaning_events),
            'cleaning_cancel_keys': before_cancel_keys - len(self._cleaning_cancel_keys),
        }

    def put_cleaning_proposal(self, actor, proposal):
        with self._lock:
            task = self.get(actor, proposal.task_id)
            if (task.tenant_id, task.owner_user_id, task.project_id, task.confirmed_revision,
                task.run_generation, task.task_version) != (
                proposal.tenant_id, proposal.owner_user_id, proposal.project_id, proposal.revision,
                proposal.generation, proposal.task_version):
                raise ServiceError("REVISION_CONFLICT", "proposal scope is stale", 409)
            if task.run_status not in {RunStatus.PLANNING, RunStatus.RUNNING}:
                raise ServiceError("INVALID_STATE_TRANSITION", "task cannot prepare cleaning", 409)
            self._cleaning_proposals[proposal.proposal_id] = proposal

    def get_cleaning_proposal(self, actor, proposal_id):
        with self._lock:
            proposal = self._cleaning_proposals.get(proposal_id)
            if proposal is None:
                raise ServiceError("NOT_FOUND", "cleaning proposal not found", 404)
            self.get(actor, proposal.task_id)
            return proposal

    def get_cleaning_operation(self, actor, operation_id):
        with self._lock:
            operation = self._cleaning_operations.get(operation_id)
            if operation is None:
                raise ServiceError("NOT_FOUND", "cleaning operation not found", 404)
            self.get(actor, operation.proposal.task_id)
            return operation

    def reserve_cleaning(self, actor, key, proposal_id, proposal_digest, expected_version):
        with self._lock:
            p = self.get_cleaning_proposal(actor, proposal_id)
            scope = (actor.tenant_id, actor.user_id, p.task_id, key)
            fingerprint = digest({"proposal_id": proposal_id, "digest": proposal_digest, "version": expected_version})
            if scope in self._cleaning_keys:
                old, operation_id = self._cleaning_keys[scope]
                if old != fingerprint:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "key reused with different cleaning approval", 409)
                return self._cleaning_operations[operation_id], True
            if proposal_digest != p.plan_digest or expected_version != p.task_version:
                raise ServiceError("PROPOSAL_CONFLICT", "confirm the displayed cleaning proposal", 409)
            if proposal_id in self._cleaning_proposal_ops:
                operation_id = self._cleaning_proposal_ops[proposal_id]
                self._cleaning_keys[scope] = (fingerprint, operation_id)
                return self._cleaning_operations[operation_id], True
            task = self._tasks[p.task_id]
            if (task.task_version, task.confirmed_revision, task.run_generation) != (p.task_version, p.revision, p.generation):
                raise ServiceError("REVISION_CONFLICT", "cleaning proposal is stale", 409)
            if task.run_status not in {RunStatus.PLANNING, RunStatus.RUNNING}:
                raise ServiceError("INVALID_STATE_TRANSITION", "task cannot start cleaning", 409)
            if task.active_cleaning_operation_id:
                active = self._cleaning_operations[task.active_cleaning_operation_id]
                if active.status not in {"succeeded", "failed", "cancelled"}:
                    raise ServiceError("CLEANING_IN_PROGRESS", "another cleaning operation is active", 409)
            operation = CleaningOperation("clean_" + uuid.uuid4().hex, p)
            self._cleaning_operations[operation.operation_id] = operation
            self._cleaning_proposal_ops[proposal_id] = operation.operation_id
            self._cleaning_keys[scope] = (fingerprint, operation.operation_id)
            task.active_cleaning_operation_id = operation.operation_id
            task.active_plan_digest = p.plan_digest
            task.run_status = RunStatus.RUNNING
            task.stage = "cleaning"
            task.task_version += 1
            self._cleaning_results.pop(task.task_id, None)
            return operation, False

    def _check_cleaning_fence(self, operation):
        task = self._tasks[operation.proposal.task_id]
        p = operation.proposal
        if (task.run_generation, task.confirmed_revision, task.active_plan_digest, task.active_cleaning_operation_id) != (
                p.generation, p.revision, p.plan_digest, operation.operation_id) or task.run_status != RunStatus.RUNNING:
            raise ServiceError("STALE_GENERATION", "cleaning result no longer has publication rights", 409)

    def attach_cleaning_backend(self, actor, operation_id, backend):
        with self._lock:
            operation = self.get_cleaning_operation(actor, operation_id)
            self._check_cleaning_fence(operation)
            if operation.status == "queued":
                operation = replace(operation, backend_json=json.dumps(backend, sort_keys=True), status="running")
                self._cleaning_operations[operation_id] = operation
            return operation

    def publish_cleaning(self, actor, operation_id, response):
        with self._lock:
            operation = self.get_cleaning_operation(actor, operation_id)
            self._check_cleaning_fence(operation)
            if response.get("binding") != operation.binding():
                raise ServiceError("RESULT_BINDING_MISMATCH", "worker scope/digests do not match", 409)
            if response.get("status") not in ("succeeded", "failed"):
                raise ServiceError("INVALID_CLEANING_RESULT", "only terminal results may publish", 422)
            rows = response.get("results")
            expected = {c.candidate_id: c.asset_version for c in operation.proposal.candidates}
            if not isinstance(rows, list) or len(rows) != len(expected):
                raise ServiceError("RESULT_COVERAGE_MISMATCH", "every input needs exactly one result", 422)
            seen = set()
            for row in rows:
                if (not isinstance(row, dict) or not isinstance(row.get("candidate_id"), str)
                        or row["candidate_id"] in seen):
                    raise ServiceError("RESULT_COVERAGE_MISMATCH", "duplicate or malformed result", 422)
                cid = row.get("candidate_id")
                if cid not in expected or row.get("asset_version") != expected[cid]:
                    raise ServiceError("RESULT_COVERAGE_MISMATCH", "unknown candidate or changed asset version", 422)
                seen.add(cid)
                if row.get("verdict") not in ("pass", "fail", "unknown"):
                    raise ServiceError("INVALID_CLEANING_RESULT", "invalid verdict", 422)
                if row["verdict"] in ("pass", "fail") and (
                        not isinstance(row.get("evidence_ref"), str) or not row["evidence_ref"]):
                    raise ServiceError("INVALID_CLEANING_RESULT", "pass/fail requires evidence", 422)
                if response["status"] == "failed" and row["verdict"] != "unknown":
                    raise ServiceError("INVALID_CLEANING_RESULT", "failed execution cannot certify pass/fail", 422)
            payload = json.dumps(response, sort_keys=True, ensure_ascii=False)
            if operation.result_json is not None:
                if operation.result_json != payload:
                    raise ServiceError("RESULT_CONFLICT", "terminal result cannot be overwritten", 409)
                return operation
            operation = replace(operation, result_json=payload, status=response["status"])
            self._cleaning_operations[operation_id] = operation
            self._cleaning_results[operation.proposal.task_id] = operation_id
            self._cleaning_events.append((operation_id, response["status"]))
            task = self._tasks[operation.proposal.task_id]
            task.stage = "cleaning_finished" if operation.status == "succeeded" else "cleaning_failed"
            task.task_version += 1
            # Cleaning success never claims final business completion or dedup acceptance.
            return operation

    def cancel_cleaning(self, actor, operation_id, key, expected_version):
        with self._lock:
            operation = self.get_cleaning_operation(actor, operation_id)
            scope = (actor.tenant_id, actor.user_id, operation_id, key)
            if scope in self._cleaning_cancel_keys:
                if self._cleaning_cancel_keys[scope] != expected_version:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "cancel key reused with different version", 409)
                return operation
            if operation.status in {"succeeded", "failed", "cancelled"}:
                return operation
            task = self._tasks[operation.proposal.task_id]
            if task.task_version != expected_version:
                raise ServiceError("REVISION_CONFLICT", "task version is stale", 409)
            self._check_cleaning_fence(operation)
            task.run_generation += 1
            task.task_version += 1
            task.run_status = RunStatus.PAUSED
            task.stage = "cleaning_cancelled"
            operation = replace(operation, status="cancelled")
            self._cleaning_operations[operation_id] = operation
            self._cleaning_cancel_keys[scope] = expected_version
            self._cleaning_results.pop(task.task_id, None)
            self._cleaning_events.append((operation_id, "cancelled"))
            return operation

    def cleaning_results(self, actor, task_id):
        with self._lock:
            self.get(actor, task_id)
            operation_id = self._cleaning_results.get(task_id)
            if not operation_id:
                return None
            operation = self._cleaning_operations[operation_id]
            self._check_cleaning_fence(operation)
            return operation
