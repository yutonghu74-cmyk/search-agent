"""Local multi-branch ledger. No network search or automatic dispatch."""
from contextlib import closing
from dataclasses import asdict, replace
import json
import sqlite3
import uuid

from .sqlite import SQLiteTaskRepository, MAX_BYTES, encode, decode, dump
from ...errors import ServiceError
from ...integration_models import AccessContext
from ...models import RunStatus
from ...multi_branch_contracts import validate_plan_set, validate_set_confirmation, validate_member_execution
from ...query_spec import digest
from ...retrieval_contracts import RetrievalScope, validate_page
from ...service import require_key


def empty_state():
    return {name: {} for name in ('proposals', 'approved', 'current', 'epochs', 'keys',
                                  'reserve_keys', 'operations', 'requests', 'cursors')} | {'events': []}


def serialize(state):
    value = json.dumps(encode(state), ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if len(value.encode()) > MAX_BYTES:
        raise ServiceError('STORAGE_LIMIT', 'Plan set ledger exceeds local limit', 507)
    return value


def restore(row):
    try:
        if row is None or row[0] not in (1, 2) or not isinstance(row[1], str) or len(row[1].encode()) > MAX_BYTES:
            raise ValueError('invalid format')
        state = decode(json.loads(row[1]))
        expected = empty_state()
        if row[0] == 1:
            added = {'reserve_keys', 'operations', 'requests', 'cursors'}
            if not isinstance(state, dict) or set(state) != set(expected) - added:
                raise ValueError('invalid legacy shape')
            state.update({key: {} for key in added})
        if (not isinstance(state, dict) or set(state) != set(expected)
                or any(type(state[k]) is not type(expected[k]) for k in expected)):
            raise ValueError('invalid shape')
        return state
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise ServiceError('STORAGE_CORRUPT', 'Invalid plan set ledger; refusing reset', 500) from error


class SQLitePlanSetApprovalRepository:
    """Implements the plan-set protocol; reservations do not dispatch work."""
    def __init__(self, path, *, projects, authorization):
        self.tasks = SQLiteTaskRepository(path)
        self.projects, self.authorization = projects, authorization
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='plan_set_ledger' AND type='table'").fetchone()
                if exists:
                    row = db.execute('SELECT format,payload FROM plan_set_ledger WHERE id=1').fetchone()
                    state = restore(row)
                    if row[0] == 1:
                        db.execute('UPDATE plan_set_ledger SET format=2,payload=? WHERE id=1', (serialize(state),))
                else:
                    db.execute('CREATE TABLE plan_set_ledger (id INTEGER PRIMARY KEY CHECK(id=1), format INTEGER NOT NULL, payload TEXT NOT NULL)')
                    db.execute('INSERT INTO plan_set_ledger VALUES (1,2,?)', (serialize(empty_state()),))
        except sqlite3.Error as error:
            raise ServiceError('STORAGE_UNAVAILABLE', 'Plan set storage unavailable', 503) from error

    def _transaction(self, write, operation):
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                memory = self.tasks._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
                state = restore(db.execute('SELECT format,payload FROM plan_set_ledger WHERE id=1').fetchone())
                result = operation(memory, state)
                if write:
                    task_payload, set_payload = dump(memory), serialize(state)
                    db.execute('UPDATE local_ledger SET payload=? WHERE id=1', (task_payload,))
                    db.execute('UPDATE plan_set_ledger SET format=2,payload=? WHERE id=1', (set_payload,))
                return result
        except sqlite3.Error as error:
            raise ServiceError('STORAGE_UNAVAILABLE', 'Plan set transaction failed', 503) from error

    def _authorize(self, memory, actor, task_id, proposal=None, action='read'):
        task = memory.get(actor, task_id)
        if self.projects is None or self.authorization is None:
            raise ServiceError('ACCESS_DENIED', 'Authority not configured', 403)
        self.projects.require_access(actor, task.project_id, task.dimension_id, action)
        if proposal is not None:
            context = AccessContext(actor, task_id, 'retrieval', 'current')
            for library in sorted({m.plan.request.binding.library_id for m in proposal.members}):
                self.authorization.require_access(context, 'library:' + library, 'search')
        return task

    @staticmethod
    def _epoch(state, proposal):
        if proposal.epoch != state['epochs'].get(proposal.scope.task_id, 0) + 1:
            raise ServiceError('PLAN_EPOCH_CONFLICT', 'Set must use next epoch', 409)

    def put_proposal(self, actor, proposal):
        def put(memory, state):
            task = self._authorize(memory, actor, proposal.scope.task_id, proposal, 'approve')
            validate_plan_set(actor, task, proposal)
            self._epoch(state, proposal)
            self._authorize(memory, actor, task.task_id, proposal, 'approve')
            state['proposals'][(task.task_id, proposal.proposal_digest)] = proposal
        self._transaction(True, put)

    def confirm(self, actor, task_id, *, proposal_digest, expected_task_version, action, key):
        require_key(key)
        fingerprint = digest({'proposal_digest': proposal_digest, 'version': expected_task_version, 'action': action})
        def approve(memory, state):
            self._authorize(memory, actor, task_id, action='approve')
            identity = (actor.tenant_id, actor.user_id, task_id, key)
            old = state['keys'].get(identity)
            if old is not None and old[0] != fingerprint:
                raise ServiceError('IDEMPOTENCY_CONFLICT', 'Confirmation key changed', 409)
            proposal_key = (task_id, proposal_digest)
            proposal = state['proposals'].get(proposal_key)
            if proposal is None:
                raise ServiceError('NOT_FOUND', 'Plan set proposal not found', 404)
            task = self._authorize(memory, actor, task_id, proposal, 'approve')
            if old is not None:
                return old[1], True
            if action != 'confirm':
                raise ServiceError('CONFIRMATION_REQUIRED', 'Explicit confirmation required', 422)
            if type(expected_task_version) is not int or expected_task_version != proposal.scope.task_version:
                raise ServiceError('REVISION_CONFLICT', 'Confirmation version changed', 409)
            saved = state['approved'].get(proposal_key)
            if saved is not None:
                state['keys'][identity] = (fingerprint, saved)
                return saved, True
            validate_set_confirmation(actor, task, proposal, action=action,
                                      proposal_digest=proposal_digest, expected_task_version=expected_task_version)
            self._epoch(state, proposal)
            if task.active_cleaning_operation_id:
                operation = memory._cleaning_operations[task.active_cleaning_operation_id]
                if operation.status not in ('succeeded', 'failed', 'cancelled'):
                    raise ServiceError('CLEANING_IN_PROGRESS', 'Active cleaning prevents replacement', 409)
            self._authorize(memory, actor, task_id, proposal, 'approve')
            task = replace(task, task_version=task.task_version + 1, run_status=RunStatus.RUNNING, stage='retrieving')
            scope = RetrievalScope.from_task(task)
            approved = replace(proposal, scope=scope, members=tuple(
                replace(member, plan=replace(member.plan, scope=scope)) for member in proposal.members))
            validate_plan_set(actor, task, approved)
            memory._tasks[task_id] = task
            state['approved'][proposal_key] = approved
            state['current'][task_id] = approved
            state['epochs'][task_id] = approved.epoch
            state['keys'][identity] = (fingerprint, approved)
            state['events'].append({'kind': 'plan_set_approved',
                                    'event_id': 'plan-set:' + approved.proposal_digest, 'plan_set': approved})
            return approved, False
        return self._transaction(True, approve)

    def get_current(self, actor, task_id):
        def read(memory, state):
            self._authorize(memory, actor, task_id)
            current = state['current'].get(task_id)
            if current is not None:
                task = self._authorize(memory, actor, task_id, current)
                validate_plan_set(actor, task, current)
                self._authorize(memory, actor, task_id, current)
            return current
        return self._transaction(False, read)

    def reserve_page(self, actor, approved, *, plan_id, request, key):
        require_key(key)
        fingerprint = digest({'set': approved.proposal_digest, 'member': plan_id, 'request': asdict(request)})
        def reserve(memory, state):
            task_id = approved.scope.task_id
            task = self._authorize(memory, actor, task_id, approved, 'approve')
            validate_member_execution(actor, task, approved, state['current'].get(task_id),
                                      plan_id=plan_id, request=request)
            identity = (actor.tenant_id, actor.user_id, task_id, key)
            old = state['reserve_keys'].get(identity)
            if old is not None:
                if old[0] != fingerprint:
                    raise ServiceError('IDEMPOTENCY_CONFLICT', 'Reservation key changed', 409)
                return old[1], True
            chain = (approved.proposal_digest, plan_id)
            if request.cursor is not None and (*chain, request.cursor) not in state['cursors']:
                raise ServiceError('CURSOR_INVALID', 'No committed member predecessor', 409)
            operation_id = state['requests'].get(fingerprint)
            replay = operation_id is not None
            if not replay:
                pending = sum(record[0] == approved and record[3] is None
                              for record in state['operations'].values())
                if pending >= 100:
                    raise ServiceError('INPUT_LIMIT', 'Plan set pending limit reached', 422)
            self._authorize(memory, actor, task_id, approved, 'approve')
            if operation_id is None:
                operation_id = 'setpage_' + uuid.uuid4().hex
                state['operations'][operation_id] = (approved, plan_id, request, None)
                state['requests'][fingerprint] = operation_id
            state['reserve_keys'][identity] = (fingerprint, operation_id)
            return operation_id, replay
        return self._transaction(True, reserve)

    def commit_page(self, actor, approved, *, operation_id, plan_id, request, page):
        def commit(memory, state):
            task_id = approved.scope.task_id
            task = self._authorize(memory, actor, task_id, approved, 'approve')
            validate_member_execution(actor, task, approved, state['current'].get(task_id),
                                      plan_id=plan_id, request=request)
            record = state['operations'].get(operation_id)
            if record is None or record[:3] != (approved, plan_id, request):
                raise ServiceError('OPERATION_MISMATCH', 'Page does not match reservation', 409)
            if record[3] is not None:
                if record[3] != page:
                    raise ServiceError('RESULT_CONFLICT', 'Committed page is immutable', 409)
                return True
            chain = (approved.proposal_digest, plan_id)
            previous = None
            if request.cursor is not None:
                predecessor = state['cursors'].get((*chain, request.cursor))
                if predecessor is None:
                    raise ServiceError('CURSOR_INVALID', 'Missing member predecessor', 409)
                previous = state['operations'][predecessor][3]
            seen = frozenset(cursor for set_digest, member_id, cursor in state['cursors']
                             if (set_digest, member_id) == chain)
            validate_page(request, page, previous=previous, seen_cursors=seen)
            self._authorize(memory, actor, task_id, approved, 'approve')
            state['operations'][operation_id] = (approved, plan_id, request, page)
            if page.next_cursor is not None:
                state['cursors'][(*chain, page.next_cursor)] = operation_id
            state['events'].append({'kind': 'plan_set_page_committed',
                                    'event_id': 'set-page:' + operation_id,
                                    'operation_id': operation_id, 'set_digest': approved.proposal_digest,
                                    'plan_id': plan_id})
            return False
        return self._transaction(True, commit)
