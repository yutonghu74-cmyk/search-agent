"""Shared retrieval ledger contracts and side-effect-free validation.

These values are trusted backend records, not client approval tokens. Validation
alone neither grants authority nor persists approval/results. A repository must
reauthorize and validate under its task transaction before writing anything.
"""
from dataclasses import asdict, dataclass, replace
import math

from .errors import ServiceError
from .integration_models import SearchHit, SearchPage, SearchRequest
from .models import Actor, RunStatus, Task, TaskLifecycle
from .query_spec import digest


def _positive(value):
    return type(value) is int and value > 0


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    tenant_id: str
    owner_user_id: str
    project_id: str
    task_id: str
    revision: int
    generation: int
    task_version: int
    requirement_digest: str

    @classmethod
    def from_task(cls, task: Task):
        if (not _positive(task.confirmed_revision) or not _positive(task.run_generation)
                or not _positive(task.task_version) or not task.project_id
                or not task.confirmed_proposal_json):
            raise ServiceError("RETRIEVAL_SCOPE_INVALID", "Confirmed task required", 409)
        return cls(task.tenant_id, task.owner_user_id, task.project_id, task.task_id,
                   task.confirmed_revision, task.run_generation, task.task_version,
                   digest({"confirmed_proposal": task.confirmed_proposal_json}))


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    """Stored proposal. Instantiating this object does NOT approve a plan.

    scope.task_version is the version displayed for confirmation. Execution uses
    the committed ledger record's scope after its atomic task-version increment.
    Plan ID/version, epoch and physical query are immutable after confirmation.
    """
    scope: RetrievalScope
    epoch: int
    request: SearchRequest

    @property
    def proposal_digest(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True, slots=True)
class RetrievalOperation:
    """Internal authorized snapshot, not a dispatch instruction or result set.

    page=None means reserved, NOT failed or empty. A committed empty SearchPage
    remains a committed result. Recheck ledger authority/fences before any work.
    """
    operation_id: str
    plan: RetrievalPlan
    request: SearchRequest
    page: SearchPage | None

    @property
    def status(self) -> str:
        return "reserved" if self.page is None else "committed"


def validate_plan(plan: RetrievalPlan) -> None:
    request, binding, vector = plan.request, plan.request.binding, plan.request.vector
    if (not _positive(plan.epoch) or not _positive(request.plan_version)
            or not _positive(request.batch_size) or request.batch_size > 100
            or request.cursor is not None or not request.plan_id
            or not request.filter_template_ref
            or not all((binding.binding_id, binding.library_id, binding.index_version,
                        binding.encoder_ref, binding.metric))):
        raise ServiceError("INVALID_RETRIEVAL_PLAN", "Invalid frozen plan", 422)
    if (not _positive(binding.vector_dimension) or vector.encoder_ref != binding.encoder_ref
            or not isinstance(vector.values, tuple) or len(vector.values) != binding.vector_dimension
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector.values)):
        raise ServiceError("ENCODER_MISMATCH", "Incompatible query vector", 422)


def validate_scope(actor: Actor, task: Task, scope: RetrievalScope) -> None:
    if (actor.tenant_id, actor.user_id) != (task.tenant_id, task.owner_user_id):
        raise ServiceError("NOT_FOUND", "Task not found", 404)
    if (task.lifecycle != TaskLifecycle.ACTIVE
            or task.run_status not in (RunStatus.PLANNING, RunStatus.RUNNING)
            or task.stage == "FINALIZING"):
        raise ServiceError("INVALID_STATE_TRANSITION", "Task cannot retrieve", 409)
    if RetrievalScope.from_task(task) != scope:
        raise ServiceError("REVISION_CONFLICT", "Retrieval task scope is stale", 409)


def validate_confirmation(actor: Actor, task: Task, proposal: RetrievalPlan, *,
                          action: str, proposal_digest: str, expected_task_version: int) -> None:
    """Preconditions only; repository performs current auth, CAS and idempotency."""
    validate_scope(actor, task, proposal.scope)
    validate_plan(proposal)
    if action != "confirm":
        raise ServiceError("CONFIRMATION_REQUIRED", "Explicit owner confirmation required", 422)
    if not _positive(expected_task_version) or expected_task_version != task.task_version:
        raise ServiceError("REVISION_CONFLICT", "Task version changed", 409)
    if proposal_digest != proposal.proposal_digest:
        raise ServiceError("PLAN_DIGEST_MISMATCH", "Displayed proposal changed", 409)


def validate_execution(actor: Actor, task: Task, submitted: RetrievalPlan,
                       current: RetrievalPlan | None, request: SearchRequest) -> None:
    """current MUST be read from the authoritative approved-plan ledger."""
    validate_scope(actor, task, submitted.scope)
    validate_plan(submitted)
    if current is None or current != submitted:
        raise ServiceError("PLAN_NOT_CURRENT", "Plan is unapproved or superseded", 409)
    if replace(request, cursor=None) != submitted.request:
        raise ServiceError("PLAN_REQUEST_MISMATCH", "Query differs from approved plan", 409)
    if request.cursor is not None and (not isinstance(request.cursor, str) or not request.cursor):
        raise ServiceError("CURSOR_INVALID", "Invalid continuation cursor", 409)


def validate_page(request: SearchRequest, page: SearchPage, *,
                  previous: SearchPage | None = None, seen_cursors: frozenset[str] = frozenset()) -> None:
    """Validate a page before atomic publication; empty exhausted pages are valid.

    snapshot exhaustion refers to the frozen query's accessible set only. The
    legacy SearchPage cannot represent live/unknown exhaustion, so live exhausted
    is rejected pending a richer connector contract, never guessed to be complete.
    """
    def reject():
        raise ServiceError("INVALID_SEARCH_PAGE", "Inconsistent connector page", 502)

    if (not isinstance(page, SearchPage) or not isinstance(page.hits, tuple)
            or not _positive(request.batch_size) or len(page.hits) > request.batch_size
            or page.stop_reason not in ("more_available", "exhausted", "depth_limit")
            or page.consistency not in ("snapshot", "live")):
        reject()
    if page.next_cursor is not None and (not isinstance(page.next_cursor, str) or not page.next_cursor):
        reject()
    if (page.stop_reason == "more_available") != (page.next_cursor is not None):
        reject()
    if page.next_cursor is not None and (page.next_cursor == request.cursor or page.next_cursor in seen_cursors):
        reject()
    if page.consistency == "snapshot":
        if not isinstance(page.snapshot_ref, str) or not page.snapshot_ref:
            reject()
    elif page.snapshot_ref is not None or page.stop_reason == "exhausted":
        reject()
    if request.cursor is not None and previous is None:
        reject()
    if previous is not None and (previous.next_cursor is None or request.cursor != previous.next_cursor
            or page.consistency != previous.consistency or page.snapshot_ref != previous.snapshot_ref):
        reject()
    for hit in page.hits:
        if (not isinstance(hit, SearchHit) or hit.binding != request.binding or type(hit.vector_id) not in (str, int)
                or (isinstance(hit.vector_id, str) and not hit.vector_id)
                or type(hit.score) not in (int, float) or not math.isfinite(hit.score)):
            reject()
