from __future__ import annotations

from typing import Any, BinaryIO, ContextManager, Protocol

from .models import Actor, Task
from .proposals import Draft
from .cleaning_models import Candidate, CleaningOperation, CleaningProposal
from .integration_models import (
    AccessContext, IndexBinding, Library, MediaResolution,
    QueryVector, SearchHit, SearchPage, SearchRequest,
)


class TaskRepository(Protocol):
    """Draft/confirmation slice. May be backed by a database or internal API.

    create must atomically store the task and the idempotency record, scoped to
    tenant/user/create-route/key. Same key + digest replays; changed digest raises
    IDEMPOTENCY_CONFLICT. Identity mismatches must never persist a task.
    Returned tasks are detached copies, not mutable storage references.
    get checks both tenant and owner and hides inaccessible tasks with NOT_FOUND.
    Durable adapters must preserve these guarantees across processes/restarts.
    New business calls use save_draft/confirm_draft, never create directly.
    create is the legacy low-level repository contract, retained for tests only.
    """

    def create(self, actor: Actor, key: str, request_digest: str, task: Task) -> tuple[Task, bool]: ...

    def get(self, actor: Actor, task_id: str) -> Task: ...

    def save_draft(self, actor: Actor, key: str, draft: Draft, expected_version: int | None = None) -> tuple[Draft, bool]: ...

    def get_draft(self, actor: Actor, draft_id: str) -> Draft: ...

    def confirm_draft(self, actor: Actor, key: str, draft_id: str, expected_version: int,
                      proposal_digest: str) -> tuple[Task, bool]:
        """Atomically validate draft, freeze proposal, create task/batch and record replay.

        Same draft/version confirmed under different keys still creates only once.
        All validation occurs before writes; stale versions/digests conflict.
        """
        ...


class ProjectContextProvider(Protocol):
    def require_access(self, actor: Actor, project_id: str, dimension_id: str, action: str) -> None: ...


class CatalogRepository(Protocol):
    """Authorization precedes catalog discovery and model-visible descriptions."""

    def find_libraries(self, context: AccessContext, topic: str, media_type: str) -> tuple[Library, ...]: ...


class MediaRepository(Protocol):
    """Resolve by physical binding + index version + vector ID, never ID alone.

    Return one resolution per input hit, in order. Missing/conflicting mappings
    remain explicit; unauthorized resources expose no media metadata.
    """

    def resolve_hits(self, context: AccessContext, hits: tuple[SearchHit, ...]) -> tuple[MediaResolution, ...]: ...


class VectorSearchGateway(Protocol):
    """Execute a frozen plan, recheck authorization and encoder compatibility.

    Resolve physical addresses/field mappings from binding_id on the server.
    Reject stale index versions/cursors; never restart at page one silently.
    Filter templates are server-owned, not arbitrary SQL/expressions from agents.
    Scores retain their source metric and are not globally comparable.
    """

    def search(self, context: AccessContext, request: SearchRequest) -> SearchPage: ...


class EmbeddingProvider(Protocol):
    """Use the encoder fixed by the index binding, including model version."""

    def encode_query(self, context: AccessContext, text: str, binding: IndexBinding) -> QueryVector: ...


class AuthorizationService(Protocol):
    """Read current authority; old authorization_version is not a permanent grant.

    Denial raises ACCESS_DENIED. Unavailable authority fails closed.
    """

    def require_access(self, context: AccessContext, resource_ref: str, action: str) -> None: ...


class MediaAccessGateway(Protocol):
    """Reauthorize an opaque media reference and open a bounded read-only stream.

    Media refs originate from the trusted mapping service, not arbitrary URLs or
    paths supplied by the model. Enforce max_bytes during streaming.
    """

    def open_read(self, context: AccessContext, media_ref: str, max_bytes: int) -> ContextManager[BinaryIO]: ...


class AgentRuntime(Protocol):
    """Version-isolated DSH adapter. No DSH SDK types may cross this boundary."""

    def propose_query_spec(self, trusted_context: dict[str, str], request: str) -> dict[str, Any]: ...


class RetrievalGateway(Protocol):
    """Executes only an already approved physical retrieval plan."""

    def start_batch(self, plan_id: str, plan_version: int, idempotency_key: str) -> str: ...


class CleaningExecutor(Protocol):
    """Internal trusted service contract; no user paths or SDK types.

    submit replays the same logical job for an operation; a changed binding,
    manifest or recipe conflicts. cancel persists a no-future-submit tombstone.
    poll returns binding plus complete versioned per-candidate results.
    """

    def submit(self, operation: CleaningOperation) -> dict: ...

    def poll(self, operation: CleaningOperation) -> dict: ...

    def cancel(self, operation: CleaningOperation) -> dict: ...


class CleaningCatalog(Protocol):
    def resolve(self, tenant_id: str, project_id: str, manifest_ref: str,
                recipe_ref: str) -> tuple[tuple[Candidate, ...], int]: ...


class CleaningRepository(TaskRepository, Protocol):
    """Development slice; durable implementations must enforce fences atomically.

    Result + current pointer + event must commit together. Cancellation removes
    publication rights before dispatch cancellation. Never derive accepted_count.
    """

    def put_cleaning_proposal(self, actor: Actor, proposal: CleaningProposal) -> None: ...
    def get_cleaning_proposal(self, actor: Actor, proposal_id: str) -> CleaningProposal: ...
    def get_cleaning_operation(self, actor: Actor, operation_id: str) -> CleaningOperation: ...
    def reserve_cleaning(self, actor: Actor, key: str, proposal_id: str, proposal_digest: str,
                         expected_version: int) -> tuple[CleaningOperation, bool]: ...
    def attach_cleaning_backend(self, actor: Actor, operation_id: str, backend: dict) -> CleaningOperation: ...
    def publish_cleaning(self, actor: Actor, operation_id: str, response: dict) -> CleaningOperation: ...
    def cancel_cleaning(self, actor: Actor, operation_id: str, key: str,
                        expected_version: int) -> CleaningOperation: ...
    def cleaning_results(self, actor: Actor, task_id: str) -> CleaningOperation | None: ...
