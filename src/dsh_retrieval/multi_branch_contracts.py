"""Multi-query approval contracts. These validators never grant authority."""
from dataclasses import asdict, dataclass, replace
import json
from typing import Protocol

from .errors import ServiceError
from .integration_models import SearchPage, SearchRequest
from .models import Actor, Task
from .physical_query_preparation import PreparedQuery
from .query_branches import compile_query_branches
from .query_spec import digest
from .retrieval_contracts import RetrievalScope, validate_plan, validate_scope


@dataclass(frozen=True, slots=True)
class RetrievalPlanSet:
    scope: RetrievalScope
    epoch: int
    members: tuple[PreparedQuery, ...]

    @property
    def proposal_digest(self):
        # Order is part of the displayed dispatch order, not silently canonicalized.
        return digest(asdict(self))


def validate_plan_set(actor: Actor, task: Task, plan_set: RetrievalPlanSet) -> None:
    if not isinstance(plan_set, RetrievalPlanSet):
        raise ServiceError('INVALID_PLAN_SET', 'Plan set required', 422)
    validate_scope(actor, task, plan_set.scope)
    if (type(plan_set.epoch) is not int or plan_set.epoch < 1
            or not isinstance(plan_set.members, tuple) or not 1 <= len(plan_set.members) <= 64):
        raise ServiceError('INVALID_PLAN_SET', 'Plan set must contain 1-64 members', 422)
    preview = compile_query_branches(json.loads(task.confirmed_proposal_json)['query_spec'])
    branches = {branch['branch_id']: branch for branch in preview['branches']}
    seen_queries, seen_plans, seen_branches = set(), set(), set()
    bindings = {}
    for member in plan_set.members:
        if not isinstance(member, PreparedQuery):
            raise ServiceError('INVALID_PLAN_SET', 'Prepared query required', 422)
        plan = member.plan
        validate_plan(plan)
        branch = branches.get(member.branch_id)
        if (plan.scope != plan_set.scope or plan.epoch != plan_set.epoch
                or branch is None or member.logical_preview_digest != preview['preview_digest']
                or member.query_fingerprint != branch['query_fingerprint']):
            raise ServiceError('PLAN_SET_BINDING_MISMATCH', 'Mixed scope or logical query', 409)
        binding = plan.request.binding
        physical_key = (member.query_fingerprint, binding)
        if physical_key in seen_queries or plan.request.plan_id in seen_plans:
            raise ServiceError('DUPLICATE_QUERY', 'Duplicate physical query or plan ID', 422)
        if binding.binding_id in bindings and bindings[binding.binding_id] != binding:
            raise ServiceError('INDEX_BINDING_MISMATCH', 'Mixed versions for one binding', 409)
        bindings[binding.binding_id] = binding
        seen_queries.add(physical_key)
        seen_plans.add(plan.request.plan_id)
        seen_branches.add(member.branch_id)
    if preview['branches'][0]['branch_id'] not in seen_branches:
        raise ServiceError('ORIGINAL_QUERY_REQUIRED', 'Original query branch must be retained', 422)


def validate_set_confirmation(actor, task, plan_set, *, action,
                              proposal_digest, expected_task_version):
    validate_plan_set(actor, task, plan_set)
    if action != 'confirm':
        raise ServiceError('CONFIRMATION_REQUIRED', 'Explicit owner confirmation required', 422)
    if type(expected_task_version) is not int or expected_task_version != task.task_version:
        raise ServiceError('REVISION_CONFLICT', 'Task version changed', 409)
    if proposal_digest != plan_set.proposal_digest:
        raise ServiceError('PLAN_DIGEST_MISMATCH', 'Displayed plan set changed', 409)


def validate_member_execution(actor, task, submitted, current, *, plan_id, request):
    """current must come from a committed approved-set ledger, never the caller.

    Cursor provenance and operation reservation are additional repository checks.
    This only validates membership and frozen physical request equality.
    """
    validate_plan_set(actor, task, submitted)
    if current is None or current != submitted:
        raise ServiceError('PLAN_NOT_CURRENT', 'Set unapproved or superseded', 409)
    member = next((m for m in current.members if m.plan.request.plan_id == plan_id), None)
    if member is None or not isinstance(request, SearchRequest):
        raise ServiceError('PLAN_REQUEST_MISMATCH', 'Unknown member request', 409)
    if replace(request, cursor=None) != member.plan.request:
        raise ServiceError('PLAN_REQUEST_MISMATCH', 'Physical query differs from approved member', 409)
    if request.cursor is not None and (not isinstance(request.cursor, str) or not request.cursor):
        raise ServiceError('CURSOR_INVALID', 'Invalid continuation cursor', 409)


class RetrievalPlanSetRepository(Protocol):
    """Future core transaction boundary, not implemented by single-plan storage.

    put rechecks owner/project/all libraries and exact current scope. confirm
    performs scope CAS, all-member authorization, digest check and one task-version
    increment atomically with idempotency, approved-set pointer and outbox. Rebind
    every member scope to the committed version; never approve members one by one.
    Same proposal under different keys has one effect; changed key body conflicts;
    replay cannot reactivate an old set. Epoch must increase when replacing a set.

    reserve validates current set, member, cursor provenance and authorization
    inside the shared task transaction. Cursor chains are isolated per member;
    outstanding reservations across the set are bounded to 100. Atomic budget
    checks precede reservation. Network work is outside transactions. Commit must
    fence set epoch/digest, member and operation as well as task scope; reuse
    validate_page for page validation. No accepted_count writes from retrieval.
    """
    def put_proposal(self, actor: Actor, proposal: RetrievalPlanSet) -> None: ...

    def confirm(self, actor: Actor, task_id: str, *, proposal_digest: str,
                expected_task_version: int, action: str, key: str) -> tuple[RetrievalPlanSet, bool]: ...

    def get_current(self, actor: Actor, task_id: str) -> RetrievalPlanSet | None: ...

    def reserve_page(self, actor: Actor, approved: RetrievalPlanSet, *, plan_id: str,
                     request: SearchRequest, key: str) -> tuple[str, bool]: ...

    def commit_page(self, actor: Actor, approved: RetrievalPlanSet, *, operation_id: str,
                    plan_id: str, request: SearchRequest, page: SearchPage) -> bool: ...
