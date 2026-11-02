"""Local multi-branch ledger. No network search or automatic dispatch."""
from contextlib import closing
from collections import Counter
from dataclasses import asdict, replace
import json
import re
import sqlite3
import uuid

from .sqlite import SQLiteTaskRepository, MAX_BYTES, encode, decode, dump
from ...errors import ServiceError
from ...candidate_contracts import (CandidateManifest, CandidateManifestProposal,
                                    ManifestItem, candidate_identity)
from ...integration_models import AccessContext, MediaRecord, SearchHit
from ...models import RunStatus
from ...models import Actor
from ...multi_branch_contracts import (PlanSetMemberProgress, plan_origin_groups, validate_plan_set,
                                        validate_set_confirmation, validate_member_execution)
from ...query_spec import digest
from ...retrieval_contracts import RetrievalScope, validate_page
from ...service import require_key
from ...candidate_browsing import create_projection, append_hit


def empty_state():
    return {name: {} for name in ('proposals', 'approved', 'current', 'epochs', 'keys',
                                  'reserve_keys', 'operations', 'requests', 'cursors',
                                  'candidate_proposals', 'candidate_manifests',
                                  'candidate_published', 'candidate_keys')} | {
                                      'events': [], 'candidate_events': []}


def serialize(state):
    value = json.dumps(encode(state), ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if len(value.encode()) > MAX_BYTES:
        raise ServiceError('STORAGE_LIMIT', 'Plan set ledger exceeds local limit', 507)
    return value


def automatic_refill(task):
    try:
        proposal = json.loads(task.confirmed_proposal_json)
        policy = proposal['query_spec']['refill_policy']
        return (policy['mode'] == 'maintain_target'
                and policy['progression'] == 'auto'
                and proposal['node_modes']['refill'] == 'auto')
    except (TypeError, ValueError, KeyError):
        return False


def restore(row):
    try:
        if row is None or row[0] not in (1, 2, 3) or not isinstance(row[1], str) or len(row[1].encode()) > MAX_BYTES:
            raise ValueError('invalid format')
        state = decode(json.loads(row[1]))
        expected = empty_state()
        candidate_added = {'candidate_proposals', 'candidate_manifests',
                           'candidate_published', 'candidate_keys', 'candidate_events'}
        if row[0] == 1:
            added = {'reserve_keys', 'operations', 'requests', 'cursors'} | candidate_added
            if not isinstance(state, dict) or set(state) != set(expected) - added:
                raise ValueError('invalid legacy shape')
            state.update({key: ([] if key == 'candidate_events' else {}) for key in added})
        elif row[0] == 2:
            if not isinstance(state, dict) or set(state) != set(expected) - candidate_added:
                raise ValueError('invalid v2 shape')
            state.update({key: ([] if key == 'candidate_events' else {}) for key in candidate_added})
        if (not isinstance(state, dict) or set(state) != set(expected)
                or any(type(state[k]) is not type(expected[k]) for k in expected)):
            raise ValueError('invalid shape')
        for proposal_digest, proposal in state['candidate_proposals'].items():
            if (not isinstance(proposal, CandidateManifestProposal)
                    or proposal.proposal_digest != proposal_digest):
                raise ValueError('candidate proposal')
        for ref, manifest in state['candidate_manifests'].items():
            if (not isinstance(manifest, CandidateManifest) or manifest.manifest_ref != ref
                    or state['candidate_proposals'].get(
                        manifest.proposal.proposal_digest) != manifest.proposal
                    or state['candidate_published'].get(
                        manifest.proposal.proposal_digest) != ref):
                raise ValueError('candidate manifest')
        for proposal_digest, ref in state['candidate_published'].items():
            if (ref not in state['candidate_manifests']
                    or state['candidate_manifests'][ref].proposal.proposal_digest != proposal_digest):
                raise ValueError('candidate publication')
        for key, value in state['candidate_keys'].items():
            if (not isinstance(key, tuple) or len(key) != 4
                    or not isinstance(value, tuple) or len(value) != 2
                    or not isinstance(value[0], str) or value[1] not in state['candidate_manifests']):
                raise ValueError('candidate key')
        expected_candidate_events = [{
            'event_id': 'candidate-published:' + ref,
            'kind': 'candidate_manifest_published', 'manifest_ref': ref,
            'task_id': manifest.proposal.scope.task_id,
            'proposal_digest': manifest.proposal.proposal_digest,
        } for ref, manifest in state['candidate_manifests'].items()]
        if state['candidate_events'] != expected_candidate_events:
            raise ValueError('candidate events')
        return state
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise ServiceError('STORAGE_CORRUPT', 'Invalid plan set ledger; refusing reset', 500) from error


def purge_for_tasks(state, task_ids):
    """Drop every plan-set record scoped to the given task ids, in place.

    ``restore`` strictly revalidates ``candidate_events`` against the surviving
    candidate manifests (rebuilt here); ``events`` is not strictly revalidated
    but is filtered for cleanliness. Returns the mutated state and a tuple of
    ``proposal_digest`` values whose ``planset:`` browse projection rows must
    also be deleted (one digest per surviving approved set is unnecessary;
    only purged tasks' digests are returned). A no-op for an empty task set.
    """
    if not task_ids:
        return state, ()
    surviving_ops = {oid for oid, record in state['operations'].items()
                     if record[0].scope.task_id not in task_ids}
    state['operations'] = {oid: rec for oid, rec in state['operations'].items()
                           if oid in surviving_ops}
    state['requests'] = {fp: oid for fp, oid in state['requests'].items()
                         if oid in surviving_ops}
    state['cursors'] = {key: oid for key, oid in state['cursors'].items()
                        if oid in surviving_ops}
    purged_digests = tuple(digest for (tid, digest), approved in state['approved'].items()
                           if tid in task_ids)
    state['proposals'] = {key: p for key, p in state['proposals'].items()
                          if key[0] not in task_ids}
    state['approved'] = {key: p for key, p in state['approved'].items()
                         if key[0] not in task_ids}
    state['current'] = {tid: p for tid, p in state['current'].items()
                        if tid not in task_ids}
    state['epochs'] = {tid: e for tid, e in state['epochs'].items()
                       if tid not in task_ids}
    state['keys'] = {key: v for key, v in state['keys'].items()
                     if key[2] not in task_ids}
    state['reserve_keys'] = {key: v for key, v in state['reserve_keys'].items()
                             if key[2] not in task_ids}
    surviving_manifests = {ref: m for ref, m in state['candidate_manifests'].items()
                           if m.proposal.scope.task_id not in task_ids}
    surviving_digests = {m.proposal.proposal_digest for m in surviving_manifests.values()}
    state['candidate_manifests'] = surviving_manifests
    state['candidate_published'] = {d: ref for d, ref in state['candidate_published'].items()
                                    if d in surviving_digests}
    state['candidate_proposals'] = {d: p for d, p in state['candidate_proposals'].items()
                                    if d in surviving_digests}
    state['candidate_keys'] = {key: v for key, v in state['candidate_keys'].items()
                               if key[2] not in task_ids}
    state['candidate_events'] = [{'event_id': 'candidate-published:' + ref,
                                  'kind': 'candidate_manifest_published', 'manifest_ref': ref,
                                  'task_id': m.proposal.scope.task_id,
                                  'proposal_digest': m.proposal.proposal_digest}
                                 for ref, m in surviving_manifests.items()]
    state['events'] = [event for event in state['events']
                       if _event_belongs_to_survivors(event, task_ids, surviving_ops)]
    return state, purged_digests


def _event_belongs_to_survivors(event, task_ids, surviving_ops):
    if not isinstance(event, dict):
        return False
    kind = event.get('kind')
    if kind == 'plan_set_approved':
        plan_set = event.get('plan_set')
        return getattr(getattr(plan_set, 'scope', None), 'task_id', None) not in task_ids
    if kind == 'plan_set_page_committed':
        return event.get('operation_id') in surviving_ops
    return True


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
                    if row[0] != 3:
                        db.execute('UPDATE plan_set_ledger SET format=3,payload=? WHERE id=1', (serialize(state),))
                else:
                    db.execute('CREATE TABLE plan_set_ledger (id INTEGER PRIMARY KEY CHECK(id=1), format INTEGER NOT NULL, payload TEXT NOT NULL)')
                    db.execute('INSERT INTO plan_set_ledger VALUES (1,3,?)', (serialize(empty_state()),))
                create_projection(db)
                self._project_hits(db, restore(db.execute('SELECT format,payload FROM plan_set_ledger WHERE id=1').fetchone()))
        except sqlite3.Error as error:
            raise ServiceError('STORAGE_UNAVAILABLE', 'Plan set storage unavailable', 503) from error

    @staticmethod
    def _project_hits(db, state):
        for operation_id, (approved, plan_id, request, page) in state['operations'].items():
            if page is None:
                continue
            branch = next(m.branch_id for m in approved.members if m.plan.request.plan_id == plan_id)
            for index, hit in enumerate(page.hits):
                append_hit(db, approved.proposal_digest, str(operation_id)+':'+str(index),
                           plan_id, branch, json.dumps(encode(hit)))

    def _transaction(self, write, operation):
        try:
            with closing(self.tasks._connect()) as db, db:
                db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                memory = self.tasks._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
                state = restore(db.execute('SELECT format,payload FROM plan_set_ledger WHERE id=1').fetchone())
                committed_before = {key for key, value in state['operations'].items() if value[3] is not None} if write else set()
                result = operation(memory, state)
                if write:
                    task_payload, set_payload = dump(memory), serialize(state)
                    db.execute('UPDATE local_ledger SET payload=? WHERE id=1', (task_payload,))
                    db.execute('UPDATE plan_set_ledger SET format=3,payload=? WHERE id=1', (set_payload,))
                    self._project_hits(db, {'operations': {key: value for key, value in state['operations'].items() if key not in committed_before}})
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

    def get_browse_current(self, actor, task_id):
        """Current approved search remains readable after same-revision cleaning advances task_version."""
        def read(memory, state):
            task = self._authorize(memory, actor, task_id)
            current = state['current'].get(task_id)
            if current is None:
                return None
            self._authorize(memory, actor, task_id, current)
            if (task.confirmed_revision != current.scope.revision
                    or task.run_generation != current.scope.generation):
                raise ServiceError('REVISION_CONFLICT', 'Search version changed; reload', 409)
            validate_plan_set(actor, replace(task, task_version=current.scope.task_version,
                                            run_status=RunStatus.RUNNING, stage='retrieving'), current)
            return current
        return self._transaction(False, read)

    def next_epoch(self, actor, task_id):
        """Return the next monotonic epoch without validating a superseded current set."""
        def read(memory, state):
            self._authorize(memory, actor, task_id, action='approve')
            return state['epochs'].get(task_id, 0) + 1
        return self._transaction(False, read)

    def recoverable(self):
        """Return persisted current owners; callers revalidate before dispatch."""
        def read(memory, state):
            rows = []
            for task_id, approved in state['current'].items():
                scope = approved.scope
                actor = Actor(scope.tenant_id, scope.owner_user_id)
                try:
                    task = self._authorize(memory, actor, task_id, approved)
                    validate_plan_set(actor, task, approved)
                except ServiceError as error:
                    if error.code in ('REVISION_CONFLICT', 'PLAN_NOT_CURRENT',
                                      'INVALID_STATE_TRANSITION'):
                        continue
                    raise
                rows.append((actor, task_id))
            return tuple(rows)
        return self._transaction(False, read)

    @staticmethod
    def _execution_task(task, approved, allow_downstream):
        if not allow_downstream:
            return task
        if (task.confirmed_revision != approved.scope.revision
                or task.run_generation != approved.scope.generation
                or task.task_version < approved.scope.task_version
                or task.run_status not in (RunStatus.PLANNING, RunStatus.RUNNING,
                                           RunStatus.AWAITING_INPUT)):
            raise ServiceError('REVISION_CONFLICT', 'Refill search scope changed', 409)
        return replace(task, task_version=approved.scope.task_version,
                       run_status=RunStatus.RUNNING, stage='retrieving')

    def get_progress(self, actor, approved, *, allow_downstream=False):
        """Rebuild every member head from the committed ledger in one read transaction."""
        def corrupt():
            raise ServiceError('STORAGE_CORRUPT', 'Invalid plan set page chain', 500)

        def read(memory, state):
            task_id = approved.scope.task_id
            task = self._authorize(memory, actor, task_id, approved)
            current = state['current'].get(task_id)
            if current != approved:
                raise ServiceError('PLAN_NOT_CURRENT', 'Set unapproved or superseded', 409)
            task = self._execution_task(task, approved, allow_downstream)
            validate_plan_set(actor, task, current)
            members = {member.plan.request.plan_id: member.plan.request for member in current.members}
            operations = {plan_id: {} for plan_id in members}
            expected_cursors = {}
            for operation_id, record in state['operations'].items():
                if not isinstance(record, tuple) or len(record) != 4:
                    corrupt()
                saved, plan_id, request, page = record
                if saved != current:
                    continue
                if (not isinstance(operation_id, str) or not operation_id
                        or plan_id not in members):
                    corrupt()
                validate_member_execution(actor, task, approved, current,
                                          plan_id=plan_id, request=request)
                fingerprint = digest({'set': current.proposal_digest, 'member': plan_id,
                                      'request': asdict(request)})
                if state['requests'].get(fingerprint) != operation_id:
                    corrupt()
                if request.cursor in operations[plan_id]:
                    corrupt()
                operations[plan_id][request.cursor] = (operation_id, request, page)
                if page is not None and page.next_cursor is not None:
                    key = (current.proposal_digest, plan_id, page.next_cursor)
                    if key in expected_cursors:
                        corrupt()
                    expected_cursors[key] = operation_id

            actual_cursors = {key: value for key, value in state['cursors'].items()
                              if isinstance(key, tuple) and len(key) == 3
                              and key[0] == current.proposal_digest}
            if actual_cursors != expected_cursors:
                corrupt()

            result = []
            for plan_id, initial in members.items():
                chain = operations[plan_id]
                cursor, previous, seen, visited, committed = None, None, set(), set(), 0
                while cursor in chain:
                    if cursor in visited:
                        corrupt()
                    visited.add(cursor)
                    operation_id, request, page = chain[cursor]
                    if page is None:
                        head = PlanSetMemberProgress(plan_id, request, operation_id, None, committed)
                        break
                    validate_page(request, page, previous=previous, seen_cursors=frozenset(seen))
                    committed += 1
                    head = PlanSetMemberProgress(plan_id, request, operation_id, page, committed)
                    if page.next_cursor is None:
                        break
                    seen.add(page.next_cursor)
                    cursor, previous = page.next_cursor, page
                else:
                    if committed == 0:
                        head = PlanSetMemberProgress(plan_id, initial, None, None, 0)
                if len(visited) != len(chain):
                    corrupt()
                result.append(head)
            self._authorize(memory, actor, task_id, current)
            return tuple(result)
        return self._transaction(False, read)

    def list_committed_pages(self, actor, approved, *, allow_downstream=False):
        """Local read projection for candidate mapping, never a media grant.

        Reuse the validated chain head and return only committed pages of the
        current approved set. A concurrent change between reads fails closed.
        """
        heads = self.get_progress(actor, approved, allow_downstream=allow_downstream)

        def read(memory, state):
            task = self._authorize(memory, actor, approved.scope.task_id, approved)
            if state['current'].get(task.task_id) != approved:
                raise ServiceError('PLAN_NOT_CURRENT', 'Approved set changed', 409)
            task = self._execution_task(task, approved, allow_downstream)
            validate_plan_set(actor, task, approved)
            result = []
            for member, head in zip(approved.members, heads):
                plan_id = member.plan.request.plan_id
                if head.plan_id != plan_id:
                    raise ServiceError('STORAGE_CORRUPT', 'Member order changed', 500)
                chain = {}
                for saved, saved_plan_id, request, page in state['operations'].values():
                    if saved == approved and saved_plan_id == plan_id:
                        if request.cursor in chain:
                            raise ServiceError('STORAGE_CORRUPT', 'Duplicate page cursor', 500)
                        chain[request.cursor] = (request, page)
                cursor, seen = None, set()
                committed = 0
                while cursor in chain:
                    if cursor in seen:
                        raise ServiceError('STORAGE_CORRUPT', 'Invalid page chain', 500)
                    seen.add(cursor)
                    request, page = chain[cursor]
                    if page is None:
                        break
                    committed += 1
                    result.append((plan_id, page))
                    if len(result) > 100:
                        raise ServiceError('INPUT_LIMIT', 'Too many committed pages', 422)
                    if page.next_cursor is None:
                        break
                    cursor = page.next_cursor
                if committed != head.committed_pages:
                    raise ServiceError('REVISION_CONFLICT', 'Page progress changed; reload', 409)
            self._authorize(memory, actor, task.task_id, approved)
            return tuple(result)

        return self._transaction(False, read)

    def summarize_committed_hits(self, actor, approved, *, limit=200):
        """Count all committed raw hits while returning only a bounded display prefix.

        Candidate browsing must not fail when a legitimate search passes the
        100-page full-manifest read limit. This projection is not a media grant.
        """
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ServiceError('INPUT_LIMIT', 'Candidate display limit must be 1..200', 422)
        heads = self.get_progress(actor, approved)

        def read(memory, state):
            task = self._authorize(memory, actor, approved.scope.task_id, approved)
            if state['current'].get(task.task_id) != approved:
                raise ServiceError('PLAN_NOT_CURRENT', 'Approved set changed', 409)
            validate_plan_set(actor, task, approved)
            operations = {member.plan.request.plan_id: {} for member in approved.members}
            for saved, plan_id, request, page in state['operations'].values():
                if saved != approved:
                    continue
                chain = operations.get(plan_id)
                if chain is None or request.cursor in chain:
                    raise ServiceError('STORAGE_CORRUPT', 'Invalid candidate page chain', 500)
                chain[request.cursor] = (request, page)
            shown, total = [], 0
            for member, head in zip(approved.members, heads):
                plan_id = member.plan.request.plan_id
                if head.plan_id != plan_id:
                    raise ServiceError('STORAGE_CORRUPT', 'Member order changed', 500)
                chain = operations[plan_id]
                cursor, seen, committed = None, set(), 0
                while cursor in chain:
                    if cursor in seen:
                        raise ServiceError('STORAGE_CORRUPT', 'Invalid candidate page chain', 500)
                    seen.add(cursor)
                    _, page = chain[cursor]
                    if page is None:
                        break
                    committed += 1
                    total += len(page.hits)
                    if len(shown) < limit:
                        shown.extend((plan_id, hit) for hit in page.hits[:limit - len(shown)])
                    if page.next_cursor is None:
                        break
                    cursor = page.next_cursor
                if committed != head.committed_pages:
                    raise ServiceError('REVISION_CONFLICT', 'Page progress changed; reload', 409)
            self._authorize(memory, actor, task.task_id, approved)
            return total, tuple(shown)

        return self._transaction(False, read)

    @staticmethod
    def candidate_source_id(approved):
        return 'planset:' + approved.proposal_digest

    def _validate_candidate_manifest(self, memory, state, actor, proposal, *, reading=False, delivery_read=False):
        if type(proposal) is not CandidateManifestProposal:
            raise ServiceError('MANIFEST_INVALID', 'Candidate manifest proposal required', 422)
        task = self._authorize(memory, actor, proposal.scope.task_id,
                               action='read' if reading else 'approve')
        approved = state['current'].get(task.task_id)
        scope = proposal.scope
        approved_scope = approved.scope if approved is not None else None
        if (approved is None
                or (scope.tenant_id, scope.owner_user_id, scope.project_id, scope.task_id,
                    scope.revision, scope.generation, scope.requirement_digest)
                != (approved_scope.tenant_id, approved_scope.owner_user_id,
                    approved_scope.project_id, approved_scope.task_id,
                    approved_scope.revision, approved_scope.generation,
                    approved_scope.requirement_digest)
                or scope.task_version < approved_scope.task_version
                or proposal.operation_id != self.candidate_source_id(approved)
                or proposal.plan_digest != approved.proposal_digest):
            raise ServiceError('MANIFEST_INVALID', 'Candidate source set changed', 409)
        downstream = scope.task_version > approved_scope.task_version
        if reading:
            if (task.task_version < scope.task_version
                    or task.confirmed_revision != scope.revision
                    or task.run_generation != scope.generation
                    or digest({'confirmed_proposal': task.confirmed_proposal_json})
                    != scope.requirement_digest):
                raise ServiceError('REVISION_CONFLICT', 'Candidate scope changed', 409)
            projected = (replace(task, task_version=approved.scope.task_version,
                                 run_status=RunStatus.RUNNING, stage='retrieving')
                         if delivery_read else self._execution_task(task, approved, True))
            validate_plan_set(actor, projected, approved, reading=delivery_read)
        else:
            if RetrievalScope.from_task(task) != scope:
                raise ServiceError('REVISION_CONFLICT', 'Candidate task version changed', 409)
            validate_plan_set(actor, self._execution_task(task, approved, True), approved)

        raw_hits = []
        origin_groups = plan_origin_groups(task, approved)
        hit_groups = {}
        for member in approved.members:
            plan_id = member.plan.request.plan_id
            chain = {}
            for saved, saved_plan_id, request, page in state['operations'].values():
                if saved == approved and saved_plan_id == plan_id:
                    if request.cursor in chain:
                        raise ServiceError('STORAGE_CORRUPT', 'Duplicate page cursor', 500)
                    chain[request.cursor] = page
            cursor, visited = None, set()
            while cursor in chain:
                if cursor in visited:
                    raise ServiceError('STORAGE_CORRUPT', 'Invalid page chain', 500)
                visited.add(cursor)
                page = chain[cursor]
                if page is None:
                    raise ServiceError('SEARCH_IN_PROGRESS', 'Search pages are still pending', 409)
                raw_hits.extend(page.hits)
                for hit in page.hits:
                    hit_groups.setdefault(digest(asdict(hit)), set()).update(origin_groups[plan_id])
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
            else:
                # Ordinary initial publication waits for a finished search.
                # Automatic maintain-target tasks intentionally publish one
                # committed initial window and retain its next approved cursor
                # for refill. A downstream refill batch likewise ends at a
                # validated committed prefix.
                if not downstream and (not automatic_refill(task) or not visited):
                    raise ServiceError('SEARCH_IN_PROGRESS', 'Search is not complete', 409)
            if len(visited) != len(chain):
                raise ServiceError('STORAGE_CORRUPT', 'Disconnected page chain', 500)

        if (not isinstance(proposal.items, tuple) or len(proposal.items) > 100
                or sum(len(item.hits) if isinstance(item, ManifestItem) else 101
                       for item in proposal.items) > 100):
            raise ServiceError('INPUT_LIMIT', 'Candidate manifest supports at most 100 items/hits', 422)
        identities, manifest_hits = set(), []
        context = AccessContext(actor, task.task_id, 'retrieval', 'current')
        for item in proposal.items:
            media = item.media if isinstance(item, ManifestItem) else None
            if (media is None or type(media) is not MediaRecord
                    or item.candidate_id != candidate_identity(media)
                    or item.candidate_id in identities
                    or not isinstance(media.content_digest, str)
                    or re.fullmatch(r'sha256:[0-9a-f]{64}', media.content_digest) is None
                    or not isinstance(item.hits, tuple) or not item.hits
                    or any(type(hit) is not SearchHit for hit in item.hits)):
                raise ServiceError('MANIFEST_INVALID', 'Invalid verified candidate identity', 422)
            identities.add(item.candidate_id)
            manifest_hits.extend(item.hits)
            self.authorization.require_access(context, media.authorized_media_ref, 'read')
        available = Counter(digest(asdict(hit)) for hit in raw_hits)
        selected = Counter(digest(asdict(hit)) for hit in manifest_hits)
        if selected - available:
            raise ServiceError('MANIFEST_INVALID', 'Candidate hit is outside current search', 422)
        # Origin groups decide which VLM checks run and so what can be counted:
        # re-derive them from the approved plan rather than trust the proposal.
        quota_task = any(origin_groups.values())
        for item in proposal.items:
            derived = tuple(sorted(set().union(*(hit_groups[digest(asdict(hit))]
                                                 for hit in item.hits))))
            if item.group_ids != derived or (quota_task and not derived):
                raise ServiceError('MANIFEST_INVALID',
                                   'Candidate quota groups differ from approved search', 422)
        self._authorize(memory, actor, task.task_id, approved,
                        'read' if reading else 'approve')
        return task, approved

    def put_candidate_proposal(self, actor, proposal):
        def put(memory, state):
            self._validate_candidate_manifest(memory, state, actor, proposal)
            old = state['candidate_proposals'].get(proposal.proposal_digest)
            if old is not None and old != proposal:
                raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Candidate preview changed', 409)
            self._validate_candidate_manifest(memory, state, actor, proposal)
            state['candidate_proposals'][proposal.proposal_digest] = proposal
        return self._transaction(True, put)

    def confirm_candidate_manifest(self, actor, proposal, *, key,
                                   expected_task_version, action):
        require_key(key)
        fingerprint = digest([proposal.proposal_digest, expected_task_version, action])

        def confirm(memory, state):
            self._validate_candidate_manifest(memory, state, actor, proposal)
            scoped = (actor.tenant_id, actor.user_id, proposal.scope.task_id, key)
            old = state['candidate_keys'].get(scoped)
            if old is not None and old[0] != fingerprint:
                raise ServiceError('IDEMPOTENCY_CONFLICT', 'Candidate confirmation key changed', 409)
            if action != 'confirm':
                raise ServiceError('CONFIRMATION_REQUIRED', 'Explicit owner confirmation required', 422)
            if expected_task_version != proposal.scope.task_version:
                raise ServiceError('REVISION_CONFLICT', 'Candidate task version changed', 409)
            if state['candidate_proposals'].get(proposal.proposal_digest) != proposal:
                raise ServiceError('MANIFEST_NOT_PREVIEWED', 'Candidate preview required', 409)
            if old is not None:
                return state['candidate_manifests'][old[1]], True
            same_version = [manifest for manifest in state['candidate_manifests'].values()
                            if (manifest.proposal.scope.task_id == proposal.scope.task_id
                                and manifest.proposal.scope.task_version
                                == proposal.scope.task_version
                                and manifest.proposal.operation_id == proposal.operation_id
                                and manifest.proposal.plan_digest == proposal.plan_digest)]
            if any(manifest.proposal != proposal for manifest in same_version):
                raise ServiceError('CANDIDATE_MANIFEST_ALREADY_PUBLISHED',
                                   'Candidate batch already fixed for this task version', 409)
            self._validate_candidate_manifest(memory, state, actor, proposal)
            ref = state['candidate_published'].get(proposal.proposal_digest)
            replayed = ref is not None
            if ref is None:
                ref = 'manifest_' + uuid.uuid4().hex
                manifest = CandidateManifest(ref, proposal)
                state['candidate_manifests'][ref] = manifest
                state['candidate_published'][proposal.proposal_digest] = ref
                state['candidate_events'].append({
                    'event_id': 'candidate-published:' + ref,
                    'kind': 'candidate_manifest_published', 'manifest_ref': ref,
                    'task_id': proposal.scope.task_id,
                    'proposal_digest': proposal.proposal_digest,
                })
            state['candidate_keys'][scoped] = (fingerprint, ref)
            return state['candidate_manifests'][ref], replayed

        return self._transaction(True, confirm)

    def get_candidate_manifest(self, actor, task_id, manifest_ref):
        def read(memory, state):
            manifest = state['candidate_manifests'].get(manifest_ref)
            if manifest is None or manifest.proposal.scope.task_id != task_id:
                raise ServiceError('NOT_FOUND', 'Candidate manifest not found', 404)
            self._validate_candidate_manifest(memory, state, actor, manifest.proposal, reading=True)
            return manifest
        return self._transaction(False, read)

    def get_current_candidate_manifest(self, actor, task_id):
        def read(memory, state):
            task = self._authorize(memory, actor, task_id, action='read')
            approved = state['current'].get(task_id)
            if approved is None:
                return None
            source_id = self.candidate_source_id(approved)
            matches = [manifest for manifest in state['candidate_manifests'].values()
                       if (manifest.proposal.scope.task_id == task_id
                           and manifest.proposal.operation_id == source_id
                           and manifest.proposal.plan_digest == approved.proposal_digest)]
            if not matches:
                return None
            latest = max(manifest.proposal.scope.task_version for manifest in matches)
            matches = [manifest for manifest in matches
                       if manifest.proposal.scope.task_version == latest]
            if len(matches) > 1:
                raise ServiceError('STORAGE_CORRUPT',
                                   'Multiple candidate manifests for one task version', 500)
            self._validate_candidate_manifest(memory, state, actor,
                                              matches[0].proposal, reading=True)
            return matches[0]
        return self._transaction(False, read)

    def candidate_manifest_candidate_ids(self, actor, task_id, approved):
        """Return identities already fixed by earlier batches of this plan set."""
        def read(memory, state):
            task = self._authorize(memory, actor, task_id, approved)
            if state['current'].get(task_id) != approved:
                raise ServiceError('PLAN_NOT_CURRENT', 'Approved set changed', 409)
            self._execution_task(task, approved, True)
            identities = set()
            for manifest in state['candidate_manifests'].values():
                if (manifest.proposal.scope.task_id == task_id
                        and manifest.proposal.plan_digest == approved.proposal_digest
                        and manifest.proposal.operation_id == self.candidate_source_id(approved)):
                    self._validate_candidate_manifest(
                        memory, state, actor, manifest.proposal, reading=True)
                    identities.update(item.candidate_id for item in manifest.proposal.items)
            return frozenset(identities)
        return self._transaction(False, read)

    def reserve_page(self, actor, approved, *, plan_id, request, key,
                     allow_downstream=False):
        require_key(key)
        fingerprint = digest({'set': approved.proposal_digest, 'member': plan_id, 'request': asdict(request)})
        def reserve(memory, state):
            task_id = approved.scope.task_id
            task = self._authorize(memory, actor, task_id, approved, 'approve')
            task = self._execution_task(task, approved, allow_downstream)
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

    def commit_page(self, actor, approved, *, operation_id, plan_id, request, page,
                    allow_downstream=False):
        def commit(memory, state):
            task_id = approved.scope.task_id
            task = self._authorize(memory, actor, task_id, approved, 'approve')
            task = self._execution_task(task, approved, allow_downstream)
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
