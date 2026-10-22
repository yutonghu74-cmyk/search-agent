"""Local retrieval ledger sharing SQLiteTaskRepository's task transaction.

No search/network work or automatic job dispatch is performed here. Authorization
is injected, fail-closed, and rechecked on every call; cross-system authorization
changes are not made atomic by SQLite. The durable events are an unsent local
outbox only, with no relay, leases, or production delivery guarantee.
"""
from contextlib import closing
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
    RetrievalScope, validate_plan, validate_scope, validate_confirmation,
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
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='retrieval_ledger'").fetchone()
                if exists:
                    load_state(db.execute("SELECT format,payload FROM retrieval_ledger WHERE id=1").fetchone())
                else:
                    db.execute("CREATE TABLE retrieval_ledger (id INTEGER PRIMARY KEY CHECK(id=1), format INTEGER NOT NULL, payload TEXT NOT NULL)")
                    db.execute("INSERT INTO retrieval_ledger VALUES (1,1,?)", (dump_state(empty_state()),))
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "retrieval ledger unavailable", 503, retryable=True) from exc

    def _transaction(self, write, operation):
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                memory = self.tasks._load(db.execute("SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
                state = load_state(db.execute("SELECT format,payload FROM retrieval_ledger WHERE id=1").fetchone())
                result = operation(memory, state)
                if write:
                    db.execute("UPDATE local_ledger SET payload=? WHERE id=1", (dump(memory),))
                    db.execute("UPDATE retrieval_ledger SET payload=? WHERE id=1", (dump_state(state),))
                return result
        except sqlite3.Error as exc:
            raise ServiceError("STORAGE_UNAVAILABLE", "retrieval transaction failed", 503, retryable=True) from exc

    def _authorize(self, memory, actor, task_id, plan=None, action="read"):
        task = memory.get(actor, task_id)
        if self.projects is None or self.authorization is None:
            raise ServiceError("ACCESS_DENIED", "retrieval authority not configured", 403)
        self.projects.require_access(actor, task.project_id, task.dimension_id, action)
        if plan is not None:
            context = AccessContext(actor, task_id, "retrieval", "current")
            self.authorization.require_access(context, "library:" + plan.request.binding.library_id, "search")
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
        def put(memory, state):
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
        return self._transaction(True, put)

    def confirm(self, actor, task_id, plan_id, *, key, proposal_digest, expected_task_version, action):
        require_key(key)
        fingerprint = digest({"plan_id": plan_id, "digest": proposal_digest,
                              "version": expected_task_version, "action": action})
        def approve(memory, state):
            self._authorize(memory, actor, task_id, action="approve")
            proposal = self._proposal(state, task_id, plan_id)
            task = self._authorize(memory, actor, task_id, proposal, "approve")
            scope = (actor.tenant_id, actor.user_id, task_id, key)
            old = state["confirm_keys"].get(scope)
            if old is not None:
                if old[0] != fingerprint:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "confirmation key changed", 409)
                return old[1], True
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
                return saved, True
            validate_confirmation(actor, task, proposal, action=action,
                                  proposal_digest=proposal_digest, expected_task_version=expected_task_version)
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
            return approved, False
        return self._transaction(True, approve)

    def get_current(self, actor, task_id):
        def read(memory, state):
            self._authorize(memory, actor, task_id)
            plan = state["current"].get(task_id)
            if plan is not None:
                task = self._authorize(memory, actor, task_id, plan)
                validate_execution(actor, task, plan, plan, plan.request)
            return plan
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
