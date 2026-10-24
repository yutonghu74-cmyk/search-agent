"""Bounded, explicit ticks over an approved plan set; no autonomous approval."""
from dataclasses import asdict, replace

from .errors import ServiceError
from .integration_models import AccessContext, SearchPage, SearchRequest
from .multi_branch_contracts import PlanSetMemberProgress
from .query_spec import digest


class RetrievalCoordinator:
    def __init__(self, repository, gateway):
        if not callable(getattr(repository, 'get_progress', None)):
            raise ServiceError('RECOVERY_NOT_CONFIGURED', 'Authorized page recovery reader required', 503)
        self.repository, self.gateway = repository, gateway

    def put_proposal(self, actor, proposal):
        self.repository.put_proposal(actor, proposal)

    def confirm(self, actor, task_id, *, proposal_digest, expected_task_version, action, key):
        # Explicit owner action only. Confirmation never dispatches a network call.
        return self.repository.confirm(actor, task_id, proposal_digest=proposal_digest,
            expected_task_version=expected_task_version, action=action, key=key)

    def _progress(self, actor, approved):
        if self.repository.get_current(actor, approved.scope.task_id) != approved:
            raise ServiceError('PLAN_NOT_CURRENT', 'Approved plan set changed', 409)
        rows = self.repository.get_progress(actor, approved)
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

    @staticmethod
    def _next(row):
        if row.page is None:
            return row.request
        if row.page.next_cursor is None:
            return None
        return replace(row.request, cursor=row.page.next_cursor)

    def tick(self, actor, task_id, *, max_requests=1, authorization_version='current'):
        """One external attempt/member/tick. Retry means another explicit tick.

        No sleeping loop, hidden infinite retry, background thread, quota decision,
        or accepted-count updates. Production leases/attempt budgets are separate.
        """
        if (type(max_requests) is not int or not 1 <= max_requests <= 64
                or not isinstance(authorization_version, str) or not authorization_version.strip()):
            raise ServiceError('INPUT_LIMIT', 'Bounded tick and authorization version required', 422)
        approved = self.repository.get_current(actor, task_id)
        if approved is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'Explicit whole-set approval required', 409)
        initial = self._progress(actor, approved)
        # Prefer pending attempts, then least-advanced branches; stable order on ties.
        ordered = sorted(initial, key=lambda row: (not (row.operation_id and row.page is None), row.committed_pages))
        outcomes, requests = [], 0
        for old in ordered:
            if requests >= max_requests:
                break
            row = next(r for r in self._progress(actor, approved) if r.plan_id == old.plan_id)
            request = self._next(row)
            if request is None:
                continue
            key = 'coord_' + digest({'set': approved.proposal_digest, 'request': asdict(request)})[7:]
            operation_id, _ = self.repository.reserve_page(actor, approved,
                plan_id=row.plan_id, request=request, key=key)
            current = next(r for r in self._progress(actor, approved) if r.plan_id == row.plan_id)
            if current.operation_id != operation_id or current.page is not None:
                # Another worker already committed/advanced. Never reissue that page.
                continue
            requests += 1
            context = AccessContext(actor, task_id, 'retrieval', authorization_version)
            try:
                page = self.gateway.search(context, request)
            except ServiceError as error:
                if not error.retryable or error.status not in (429, 502, 503, 504):
                    raise
                self._progress(actor, approved)
                outcomes.append({'plan_id': row.plan_id, 'operation_id': operation_id,
                                 'status': 'retry_pending', 'code': error.code})
                continue
            replay = self.repository.commit_page(actor, approved, operation_id=operation_id,
                plan_id=row.plan_id, request=request, page=page)
            outcomes.append({'plan_id': row.plan_id, 'operation_id': operation_id,
                             'status': 'committed', 'replayed': replay})
        final = self._progress(actor, approved)
        return {'task_id': task_id, 'set_digest': approved.proposal_digest,
                'network_attempts': requests, 'outcomes': outcomes,
                'pagination_stopped': all(self._next(row) is None for row in final),
                'members': [{'plan_id': row.plan_id, 'committed_pages': row.committed_pages,
                             'pending': row.operation_id is not None and row.page is None,
                             'stop_reason': row.page.stop_reason if row.page else None} for row in final]}
