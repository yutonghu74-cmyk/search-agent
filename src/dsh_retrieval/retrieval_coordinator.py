"""Bounded, explicit ticks over an approved plan set; no autonomous approval."""
from dataclasses import asdict, replace

from .errors import ServiceError
from .integration_models import AccessContext, SearchPage, SearchRequest
from .multi_branch_contracts import PlanSetMemberProgress
from .query_spec import digest
from .conversation_timing import timed_call, increment
from .pipeline_timing import measured, bind


class RetrievalCoordinator:
    def __init__(self, repository, gateway, *, budget=None, strict_unknown=False):
        if not callable(getattr(repository, 'get_progress', None)):
            raise ServiceError('RECOVERY_NOT_CONFIGURED', 'Authorized page recovery reader required', 503)
        self.repository, self.gateway, self.budget = repository, gateway, budget
        self.strict_unknown = strict_unknown

    def _search(self, approved, context, request):
        if self.budget is not None:
            self.budget._charge(approved.scope, request.batch_size)
        increment('vector_attempts')
        return timed_call('vector_search', self.gateway.search, context, request)

    def put_proposal(self, actor, proposal):
        self.repository.put_proposal(actor, proposal)

    def confirm(self, actor, task_id, *, proposal_digest, expected_task_version, action, key):
        # Explicit owner action only. Confirmation never dispatches a network call.
        return self.repository.confirm(actor, task_id, proposal_digest=proposal_digest,
            expected_task_version=expected_task_version, action=action, key=key)

    def _progress(self, actor, approved, *, allow_downstream=False):
        snapshot_reader = getattr(self.repository, 'get_progress_snapshot', None)
        if callable(snapshot_reader):
            current, rows = snapshot_reader(actor, approved.scope.task_id,
                                             allow_downstream=allow_downstream)
        else:
            current = (self.repository.get_browse_current(actor, approved.scope.task_id)
                       if allow_downstream else
                       self.repository.get_current(actor, approved.scope.task_id))
            rows = None
        if current != approved:
            raise ServiceError('PLAN_NOT_CURRENT', 'Approved plan set changed', 409)
        if not callable(snapshot_reader):
            rows = (self.repository.get_progress(actor, approved, allow_downstream=True)
                    if allow_downstream else self.repository.get_progress(actor, approved))
        members = {member.plan.request.plan_id: member.plan.request for member in approved.members}
        if (type(rows) is not tuple or len(rows) != len(members)
                or any(type(row) is not PlanSetMemberProgress for row in rows)
                or tuple(row.plan_id for row in rows) != tuple(members)):
            raise ServiceError('STORAGE_CORRUPT', 'Invalid member progress', 500)
        for row in rows:
            if (type(row.request) is not SearchRequest
                    or replace(row.request, cursor=None) != members[row.plan_id]
                    or type(row.committed_pages) is not int or row.committed_pages < 0
                    or row.operation_id is None and (row.page is not None or row.committed_pages != 0 or row.request.cursor is not None)
                    or row.operation_id is not None and (not isinstance(row.operation_id, str) or not row.operation_id)
                    or row.page is not None and (type(row.page) is not SearchPage or row.committed_pages < 1)):
                raise ServiceError('STORAGE_CORRUPT', 'Invalid member checkpoint', 500)
        return rows

    @measured('retrieval_page')
    def tick_position(self, actor, approved, *, plan_id, expected_cursor,
                      authorization_version='current'):
        from contextlib import nullcontext
        guard = self.budget.execution(approved.scope) if self.budget is not None else nullcontext()
        with guard:
            return self._tick_position(actor, approved, plan_id=plan_id,
                expected_cursor=expected_cursor, authorization_version=authorization_version)

    def _tick_position(self, actor, approved, *, plan_id, expected_cursor,
                       authorization_version='current'):
        """Execute exactly one approved downstream page for a refill reservation."""
        rows = self._progress(actor, approved, allow_downstream=True)
        row = next((item for item in rows if item.plan_id == plan_id), None)
        if row is None:
            raise ServiceError('PLAN_REQUEST_MISMATCH', 'Unknown refill plan member', 409)
        request = self._next(row)
        if request is None:
            return {'status': 'replayed', 'network_attempts': 0, 'outcomes': []}
        if request.cursor != expected_cursor:
            raise ServiceError('REFILL_POSITION_CHANGED', 'Refill cursor changed', 409)
        key = 'refill_' + digest({'set': approved.proposal_digest,
                                  'plan_id': plan_id, 'cursor': expected_cursor})[7:]
        operation_id, replayed = self.repository.reserve_page(
            actor, approved, plan_id=plan_id, request=request, key=key,
            allow_downstream=True)
        current = next(item for item in self._progress(
            actor, approved, allow_downstream=True) if item.plan_id == plan_id)
        if current.page is not None:
            return {'status': 'replayed', 'network_attempts': 0, 'outcomes': []}
        context = AccessContext(actor, approved.scope.task_id, 'retrieval',
                                authorization_version)
        try:
            page = self._search(approved, context, request)
        except ServiceError as error:
            if error.retryable and error.status in (429, 502, 503, 504):
                increment('retrieval_retries')
                return {'status': 'retry_pending', 'network_attempts': 0 if error.code == 'SEARCH_BUSY' else 1,
                        'outcomes': [{'plan_id': plan_id, 'operation_id': operation_id,
                                      'status': 'retry_pending', 'code': error.code}]}
            raise
        committed_replay = timed_call('retrieval_commit', self.repository.commit_page,
            actor, approved, operation_id=operation_id, plan_id=plan_id,
            request=request, page=page, allow_downstream=True)
        return {'status': 'replayed' if replayed and committed_replay else 'committed',
                'network_attempts': 1,
                'outcomes': [{'plan_id': plan_id, 'operation_id': operation_id,
                              'status': 'committed', 'replayed': committed_replay}]}

    @staticmethod
    def _next(row):
        if row.page is None:
            return row.request
        if row.page.next_cursor is None:
            return None
        return replace(row.request, cursor=row.page.next_cursor)

    @measured('retrieval_tick')
    def tick(self, actor, task_id, *, max_requests=1, authorization_version='current', plan_ids=None):
        from contextlib import nullcontext
        if (type(max_requests) is not int or not 1 <= max_requests <= 64
                or not isinstance(authorization_version, str) or not authorization_version.strip()):
            raise ServiceError('INPUT_LIMIT', 'Bounded tick and authorization version required', 422)
        approved = self.repository.get_current(actor, task_id)
        if approved is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'Explicit whole-set approval required', 409)
        guard = self.budget.execution(approved.scope) if self.budget is not None else nullcontext()
        with guard:
            return self._tick(actor, task_id, approved=approved, max_requests=max_requests,
                              authorization_version=authorization_version, plan_ids=plan_ids)

    def _tick(self, actor, task_id, *, approved, max_requests=1, authorization_version='current', plan_ids=None):
        """One external attempt/member/tick under the optional service budget.

        No autonomous approval, accepted-count update, or unbounded retry loop.
        """
        bind(task_id=task_id, revision=approved.scope.revision,
             generation=approved.scope.generation, task_version=approved.scope.task_version)
        initial = self._progress(actor, approved)
        if plan_ids is not None and (type(plan_ids) is not frozenset or not plan_ids
                or not plan_ids <= {row.plan_id for row in initial}):
            raise ServiceError('INVALID_ROUTE_SELECTION', 'Approved plan subset required', 422)
        # Prefer pending attempts, then least-advanced branches; stable order on ties.
        ordered = sorted(initial, key=lambda row: (not (row.operation_id and row.page is None), row.committed_pages))
        outcomes, requests = [], 0
        for old in ordered:
            if plan_ids is not None and (old.plan_id not in plan_ids or old.committed_pages >= 1):
                continue
            if requests >= max_requests:
                break
            row = next(r for r in self._progress(actor, approved) if r.plan_id == old.plan_id)
            request = self._next(row)
            if request is None:
                continue
            key = 'coord_' + digest({'set': approved.proposal_digest, 'request': asdict(request)})[7:]
            operation_id, reserved_before = self.repository.reserve_page(actor, approved,
                plan_id=row.plan_id, request=request, key=key)
            current = next(r for r in self._progress(actor, approved) if r.plan_id == row.plan_id)
            if current.operation_id != operation_id or current.page is not None:
                # Another worker already committed/advanced. Never reissue that page.
                continue
            if self.strict_unknown and reserved_before:
                raise ServiceError('AGENT_SEARCH_OUTCOME_UNKNOWN', 'Original page reservation needs reconciliation; not resent', 409)
            requests += 1
            context = AccessContext(actor, task_id, 'retrieval', authorization_version)
            try:
                page = self._search(approved, context, request)
            except ServiceError as error:
                if self.strict_unknown and error.code != 'SEARCH_BUDGET_EXHAUSTED':
                    raise ServiceError('AGENT_SEARCH_OUTCOME_UNKNOWN', 'Search outcome needs reconciliation; not retried', 409) from None
                if not error.retryable or error.status not in (429, 502, 503, 504):
                    raise
                if error.code == 'SEARCH_BUSY':
                    requests -= 1
                increment('retrieval_retries')
                self._progress(actor, approved)
                outcomes.append({'plan_id': row.plan_id, 'operation_id': operation_id,
                                 'status': 'retry_pending', 'code': error.code})
                continue
            except Exception:
                if self.strict_unknown:
                    raise ServiceError('AGENT_SEARCH_OUTCOME_UNKNOWN', 'Search transport outcome needs reconciliation', 409) from None
                raise
            try:
                replay = timed_call('retrieval_commit', self.repository.commit_page, actor, approved, operation_id=operation_id,
                    plan_id=row.plan_id, request=request, page=page)
            except Exception:
                if self.strict_unknown:
                    raise ServiceError('AGENT_SEARCH_OUTCOME_UNKNOWN', 'Page publication outcome needs reconciliation', 409) from None
                raise
            outcomes.append({'plan_id': row.plan_id, 'operation_id': operation_id,
                             'status': 'committed', 'replayed': replay})
        final = self._progress(actor, approved)
        return {'task_id': task_id, 'set_digest': approved.proposal_digest,
                'network_attempts': requests, 'outcomes': outcomes,
                'pagination_stopped': all(self._next(row) is None for row in final),
                'members': [{'plan_id': row.plan_id, 'committed_pages': row.committed_pages,
                             'pending': row.operation_id is not None and row.page is None,
                             **({'initial_deferred': row.plan_id not in plan_ids} if plan_ids is not None else {}),
                             'stop_reason': row.page.stop_reason if row.page else None} for row in final]}
