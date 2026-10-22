from __future__ import annotations

from typing import Any, BinaryIO, ContextManager, Protocol

from .models import Actor, Task
from .proposals import Draft
from .cleaning_models import Candidate, CleaningOperation, CleaningProposal
from .integration_models import (
    AccessContext, IndexBinding, Library, MediaResolution,
    QueryVector, SearchHit, SearchPage, SearchRequest,
)
from .retrieval_contracts import RetrievalOperation, RetrievalPlan
from .materialization_models import MaterializedPage
from .candidate_contracts import CandidateManifest, CandidateManifestProposal
from .conversation_models import ConversationMessage, ConversationSnapshot


class CandidateManifestRepository(Protocol):
    """Trusted backend only; proposals contain verified refs, never client paths.

    put/confirm validate current committed operation in the task transaction and
    reauthorize project/library/media. confirm accepts a freshly rematerialized
    proposal matching a stored preview. Owner-only, strict version CAS, same body
    replay and same-proposal multi-key dedup; manifest/key/outbox commit together.
    Publication does not advance task state/version or start cleaning. get keeps
    stable scope/plan fences but permits later task_version in that same scope;
    still denies inactive tasks and current access failures. No media bytes are
    stored; later consumers must authorize and verify content again.
    """
    def put_proposal(self, actor: Actor, proposal: CandidateManifestProposal) -> None: ...

    def confirm(self, actor: Actor, proposal: CandidateManifestProposal, *, key: str,
                expected_task_version: int, action: str) -> tuple[CandidateManifest, bool]: ...

    def get(self, actor: Actor, task_id: str, manifest_ref: str) -> CandidateManifest: ...


class CandidateMaterializer(Protocol):
    def materialize(self, actor: Actor, task_id: str, operation_id: str) -> ContextManager[MaterializedPage]:
        """Read a committed current retrieval page, map and authorize its media.

        Bounded temporary bytes, verified against authoritative SHA-256 digests;
        no public paths/URLs accepted. Incomplete/conflicting mappings or changed
        authority/content/scope fail closed. Empty committed pages yield no files.
        Same versioned media identity shares a candidate but keeps every hit;
        different source identities remain separate even with identical bytes.
        Files expire on context exit (success or failure). Does NOT publish a
        manifest, approve candidates, dispatch cleaning or certify business rules.
        """
        ...


