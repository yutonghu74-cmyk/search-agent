"""Local development adapter. Data disappears on process exit."""

import threading
import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from dataclasses import replace

from ..errors import ServiceError
from ..conversation_models import ConversationConfirmation, ConversationMessage, ConversationSnapshot
from ..models import Actor, Task, RunStatus
from ..proposals import Draft
from ..query_spec import digest
from .cleaning_memory import MemoryCleaningMixin


def _feedback_request_belongs_to(value, task_ids):
    """Return True when a feedback request-scope value is tied to a purged task.

    ``_feedback_requests`` maps a scope tuple to ``(fingerprint, view_or_result)``.
    The stored value is either a feedback draft view dict (``save_feedback_draft``)
    carrying ``task_id``, or a confirm result dict (``confirm_feedback_draft``)
    whose ``task`` summary carries ``task_id``. Either way the task id is the
    cascade key; if it is missing the request is not task-scoped and is kept.
    """
    if not task_ids:
        return False
    if isinstance(value, dict):
        if value.get('task_id') in task_ids:
            return True
        nested = value.get('task')
        if isinstance(nested, dict) and nested.get('task_id') in task_ids:
            return True
    return False


class InMemoryTaskRepository(MemoryCleaningMixin):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._idempotency: dict[tuple[str, str, str, str], tuple[str, str]] = {}
        self._drafts: dict[str, Draft] = {}
        self._draft_requests: dict[tuple, tuple[str, Draft]] = {}
        self._batches: dict[str, tuple[str, str, str]] = {}
        self._feedback_drafts: dict[str, dict] = {}
        self._feedback_requests: dict[tuple, tuple[str, dict]] = {}
        self._revisions: dict[str, dict[int, str]] = {}
        self._conversation_messages: dict[str, dict[int, ConversationMessage]] = {}
        self._conversation_confirmations: dict[str, dict[str, ConversationConfirmation]] = {}
        self._init_cleaning()

    def _validate_conversation_message(self, actor, message, *, project_id, draft_id,
                                       conversation_id):
        if not isinstance(message, ConversationMessage):
            raise ServiceError("INVALID_SPEC", "conversation message is invalid", 422)
        if ((message.tenant_id, message.owner_user_id) != (actor.tenant_id, actor.user_id)
                or message.project_id != project_id or message.draft_id != draft_id
                or message.conversation_id != conversation_id):
            raise ServiceError("CONVERSATION_BINDING_CONFLICT", "conversation binding does not match draft", 409)
        messages = self._conversation_messages.get(conversation_id, {})
        if message.sequence != len(messages) + 1:
            raise ServiceError("CONVERSATION_VERSION_CONFLICT", "conversation sequence is stale", 409)
        if messages:
            first = next(iter(messages.values()))
            if ((first.tenant_id, first.owner_user_id, first.project_id)
                    != (message.tenant_id, message.owner_user_id, message.project_id)):
                raise ServiceError("CONVERSATION_BINDING_CONFLICT", "conversation identity cannot change", 409)

    def _store_conversation_message(self, message):
        self._conversation_messages.setdefault(message.conversation_id, {})[message.sequence] = message

    @staticmethod
    def _feedback_view(draft: dict) -> dict:
        return {
            "draft_id": draft["draft_id"], "draft_version": draft["draft_version"],
            "task_id": draft["task_id"], "base_revision": draft["base_revision"],
            "base_task_version": draft["base_task_version"],
            "proposal": json.loads(draft["proposal_json"]),
            "proposal_digest": draft["proposal_digest"], "feedback_text": draft["feedback_text"],
            "scope": draft["scope"], "status": draft["status"],
            "confirmed_revision": draft.get("confirmed_revision"),
            "execution_note": "反馈文本尚未编译为新的可执行清洗规则",
        }

    def get_draft(self, actor: Actor, draft_id: str) -> Draft:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None or (draft.tenant_id, draft.owner_user_id) != (actor.tenant_id, actor.user_id):
                raise ServiceError("NOT_FOUND", "draft was not found", 404)
            return draft

    def save_draft(self, actor: Actor, key: str, draft: Draft, expected_version: int | None = None,
                   *, conversation_message: ConversationMessage | None = None) -> tuple[Draft, bool]:
        if (draft.tenant_id, draft.owner_user_id) != (actor.tenant_id, actor.user_id):
            raise ServiceError("ACCESS_DENIED", "draft identity does not match caller", 403)
        route = ("create", draft.project_id) if expected_version is None else ("update", draft.draft_id)
        scope = (actor.tenant_id, actor.user_id, route, key)
        fingerprint = digest({"proposal": draft.proposal_digest, "expected_version": expected_version,
                              "conversation_text": (conversation_message.text if conversation_message else None),
                              "conversation_kind": (conversation_message.kind if conversation_message else None)})
        with self._lock:
            if scope in self._draft_requests:
                previous, saved = self._draft_requests[scope]
                if previous != fingerprint:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "key was used with another proposal", 409)
                return saved, True
            if expected_version is not None:
                previous = self.get_draft(actor, draft.draft_id)
                if previous.project_id != draft.project_id or previous.dimension_id != draft.dimension_id:
                    raise ServiceError("PROJECT_MISMATCH", "draft binding cannot change", 409)
                if previous.confirmed_task_id or previous.draft_version != expected_version:
                    raise ServiceError("DRAFT_VERSION_CONFLICT", "draft is stale or already confirmed", 409)
                draft = replace(draft, draft_version=expected_version + 1)
            elif draft.draft_id in self._drafts:
                raise ServiceError("DRAFT_ID_CONFLICT", "draft identifier is already in use", 409)
            if conversation_message is not None:
                if not draft.conversation_id:
                    raise ServiceError("CONVERSATION_BINDING_CONFLICT", "draft has no conversation", 409)
                self._validate_conversation_message(
                    actor, conversation_message, project_id=draft.project_id,
                    draft_id=draft.draft_id, conversation_id=draft.conversation_id)
            self._drafts[draft.draft_id] = draft
            if conversation_message is not None:
                self._store_conversation_message(conversation_message)
            self._draft_requests[scope] = (fingerprint, draft)
            return draft, False

    def confirm_draft(self, actor: Actor, key: str, draft_id: str, expected_version: int,
                      proposal_digest: str) -> tuple[Task, bool]:
        fingerprint = digest({"draft_id": draft_id, "expected_draft_version": expected_version,
                              "proposal_digest": proposal_digest, "action": "confirm"})
        scope = (actor.tenant_id, actor.user_id, "POST:/v1/retrieval/tasks", key)
        with self._lock:
            draft = self.get_draft(actor, draft_id)
            if scope in self._idempotency:
                previous, task_id = self._idempotency[scope]
                if previous != fingerprint:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "key was used with another confirmation", 409)
                return replace(self._tasks[task_id]), True
            if draft.draft_version != expected_version or draft.proposal_digest != proposal_digest:
                raise ServiceError("DRAFT_VERSION_CONFLICT", "draft version or proposal digest is stale", 409)
            if draft.confirmed_task_id:
                self._idempotency[scope] = (fingerprint, draft.confirmed_task_id)
                return replace(self._tasks[draft.confirmed_task_id]), True
            spec = json.loads(draft.proposal_json)["query_spec"]
            task = Task(
                task_id="rt_" + uuid.uuid4().hex, tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
                raw_request=spec["raw_request"], media_type=spec["media_type"], target_count=spec["target_count"],
                business_domain=spec["business_domain"], auto_refill=False, execution_mode="interactive",
                request_digest=proposal_digest, confirmed_revision=1, run_status=RunStatus.PLANNING,
                created_at=datetime.now(UTC), project_id=draft.project_id, dimension_id=draft.dimension_id,
                batch_id="batch_" + uuid.uuid4().hex, confirmed_proposal_json=draft.proposal_json,
                conversation_id=draft.conversation_id,
            )
            self._tasks[task.task_id] = task
            self._revisions[task.task_id] = {1: draft.proposal_json}
            self._batches[task.batch_id] = (actor.tenant_id, draft.project_id, task.task_id)
            self._drafts[draft_id] = replace(draft, confirmed_task_id=task.task_id)
            if draft.conversation_id:
                link = ConversationConfirmation(
                    "confirmation_" + uuid.uuid4().hex, draft.conversation_id,
                    draft_id, task.task_id, 1, datetime.now(UTC))
                self._conversation_confirmations.setdefault(draft.conversation_id, {})[draft_id] = link
            self._idempotency[scope] = (fingerprint, task.task_id)
            return replace(task), False

    def save_feedback_draft(self, actor, key, task_id, *, expected_revision,
                            expected_task_version, proposal_json, feedback_text, scope,
                            draft_id=None, expected_draft_version=None,
                            conversation_message=None):
        updating = expected_draft_version is not None
        route = ("update", draft_id) if updating else ("create", task_id)
        request = {"task_id": task_id, "revision": expected_revision,
                   "task_version": expected_task_version, "proposal_json": proposal_json,
                   "feedback_text": feedback_text, "scope": scope,
                   "draft_id": draft_id if updating else None, "draft_version": expected_draft_version,
                   "conversation_text": (conversation_message.text if conversation_message else None),
                   "conversation_kind": (conversation_message.kind if conversation_message else None)}
        fingerprint = digest(request)
        request_scope = (actor.tenant_id, actor.user_id, "feedback", route, key)
        with self._lock:
            task = self.get(actor, task_id)
            old = self._feedback_requests.get(request_scope)
            if old is not None:
                if old[0] != fingerprint:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "key was used with another feedback body", 409)
                return deepcopy(old[1]), True
            if (task.confirmed_revision != expected_revision
                    or task.task_version != expected_task_version):
                raise ServiceError("REVISION_CONFLICT", "feedback base is stale", 409)
            if scope != "all":
                raise ServiceError("FEATURE_NOT_SUPPORTED", "unsupported feedback scope", 422)
            try:
                proposal = json.loads(proposal_json)
            except (TypeError, json.JSONDecodeError):
                raise ServiceError("INVALID_SPEC", "feedback proposal is invalid", 422) from None
            proposal_digest = digest({"project_id": task.project_id, "proposal": proposal})
            if updating:
                stored = self._feedback_drafts.get(draft_id)
                if stored is None or stored["task_id"] != task_id or stored["owner_user_id"] != actor.user_id:
                    raise ServiceError("NOT_FOUND", "feedback draft was not found", 404)
                if stored["status"] != "ready" or stored["draft_version"] != expected_draft_version:
                    raise ServiceError("DRAFT_VERSION_CONFLICT", "feedback draft is stale", 409)
                version = expected_draft_version + 1
            else:
                if any(d["task_id"] == task_id and d["status"] == "ready"
                       for d in self._feedback_drafts.values()):
                    raise ServiceError("PENDING_DRAFT_EXISTS", "confirm or edit the pending feedback draft", 409)
                draft_id, version = draft_id or "feedback_" + uuid.uuid4().hex, 1
            if conversation_message is not None:
                if not task.conversation_id:
                    raise ServiceError("CONVERSATION_BINDING_CONFLICT", "task has no conversation", 409)
                self._validate_conversation_message(
                    actor, conversation_message, project_id=task.project_id,
                    draft_id=draft_id, conversation_id=task.conversation_id)
            stored = {
                "draft_id": draft_id, "draft_version": version, "task_id": task_id,
                "tenant_id": actor.tenant_id, "owner_user_id": actor.user_id,
                "base_revision": expected_revision, "base_task_version": expected_task_version,
                "proposal_json": proposal_json, "proposal_digest": proposal_digest,
                "feedback_text": feedback_text, "scope": scope, "status": "ready",
                "confirmed_revision": None,
                "conversation_id": task.conversation_id,
            }
            self._feedback_drafts[draft_id] = stored
            if conversation_message is not None:
                self._store_conversation_message(conversation_message)
            view = self._feedback_view(stored)
            self._feedback_requests[request_scope] = (fingerprint, view)
            return deepcopy(view), False

    def get_feedback_draft(self, actor, task_id, draft_id):
        with self._lock:
            self.get(actor, task_id)
            stored = self._feedback_drafts.get(draft_id)
            if (stored is None or stored["task_id"] != task_id
                    or (stored["tenant_id"], stored["owner_user_id"]) != (actor.tenant_id, actor.user_id)):
                raise ServiceError("NOT_FOUND", "feedback draft was not found", 404)
            return self._feedback_view(stored)

    def get_pending_feedback(self, actor, task_id):
        with self._lock:
            self.get(actor, task_id)
            pending = [d for d in self._feedback_drafts.values()
                       if d["task_id"] == task_id and d["status"] == "ready"]
            if len(pending) > 1:
                raise ServiceError("STORAGE_CORRUPT", "multiple pending feedback drafts", 500)
            return None if not pending else self._feedback_view(pending[0])

    def confirm_feedback_draft(self, actor, key, task_id, *, draft_id,
                               expected_draft_version, proposal_digest,
                               expected_task_version, action):
        request = {"task_id": task_id, "draft_id": draft_id,
                   "draft_version": expected_draft_version, "proposal_digest": proposal_digest,
                   "task_version": expected_task_version, "action": action}
        fingerprint = digest(request)
        request_scope = (actor.tenant_id, actor.user_id, "POST:/feedback-confirmations", task_id, key)
        with self._lock:
            self.get(actor, task_id)
            task = self._tasks[task_id]
            old = self._feedback_requests.get(request_scope)
            if old is not None:
                if old[0] != fingerprint:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "key was used with another confirmation", 409)
                return deepcopy(old[1]), True
            stored = self._feedback_drafts.get(draft_id)
            if stored is None or stored["task_id"] != task_id:
                raise ServiceError("NOT_FOUND", "feedback draft was not found", 404)
            if (stored["status"] != "ready" or stored["draft_version"] != expected_draft_version
                    or stored["proposal_digest"] != proposal_digest):
                raise ServiceError("DRAFT_VERSION_CONFLICT", "feedback draft or digest is stale", 409)
            if (task.task_version != expected_task_version
                    or stored["base_task_version"] != expected_task_version
                    or task.confirmed_revision != stored["base_revision"]):
                raise ServiceError("REVISION_CONFLICT", "feedback base is stale", 409)
            if action == "reject":
                stored["status"] = "rejected"
                result = {"task": task.to_summary(), "draft": self._feedback_view(stored)}
            elif action == "confirm":
                self._revisions.setdefault(task_id, {}).setdefault(
                    task.confirmed_revision, task.confirmed_proposal_json)
                task.confirmed_revision += 1
                task.task_version += 1
                task.run_generation += 1
                task.run_status = RunStatus.PLANNING
                task.stage = None
                task.confirmed_proposal_json = stored["proposal_json"]
                task.request_digest = stored["proposal_digest"]
                task.raw_request = json.loads(stored["proposal_json"])["query_spec"]["raw_request"]
                task.active_cleaning_operation_id = None
                task.active_plan_digest = None
                self._revisions.setdefault(task_id, {})[task.confirmed_revision] = stored["proposal_json"]
                stored["status"] = "confirmed"
                stored["confirmed_revision"] = task.confirmed_revision
                if stored.get("conversation_id"):
                    link = ConversationConfirmation(
                        "confirmation_" + uuid.uuid4().hex, stored["conversation_id"],
                        draft_id, task_id, task.confirmed_revision, datetime.now(UTC))
                    self._conversation_confirmations.setdefault(
                        stored["conversation_id"], {})[draft_id] = link
                result = {"task": task.to_summary(), "draft": self._feedback_view(stored)}
            else:
                raise ServiceError("INVALID_SPEC", "action must be confirm or reject", 422)
            self._feedback_requests[request_scope] = (fingerprint, result)
            return deepcopy(result), False

    def list_revisions(self, actor, task_id):
        with self._lock:
            task = self.get(actor, task_id)
            revisions = dict(self._revisions.get(task_id, {}))
            if task.confirmed_revision and task.confirmed_proposal_json:
                revisions.setdefault(task.confirmed_revision, task.confirmed_proposal_json)
            return tuple({"revision": revision, "proposal": json.loads(payload),
                          "proposal_digest": digest({"project_id": task.project_id,
                                                     "proposal": json.loads(payload)})}
                         for revision, payload in sorted(revisions.items()))

    def list_tasks(self, actor, project_id):
        with self._lock:
            rows = [replace(task) for task in self._tasks.values()
                    if (task.tenant_id, task.owner_user_id, task.project_id)
                    == (actor.tenant_id, actor.user_id, project_id)]
            rows.sort(key=lambda task: task.task_id)
            rows.sort(key=lambda task: task.created_at, reverse=True)
            return tuple(rows[:100])

    def list_tasks_with_feedback(self, actor, project_id):
        with self._lock:
            tasks = self.list_tasks(actor, project_id)
            return tuple((task, self.get_pending_feedback(actor, task.task_id))
                         for task in tasks)

    def list_tasks_page_with_feedback(self, actor, project_id, *, limit, after=None):
        with self._lock:
            rows = [replace(task) for task in self._tasks.values()
                    if (task.tenant_id, task.owner_user_id, task.project_id)
                    == (actor.tenant_id, actor.user_id, project_id)
                    and (after is None or (task.created_at, task.task_id) < after)]
            rows.sort(key=lambda task: (task.created_at, task.task_id), reverse=True)
            return tuple((task, self.get_pending_feedback(actor, task.task_id))
                         for task in rows[:limit])

    def get_conversation(self, actor, conversation_id):
        with self._lock:
            messages = self._conversation_messages.get(conversation_id)
            if not messages:
                raise ServiceError("NOT_FOUND", "conversation was not found", 404)
            ordered = tuple(message for _, message in sorted(messages.items()))
            first = ordered[0]
            if (first.tenant_id, first.owner_user_id) != (actor.tenant_id, actor.user_id):
                raise ServiceError("NOT_FOUND", "conversation was not found", 404)
            confirmations = tuple(
                sorted(self._conversation_confirmations.get(conversation_id, {}).values(),
                       key=lambda item: (item.revision, item.confirmation_id)))
            return ConversationSnapshot(conversation_id, first.project_id, ordered, confirmations)

    def list_conversations(self, actor, project_id):
        with self._lock:
            rows = []
            for conversation_id, messages in self._conversation_messages.items():
                if not messages:
                    continue
                first = next(iter(messages.values()))
                if ((first.tenant_id, first.owner_user_id, first.project_id)
                        != (actor.tenant_id, actor.user_id, project_id)):
                    continue
                rows.append(self.get_conversation(actor, conversation_id))
            rows.sort(key=lambda item: item.messages[-1].created_at, reverse=True)
            return tuple(rows[:100])

    def delete_conversation(self, actor, conversation_id):
        with self._lock:
            snapshot = self.get_conversation(actor, conversation_id)
            if snapshot.confirmations:
                raise ServiceError('CONVERSATION_DELETE_FORBIDDEN',
                                   '已确认或进入检索的 Chat 不能删除', 409)
            draft_ids = {message.draft_id for message in snapshot.messages}
            self._conversation_messages.pop(conversation_id, None)
            self._conversation_confirmations.pop(conversation_id, None)
            for draft_id in draft_ids:
                draft = self._drafts.get(draft_id)
                if draft is not None and not draft.confirmed_task_id:
                    self._drafts.pop(draft_id, None)
            self._draft_requests = {scope: value for scope, value in self._draft_requests.items()
                                    if value[1].conversation_id != conversation_id}

    def hard_delete_conversation(self, actor, conversation_id):
        """Force-delete an owner-only conversation and cascade its task tree.

        Unlike ``delete_conversation`` this path is allowed on confirmed Chats:
        it removes the conversation messages/confirmations, every linked formal
        task and its revisions/batches/feedback drafts, all conversation drafts
        (confirmed or not), and the task-scoped cleaning records. Idempotency
        and request-scope indexes are filtered so no dangling scope survives.
        Returns a cascade summary; the conversation must already exist and be
        owned by ``actor`` (otherwise NOT_FOUND is raised before any mutation).
        """
        with self._lock:
            snapshot = self.get_conversation(actor, conversation_id)
            task_ids = {confirmation.task_id for confirmation in snapshot.confirmations}
            draft_ids = {message.draft_id for message in snapshot.messages}
            purged_batches = []
            for task_id in task_ids:
                task = self._tasks.pop(task_id, None)
                if task is not None:
                    purged_batches.append(task.batch_id)
            for batch_id in purged_batches:
                self._batches.pop(batch_id, None)
            for task_id in task_ids:
                self._revisions.pop(task_id, None)
                self._feedback_drafts = {fd_id: fd for fd_id, fd in self._feedback_drafts.items()
                                         if fd.get('task_id') != task_id}
            self._feedback_requests = {
                scope: value for scope, value in self._feedback_requests.items()
                if not _feedback_request_belongs_to(value[1], task_ids)}
            self._idempotency = {scope: value for scope, value in self._idempotency.items()
                                 if value[1] not in task_ids}
            self._conversation_messages.pop(conversation_id, None)
            self._conversation_confirmations.pop(conversation_id, None)
            for draft_id in draft_ids:
                self._drafts.pop(draft_id, None)
            self._draft_requests = {scope: value for scope, value in self._draft_requests.items()
                                    if value[1].conversation_id != conversation_id}
            cleaning_summary = self._purge_cleaning_for_tasks(task_ids)
            return {
                'conversation_id': conversation_id,
                'tasks': sorted(task_ids),
                'drafts': sorted(draft_ids),
                'batches': sorted(purged_batches),
                'cleaning': cleaning_summary,
            }

    def hard_delete_task(self, actor, task_id):
        """Force-delete a legacy task tree by task id, with no conversation boundary.

        Mirrors ``hard_delete_conversation`` but keyed by task id directly, for
        pre-conversation legacy tasks that carry no ``conversation_id``. A
        conversation-linked task is refused (``TASK_HAS_CONVERSATION``) so no
        confirmation is left dangling — those must go through the conversation
        force path. Purges the task, its revisions/batches/feedback drafts,
        idempotency and request-scope indexes, the confirmed draft, and
        task-scoped cleaning records. Returns a cascade summary; the task must
        exist and be owned by ``actor`` (otherwise NOT_FOUND before any mutation).
        """
        with self._lock:
            task = self.get(actor, task_id)
            if task.conversation_id:
                raise ServiceError('TASK_HAS_CONVERSATION',
                                   'conversation-linked tasks must be deleted via the conversation force path', 409)
            task_ids = {task_id}
            purged_batches = []
            removed = self._tasks.pop(task_id, None)
            if removed is not None:
                purged_batches.append(removed.batch_id)
            for batch_id in purged_batches:
                self._batches.pop(batch_id, None)
            self._revisions.pop(task_id, None)
            self._feedback_drafts = {fd_id: fd for fd_id, fd in self._feedback_drafts.items()
                                     if fd.get('task_id') != task_id}
            self._feedback_requests = {
                scope: value for scope, value in self._feedback_requests.items()
                if not _feedback_request_belongs_to(value[1], task_ids)}
            self._idempotency = {scope: value for scope, value in self._idempotency.items()
                                 if value[1] not in task_ids}
            draft_ids = [draft_id for draft_id, draft in self._drafts.items()
                         if draft.confirmed_task_id == task_id]
            draft_id_set = set(draft_ids)
            for draft_id in draft_ids:
                self._drafts.pop(draft_id, None)
            self._draft_requests = {scope: value for scope, value in self._draft_requests.items()
                                    if value[1].draft_id not in draft_id_set}
            cleaning_summary = self._purge_cleaning_for_tasks(task_ids)
            return {
                'task_id': task_id,
                'tasks': sorted(task_ids),
                'drafts': sorted(draft_ids),
                'batches': sorted(purged_batches),
                'cleaning': cleaning_summary,
            }

    def create(self, actor: Actor, key: str, request_digest: str, task: Task) -> tuple[Task, bool]:
        if (task.tenant_id, task.owner_user_id) != (actor.tenant_id, actor.user_id):
            raise ServiceError("ACCESS_DENIED", "task identity does not match caller", 403)
        scope = (actor.tenant_id, actor.user_id, "POST:/v1/retrieval/tasks", key)
        with self._lock:
            existing = self._idempotency.get(scope)
            if existing is not None:
                existing_digest, task_id = existing
                if existing_digest != request_digest:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "idempotency key was used with another body", 409)
                return replace(self._tasks[task_id]), True
            if task.task_id in self._tasks:
                raise ServiceError("TASK_ID_CONFLICT", "task identifier is already in use", 409)
            self._tasks[task.task_id] = replace(task)
            self._idempotency[scope] = (request_digest, task.task_id)
            return replace(task), False

    def get(self, actor: Actor, task_id: str) -> Task:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.tenant_id != actor.tenant_id or task.owner_user_id != actor.user_id:
                raise ServiceError("NOT_FOUND", "task was not found", 404)
            return replace(task)
