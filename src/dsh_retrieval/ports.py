from __future__ import annotations

from typing import Any, BinaryIO, ContextManager, Protocol

from .models import Actor, Task
from .integration_models import (
    AccessContext, IndexBinding, Library, MediaResolution,
    QueryVector, SearchHit, SearchPage, SearchRequest,
)


class TaskRepository(Protocol):
    """Current create/read slice. May be backed by a database or internal API.

    create must atomically store the task and the idempotency record, scoped to
    tenant/user/create-route/key. Same key + digest replays; changed digest raises
    IDEMPOTENCY_CONFLICT. Identity mismatches must never persist a task.
    Returned tasks are detached copies, not mutable storage references.
    get checks both tenant and owner and hides inaccessible tasks with NOT_FOUND.
    Durable adapters must preserve these guarantees across processes/restarts.
    """

    def create(self, actor: Actor, key: str, request_digest: str, task: Task) -> tuple[Task, bool]: ...

    def get(self, actor: Actor, task_id: str) -> Task: ...


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
    """Data-Juicer/business-operator adapter using immutable manifests."""

    def submit(self, manifest_ref: str, recipe_ref: str, operation_id: str) -> None: ...