class RetrievalPlanRepository(Protocol):
    """Retrieval ledger contract; a local SQLite development adapter is available.

    Trusted backend proposals only. Every call rechecks owner/project/library
    authority. confirm atomically checks digest + expected_task_version, increments
    task_version, stores an approved plan rebound to that committed task version,
    supersedes the prior plan, and records route-scoped idempotency. Replays of
    the same body return the original record; changed bodies conflict. Different
    keys confirming the same proposal have one effect. Replays do not reactivate
    superseded plans. The existing task repository must share this transaction.

    commit_page validates the current task/revision/generation/plan/epoch/input,
    cursor provenance and snapshot in the same transaction as the page and outbox.
    Empty pages are recorded without creating a cleaning operation. Do not write
    accepted_count or mark the business task complete based on search exhaustion.
    Network search runs OUTSIDE the transaction. This protocol is not an HTTP API.
    """

    def put_proposal(self, actor: Actor, proposal: RetrievalPlan) -> None: ...

    def confirm(self, actor: Actor, task_id: str, plan_id: str, *, key: str,
                proposal_digest: str, expected_task_version: int,
                action: str) -> tuple[RetrievalPlan, bool]: ...

    def get_current(self, actor: Actor, task_id: str) -> RetrievalPlan | None: ...

    def get_operation(self, actor: Actor, task_id: str, operation_id: str) -> RetrievalOperation:
        """Owner/project/library-authorized immutable current-plan snapshot.

        Unknown/cross-task IDs return NOT_FOUND without disclosing another task.
        Stale scope/epoch, pause/cancel/finalization and revoked authority reject.
        Committed pages contain raw retrieval evidence, never media read grants.
        """
        ...

    def list_pending(self, actor: Actor, task_id: str) -> tuple[RetrievalOperation, ...]:
        """Return only reserved operations for the current approved plan, ID-sorted.

        No approved plan -> empty tuple. Recheck current authorization and fences;
        dependency errors must not become empty results. Bounded to 100 entries;
        overflow raises INPUT_LIMIT without truncating. Reading does not claim,
        lease, dispatch or approve work; concurrent readers may see the same item.
        """
        ...

    def commit_page(self, actor: Actor, operation_id: str, plan: RetrievalPlan,
                    request: SearchRequest, page: SearchPage) -> bool:
        """Return True only on an identical replay; changed operation bodies conflict.

        operation_id must reference a previously reserved, authorized request,
        never a worker-chosen identifier. Stale results are not current results.
        """
        ...

    def reserve_page(self, actor: Actor, plan: RetrievalPlan, request: SearchRequest,
                     key: str) -> tuple[str, bool]:
        """Atomically reserve a logical operation before dispatch, scoped to task/route.

        Validate current plan + owned predecessor cursor; replay returns the same
        operation ID. Unapproved cursors and cross-plan continuations are rejected.
        """
        ...


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

    def save_draft(self, actor: Actor, key: str, draft: Draft,
                   expected_version: int | None = None, *,
                   conversation_message: ConversationMessage | None = None) -> tuple[Draft, bool]:
        """Atomically save the draft and optional independent conversation message."""
        ...

    def get_draft(self, actor: Actor, draft_id: str) -> Draft: ...

    def confirm_draft(self, actor: Actor, key: str, draft_id: str, expected_version: int,
                      proposal_digest: str) -> tuple[Task, bool]:
        """Atomically validate draft, freeze proposal, create task/batch and record replay.

        Same draft/version confirmed under different keys still creates only once.
        All validation occurs before writes; stale versions/digests conflict.
        """
        ...

    def save_feedback_draft(self, actor: Actor, key: str, task_id: str, *,
                            expected_revision: int, expected_task_version: int,
                            proposal_json: str, feedback_text: str, scope: str,
                            draft_id: str | None = None,
                            expected_draft_version: int | None = None,
                            conversation_message: ConversationMessage | None = None) -> tuple[dict[str, Any], bool]:
        """Save an owner-only proposed revision without changing the active task.

        The proposal is a complete normalized snapshot prepared by the business
        service, not executable text. Creating/updating a draft must preserve the
        confirmed revision, generation and run. Idempotency is route scoped.
        """
        ...

    def get_feedback_draft(self, actor: Actor, task_id: str, draft_id: str) -> dict[str, Any]: ...

    def get_pending_feedback(self, actor: Actor, task_id: str) -> dict[str, Any] | None:
        """Return the sole ready feedback draft, or None; never infer a future revision."""
        ...

    def confirm_feedback_draft(self, actor: Actor, key: str, task_id: str, *,
                               draft_id: str, expected_draft_version: int,
                               proposal_digest: str, expected_task_version: int,
                               action: str) -> tuple[dict[str, Any], bool]:
        """Confirm or reject one still-current feedback draft atomically.

        Confirmation publishes one immutable revision, increments task_version
        and generation, supersedes an active run and starts planning the new
        revision. Rejection changes only the draft. Replays return the recorded
        response; stale task/draft versions or changed bodies conflict.
        """
        ...

    def list_revisions(self, actor: Actor, task_id: str) -> tuple[dict[str, Any], ...]: ...

    def list_tasks(self, actor: Actor, project_id: str) -> tuple[Task, ...]:
        """List at most 100 caller-owned confirmed tasks for one project."""
        ...

    def get_conversation(self, actor: Actor, conversation_id: str) -> ConversationSnapshot:
        """Read one owner-only conversation after current project authorization."""
        ...

    def list_conversations(self, actor: Actor, project_id: str) -> tuple[ConversationSnapshot, ...]:
        """List at most 100 owner-only conversations, newest activity first."""
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
    def require_scope(self, task: Task, manifest_ref: str) -> None:
        """Check manifest task/revision/generation binding before resolving it.

        Legacy local fixtures are project-scoped only; published intake manifests
        must enforce exact task ownership and current generation.
        """
        ...

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
