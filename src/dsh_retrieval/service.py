from __future__ import annotations

import json
import re
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from .conversation_models import ConversationMessage
from .errors import ServiceError, invalid
from .models import Actor
from .ports import TaskRepository, ProjectContextProvider
from .proposals import Draft, normalize_proposal
from .adapters.memory import InMemoryTaskRepository  # compatibility import
from .adapters.projects import LocalProjectAccess


def _revision_changes(previous: dict[str, Any], current: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Bounded semantic diff for revision history; excludes appended prose noise."""
    old = previous.get("query_spec", {})
    new = current.get("query_spec", {})
    paths = (
        ("target_count", "目标数量"),
        ("grouped_expansion", "分组与配额"),
        ("retrieval_execution", "检索执行范围"),
        ("conditions", "清洗条件"),
        ("cleaning_conditions", "清洗检测条件"),
        ("constraints", "清洗约束"),
        ("refill_policy", "补数策略"),
    )
    rows = []
    for field, label in paths:
        if old.get(field) != new.get(field):
            rows.append({"field": field, "label": label,
                         "before": deepcopy(old.get(field)),
                         "after": deepcopy(new.get(field))})
    if old.get("raw_request") != new.get("raw_request"):
        feedback = str(new.get("raw_request", "")).removeprefix(
            str(old.get("raw_request", ""))).removeprefix("\n追加反馈：")
        rows.append({"field": "feedback", "label": "用户反馈",
                     "before": None, "after": feedback[:2000]})
    if previous.get("node_modes") != current.get("node_modes"):
        rows.append({"field": "node_modes", "label": "节点执行方式",
                     "before": deepcopy(previous.get("node_modes")),
                     "after": deepcopy(current.get("node_modes"))})
    return tuple(rows)


def require_key(key: str) -> None:
    if not isinstance(key, str) or not key.strip() or len(key) > 255:
        raise ServiceError("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key must be 1-255 characters", 400)


def require_version(value: Any) -> None:
    if type(value) is not int or value < 1:
        raise invalid("expected_draft_version must be a positive integer", "$.expected_draft_version")


def require_positive(value: Any, field: str) -> None:
    if type(value) is not int or value < 1:
        raise invalid(f"{field} must be a positive integer", f"$.{field}")


class TaskService:
    def __init__(self, repository: TaskRepository, projects: ProjectContextProvider | None = None,
                 query_images=None, video_execution=None, agent_execution=None) -> None:
        self.repository = repository
        self.projects = projects if projects is not None else LocalProjectAccess()
        self.query_images = query_images
        self.video_execution = video_execution
        self.agent_execution = agent_execution

    def save_draft(self, actor: Actor, project_id: str, key: str, body: dict,
                   draft_id: str | None = None) -> tuple[dict, bool]:
        require_key(key)
        if not isinstance(body, dict):
            raise invalid("body must be an object", "$")
        raw = dict(body)
        version = raw.pop("expected_draft_version", None) if draft_id else None
        if draft_id:
            require_version(version)
            existing = self.repository.get_draft(actor, draft_id)
            if existing.project_id != project_id:
                raise ServiceError("NOT_FOUND", "draft was not found", 404)
        proposal = normalize_proposal(raw)
        if proposal['query_spec']['cross_batch_dedup']['scope'] != 'none' and not callable(
                getattr(self.repository, 'historical_options', None)):
            raise ServiceError('FEATURE_NOT_SUPPORTED', 'Persistent historical resolver required', 422)
        self.projects.require_access(actor, project_id, proposal["dimension_id"], "create")
        query_input = proposal["query_spec"].get("query_input")
        if query_input is not None and query_input.get("type") == "reference_image":
            if self.query_images is None:
                raise ServiceError("QUERY_IMAGES_UNAVAILABLE", "参考图输入尚未启用", 503)
            self.query_images.require_input(actor, project_id, query_input)
        resolved_draft_id = draft_id or "draft_" + uuid.uuid4().hex
        conversation_id = (existing.conversation_id if draft_id else None) or "conversation_" + uuid.uuid4().hex
        try:
            previous = self.repository.get_conversation(actor, conversation_id)
            sequence = len(previous.messages) + 1
        except ServiceError as error:
            if error.code != "NOT_FOUND":
                raise
            sequence = 1
        draft = Draft(resolved_draft_id, actor.tenant_id, actor.user_id,
                      project_id, proposal["dimension_id"], json.dumps(proposal, sort_keys=True, ensure_ascii=False),
                      conversation_id=conversation_id)
        message = ConversationMessage(
            "message_" + uuid.uuid4().hex, conversation_id, actor.tenant_id, actor.user_id,
            project_id, resolved_draft_id, sequence, "request",
            proposal["query_spec"]["raw_request"], datetime.now(UTC),
        )
        stored, replayed = self.repository.save_draft(
            actor, key, draft, version, conversation_message=message)
        return self._draft_view(actor, stored), replayed

    def _draft_view(self, actor, draft):
        view = draft.to_summary()
        if "video_summary" in view and self.video_execution is not None:
            view["video_summary"]["execution"] = self.video_execution.describe(
                actor, draft.project_id, view["proposal"]["query_spec"])
        return view

    def video_capabilities(self, actor, project_id):
        self.projects.require_access(actor, project_id, "default", "read")
        return (self.video_execution.describe(actor, project_id) if self.video_execution is not None else
                {"available": False, "reason_code": "VIDEO_EXECUTION_UNAVAILABLE"})

    def get_draft(self, actor: Actor, project_id: str, draft_id: str) -> dict:
        draft = self.repository.get_draft(actor, draft_id)
        if draft.project_id != project_id:
            raise ServiceError("NOT_FOUND", "draft was not found", 404)
        self.projects.require_access(actor, draft.project_id, draft.dimension_id, "read")
        return self._draft_view(actor, draft)

    def create_task(self, actor: Actor, idempotency_key: str, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        require_key(idempotency_key)
        fields = {"draft_id", "expected_draft_version", "proposal_digest", "action"}
        if not isinstance(body, dict) or set(body) != fields:
            raise ServiceError("CONFIRMATION_REQUIRED", "create requires an explicit draft confirmation", 422)
        require_version(body["expected_draft_version"])
        if body["action"] != "confirm" or not isinstance(body["draft_id"], str) or not body["draft_id"]:
            raise invalid("explicit confirm action and draft_id are required")
        if not isinstance(body["proposal_digest"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", body["proposal_digest"]):
            raise invalid("proposal_digest must be a SHA-256 digest", "$.proposal_digest")
        draft = self.repository.get_draft(actor, body["draft_id"])
        self.projects.require_access(actor, draft.project_id, draft.dimension_id, "approve")
        from .video_query import require_video_execution
        query_spec = json.loads(draft.proposal_json)["query_spec"]
        from .processing_requirements import require_agent_execution_available
        agent_mode = 'agent_requirements' in query_spec
        execution = None
        if 'retrieval_execution' in query_spec:
            execution = getattr(self.agent_execution, 'confirmed_terms', None)
            if execution is None:
                raise ServiceError('AGENT_EXECUTION_UNAVAILABLE', 'Confirmed terms execution Agent is not installed', 503)
            if draft.draft_version != body['expected_draft_version'] or draft.proposal_digest != body['proposal_digest']:
                raise ServiceError('DRAFT_VERSION_CONFLICT', 'Draft changed before execution delegation', 409)
            if not draft.confirmed_task_id:
                execution.require(actor, draft.project_id, json.loads(draft.proposal_json))
        if agent_mode and self.agent_execution is not None:
            if draft.draft_version != body['expected_draft_version'] or draft.proposal_digest != body['proposal_digest']:
                raise ServiceError('DRAFT_VERSION_CONFLICT', 'Draft changed before admission', 409)
            if not draft.confirmed_task_id:
                self.agent_execution.require(actor, draft.project_id, json.loads(draft.proposal_json))
        else:
            require_agent_execution_available(query_spec)
        if query_spec.get("media_type") == "video" and (
                draft.draft_version != body["expected_draft_version"]
                or draft.proposal_digest != body["proposal_digest"]):
            raise ServiceError("DRAFT_VERSION_CONFLICT", "draft version or proposal digest is stale", 409)
        if query_spec.get("media_type") == "video" and self.video_execution is not None:
            self.video_execution.require(actor, draft.project_id, query_spec)
        else:
            require_video_execution(query_spec)
        guard = {}
        if execution is not None:
            def execution_guard(task, replayed):
                if not replayed:
                    execution.require(actor, task.project_id, json.loads(task.confirmed_proposal_json), task=task)
            guard['confirmation_guard'] = execution_guard
        if agent_mode and self.agent_execution is not None:
            def confirm_guard(task, replayed):
                if not replayed:
                    self.agent_execution.require(actor, task.project_id, json.loads(task.confirmed_proposal_json), task=task)
            guard['confirmation_guard'] = confirm_guard
        stored, replayed = self.repository.confirm_draft(actor, idempotency_key, body["draft_id"],
                                                        body["expected_draft_version"], body["proposal_digest"], **guard)
        return stored.to_summary(), replayed

    def get_task(self, actor: Actor, task_id: str) -> dict[str, Any]:
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, "read")
        result = task.to_summary()
        result["pending_draft"] = self.repository.get_pending_feedback(actor, task_id)
        if task.media_type == "video" and callable(getattr(self.repository, "search_dispatch_result", None)):
            receipt = self.repository.search_dispatch_result(task_id)
            if receipt and receipt.get("result"):
                result["video_intake"] = receipt["result"].get("video_intake")
        return result

    def current_read_binding(self, actor: Actor, task_id: str) -> tuple[dict, dict]:
        """Read the confirmed task and query without a separate revisions scan."""
        reader = getattr(self.repository, 'get_task_read_binding', self.repository.get)
        task = reader(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, "read")
        if task.confirmed_revision is None or not task.confirmed_proposal_json:
            raise ServiceError("REVISION_CONFLICT", "Task has no confirmed query", 409)
        try:
            proposal = json.loads(task.confirmed_proposal_json)
        except (ValueError, TypeError) as exc:
            raise ServiceError("TASK_CORRUPT", "Confirmed query is invalid", 500) from exc
        if not isinstance(proposal, dict):
            raise ServiceError("TASK_CORRUPT", "Confirmed query is invalid", 500)
        return {**task.to_summary(), "created_at": task.created_at.isoformat()}, proposal

    def save_feedback(self, actor: Actor, task_id: str, key: str, body: dict[str, Any],
                      draft_id: str | None = None) -> tuple[dict[str, Any], bool]:
        return self.commit_feedback(actor, self.prepare_feedback(actor, task_id, key, body, draft_id))

    def prepare_feedback(self, actor: Actor, task_id: str, key: str, body: dict[str, Any],
                         draft_id: str | None = None) -> dict:
        """Freeze a validated write before a durable command may commit it."""
        require_key(key)
        fields = {"text", "scope", "expected_revision", "expected_task_version"}
        update_fields = fields | {"expected_draft_version"}
        required = update_fields if draft_id else fields
        optional = {"grouped_expansion", "target_count", "cleaning_feedback", "cleaning_changes", "requirement_changes", "cross_batch_dedup"}
        if (not isinstance(body, dict) or not required <= set(body)
                or set(body) - required - optional
                or ("target_count" in body) != ("grouped_expansion" in body)):
            raise invalid("feedback requires text, scope, expected_revision and expected_task_version")
        require_positive(body["expected_revision"], "expected_revision")
        require_positive(body["expected_task_version"], "expected_task_version")
        if draft_id:
            require_positive(body["expected_draft_version"], "expected_draft_version")
        text = body["text"]
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 2_000:
            raise invalid("feedback text must be 1-2000 characters", "$.text")
        if body["scope"] != "all":
            raise ServiceError("FEATURE_NOT_SUPPORTED", "the local preview supports only all scope", 422,
                               field_path="$.scope")
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, "create")
        if task.confirmed_revision != body["expected_revision"] or task.task_version != body["expected_task_version"]:
            raise ServiceError("REVISION_CONFLICT", "feedback base is stale", 409)
        try:
            proposal = json.loads(task.confirmed_proposal_json)
        except (TypeError, json.JSONDecodeError):
            raise ServiceError("STORAGE_CORRUPT", "confirmed proposal is unavailable", 500) from None
        proposal = deepcopy(proposal)
        current = proposal.get("query_spec", {}).get("raw_request")
        if not isinstance(current, str) or not current:
            raise ServiceError("STORAGE_CORRUPT", "confirmed proposal is invalid", 500)
        if 'cleaning_feedback' in body and body['cleaning_feedback'] is not True:
            raise invalid('cleaning_feedback must be true', '$.cleaning_feedback')
        if 'cleaning_changes' in body or 'requirement_changes' in body:
            if not body.get('cleaning_feedback') or 'grouped_expansion' in body:
                raise invalid('structured cleaning changes cannot change retrieval fields')
            if 'cleaning_changes' in body and 'requirement_changes' in body:
                raise invalid('use one structured change protocol')
            from .requirement_changes import apply_changes
            hard = [row for row in proposal['query_spec'].get('rules', []) if row.get('hardness') == 'hard']
            if (len(hard) != 1 or hard[0].get('predicate_ref') != 'image.semantic-match@1'
                    or hard[0].get('operator') != 'eq' or hard[0].get('value') is not True):
                raise ServiceError('STRUCTURED_CLEANING_UNAVAILABLE', '此任务尚未配置逐图语义清洗规则。', 409)
            if draft_id:
                pending = self.get_feedback(actor, task_id, draft_id)
                if (pending['status'] != 'ready' or pending['draft_version'] != body['expected_draft_version']
                        or pending['base_revision'] != task.confirmed_revision
                        or pending['base_task_version'] != task.task_version):
                    raise ServiceError('DRAFT_VERSION_CONFLICT', '清洗草稿已变化，请刷新。', 409)
                proposal = deepcopy(pending['proposal'])
            if 'requirement_changes' in body:
                from .requirement_changes import apply_requirement_changes
                proposal['query_spec'] = apply_requirement_changes(proposal['query_spec'], body['requirement_changes'], text)
            else:
                proposal['query_spec']['cleaning_conditions'] = apply_changes(
                    proposal['query_spec'], body['cleaning_changes'], text)
        else:
            if 'cleaning_conditions' in proposal['query_spec'] and body.get('cleaning_feedback'):
                raise ServiceError('STRUCTURED_CHANGE_REQUIRED', '此需求请使用结构化修改，不能追加文本。', 422)
            marker = "\n追加检测条件：" if body.get('cleaning_feedback') else "\n追加反馈："
            proposal["query_spec"]["raw_request"] = current + marker + text.strip()
        if "grouped_expansion" in body:
            proposal["query_spec"]["grouped_expansion"] = body["grouped_expansion"]
            proposal["query_spec"]["target_count"] = body["target_count"]
            proposal["query_spec"]["refill_policy"]["target_count"] = body["target_count"]
        if 'cross_batch_dedup' in body:
            proposal['query_spec']['cross_batch_dedup'] = body['cross_batch_dedup']
        normalized = normalize_proposal(proposal)
        if normalized['query_spec']['cross_batch_dedup']['scope'] != 'none' and not callable(
                getattr(self.repository, 'historical_options', None)):
            raise ServiceError('FEATURE_NOT_SUPPORTED', 'Persistent historical resolver required', 422)
        proposal_json = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
        feedback_draft_id = draft_id
        message = None
        if task.conversation_id:
            feedback_draft_id = draft_id or "feedback_" + uuid.uuid4().hex
            conversation = self.repository.get_conversation(actor, task.conversation_id)
            message = ConversationMessage(
                "message_" + uuid.uuid4().hex, task.conversation_id,
                actor.tenant_id, actor.user_id, task.project_id, feedback_draft_id,
                len(conversation.messages) + 1, "feedback", text.strip(), datetime.now(UTC),
            )
        message_data = asdict(message) if message else None
        if message_data:
            message_data['created_at'] = message.created_at.isoformat()
        return {'tenant_id': actor.tenant_id, 'user_id': actor.user_id, 'task_id': task_id,
                'project_id': task.project_id, 'dimension_id': task.dimension_id, 'key': key,
                'args': {'expected_revision': body['expected_revision'],
                         'expected_task_version': body['expected_task_version'], 'proposal_json': proposal_json,
                         'feedback_text': text.strip(), 'scope': 'all', 'draft_id': feedback_draft_id,
                         'expected_draft_version': body.get('expected_draft_version')},
                'message': message_data}

    def commit_feedback(self, actor: Actor, prepared: dict) -> tuple[dict, bool]:
        """Replay exactly the frozen write; repository receipts precede stale checks."""
        if (prepared.get('tenant_id'), prepared.get('user_id')) != (actor.tenant_id, actor.user_id):
            raise ServiceError('ACCESS_DENIED', 'Prepared feedback belongs to another actor', 403)
        task = self.repository.get(actor, prepared['task_id'])
        self.projects.require_access(actor, task.project_id, task.dimension_id, 'create')
        if (task.project_id, task.dimension_id) != (prepared['project_id'], prepared['dimension_id']):
            raise ServiceError('STORAGE_CORRUPT', 'Prepared feedback scope changed', 500)
        message = deepcopy(prepared['message'])
        if message:
            message['created_at'] = datetime.fromisoformat(message['created_at'])
            message = ConversationMessage(**message)
        return self.repository.save_feedback_draft(actor, prepared['key'], task.task_id,
                                                   **prepared['args'], conversation_message=message)

    def get_feedback(self, actor: Actor, task_id: str, draft_id: str) -> dict[str, Any]:
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, "read")
        return self.repository.get_feedback_draft(actor, task_id, draft_id)

    def confirm_feedback(self, actor: Actor, task_id: str, key: str,
                         body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        require_key(key)
        fields = {"draft_id", "expected_draft_version", "proposal_digest",
                  "expected_task_version", "action"}
        if not isinstance(body, dict) or set(body) != fields:
            raise invalid("confirmation requires draft/version/digest/task version/action")
        require_positive(body["expected_draft_version"], "expected_draft_version")
        require_positive(body["expected_task_version"], "expected_task_version")
        if body["action"] not in {"confirm", "reject"}:
            raise invalid("action must be confirm or reject", "$.action")
        if not isinstance(body["draft_id"], str) or not body["draft_id"]:
            raise invalid("draft_id is required", "$.draft_id")
        if not isinstance(body["proposal_digest"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", body["proposal_digest"]):
            raise invalid("proposal_digest must be a SHA-256 digest", "$.proposal_digest")
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, "approve")
        guard = {}
        if body['action'] == 'confirm':
            feedback = self.repository.get_feedback_draft(actor, task_id, body['draft_id'])
            before = json.loads(task.confirmed_proposal_json)['query_spec']
            after = feedback['proposal']['query_spec']
            if 'agent_requirements' in before or 'agent_requirements' in after:
                raise ServiceError('AGENT_REVISION_UNAVAILABLE',
                    'Agent retrieval revisions are not enabled; prepare a new reviewed request', 422)
            if 'retrieval_execution' in before and 'retrieval_execution' not in after:
                raise ServiceError('AGENT_EXECUTION_POLICY_CHANGED', 'Keep the confirmed execution delegation when editing terms', 409)
            execution_changed = any(before.get(k) != after.get(k) for k in
                ('retrieval_execution','grouped_expansion','target_count','cross_batch_dedup'))
            if 'retrieval_execution' in after and execution_changed:
                execution = getattr(self.agent_execution, 'confirmed_terms', None)
                if execution is None: raise ServiceError('AGENT_EXECUTION_UNAVAILABLE', 'Execution Agent is unavailable', 503)
                execution.require(actor, task.project_id, feedback['proposal'])
                def execution_guard(current, replayed):
                    if not replayed:
                        execution.require(actor, current.project_id, json.loads(current.confirmed_proposal_json), task=current)
                guard['confirmation_guard'] = execution_guard
        return self.repository.confirm_feedback_draft(
            actor, key, task_id, draft_id=body["draft_id"],
            expected_draft_version=body["expected_draft_version"],
            proposal_digest=body["proposal_digest"],
            expected_task_version=body["expected_task_version"], action=body["action"],
            **guard,
        )

    def list_revisions(self, actor: Actor, task_id: str) -> tuple[dict[str, Any], ...]:
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, "read")
        revisions = self.repository.list_revisions(actor, task_id)
        result = []
        previous = None
        for item in revisions:
            current = item["proposal"]
            changes = () if previous is None else _revision_changes(previous, current)
            result.append({**item, "changes": changes,
                           "recompute_required": bool(changes),
                           "revision_note": ("初始确认版本" if previous is None else
                               "新版本只接受绑定本 revision/generation 的结果；旧结果与交付包保持历史只读。")})
            previous = current
        return tuple(result)

    def list_tasks(self, actor: Actor, project_id: str) -> tuple[dict[str, Any], ...]:
        if not isinstance(project_id, str) or not project_id.strip():
            raise invalid("project_id is required", "$.project_id")
        result = []
        combined = getattr(self.repository, 'list_tasks_with_feedback', None)
        rows = (combined(actor, project_id) if callable(combined)
                else tuple((task, self.repository.get_pending_feedback(actor, task.task_id))
                           for task in self.repository.list_tasks(actor, project_id)))
        for task, pending in rows:
            try:
                self.projects.require_access(actor, task.project_id, task.dimension_id, "read")
            except ServiceError as error:
                if error.code == "PROJECT_ACCESS_DENIED":
                    continue
                raise
            try:
                proposal = json.loads(task.confirmed_proposal_json)
                raw_request = proposal["query_spec"]["raw_request"]
            except (TypeError, KeyError, json.JSONDecodeError):
                raise ServiceError("STORAGE_CORRUPT", "confirmed proposal is invalid", 500) from None
            view = task.to_summary()
            view.update(raw_request=raw_request, created_at=task.created_at.isoformat(),
                        pending_draft=pending)
            result.append(view)
        return tuple(result)

    def task_read_summary(self, actor: Actor, task_id: str):
        reader = getattr(self.repository, 'get_task_read_summary', None)
        summary = reader(actor, task_id) if callable(reader) else None
        if summary is None:
            task = self.repository.get(actor, task_id)
            summary = task.to_summary()
        self.projects.require_access(actor, summary['project_id'],
                                     summary['dimension_id'], 'read')
        return summary

    def list_tasks_page(self, actor: Actor, project_id: str, *, limit=20,
                        cursor=None) -> dict[str, Any]:
        if not isinstance(project_id, str) or not project_id.strip():
            raise invalid('project_id is required', '$.project_id')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise invalid('task page limit must be 1-100', '$.limit')
        after = None
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or not 0 < len(cursor) <= 2048:
                    raise ValueError
                data = json.loads(urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                payload = data['payload']
                canonical = json.dumps(payload, sort_keys=True,
                                       separators=(',', ':')).encode()
                if (set(data) != {'payload', 'checksum'}
                        or data['checksum'] != sha256(canonical).hexdigest()
                        or set(payload) != {'tenant_id', 'user_id', 'project_id',
                                            'created_at', 'task_id'}
                        or (payload['tenant_id'], payload['user_id'],
                            payload['project_id']) !=
                           (actor.tenant_id, actor.user_id, project_id)
                        or not isinstance(payload['task_id'], str)
                        or not payload['task_id']):
                    raise ValueError
                created_at = datetime.fromisoformat(payload['created_at'])
                if created_at.tzinfo is None:
                    raise ValueError
                after = (created_at,
                         payload['task_id'])
            except (ValueError, TypeError, KeyError, UnicodeError, Base64Error):
                raise ServiceError('INVALID_CURSOR', 'Task page cursor is invalid', 422) from None
        fast_reader = getattr(self.repository, 'list_task_read_page', None)
        summaries = (fast_reader(actor, project_id, limit=limit + 1, after=after)
                     if callable(fast_reader) else None)
        rows = (self.repository.list_tasks_page_with_feedback(
                    actor, project_id, limit=limit + 1, after=after)
                if summaries is None else ())
        items = []
        page_rows = (summaries if summaries is not None else rows)
        for row in page_rows[:limit]:
            task, pending = row if summaries is None else (None, None)
            project = task.project_id if task is not None else row['project_id']
            dimension = task.dimension_id if task is not None else row['dimension_id']
            try:
                self.projects.require_access(actor, project, dimension, 'read')
            except ServiceError as error:
                if error.code == 'PROJECT_ACCESS_DENIED':
                    continue
                raise
            if summaries is None:
                try:
                    proposal = json.loads(task.confirmed_proposal_json)
                    raw_request = proposal['query_spec']['raw_request']
                except (TypeError, KeyError, json.JSONDecodeError):
                    raise ServiceError('STORAGE_CORRUPT',
                                       'confirmed proposal is invalid', 500) from None
                item = task.to_summary()
                item.update(raw_request=raw_request,
                            created_at=task.created_at.isoformat(), pending_draft=pending)
            else:
                item = row
            items.append(item)
        next_cursor = None
        if len(page_rows) > limit:
            last = page_rows[limit - 1][0] if summaries is None else page_rows[limit - 1]
            payload = {'tenant_id': actor.tenant_id, 'user_id': actor.user_id,
                       'project_id': project_id,
                       'created_at': (last.created_at.isoformat()
                                      if summaries is None else last['created_at']),
                       'task_id': last.task_id if summaries is None else last['task_id']}
            canonical = json.dumps(payload, sort_keys=True,
                                   separators=(',', ':')).encode()
            envelope = {'payload': payload,
                        'checksum': sha256(canonical).hexdigest()}
            next_cursor = urlsafe_b64encode(json.dumps(envelope,
                sort_keys=True, separators=(',', ':')).encode()).decode().rstrip('=')
        return {'items': items, 'next_cursor': next_cursor}

    def get_conversation(self, actor: Actor, conversation_id: str) -> dict[str, Any]:
        snapshot = self.repository.get_conversation(actor, conversation_id)
        if not snapshot.messages:
            raise ServiceError("STORAGE_CORRUPT", "conversation has no messages", 500)
        draft = self.repository.get_draft(actor, snapshot.messages[0].draft_id)
        self.projects.require_access(actor, snapshot.project_id, draft.dimension_id, "read")
        return snapshot.to_view()

    def list_conversations(self, actor: Actor, project_id: str) -> tuple[dict[str, Any], ...]:
        if not isinstance(project_id, str) or not project_id.strip():
            raise invalid("project_id is required", "$.project_id")
        result = []
        for snapshot in self.repository.list_conversations(actor, project_id):
            if not snapshot.messages:
                raise ServiceError("STORAGE_CORRUPT", "conversation has no messages", 500)
            draft = self.repository.get_draft(actor, snapshot.messages[0].draft_id)
            try:
                self.projects.require_access(actor, project_id, draft.dimension_id, "read")
            except ServiceError as error:
                if error.code == "PROJECT_ACCESS_DENIED":
                    continue
                raise
            result.append(snapshot.to_summary())
        return tuple(result)

    def delete_conversation(self, actor: Actor, conversation_id: str) -> dict[str, Any]:
        snapshot = self.repository.get_conversation(actor, conversation_id)
        if snapshot.confirmations:
            raise ServiceError('CONVERSATION_DELETE_FORBIDDEN',
                               '已确认或进入检索的 Chat 不能删除', 409)
        draft = self.repository.get_draft(actor, snapshot.messages[0].draft_id)
        self.projects.require_access(actor, snapshot.project_id, draft.dimension_id, 'create')
        self.repository.delete_conversation(actor, conversation_id)
        return {'conversation_id': conversation_id, 'deleted': True}

    def hard_delete_conversation(self, actor: Actor, conversation_id: str) -> dict[str, Any]:
        """Force-cascade delete a Chat even after it entered the formal task boundary.

        This is an explicit override of the default ``delete_conversation`` guard
        (the ``?force=true`` path). Authorization still mirrors the default path:
        the actor must be the conversation owner and hold ``create`` on the first
        message's project/dimension. The repository cascade removes the linked
        task(s), revisions, batches, feedback/drafts, and task-scoped cleaning
        records. Local development only; production retention/async cleanup is a
        separate v1.9 concern and is not implemented here.
        """
        snapshot = self.repository.get_conversation(actor, conversation_id)
        if not snapshot.messages:
            raise ServiceError('STORAGE_CORRUPT', 'conversation has no messages', 500)
        draft = self.repository.get_draft(actor, snapshot.messages[0].draft_id)
        self.projects.require_access(actor, snapshot.project_id, draft.dimension_id, 'create')
        cascade = self.repository.hard_delete_conversation(actor, conversation_id)
        result = {'conversation_id': conversation_id, 'deleted': True, 'force': True}
        result.update(cascade)
        return result

    def hard_delete_task(self, actor: Actor, task_id: str) -> dict[str, Any]:
        """Force-cascade delete a legacy task tree by task id, with no Chat boundary.

        The ``?force=true`` path for pre-conversation legacy tasks that carry no
        ``conversation_id`` and therefore have no Chat to delete through.
        Authorization mirrors the conversation force path: the actor must own the
        task and hold ``create`` on its project/dimension. A conversation-linked
        task is refused (``TASK_HAS_CONVERSATION``) so no confirmation is left
        dangling — those must go through ``hard_delete_conversation``. Same
        task-scoped cascade and same local-development scoping.
        """
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, 'create')
        cascade = self.repository.hard_delete_task(actor, task_id)
        result = {'task_id': task_id, 'deleted': True, 'force': True}
        result.update(cascade)
        return result
