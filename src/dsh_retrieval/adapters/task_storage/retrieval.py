"""Local retrieval ledger sharing SQLiteTaskRepository's task transaction.

No search/network work or automatic job dispatch is performed here. Authorization
is injected, fail-closed, and rechecked on every call; cross-system authorization
changes are not made atomic by SQLite. The durable events are an unsent local
outbox only, with no relay, leases, or production delivery guarantee.
"""
from contextlib import closing
from . import transactions
from .transactions import checked_access
from . import ledger_rows
from dataclasses import asdict, replace
import json
import sqlite3
import uuid

from .sqlite import SQLiteTaskRepository, MAX_BYTES, encode, decode, dump
from ...errors import ServiceError
from ...integration_models import AccessContext
from ...models import RunStatus
from ...query_spec import digest
from ...retrieval_contracts import (
    RetrievalOperation, RetrievalScope, validate_plan, validate_scope, validate_confirmation,
    validate_execution, validate_page,
)
from ...service import require_key


def empty_state():
    return {"proposals": {}, "approved": {}, "current": {}, "epochs": {},
            "confirm_keys": {}, "reserve_keys": {}, "operations": {},
            "requests": {}, "cursors": {}, "events": []}


def dump_state(state):
    payload = json.dumps(encode(state), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(payload.encode()) > MAX_BYTES:
        raise ServiceError("STORAGE_LIMIT", "retrieval snapshot exceeds 16 MB", 507)
    return payload


def load_state(row):
    if row is None:
        raise ServiceError("STORAGE_CORRUPT", "retrieval ledger missing", 500)
    if row[0] != 1:
        raise ServiceError("STORAGE_FORMAT_UNSUPPORTED", "unsupported retrieval format", 500)
    try:
        if not isinstance(row[1], str) or len(row[1].encode()) > MAX_BYTES:
            raise ValueError("invalid size")
        state = decode(json.loads(row[1]))
        expected = empty_state()
        if not isinstance(state, dict) or set(state) != set(expected):
            raise ValueError("invalid state")
        if any(type(state[k]) is not type(expected[k]) for k in expected):
            raise ValueError("invalid state container")
        return state
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ServiceError("STORAGE_CORRUPT", "invalid retrieval ledger; refusing reset", 500) from exc


def _event_task_id(event):
    plan = event.get("plan") if isinstance(event, dict) else None
    return getattr(getattr(plan, "scope", None), "task_id", None)


def purge_for_tasks(state, task_ids):
    """Drop every retrieval record scoped to the given task ids, in place.

    ``load_state`` validates shape only, so the events list is not strictly
    revalidated; it is still filtered so no dangling plan reference survives.
    A no-op for an empty task set. Returns the mutated state.
    """
    if not task_ids:
        return state
    surviving_ops = {oid for oid, record in state["operations"].items()
                     if record[0].scope.task_id not in task_ids}
    state["operations"] = {oid: rec for oid, rec in state["operations"].items()
                           if oid in surviving_ops}
    state["requests"] = {fp: oid for fp, oid in state["requests"].items()
                         if oid in surviving_ops}
    state["cursors"] = {key: oid for key, oid in state["cursors"].items()
                        if oid in surviving_ops}
    state["proposals"] = {key: p for key, p in state["proposals"].items()
                          if key[0] not in task_ids}
    state["approved"] = {key: p for key, p in state["approved"].items()
                         if key[0] not in task_ids}
    state["current"] = {tid: p for tid, p in state["current"].items()
                        if tid not in task_ids}
    state["epochs"] = {tid: e for tid, e in state["epochs"].items()
                       if tid not in task_ids}
    state["confirm_keys"] = {key: v for key, v in state["confirm_keys"].items()
                             if key[2] not in task_ids}
    state["reserve_keys"] = {key: v for key, v in state["reserve_keys"].items()
                             if key[2] not in task_ids}
    state["events"] = [event for event in state["events"]
                       if _event_task_id(event) not in task_ids]
    return state


class SQLiteRetrievalPlanRepository:
    """Implements RetrievalPlanRepository; shares one file with task/cleaning data.

    Use trusted backend-built proposals. plan_id is unique per task and immutable;
    changing a plan uses a fresh ID. epoch must equal the ledger's next epoch,
    checked both when saving and when confirming, never an arbitrary client value.
    Missing authorities deny retrieval without changing legacy task behavior.
    """

    def __init__(self, path, *, projects, authorization):
        self.tasks = SQLiteTaskRepository(path)
        self.projects, self.authorization = projects, authorization
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name='retrieval_ledger'").fetchone()
                if exists:
                    load_state(db.execute("SELECT format,payload FROM retrieval_ledger WHERE id=1").fetchone())
                else:
                    db.execute("CREATE TABLE retrieval_ledger (id INTEGER PRIMARY KEY CHECK(id=1), format INTEGER NOT NULL, payload TEXT NOT NULL)")
                    db.execute("INSERT INTO retrieval_ledger VALUES (1,1,?)", (dump_state(empty_state()),))
                ledger_rows.migrate(db, 'retrieval_ledger', load_state)
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "retrieval ledger unavailable", 503, retryable=True) from exc

    @transactions.staged()
    def _transaction(self, write, operation, *, with_connection=False):
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                memory = self.tasks._load(db.execute("SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                state = load_state(db.execute("SELECT format,payload FROM retrieval_ledger WHERE id=1").fetchone())
                result = operation(memory, state, db) if with_connection else operation(memory, state)
                if write:
                    ledger_rows.write_memory(db, memory)
                    self.tasks._sync_task_read_summaries(db, memory)
                    ledger_rows.write_state(db, 'retrieval_ledger', 1, state)
                return result
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "retrieval transaction failed", 503, retryable=True) from exc

    def _authorize(self, memory, actor, task_id, plan=None, action="read"):
        task = memory.get(actor, task_id)
        if self.projects is None or self.authorization is None:
            raise ServiceError("ACCESS_DENIED", "retrieval authority not configured", 403)
        checked_access(self.projects, actor, task.project_id, task.dimension_id, action)
        if plan is not None:
            context = AccessContext(actor, task_id, "retrieval", "current")
            checked_access(self.authorization, context, "library:" + plan.request.binding.library_id, "search")
        return task

    @staticmethod
    def _proposal(state, task_id, plan_id):
        proposal = state["proposals"].get((task_id, plan_id))
        if proposal is None:
            raise ServiceError("NOT_FOUND", "retrieval proposal not found", 404)
        return proposal

    @staticmethod
    def _epoch(state, proposal):
        expected = state["epochs"].get(proposal.scope.task_id, 0) + 1
        if proposal.epoch != expected:
            raise ServiceError("PLAN_EPOCH_CONFLICT", "proposal does not use next ledger epoch", 409,
                               details={"expected_epoch": expected})

    def put_proposal(self, actor, proposal):
        return self._transaction(True, lambda memory, state: self._put_proposal(memory, state, actor, proposal))

    def _put_proposal(self, memory, state, actor, proposal):
        """Same validation for ordinary writes and atomic Agent handoff writes."""
        task = self._authorize(memory, actor, proposal.scope.task_id, proposal, "approve")
        validate_plan(proposal)
        key = (task.task_id, proposal.request.plan_id)
        old = state["proposals"].get(key)
        if old is not None:
            if old != proposal:
                raise ServiceError("PLAN_ID_CONFLICT", "plan ID is immutable", 409)
            return
        validate_scope(actor, task, proposal.scope)
        self._epoch(state, proposal)
        self._authorize(memory, actor, task.task_id, proposal, "approve")
        state["proposals"][key] = proposal

    def confirm(self, actor, task_id, plan_id, *, key, proposal_digest, expected_task_version, action,
                on_confirm=None):
        require_key(key)
        fingerprint = digest({"plan_id": plan_id, "digest": proposal_digest,
                              "version": expected_task_version, "action": action})
        def approve(memory, state, db):
            def linked(value, replayed):
                if on_confirm is not None: on_confirm(db, value)
                return value, replayed
            self._authorize(memory, actor, task_id, action="approve")
            proposal = self._proposal(state, task_id, plan_id)
            task = self._authorize(memory, actor, task_id, proposal, "approve")
            scope = (actor.tenant_id, actor.user_id, task_id, key)
            old = state["confirm_keys"].get(scope)
            if old is not None:
                if old[0] != fingerprint:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "confirmation key changed", 409)
                return linked(old[1], True)
            if action != "confirm":
                raise ServiceError("CONFIRMATION_REQUIRED", "explicit owner confirmation required", 422)
            if type(expected_task_version) is not int or expected_task_version != proposal.scope.task_version:
                raise ServiceError("REVISION_CONFLICT", "confirmation task version changed", 409)
            if proposal_digest != proposal.proposal_digest:
                raise ServiceError("PLAN_DIGEST_MISMATCH", "confirmation differs from proposal", 409)
            proposal_key = (task_id, plan_id)
            saved = state["approved"].get(proposal_key)
            if saved is not None:
                state["confirm_keys"][scope] = (fingerprint, saved)
                return linked(saved, True)
            validate_confirmation(actor, task, proposal, action=action,
                                  proposal_digest=proposal_digest, expected_task_version=expected_task_version)
            from .agent_retrieval import require_current_handoff
            require_current_handoff(db, proposal)
            self._epoch(state, proposal)
            if task.active_cleaning_operation_id:
                cleaning = memory._cleaning_operations[task.active_cleaning_operation_id]
                if cleaning.status not in ("succeeded", "failed", "cancelled"):
                    raise ServiceError("CLEANING_IN_PROGRESS", "cannot replace plan during active cleaning", 409)
            self._authorize(memory, actor, task_id, proposal, "approve")
            task = replace(task, task_version=task.task_version + 1, run_status=RunStatus.RUNNING, stage="retrieving")
            approved = replace(proposal, scope=RetrievalScope.from_task(task))
            memory._tasks[task_id] = task
            state["approved"][proposal_key] = approved
            state["current"][task_id] = approved
            state["epochs"][task_id] = approved.epoch
            state["confirm_keys"][scope] = (fingerprint, approved)
            state["events"].append({"event_id": "retrieval-approved:" + approved.proposal_digest,
                                    "kind": "plan_approved", "plan": approved})
            return linked(approved, False)
        return self._transaction(True, approve, with_connection=True)

    def get_current(self, actor, task_id):
        def read(memory, state):
            self._authorize(memory, actor, task_id)
            plan = state["current"].get(task_id)
            if plan is not None:
                task = self._authorize(memory, actor, task_id, plan)
                validate_execution(actor, task, plan, plan, plan.request)
            return plan
        return self._transaction(False, read)

    def get_operation(self, actor, task_id, operation_id):
        def read(memory, state):
            self._authorize(memory, actor, task_id)
            record = state["operations"].get(operation_id)
            if record is None or record[0].scope.task_id != task_id:
                raise ServiceError("NOT_FOUND", "retrieval operation not found", 404)
            plan, request, page = record
            task = self._authorize(memory, actor, task_id, plan)
            validate_execution(actor, task, plan, state["current"].get(task_id), request)
            self._authorize(memory, actor, task_id, plan)
            return RetrievalOperation(operation_id, plan, request, page)
        return self._transaction(False, read)

    def list_pending(self, actor, task_id):
        def read(memory, state):
            self._authorize(memory, actor, task_id)
            current = state["current"].get(task_id)
            if current is None:
                return ()
            task = self._authorize(memory, actor, task_id, current)
            validate_execution(actor, task, current, current, current.request)
            pending = []
            for operation_id, (plan, request, page) in state["operations"].items():
                if plan == current and page is None:
                    validate_execution(actor, task, plan, current, request)
                    pending.append(RetrievalOperation(operation_id, plan, request, None))
                    if len(pending) > 100:
                        raise ServiceError("INPUT_LIMIT", "pending operation read exceeds 100", 422)
            self._authorize(memory, actor, task_id, current)
            return tuple(sorted(pending, key=lambda operation: operation.operation_id))
        return self._transaction(False, read)

    def reserve_page(self, actor, plan, request, key):
        require_key(key)
        fingerprint = digest({"plan": plan.proposal_digest, "request": asdict(request)})
        def reserve(memory, state):
            task = self._authorize(memory, actor, plan.scope.task_id, plan, "approve")
            validate_execution(actor, task, plan, state["current"].get(task.task_id), request)
            scope = (actor.tenant_id, actor.user_id, task.task_id, key)
            old = state["reserve_keys"].get(scope)
            if old is not None:
                if old[0] != fingerprint:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "page reservation key changed", 409)
                return old[1], True
            if request.cursor is not None and (plan.proposal_digest, request.cursor) not in state["cursors"]:
                raise ServiceError("CURSOR_INVALID", "cursor has no committed predecessor", 409)
            self._authorize(memory, actor, task.task_id, plan, "approve")
            operation_id = state["requests"].get(fingerprint)
            replay = operation_id is not None
            if operation_id is None:
                operation_id = "retrieve_" + uuid.uuid4().hex
                state["operations"][operation_id] = (plan, request, None)
                state["requests"][fingerprint] = operation_id
            state["reserve_keys"][scope] = (fingerprint, operation_id)
            return operation_id, replay
        return self._transaction(True, reserve)

    def commit_page(self, actor, operation_id, plan, request, page):
        def commit(memory, state):
            task = self._authorize(memory, actor, plan.scope.task_id, plan, "approve")
            validate_execution(actor, task, plan, state["current"].get(task.task_id), request)
            record = state["operations"].get(operation_id)
            if record is None or record[:2] != (plan, request):
                raise ServiceError("OPERATION_MISMATCH", "page was not reserved for this plan/request", 409)
            if record[2] is not None:
                if record[2] != page:
                    raise ServiceError("RESULT_CONFLICT", "committed page is immutable", 409)
                return True
            previous = None
            if request.cursor is not None:
                predecessor = state["cursors"].get((plan.proposal_digest, request.cursor))
                if predecessor is None:
                    raise ServiceError("CURSOR_INVALID", "cursor has no predecessor", 409)
                previous = state["operations"][predecessor][2]
            seen = frozenset(cursor for fingerprint, cursor in state["cursors"] if fingerprint == plan.proposal_digest)
            validate_page(request, page, previous=previous, seen_cursors=seen)
            # Check authority again immediately before staging any write. DB writes
            # and local events below commit together; external ACL is not locked.
            self._authorize(memory, actor, task.task_id, plan, "approve")
            state["operations"][operation_id] = (plan, request, page)
            if page.next_cursor is not None:
                state["cursors"][(plan.proposal_digest, page.next_cursor)] = operation_id
            state["events"].append({"event_id": "retrieval-page:" + operation_id,
                                    "kind": "page_committed", "operation_id": operation_id, "plan": plan})
            return False
        return self._transaction(True, commit)
