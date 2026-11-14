from __future__ import annotations

import json
import os
import re
import sys
import uuid
import argparse
import subprocess
import time
from datetime import datetime, timezone
from contextlib import nullcontext
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .errors import ServiceError
from .conversation_timing import ConversationTiming, timed_call, increment
from .models import Actor
from .service import InMemoryTaskRepository, TaskService
from .adapters.projects import LocalProjectAccess
from .adapters.task_storage.sqlite import SQLiteTaskRepository
from .chat_ui import CHAT_CSS, CHAT_HTML, CHAT_JS
from .production_board_ui import BOARD_CSS, BOARD_HTML, BOARD_JS
from .progress_models import build_task_progress
from .vlm_instruction_plan import confirmed_instruction_plan
from .task_events import TaskEventFeed
from .cleaning_step_outputs import StepOutputCursorError, build_step_output_page
from .result_views import ResultCursorError, build_result_page
from .expansion_models import preview_grouped_expansion, preview_edited_groups
from .query_branches import build_query_branch_view
from .model_runtime import ModelRuntime, load_model_runtime
from .query_spec import digest
from .catalog_ui import CATALOG_HTML, CATALOG_JS
from .text_catalog_config import CatalogConfigScope
from .local_catalog import build_local_catalog
from .routing_preview import LocalRoutingPreview
from .retrieval_progress import build_local_demo_progress_repository, read_retrieval_progress
from .first_search_local import LocalFirstSearch
from .search_dispatch import SearchDispatchCoordinator
from .refill_runtime import build_refill_runtime
from .dialogue import DialogueStore, DialogueCoordinator
from .expansion_jobs import ExpansionJobs


TASK_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)$")
DRAFT_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/drafts(?:/([^/]+))?$")
FEEDBACK_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/feedback(?:/([^/]+))?$")
FEEDBACK_SUGGESTION_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/feedback-suggestions$")
FOLLOWUP_INTENT_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/(?:followup-intent|conversation-turn)$")
TASK_COMMAND_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/conversation-turns/([^/]+)$")
CONFIRMATION_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/confirmations$")
REVISIONS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/revisions$")
TASK_SUMMARY_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/summary$")
TASK_WORKSPACE_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/workspace$")
READ_BINDING_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/read-binding$")
PROGRESS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/progress$")
RESULTS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/results$")
RESULT_EXCLUSIONS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/result-exclusions$")
MANUAL_REVIEWS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/manual-reviews$")
PROJECT_TASKS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/tasks$")
PROJECT_CONVERSATIONS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/conversations$")
CONVERSATION_PATH = re.compile(r"^/v1/retrieval/conversations/([^/]+)$")
EXPANSIONS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/expansions$")
EXPANSION_JOBS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/dialogue-sessions/(dlg_[A-Za-z0-9]+)/expansion-jobs(?:/(expjob_[a-f0-9]{32})(?:/(retry))?)?$")
TASK_EXPANSION_JOBS_PATH = re.compile(r"^/v1/retrieval/tasks/(rt_[a-f0-9]{32})/expansion-jobs(?:/(expjob_[a-f0-9]{32})(?:/(retry))?)?$")
INTENT_PREVIEW_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/intent-preview$")
DIALOGUE_TURN_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/(?:dialogue-turn|dialogue-command)$")
DIALOGUE_COMMAND_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/dialogue-commands/([^/]+)$")
PROJECT_DIALOGUE_SESSIONS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/dialogue-sessions$")
DIALOGUE_SESSION_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/dialogue-sessions/(dlg_[A-Za-z0-9]+)$")
DIALOGUE_ARCHIVE_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/dialogue-sessions/(dlg_[A-Za-z0-9]+)/archive$")
QUERY_BRANCHES_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/query-branches$")
MODELS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/models$")
MODEL_SETTINGS_PATH = re.compile(r'^/v1/retrieval/projects/([^/]+)/model-settings(?:/(connections|defaults|probe)(?:/(conn_[a-f0-9]{32})/(discover|bind|rotate))?)?$')
CATALOG_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/catalog-config(?:/(preview|confirm))?$")
ROUTING_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/query-routing$")
RETRIEVAL_PROGRESS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/retrieval-progress$")
AGENT_RUNS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/agent-runs$")
TASK_EVENTS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/events$")
TASK_EVENT_STREAM_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/events/stream$")
FIRST_SEARCH_GET_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/(?:first-search(?:/(candidates(?:/changes)?|progress))?|(?P<candidate_view>candidates(?:/changes)?))$")
FIRST_SEARCH_PREVIEW_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/(?:first-search/)?candidates/(candidate_[a-f0-9]{64})/preview$")
FIRST_SEARCH_POST_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/first-search/(prepare|confirm|tick)$")
FIRST_SEARCH_MANIFEST_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/first-search/manifest$")
CLEANING_POST_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/cleaning/(prepare|confirm|execute)$")
CLEANING_PREVIEW_PATH = re.compile(
    r"^/v1/retrieval/tasks/([^/]+)/cleaning/candidates/(candidate_[a-f0-9]{64})/preview$")
NODE_OUTPUTS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/nodes/([^/]+)/outputs$")
REFILL_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/refill$")
QUERY_IMAGES_PATH = re.compile(
    r"^/v1/retrieval/projects/([^/]+)/query-images(?:/(qimg_[a-f0-9]{32})(?:/(preview))?)?$")


def platform_request(method):
    """Bind identity/project for all API dispatch paths, including SSE and uploads."""
    from functools import wraps

    @wraps(method)
    def dispatch(self):
        if self.identity_provider is None or not urlsplit(self.path).path.startswith('/v1/') or urlsplit(self.path).path == '/v1/build':
            return method(self)
        original_headers = self.headers
        try:
            if self.platform_browser is not None:
                if urlsplit(self.path).path.startswith('/v1/platform/'):
                    return self.platform_browser.handle(self, urlsplit(self.path).path)
                self.headers = self.platform_browser.request_headers(self.headers, self.command)
            with self.service.projects.request_scope(self.headers) as actor:
                path_project = re.match(r'^/v1/retrieval/projects/([^/]+)(?:/|$)', urlsplit(self.path).path)
                if path_project and path_project.group(1) != self.headers.get('x-app-id'):
                    raise ServiceError('PROJECT_ACCESS_DENIED', 'request project does not match route', 403)
                self._platform_actor = actor
                try:
                    return method(self)
                finally:
                    self._platform_actor = None
        except ServiceError as error:
            self._json(error.status, error.as_dict(trace_id=self._trace_id()))
        finally:
            self.headers = original_headers
    return dispatch


class Handler(BaseHTTPRequestHandler):
    identity_provider = None
    platform_browser = None
    browse_only = False
    server_version = "dsh-retrieval-p0"
    service: TaskService
    result_reader: Any = None
    cleaning_reader: Any = None
    cleaning_service: Any = None
    cleaning_dispatch: Any = None
    delivery_service: Any = None
    model_runtime: Any = None
    catalog_config: Any = None
    routing_preview: Any = None
    plan_set_repository: Any = None
    first_search: Any = None
    search_dispatch: Any = None
    refill_service: Any = None
    dialogue: Any = None
    expansion_jobs: Any = None
    task_events: Any = None
    query_images: Any = None
    event_stream_duration = 25.0
    event_stream_poll_interval = 1.0
    event_stream_heartbeat_interval = 5.0
    build_info: dict[str, Any] = {}

    def _catalog_action(self, match, post=False):
        actor = self._actor()
        self.service.projects.require_access(actor, match.group(1), 'default', 'read')
        if self.catalog_config is None:
            raise ServiceError('CATALOG_NOT_CONFIGURED', '未装配受信索引清单与独立配置权限', 503)
        scope = CatalogConfigScope(actor.tenant_id, actor.user_id, match.group(1))
        if not post:
            return HTTPStatus.OK, {'config': self.catalog_config.read(scope)}, False
        body = self._body()
        if match.group(2) == 'preview' and set(body) == {'text', 'llm_id'}:
            result = self.catalog_config.preview(scope, body['text'], body['llm_id'])
        elif match.group(2) == 'confirm' and set(body) == {'proposal', 'proposal_digest'}:
            result = {'version': self.catalog_config.save(scope, body['proposal'], body['proposal_digest'])}
        else:
            raise ServiceError('INVALID_CATALOG_CONFIG', 'Invalid configuration payload', 422)
        return HTTPStatus.OK, result, False

    def _trace_id(self) -> str:
        return self.headers.get("X-Request-Id") or "tr_" + uuid.uuid4().hex

    def _actor(self) -> Actor:
        if self.identity_provider is not None:
            return getattr(self, '_platform_actor', None) or self.identity_provider.authenticate(self.headers)
        tenant_id = self.headers.get("X-Tenant-Id", "").strip()
        user_id = self.headers.get("X-User-Id", "").strip()
        if not tenant_id or not user_id:
            raise ServiceError("UNAUTHENTICATED", "trusted gateway identity is required", 401)
        return Actor(tenant_id, user_id)

    def _task_snapshot(self, actor, task_id, *, include_cleaning_items=False):
        """Read one authoritative task snapshot and advance its event watermark."""
        mutation_reader = getattr(self.task_events, 'task_mutation_sequence', None)
        mutation = mutation_reader(task_id) if callable(mutation_reader) else None
        task, proposal = self.service.current_read_binding(actor, task_id)
        result_set = None
        cleaning_view = None
        paired_reader = getattr(self.cleaning_service, 'progress_inputs', None)
        cleaning_reader = self.cleaning_service or self.cleaning_reader
        fast_result = getattr(self.result_reader,
                              'get_current_result_read_snapshot', None)
        fast_steps = (getattr(cleaning_reader, 'steps_summary', None)
                      if self.cleaning_service is not None else
                      getattr(cleaning_reader, 'step_results_summary', None))
        used_projection = False
        if (not include_cleaning_items and callable(fast_result)
                and callable(fast_steps)):
            try:
                try:
                    result_set = fast_result(actor, task_id)
                except ServiceError as error:
                    if error.code != 'NOT_FOUND':
                        raise
                cleaning_view = fast_steps(actor, task_id)
                used_projection = True
            except ServiceError as error:
                if (error.code != 'READ_PROJECTION_MISSING'
                        or getattr(self.result_reader,
                                   'require_read_projection', False)):
                    raise
                result_set = None
                cleaning_view = None
        if (not used_projection and paired_reader is not None
                and self.result_reader is
                getattr(self.cleaning_service, 'repository', None)):
            result_set, cleaning_view = paired_reader(actor, task_id)
        elif not used_projection and self.result_reader is not None:
            try:
                result_set = self.result_reader.get_current_result_set(actor, task_id)
            except ServiceError as error:
                if error.code != "NOT_FOUND":
                    raise
        raw_returned = None
        if self.first_search is not None:
            try:
                raw_returned = self.first_search.candidate_count(actor, task_id)
            except ServiceError as error:
                if error.code not in ("PLAN_NOT_APPROVED", "PLAN_NOT_CURRENT"):
                    raise
        try:
            progress = build_task_progress(
                task, proposal, result_set, raw_returned=raw_returned).to_view()
        except ValueError:
            raise ServiceError("STORAGE_CORRUPT", "task progress binding is invalid", 500) from None
        runs_reader = getattr(self.model_runtime, "agent_runs", None)
        runs = runs_reader(actor.tenant_id, task_id) if runs_reader else []
        cleaning_outputs = []
        if cleaning_reader is not None:
            if cleaning_view is None:
                cleaning_view = (cleaning_reader.steps(actor, task_id)
                    if self.cleaning_service is not None
                    else cleaning_reader.step_results(actor, task_id))
            for run in cleaning_view.get('runs', ()):
                for step in run.get('steps', ()):
                    cleaning_outputs.append({
                        'operation_id': run.get('operation_id'),
                        'step_execution_id': step.get('step_execution_id'),
                        'step_id': step.get('step_id'),
                        'output_version': run.get('output_version'),
                        'counts': step.get('counts')})
        if cleaning_view is None:
            cleaning_view = {"available": False, "runs": []}
        sequence = self.task_events.observe(actor, task, progress, result_set, runs,
                                            cleaning_outputs, mutation_sequence=mutation)
        progress["as_of_sequence"] = sequence
        result_binding = None if result_set is None else {
            key: result_set.get(key) for key in (
                "result_set_id", "result_set_digest", "kind", "accepted_count",
                "failed_count", "pending_review_count", "user_excluded_count", "gap")}
        return {"task": task, "progress": progress,
                "result_binding": result_binding, "executions": runs,
                "step_outputs": cleaning_outputs,
                "cleaning_view": cleaning_view,
                "as_of_sequence": sequence}

    def _task_workspace(self, actor, task_id):
        """Bounded read model shared by Chat and sidebar; never prepare a manifest."""
        from copy import deepcopy
        from .workflow_state import binding, current_source, branch_counts, project_workflow
        mutation_reader = getattr(self.task_events, 'task_mutation_sequence', None)
        for attempt in range(2):
            before_mutation = mutation_reader(task_id) if callable(mutation_reader) else None
            task = self.service.task_read_summary(actor, task_id)
            progress = deepcopy(task.get('progress'))
            if not progress:
                progress = self._task_snapshot(actor, task_id)['progress']
            if any(progress.get(key) != value for key, value in (
                    ('task_id', task_id), ('revision', task['confirmed_revision']),
                    ('task_version', task['task_version']), ('run_generation', task['run_generation']))):
                raise ServiceError('STORAGE_CORRUPT', 'Workspace progress binding differs', 500)
            if not callable(getattr(self.service.repository, 'get_task_read_summary', None)):
                task['pending_draft'] = self.service.repository.get_pending_feedback(actor, task_id)
            errors, search, manifest, cleaning = [], None, None, None
            needs_search = (next(row for row in progress['nodes'] if row['node'] == 'retrieval')['node_status'] != 'succeeded'
                            or str(task.get('stage', '')).upper() == 'REFILL' and task['run_status'] == 'RUNNING')
            if self.first_search is not None and needs_search:
                try:
                    search = self.first_search.progress(actor, task_id)
                except ServiceError as error:
                    if error.status in (401, 403, 404):
                        raise
                    errors.append({'source': 'retrieval', 'code': error.code})
                repository = getattr(self.first_search, 'repository', None)
                manifest_reader = getattr(repository, 'get_current_candidate_manifest', None)
                if callable(manifest_reader):
                    try:
                        saved = manifest_reader(actor, task_id)
                        if saved is not None:
                            source = saved.proposal.scope
                            if ((source.tenant_id, source.owner_user_id, source.project_id)
                                    != (actor.tenant_id, actor.user_id, task['project_id'])):
                                raise ServiceError('ACCESS_DENIED', 'Candidate scope differs', 403)
                            manifest = {'task_id': source.task_id, 'revision': source.revision,
                                'generation': source.generation, 'task_version': source.task_version,
                                'expected_task_version': source.task_version, 'confirmed': True,
                                'candidate_count': len(saved.proposal.items), 'manifest_ref': saved.manifest_ref}
                            # Old confirmed inputs remain historical evidence;
                            # they are not an approval for a newer task version.
                            if not current_source(manifest, task):
                                manifest = None
                    except ServiceError as error:
                        if error.status in (401, 403, 404):
                            raise
                        errors.append({'source': 'manifest', 'code': error.code})
            operation = task.get('active_cleaning_operation_id')
            if operation and self.cleaning_dispatch is not None:
                try:
                    cleaning = self.cleaning_dispatch.status(actor, task_id, operation)
                except ServiceError as error:
                    if error.status in (401, 403, 404):
                        raise
                    errors.append({'source': 'cleaning', 'code': error.code})
            latest = self.service.task_read_summary(actor, task_id)
            after_mutation = mutation_reader(task_id) if callable(mutation_reader) else None
            if (binding(latest) != binding(task) or before_mutation is not None
                    and after_mutation is not None and after_mutation != before_mutation):
                if attempt == 0:
                    continue
                raise ServiceError('REVISION_CONFLICT', '状态正在更新，请重新读取。', 409, True)
            # A feedback draft can change without advancing the formal version.
            task['pending_draft'] = (latest.get('pending_draft') if callable(
                getattr(self.service.repository, 'get_task_read_summary', None)) else
                self.service.repository.get_pending_feedback(actor, task_id))
            capabilities = {'cleaning': self.cleaning_service is not None, 'delivery': self.delivery_service is not None}
            for action, key in (('approve', 'can_approve'), ('create', 'can_create')):
                try:
                    self.service.projects.require_access(actor, task['project_id'], task['dimension_id'], action)
                    capabilities[key] = True
                except ServiceError as error:
                    if error.status != 403:
                        raise
                    capabilities[key] = False
            workflow = project_workflow(task, progress, search=search, manifest=manifest, cleaning=cleaning,
                                        errors=errors, capabilities=capabilities)
            if current_source(search, task, allow_unapproved=True):
                search = {**{key: search[key] for key in ('task_id', 'revision', 'generation', 'task_version',
                    'status', 'job_status', 'pagination_stopped', 'initial_window_complete') if key in search},
                    'branch_counts': branch_counts(search)}
                if search['status'] == 'unapproved':
                    search.update(binding(task))
            else:
                search = None
            progress.update(nodes=workflow['nodes'], workflow=workflow)
            return {**task, 'schema_version': 'task-workspace@1', 'progress': progress,
                    'workflow': workflow, 'search': search, 'manifest': manifest,
                    'mutation_sequence': after_mutation}
        raise AssertionError('unreachable workspace retry')

    def _json(self, status: int, body: dict[str, Any], *, replayed: bool = False, extra_headers=None) -> None:
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header('Cache-Control', 'no-store')
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        if replayed:
            self.send_header("Idempotent-Replayed", "true")
        timing = body.get('timings') if isinstance(body, dict) else None
        if not replayed and isinstance(timing, dict) and type(timing.get('total_ms')) in (int, float):
            self.send_header('Server-Timing', f'conversation;dur={timing["total_ms"]:.3f}')
        self.end_headers()
        self.wfile.write(encoded)

    def _asset(self, content_type: str, value: str) -> None:
        if self.platform_browser is not None and content_type == 'text/html':
            from .platform_ui import platform_html
            value = platform_html(value)
        encoded = value.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        frame_ancestors = " ".join(self.platform_browser.frame_origins) if self.platform_browser and self.platform_browser.frame_origins else "'none'"
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors " + frame_ancestors)
        self.end_headers()
        self.wfile.write(encoded)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError
            value = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            raise ServiceError("INVALID_JSON", "body must be valid JSON", 400) from None
        if not isinstance(value, dict):
            raise ServiceError("INVALID_SPEC", "body must be an object", 422, field_path="$")
        return value

    def _image_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            raise ServiceError("QUERY_IMAGE_EMPTY", "请选择一张参考图", 400)
        from .query_images import MAX_QUERY_IMAGE_BYTES
        if length > MAX_QUERY_IMAGE_BYTES:
            raise ServiceError("QUERY_IMAGE_TOO_LARGE", "参考图不得超过 20 MB", 413)
        value = self.rfile.read(length)
        if len(value) != length:
            raise ServiceError("QUERY_IMAGE_INVALID", "参考图上传不完整", 400)
        return value

    def _run(self, action: Any) -> None:
        trace_id = self._trace_id()
        try:
            scope = getattr(self.model_runtime, 'request_scope', None)
            settings = getattr(self.model_runtime, 'settings', None)
            actor = self._actor() if self.identity_provider is not None else None
            tenant = actor.tenant_id if actor else self.headers.get('X-Tenant-Id', '')
            user = actor.user_id if actor else self.headers.get('X-User-Id', '')
            with (settings.scope(tenant, user)
                  if settings else nullcontext()), (scope(tenant, user,
                        trace_id, urlsplit(self.path).path) if scope else nullcontext()):
                status, body, replayed = action()
            body["trace_id"] = trace_id
            self._json(status, body, replayed=replayed)
        except ServiceError as error:
            self._json(error.status, error.as_dict(trace_id=trace_id))

    def _event_cursor(self, parsed, task_id):
        try:
            params = parse_qs(parsed.query, keep_blank_values=True,
                              strict_parsing=True)
            if set(params) - {'after_sequence'} or any(
                    len(values) != 1 for values in params.values()):
                raise ValueError
            query_value = params.get('after_sequence', [None])[0]
            query_after = int(query_value) if query_value is not None else None
            last_id = self.headers.get('Last-Event-ID')
            header_after = None
            if last_id:
                prefix, separator, sequence = last_id.rpartition(':')
                if not separator or prefix != task_id:
                    raise ValueError
                header_after = int(sequence)
            if (query_after is not None and header_after is not None
                    and query_after != header_after):
                raise ValueError
            after = (query_after if query_after is not None else
                     header_after if header_after is not None else 0)
            if after < 0:
                raise ValueError
            return after
        except ValueError:
            raise ServiceError('INVALID_EVENT_CURSOR',
                               'invalid task event stream cursor', 422) from None

    @staticmethod
    def _sse_event(event):
        data = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
        return (f"id: {event['event_id']}\n"
                f"event: task-event\n"
                f"data: {data}\n\n").encode('utf-8')

    def _stream_task_events(self, parsed, task_id):
        """Bounded SSE connection; clients reconnect from the durable sequence."""
        actor = self._actor()
        after = self._event_cursor(parsed, task_id)
        scoped_reader = getattr(self.task_events, 'task_mutation_sequence', None)
        mutation_reader = ((lambda: scoped_reader(task_id)) if callable(scoped_reader)
                           else getattr(self.task_events, 'mutation_sequence', None))
        observed_mutation = mutation_reader() if callable(mutation_reader) else None
        task = self.service.task_read_summary(actor, task_id)
        replay = getattr(self.task_events, 'can_replay', None)
        snapshot = ({'task': task} if callable(replay) and replay(
            actor, task, observed_mutation) else self._task_snapshot(actor, task_id))
        page = self.task_events.read(actor, task_id, after_sequence=after, limit=100)
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache, no-store')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        deadline = time.monotonic() + max(0, self.event_stream_duration)
        heartbeat_at = time.monotonic()
        try:
            self.wfile.write(b'retry: 1000\n\n')
            while True:
                for event in page['events']:
                    self.wfile.write(self._sse_event(event))
                    after = event['sequence']
                if page['events']:
                    self.wfile.flush()
                if time.monotonic() >= deadline:
                    break
                if not page['has_more']:
                    time.sleep(min(self.event_stream_poll_interval,
                                   max(0, deadline - time.monotonic())))
                    mutation = (mutation_reader() if callable(mutation_reader)
                                else None)
                    self.service.projects.require_access(actor,
                        snapshot['task']['project_id'],
                        snapshot['task']['dimension_id'], 'read')
                    if mutation is None or mutation != observed_mutation:
                        # Capture the watermark before the snapshot. A write
                        # racing after this read remains visible next cycle.
                        observed_mutation = mutation
                        task = self.service.task_read_summary(actor, task_id)
                        snapshot = ({'task': task} if callable(replay) and replay(
                            actor, task, mutation) else self._task_snapshot(actor, task_id))
                page = self.task_events.read(
                    actor, task_id, after_sequence=after, limit=100)
                if time.monotonic() >= heartbeat_at:
                    self.wfile.write(
                        f": heartbeat {page['as_of_sequence']}\n\n".encode('ascii'))
                    self.wfile.flush()
                    heartbeat_at = time.monotonic() + self.event_stream_heartbeat_interval
        except (BrokenPipeError, ConnectionResetError):
            return
        except ServiceError as error:
            # Headers are already committed. Send a bounded control event; the
            # client then recovers through the ordinary JSON snapshot endpoint.
            payload = json.dumps({'code': error.code, 'message': error.message},
                                 ensure_ascii=False, separators=(',', ':'))
            try:
                self.wfile.write(
                    f"event: stream-error\ndata: {payload}\n\n".encode('utf-8'))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _model_settings_action(self, match, post=False):
        port = self.server.server_port
        if self.headers.get('Host') not in {f'127.0.0.1:{port}', f'localhost:{port}'}:
            raise ServiceError('FORBIDDEN', '模型设置仅允许本机地址访问', 403)
        actor = self._actor()
        self.service.projects.require_access(actor, match.group(1), 'default', 'create' if post else 'read')
        settings = getattr(self.model_runtime, 'settings', None)
        if settings is None:
            raise ServiceError('MODEL_SETTINGS_DISABLED', '服务端尚未启用持久化模型设置', 503)
        if not post and not match.group(2):
            return HTTPStatus.OK, settings.read(), False
        # Browser cross-origin writes must never reach the credential store.
        if self.headers.get('Origin') and self.headers['Origin'] != 'http://' + self.headers.get('Host', ''):
            raise ServiceError('FORBIDDEN', '跨站模型配置请求已拒绝', 403)
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            raise ServiceError('INVALID_JSON', '模型设置仅接受 application/json', 400)
        body = self._body()
        resource, identifier, operation = match.group(2, 3, 4)
        if post and resource == 'connections' and identifier is None:
            result = {'connection': settings.register(body)}
        elif post and resource == 'defaults' and identifier is None:
            result = settings.save_defaults(body, self.model_runtime)
        elif post and resource == 'probe' and identifier is None:
            result = settings.probe(body, self.model_runtime)
        elif post and resource == 'connections' and operation == 'discover' and not body:
            result = {'connection': settings.discover(identifier)}
        elif post and resource == 'connections' and operation == 'bind':
            result = settings.bind(identifier, body)
        elif post and resource == 'connections' and operation == 'rotate':
            result = settings.rotate(identifier, body)
        else:
            raise ServiceError('MODEL_SETTINGS_INVALID', '模型配置请求无效', 422)
        return HTTPStatus.OK, result, False

    @staticmethod
    def _turn_is_current(result, task):
        if result['base_task_version'] != task['task_version']:
            return False
        draft = result.get('feedback_draft')
        pending = task.get('pending_draft')
        return not draft or bool(pending and all(pending[key] == draft[key]
                                     for key in ('draft_id', 'draft_version', 'proposal_digest')))

    @staticmethod
    def _conversation_result(task, result, orchestrated):
        return {'schema_version': 'task-conversation-turn@1' if orchestrated else 'followup-intent@1',
                'task_id': task['task_id'], 'base_revision': task['confirmed_revision'],
                'base_task_version': task['task_version'], **result}

    def _dialogue_command_receipt(self, actor, project_id, key):
        self.service.projects.require_access(actor, project_id, 'default', 'create')
        result = self.task_commands.read(actor, self.task_commands.dialogue_scope(project_id), key)
        result.pop('task_id')
        result.update(schema_version='dialogue-command@1', project_id=project_id)
        if result['result'] is not None:
            current = self.dialogue.store.read(actor, project_id, result['result']['session_id'])
            result['result']['is_current'] = result['result']['version'] == current['version']
        return result

    def _task_command_receipt(self, actor, task_id, key):
        task = self.service.task_read_summary(actor, task_id)
        if 'pending_draft' not in task:
            task = self.service.get_task(actor, task_id)
        receipt = self.task_commands.read(actor, task_id, key)
        if receipt['result'] is not None:
            receipt['result'].update(is_current=self._turn_is_current(receipt['result'], task),
                                     current_task_version=task['task_version'])
        return receipt

    def _task_requires_worker(self, actor, task_id, body):
        from .workflow_state import status_question_topic
        from .followup_inputs import explicit_requirement
        from .task_conversation import TaskConversationCoordinator
        if status_question_topic(body['text']) is not None:
            return False
        if body.get('draft_preview') is not None:
            return True
        try:
            task, proposal = self.service.current_read_binding(actor, task_id)
            if (task['task_version'] != body['expected_task_version']
                    or task['confirmed_revision'] != body['expected_revision']):
                return True
            context, _ = TaskConversationCoordinator(self.service).context(
                actor, task, proposal['query_spec'], body.get('feedback_ref'))
            groups = [{'primary_label': row['primary_label'], 'auxiliary_terms': row['auxiliary_terms']}
                      for row in (context['requirement_spec'].get('grouped_expansion') or {}).get('groups', [])]
            return explicit_requirement(body['text'], groups, context) is None
        except ServiceError:
            # The journal must get the first chance to replay a completed key;
            # callbacks retain all stale-draft and permission checks.
            return True

    def _followup_response(self, actor, task_id, body, orchestrated, receipt=None):
        trace = ConversationTiming()
        try:
            with trace.activate():
                result = self._followup_response_impl(actor, task_id, body, orchestrated, receipt)
        except BaseException:
            self.log_message('task_conversation_timing %s', json.dumps(trace.snapshot('failed')))
            raise
        timings = trace.snapshot()
        if receipt is not None and hasattr(receipt, 'queue_wait_ms'):
            timings['queue_wait_ms'] = receipt.queue_wait_ms
        self.log_message('task_conversation_timing %s', json.dumps(timings))
        return {**result, 'timings': timings}

    def _followup_response_impl(self, actor, task_id, body, orchestrated, receipt=None):
        if receipt is not None and receipt.plan is not None:
            from .task_conversation import TaskConversationCoordinator
            result = timed_call('draft_save', TaskConversationCoordinator(self.service).commit, actor, receipt.plan)
            task = receipt.plan['task']
            return self._conversation_result(task, result, True)
        task, proposal = timed_call('requirement_read', self.service.current_read_binding, actor, task_id)
        if (task['confirmed_revision'] != body['expected_revision']
                or task['task_version'] != body['expected_task_version']):
            raise ServiceError('REVISION_CONFLICT', 'Task changed; reload before classifying', 409)
        spec = proposal.get('query_spec')
        if not spec:
            raise ServiceError('STORAGE_CORRUPT', 'Confirmed request unavailable', 500)
        conversation_context, cleaning_draft = {}, None
        if orchestrated:
            from .task_conversation import TaskConversationCoordinator
            coordinator = TaskConversationCoordinator(self.service)
            conversation_context, cleaning_draft = timed_call('draft_read', coordinator.context, actor, task, spec, body.get('feedback_ref'))
        effective_spec = cleaning_draft['proposal']['query_spec'] if cleaning_draft else spec
        groups = (effective_spec.get('grouped_expansion') or {}).get('groups', [])
        current = [{'primary_label': group['primary_label'],
                    'auxiliary_terms': group['auxiliary_terms']} for group in groups]
        target_count = spec['target_count']
        if cleaning_draft is not None:
            target_count = cleaning_draft['proposal']['query_spec']['target_count']
        preview = body.get('draft_preview')
        if preview is not None:
            try:
                if (not isinstance(preview, dict) or set(preview) != {'groups', 'target_count'}
                        or type(preview['target_count']) is not int or not 1 <= preview['target_count'] <= 10000):
                    raise ValueError('invalid preview')
                preview_edited_groups(None, preview['groups'])
            except ValueError:
                raise ServiceError('INVALID_SPEC', 'Invalid unconfirmed preview', 422) from None
            current, target_count = preview['groups'], preview['target_count']
        messages = body.get('recent_messages', [])
        if (not isinstance(messages, list) or len(messages) > 20 or any(
                not isinstance(item, dict) or set(item) != {'role', 'text'}
                or item['role'] not in ('user', 'assistant') or not isinstance(item['text'], str)
                or len(item['text']) > 2000 for item in messages)):
            raise ServiceError('INVALID_SPEC', 'Invalid conversation context', 422)
        from .followup_inputs import explicit_requirement
        explicit_context = {**conversation_context, 'has_unconfirmed_preview': preview is not None}
        direct = explicit_requirement(body['text'], current, explicit_context)
        if direct is not None:
            increment('explicit_requirement_hits')
            result = timed_call('explicit_validation', ModelRuntime._validate_followup,
                                direct, body['text'], current, explicit_context)
            workspace = None
        else:
            if self.dialogue is not None:
                messages = timed_call('history_read', self.dialogue.store.task_history, actor, task['project_id'], task_id)
            workspace = timed_call('workspace_read', self._task_workspace, actor, task_id)
            if workspace['task_version'] != task['task_version']:
                raise ServiceError('REVISION_CONFLICT', 'Task changed while reading workflow', 409)
            scope = getattr(self.model_runtime, 'business_scope', None)
            with (scope(project_id=task['project_id'], task_id=task_id,
                        task_version=task['task_version'], revision=task['confirmed_revision'],
                        generation=task['run_generation']) if scope else nullcontext()):
                runtime = self.model_runtime.conversation_runtime() if orchestrated else self.model_runtime
                result = runtime.classify_followup(
                    spec['raw_request'], current, body['text'], body['llm_id'],
                    task_context={'task_id': task_id, 'stage': task['stage'], 'run_status': task['run_status'],
                                  'target_count': target_count, 'has_unconfirmed_preview': preview is not None,
                                  'workflow': workspace['workflow'],
                                  'pending_cleaning_feedback': (workspace.get('pending_draft') or {}).get('feedback_text'),
                                  'cleaning_plan': [{'node_id': node['node_id'], 'source': node['source']}
                                                    for node in confirmed_instruction_plan(effective_spec)], **conversation_context},
                    recent_messages=messages)
        after = timed_call('version_check', self.service.task_read_summary, actor, task_id)
        if (after['confirmed_revision'] != task['confirmed_revision']
                or after['task_version'] != task['task_version']):
            raise ServiceError('REVISION_CONFLICT', 'Task changed during classification', 409)
        if result.get('status_requested'):
            from .workflow_state import workflow_reply, status_question_topic
            # A deterministic status reply made no external call or mutation.
            # Model-routed status still refreshes after the model has waited.
            reader = getattr(self.task_events, 'task_mutation_sequence', None)
            sequence = workspace.get('mutation_sequence')
            reusable = (status_question_topic(body['text']) is not None and sequence is not None
                        and callable(reader) and reader(task_id) == sequence)
            refreshed = (workspace if reusable else
                         timed_call('workspace_refresh', self._task_workspace, actor, task_id))
            if refreshed['task_version'] != task['task_version']:
                raise ServiceError('REVISION_CONFLICT', 'Task changed while answering status', 409)
            result.update(workspace=refreshed, workflow=refreshed['workflow'],
                          reply=workflow_reply(refreshed['workflow'], result.get('status_topic', 'progress')))
        if orchestrated:
            plan = timed_call('draft_prepare', coordinator.prepare, actor, task, spec, cleaning_draft, body['text'],
                                       self.headers.get('Idempotency-Key', ''), result)
            receipt.checkpoint(plan)
            result = timed_call('draft_save', coordinator.commit, actor, plan)
        if self.dialogue is not None and not orchestrated:
            self.dialogue.store.append_task_turn(actor, task['project_id'], task_id, task['task_version'],
                                                 body['text'], result.get('reply') or '本轮理解：' + result['kind'])
        return self._conversation_result(task, result, orchestrated)

    @platform_request
    def do_POST(self) -> None:  # noqa: N802
        if self.browse_only:
            self._json(503, {'code':'BROWSE_ONLY', 'message':'This validation instance is browse-only',
                             'retryable':False, 'trace_id':self._trace_id()})
            return
        query_image_match = QUERY_IMAGES_PATH.fullmatch(urlsplit(self.path).path)
        if query_image_match and query_image_match.group(2) is None:
            def query_image_action():
                if urlsplit(self.path).query:
                    raise ServiceError("INVALID_SPEC", "参考图上传地址不接受查询参数", 422)
                if self.query_images is None:
                    raise ServiceError("QUERY_IMAGES_UNAVAILABLE", "参考图输入尚未启用", 503)
                result, replayed = self.query_images.create(
                    self._actor(), query_image_match.group(1),
                    self.headers.get("Idempotency-Key", ""), self._image_body(),
                    self.headers.get("Content-Type", ""))
                return HTTPStatus.CREATED if not replayed else HTTPStatus.OK, result, replayed
            self._run(query_image_action)
            return
        refill_match = REFILL_PATH.fullmatch(self.path)
        if refill_match:
            def refill_action():
                if self.refill_service is None:
                    raise ServiceError('REFILL_NOT_CONFIGURED',
                                       'Confirmed refill planning is unavailable', 503)
                body = self._body()
                expected = {'expected_revision', 'expected_generation',
                            'expected_task_version', 'position_fingerprint', 'action'}
                if set(body) != expected:
                    raise ServiceError('INVALID_SPEC', 'Invalid refill confirmation payload', 422)
                result = self.refill_service.confirm_next(
                    self._actor(), refill_match.group(1),
                    key=self.headers.get('Idempotency-Key', ''), **body)
                return HTTPStatus.ACCEPTED, result, result['replayed']
            self._run(refill_action)
            return
        task_expansion_match = TASK_EXPANSION_JOBS_PATH.fullmatch(self.path)
        if task_expansion_match:
            def task_expansion_action():
                if self.expansion_jobs is None:
                    raise ServiceError('EXPANSION_JOBS_UNAVAILABLE', 'Expansion jobs are unavailable', 503)
                task_id, job_id, operation = task_expansion_match.groups()
                actor = self._actor()
                body = self._body()
                if job_id is None:
                    return HTTPStatus.ACCEPTED, self.expansion_jobs.start_task(actor, task_id, body), False
                if operation != 'retry' or body:
                    raise ServiceError('INVALID_SPEC', 'Invalid expansion retry', 422)
                task = self.service.get_task(actor, task_id)
                return HTTPStatus.ACCEPTED, self.expansion_jobs.retry(
                    actor, task['project_id'], 'task:' + task_id, job_id), False
            self._run(task_expansion_action)
            return
        expansion_job_match = EXPANSION_JOBS_PATH.fullmatch(self.path)
        archive_match = DIALOGUE_ARCHIVE_PATH.fullmatch(self.path)
        if archive_match:
            def archive_action():
                if self.dialogue is None:
                    raise ServiceError('DIALOGUE_NOT_CONFIGURED', 'Conversation state is unavailable', 503)
                actor = self._actor()
                project_id, session_id = archive_match.groups()
                body = self._body()
                if set(body) != {'task_id'} or not isinstance(body['task_id'], str):
                    raise ServiceError('INVALID_SPEC', 'Invalid clarification archive payload', 422)
                task = self.service.get_task(actor, body['task_id'])
                if task['project_id'] != project_id:
                    raise ServiceError('INVALID_SPEC', 'Task belongs to another project', 422)
                return HTTPStatus.OK, self.dialogue.store.archive_to_task(
                    actor, project_id, session_id, body['task_id']), False
            self._run(archive_action)
            return
        if expansion_job_match:
            def expansion_job_action():
                if self.expansion_jobs is None:
                    raise ServiceError('EXPANSION_JOBS_UNAVAILABLE', 'Expansion jobs are unavailable', 503)
                actor = self._actor()
                project_id, session_id, job_id, operation = expansion_job_match.groups()
                body = self._body()
                if job_id is None:
                    if body.get('dialogue_session_id') != session_id:
                        raise ServiceError('INVALID_SPEC', 'Session binding mismatch', 422)
                    return HTTPStatus.ACCEPTED, self.expansion_jobs.start(actor, project_id, body), False
                if operation != 'retry' or body:
                    raise ServiceError('INVALID_SPEC', 'Invalid expansion retry', 422)
                return HTTPStatus.ACCEPTED, self.expansion_jobs.retry(actor, project_id, session_id, job_id), False
            self._run(expansion_job_action)
            return
        delivery_match = re.fullmatch(r'/v1/retrieval/tasks/([^/]+)/delivery/(prepare|publish)', self.path)
        if delivery_match:
            def delivery_action():
                if self.delivery_service is None:
                    raise ServiceError('DELIVERY_UNAVAILABLE', 'Image delivery is not configured', 503)
                actor = self._actor()
                task_id, action = delivery_match.groups()
                body = self._body()
                expected = ({'expected_task_version', 'result_set_digest'} if action == 'prepare'
                            else {'package_id', 'manifest_digest', 'expected_task_version', 'action'})
                if set(body) != expected:
                    raise ServiceError('INVALID_SPEC', 'Invalid delivery request fields', 422)
                result, replayed = getattr(self.delivery_service, action)(actor, task_id, **body)
                return HTTPStatus.OK, result, replayed
            self._run(delivery_action)
            return
        result_exclusion_match = RESULT_EXCLUSIONS_PATH.fullmatch(self.path)
        if result_exclusion_match:
            def result_exclusion_action():
                if self.result_reader is None or not callable(
                        getattr(self.result_reader, 'exclude_result_candidates', None)):
                    raise ServiceError('RESULT_FEEDBACK_UNAVAILABLE',
                                       'Result exclusion is unavailable', 503)
                body = self._body()
                required = {'expected_task_version', 'result_set_digest',
                            'candidate_id', 'scope', 'reason'}
                if (not required <= set(body)
                        or set(body) - required - {'cluster_id'}):
                    raise ServiceError('INVALID_SPEC',
                                       'Invalid result exclusion fields', 422)
                result, replayed = self.result_reader.exclude_result_candidates(
                    self._actor(), result_exclusion_match.group(1),
                    key=self.headers.get('Idempotency-Key', ''), **body)
                return HTTPStatus.OK, result, replayed
            self._run(result_exclusion_action)
            return
        manual_review_match = MANUAL_REVIEWS_PATH.fullmatch(self.path)
        if manual_review_match:
            def manual_review_action():
                if self.result_reader is None or not callable(
                        getattr(self.result_reader, 'review_unknown_candidate', None)):
                    raise ServiceError('MANUAL_REVIEW_UNAVAILABLE',
                                       'Manual review is unavailable', 503)
                body = self._body()
                expected = {'expected_task_version', 'result_set_digest',
                            'candidate_id', 'asset_version', 'action', 'reason'}
                if set(body) != expected:
                    raise ServiceError('INVALID_SPEC',
                                       'Invalid manual review fields', 422)
                result, replayed = self.result_reader.review_unknown_candidate(
                    self._actor(), manual_review_match.group(1),
                    key=self.headers.get('Idempotency-Key', ''), **body)
                return HTTPStatus.OK, result, replayed
            self._run(manual_review_action)
            return
        settings_match = MODEL_SETTINGS_PATH.fullmatch(self.path)
        if settings_match:
            self._run(lambda: self._model_settings_action(settings_match, True))
            return
        dialogue_list_match = PROJECT_DIALOGUE_SESSIONS_PATH.fullmatch(self.path)
        if dialogue_list_match:
            def start_dialogue():
                if self.dialogue is None:
                    raise ServiceError('DIALOGUE_NOT_CONFIGURED', 'Conversation state is unavailable', 503)
                if self._body():
                    raise ServiceError('INVALID_SPEC', 'New Chat request must be empty', 422)
                return HTTPStatus.CREATED, self.dialogue.start(
                    self._actor(), dialogue_list_match.group(1)), False
            self._run(start_dialogue)
            return
        dialogue_match = DIALOGUE_TURN_PATH.fullmatch(self.path)
        if dialogue_match:
            def dialogue_action():
                if self.dialogue is None:
                    raise ServiceError('DIALOGUE_NOT_CONFIGURED', 'Conversation state is unavailable', 503)
                actor, project_id, body = self._actor(), dialogue_match.group(1), self._body()
                if self.path.endswith('/dialogue-turn'):
                    return HTTPStatus.OK, self.dialogue.turn(actor, project_id, body), False
                if getattr(self.dialogue, 'store', None) is None:
                    raise ServiceError('DIALOGUE_NOT_CONFIGURED', 'Durable conversation storage unavailable', 503)
                self.service.projects.require_access(actor, project_id, 'default', 'create')
                key = self.headers.get('Idempotency-Key', '')
                if self.headers.get('Prefer') == 'respond-async':
                    _, replayed = self.task_commands.submit(actor, self.task_commands.dialogue_scope(project_id),
                        key, body, lambda receipt: self.dialogue.turn(actor, project_id, body, receipt=receipt),
                        retry_failed=self.headers.get('X-Conversation-Retry') == 'true')
                    receipt = self._dialogue_command_receipt(actor, project_id, key)
                    return (HTTPStatus.ACCEPTED if receipt['status'] in ('understanding', 'prepared') else HTTPStatus.OK), receipt, replayed
                result, replayed = self.task_commands.execute(actor, self.task_commands.dialogue_scope(project_id),
                    key, body, lambda receipt: self.dialogue.turn(actor, project_id, body, receipt=receipt),
                    retry_failed=self.headers.get('X-Conversation-Retry') == 'true')
                current = self.dialogue.store.read(actor, project_id, result['session_id'])
                return HTTPStatus.OK, {**result, 'command_key': key,
                                      'is_current': current['version'] == result['version']}, replayed
            self._run(dialogue_action)
            return
        cleaning_match = CLEANING_POST_PATH.fullmatch(self.path)
        if cleaning_match:
            def cleaning_action():
                if self.cleaning_service is None:
                    raise ServiceError('CLEANING_NOT_CONFIGURED',
                                       'Confirmed cleaning execution is unavailable', 503)
                actor = self._actor()
                task_id, operation = cleaning_match.groups()
                body = self._body()
                if operation == 'prepare':
                    if set(body) == {'manifest_ref'}:
                        return HTTPStatus.OK, self.cleaning_service.prepare(
                            actor, task_id, body['manifest_ref']), False
                    if body == {'incremental_recompute': True} and callable(
                            getattr(self.cleaning_service, 'prepare_incremental_recompute', None)):
                        return HTTPStatus.OK, self.cleaning_service.prepare_incremental_recompute(
                            actor, task_id), False
                if operation == 'confirm' and set(body) == {
                        'proposal_digest', 'expected_task_version', 'action'}:
                    result, replayed = self.cleaning_service.confirm(
                        actor, task_id, key=self.headers.get('Idempotency-Key', ''), **body)
                    if self.cleaning_dispatch is not None:
                        self.cleaning_dispatch.notify()
                        result = {**result, 'status': self.cleaning_dispatch.status(
                            actor, task_id, result['operation_id'])['status']}
                    return HTTPStatus.ACCEPTED, result, replayed
                if operation == 'execute' and set(body) == {'operation_id'}:
                    if self.cleaning_dispatch is not None:
                        self.cleaning_service.steps(actor, task_id)
                        self.cleaning_dispatch.notify()
                        return HTTPStatus.ACCEPTED, self.cleaning_dispatch.status(
                            actor, task_id, body['operation_id']), False
                    result, replayed = self.cleaning_service.execute(
                        actor, task_id, body['operation_id'])
                    return HTTPStatus.OK, result, replayed
                raise ServiceError('INVALID_CLEANING_REQUEST',
                                   'Invalid confirmed cleaning payload', 422)
            self._run(cleaning_action)
            return
        manifest_match = FIRST_SEARCH_MANIFEST_PATH.fullmatch(self.path)
        if manifest_match:
            def manifest_action():
                if self.first_search is None or not callable(
                        getattr(self.first_search, 'confirm_manifest', None)):
                    raise ServiceError('CANDIDATE_MANIFEST_NOT_CONFIGURED',
                                       'Candidate manifest publication is unavailable', 503)
                body = self._body()
                if set(body) != {'proposal_digest', 'expected_task_version', 'action'}:
                    raise ServiceError('INVALID_SPEC', 'Invalid candidate confirmation payload', 422)
                result, replayed = self.first_search.confirm_manifest(
                    self._actor(), manifest_match.group(1),
                    key=self.headers.get('Idempotency-Key', ''), **body)
                return HTTPStatus.ACCEPTED, result, replayed
            self._run(manifest_action)
            return
        first_search_match = FIRST_SEARCH_POST_PATH.fullmatch(self.path)
        if first_search_match:
            def first_search_action():
                actor = self._actor()
                task_id, action = first_search_match.groups()
                if self.first_search is None:
                    raise ServiceError('LOCAL_SEARCH_NOT_CONFIGURED', 'Local first search is not enabled', 503)
                body = self._body()
                if action == 'prepare' and set(body) == {'routing_digest', 'choices'}:
                    return HTTPStatus.OK, self.first_search.prepare(actor, task_id, **body), False
                if action == 'confirm' and set(body) == {'proposal_digest', 'expected_task_version', 'action'}:
                    result, replay = self.first_search.confirm(actor, task_id,
                        key=self.headers.get('Idempotency-Key', ''), **body)
                    self.first_search.enqueue(actor, task_id)
                    return HTTPStatus.OK, result, replay
                if action == 'tick' and set(body) == {'max_requests'}:
                    return HTTPStatus.OK, self.first_search.tick(actor, task_id, **body), False
                raise ServiceError('INVALID_SEARCH_REQUEST', 'Invalid local search action payload', 422)
            self._run(first_search_action)
            return
        catalog_match = CATALOG_PATH.fullmatch(self.path)
        if catalog_match and catalog_match.group(2):
            self._run(lambda: self._catalog_action(catalog_match, True))
            return
        draft_match = DRAFT_PATH.fullmatch(self.path)
        feedback_match = FEEDBACK_PATH.fullmatch(self.path)
        suggestion_match = FEEDBACK_SUGGESTION_PATH.fullmatch(self.path)
        followup_match = FOLLOWUP_INTENT_PATH.fullmatch(self.path)
        confirmation_match = CONFIRMATION_PATH.fullmatch(self.path)
        expansion_match = EXPANSIONS_PATH.fullmatch(self.path)
        intent_match = INTENT_PREVIEW_PATH.fullmatch(self.path)
        if (self.path != "/v1/retrieval/tasks" and draft_match is None
                and feedback_match is None
                and suggestion_match is None and followup_match is None and confirmation_match is None
                and expansion_match is None and intent_match is None):
            self._json(404, {"code": "NOT_FOUND", "message": "route was not found", "retryable": False, "trace_id": self._trace_id()})
            return

        def action() -> tuple[int, dict[str, Any], bool]:
            if followup_match:
                actor = self._actor()
                body = self._body()
                orchestrated = self.path.endswith('/conversation-turn')
                if orchestrated:
                    from .service import require_key
                    require_key(self.headers.get('Idempotency-Key', ''))
                required = {'text', 'llm_id', 'expected_revision', 'expected_task_version'}
                if (not required <= set(body) or set(body) - required - ({'draft_preview', 'recent_messages', 'feedback_ref'} if orchestrated else {'draft_preview', 'recent_messages'})
                        or not isinstance(body['text'], str) or not isinstance(body['llm_id'], str)
                        or type(body['expected_revision']) is not int
                        or type(body['expected_task_version']) is not int):
                    raise ServiceError('INVALID_SPEC', 'Bound follow-up classification request required', 422)
                task_id = followup_match.group(1)
                if not orchestrated:
                    return HTTPStatus.OK, self._followup_response(actor, task_id, body, False), False
                self.service.task_read_summary(actor, task_id)
                if (self.headers.get('Prefer') == 'respond-async'
                        and self._task_requires_worker(actor, task_id, body)):
                    from copy import copy, deepcopy
                    worker = copy(self)
                    worker.headers = deepcopy(self.headers)
                    key = self.headers.get('Idempotency-Key', '')
                    _, replayed = self.task_commands.submit(actor, task_id, key, body,
                        lambda receipt: worker._followup_response(actor, task_id, body, True, receipt),
                        retry_failed=self.headers.get('X-Conversation-Retry') == 'true')
                    receipt = self._task_command_receipt(actor, task_id, key)
                    receipt['command_replayed'] = replayed
                    return (HTTPStatus.ACCEPTED if receipt['status'] in ('understanding', 'prepared') else HTTPStatus.OK), receipt, replayed
                result, replayed = self.task_commands.execute(actor, task_id,
                    self.headers.get('Idempotency-Key', ''), body,
                    lambda receipt: self._followup_response(actor, task_id, body, True, receipt),
                    retry_failed=self.headers.get('X-Conversation-Retry') == 'true')
                current = self.service.task_read_summary(actor, task_id)
                if 'pending_draft' not in current:
                    current = self.service.get_task(actor, task_id)
                return HTTPStatus.OK, {**result, 'command_key': self.headers.get('Idempotency-Key', ''),
                    'command_replayed': replayed,
                    'current_task_version': current['task_version'],
                    'is_current': self._turn_is_current(result, current)}, replayed
            if suggestion_match:
                actor = self._actor()
                body = self._body()
                if (set(body) != {'draft_id', 'expected_draft_version', 'proposal_digest', 'model_selection'}
                        or not isinstance(body['model_selection'], dict)
                        or set(body['model_selection']) != {'llm', 'vlm'}
                        or not isinstance(body['draft_id'], str)
                        or type(body['expected_draft_version']) is not int
                        or not isinstance(body['proposal_digest'], str)):
                    raise ServiceError('INVALID_SPEC', 'Bound feedback suggestion request required', 422)
                task_id = suggestion_match.group(1)
                draft = self.service.get_feedback(actor, task_id, body['draft_id'])
                task = self.service.get_task(actor, task_id)
                if (draft['status'] != 'ready' or draft['draft_version'] != body['expected_draft_version']
                        or draft['proposal_digest'] != body['proposal_digest']
                        or draft['base_revision'] != task['confirmed_revision']
                        or draft['base_task_version'] != task['task_version']):
                    raise ServiceError('REVISION_CONFLICT', 'Feedback draft changed; reload', 409)
                revisions = self.service.list_revisions(actor, task_id)
                base = next((item['proposal']['query_spec']['raw_request'] for item in revisions
                             if item['revision'] == draft['base_revision']), None)
                if not isinstance(base, str):
                    raise ServiceError('STORAGE_CORRUPT', 'Confirmed request unavailable', 500)
                if 'cleaning_conditions' in draft['proposal']['query_spec']:
                    from .requirement_changes import preview_changes
                    base_spec = next(row['proposal']['query_spec'] for row in revisions
                                     if row['revision'] == draft['base_revision'])
                    result = {'schema_version': 'feedback-cleaning-suggestion@1',
                              'task_id': task_id, 'draft_id': draft['draft_id'],
                              'draft_version': draft['draft_version'], 'proposal_digest': draft['proposal_digest'],
                              'base_revision': draft['base_revision'], 'base_task_version': draft['base_task_version'],
                              'feedback_status': 'ready', 'needs_clarification': False,
                              'requirement': '', 'rationale': '', 'status': 'reviewable',
                              'cleaning_plan_preview': preview_changes(base_spec, draft['proposal']['query_spec'])}
                    result['suggestion_digest'] = digest(result)
                    return HTTPStatus.OK, result, False
                selection = body['model_selection']
                scope = getattr(self.model_runtime, 'business_scope', None)
                with (scope(project_id=task['project_id'], task_id=task_id,
                            task_version=task['task_version'], revision=task['confirmed_revision'],
                            generation=task['run_generation']) if scope else nullcontext()):
                    suggestion = self.model_runtime.suggest_cleaning(base, draft['feedback_text'],
                                                                   selection['llm'], selection['vlm'])
                current_draft = self.service.get_feedback(actor, task_id, body['draft_id'])
                current_task = self.service.get_task(actor, task_id)
                if (current_draft != draft or current_task['confirmed_revision'] != task['confirmed_revision']
                        or current_task['task_version'] != task['task_version']):
                    raise ServiceError('REVISION_CONFLICT', 'Feedback draft changed during suggestion', 409)
                result = {'schema_version': 'feedback-cleaning-suggestion@1',
                          'task_id': task_id, 'base_revision': draft['base_revision'],
                          'base_task_version': draft['base_task_version'],
                          'draft_id': draft['draft_id'], 'draft_version': draft['draft_version'],
                          'proposal_digest': draft['proposal_digest'], **suggestion,
                          'status': 'advisory_only',
                          'note': '仅供审阅；不在反馈确认摘要内，不能创建或运行清洗管线。'}
                if suggestion.get('feedback_status') == 'ready':
                    old_nodes = confirmed_instruction_plan(next(row['proposal']['query_spec'] for row in revisions
                        if row['revision'] == draft['base_revision']))
                    new_nodes = confirmed_instruction_plan(draft['proposal']['query_spec'])
                    result['cleaning_plan_preview'] = {'before_count': len(old_nodes),
                        'after_count': len(new_nodes),
                        'nodes': [{'node_id': node['node_id'], 'source': node['source'],
                                   'instruction': node['instruction']} for node in new_nodes]}
                result['suggestion_digest'] = digest(result)
                return HTTPStatus.OK, result, False
            if intent_match:
                actor = self._actor()
                body = self._body()
                if set(body) != {'raw_request', 'dimension_id', 'llm_id'} or not isinstance(body['dimension_id'], str) or not body['dimension_id'].strip():
                    raise ServiceError('INVALID_SPEC', 'Intent preview requires raw_request, dimension_id and llm_id', 422)
                self.service.projects.require_access(actor, intent_match.group(1), body['dimension_id'], 'create')
                result = self.model_runtime.analyze_request(body['raw_request'], body['llm_id'])
                self.service.projects.require_access(actor, intent_match.group(1), body['dimension_id'], 'create')
                return HTTPStatus.OK, result, False
            if expansion_match:
                actor = self._actor()
                body = self._body()
                selection = body.pop('model_selection', None)
                target_count = body.pop('target_count', None)
                feedback_text = body.pop('feedback_text', None)
                current_groups = body.pop('current_groups', None)
                requested_terms_per_group = body.pop('requested_terms_per_group', None)
                count_edits = body.pop('count_edits', None)
                current_target_count = body.pop('current_target_count', None)
                if selection is not None and (not isinstance(selection, dict) or set(selection) != {'llm', 'vlm'}):
                    raise ServiceError('INVALID_SPEC', 'Select LLM and VLM profile IDs', 422)
                edited = set(body) == {"parent_label", "groups", "dimension_id"}
                if any(value is not None for value in (target_count, feedback_text, current_groups,
                                                       requested_terms_per_group, count_edits, current_target_count)) and (edited or selection is None):
                    raise ServiceError('INVALID_SPEC', 'Count or feedback requires model expansion', 422)
                if not edited and set(body) != {"raw_request", "dimension_id"}:
                    raise ServiceError("INVALID_SPEC", "expansion preview requires raw_request and dimension_id", 422)
                dimension = body["dimension_id"]
                if not isinstance(dimension, str) or not dimension.strip():
                    raise ServiceError("INVALID_SPEC", "dimension_id is required", 422,
                                       field_path="$.dimension_id")
                self.service.projects.require_access(
                    actor, expansion_match.group(1), dimension, "create")
                try:
                    if selection is not None:
                        binding = self.model_runtime.binding(selection['llm'], selection['vlm'])
                        if edited:
                            return HTTPStatus.OK, self.model_runtime.bind(
                                preview_edited_groups(body['parent_label'], body['groups']), binding), False
                        result = self.model_runtime.expand(body['raw_request'], selection['llm'], selection['vlm'],
                                                           target_count=target_count,
                                                           feedback_text=feedback_text,
                                                           current_groups=current_groups,
                                                           requested_terms_per_group=requested_terms_per_group,
                                                           count_edits=count_edits, current_target_count=current_target_count)
                        self.service.projects.require_access(actor, expansion_match.group(1), dimension, 'create')
                        return HTTPStatus.OK, result, False
                    if edited:
                        return HTTPStatus.OK, preview_edited_groups(body["parent_label"], body["groups"]), False
                    return HTTPStatus.OK, preview_grouped_expansion(body["raw_request"]), False
                except ValueError as error:
                    raise ServiceError("INVALID_EXPANSION_REQUEST", str(error), 422) from None
            if draft_match:
                result, replayed = self.service.save_draft(self._actor(), draft_match.group(1),
                    self.headers.get("Idempotency-Key", ""), self._body(), draft_match.group(2))
                return HTTPStatus.OK, result, replayed
            if feedback_match:
                result, replayed = self.service.save_feedback(
                    self._actor(), feedback_match.group(1), self.headers.get("Idempotency-Key", ""),
                    self._body(), feedback_match.group(2))
                return HTTPStatus.OK, result, replayed
            if confirmation_match:
                actor, task_id, body = self._actor(), confirmation_match.group(1), self._body()
                result, replayed = self.service.confirm_feedback(
                    actor, task_id, self.headers.get("Idempotency-Key", ""), body)
                draft = result['draft']
                revisions = self.service.list_revisions(actor, task_id)
                base = next((row['proposal'] for row in revisions
                             if row['revision'] == draft['base_revision']), None)
                if (self.first_search is not None and body.get('action') == 'confirm'
                        and base is not None and (
                            draft['proposal']['query_spec'].get('grouped_expansion')
                            != base['query_spec'].get('grouped_expansion')
                            or draft['proposal']['query_spec'].get('target_count')
                            != base['query_spec'].get('target_count'))):
                    result['search_enqueue'] = (self.search_dispatch.dispatch_task(task_id)
                        if self.search_dispatch is not None else self.first_search.auto_start(
                            actor, task_id, key='terms:' + draft['proposal_digest']))
                    result['task'] = self.service.get_task(actor, task_id)
                return HTTPStatus.ACCEPTED, result, replayed
            actor = self._actor()
            result, replayed = self.service.create_task(actor, self.headers.get("Idempotency-Key", ""), self._body())
            if self.first_search is not None:
                enqueue = (self.search_dispatch.dispatch_task(result['task_id'])
                    if self.search_dispatch is not None else self.first_search.auto_start(
                        actor, result['task_id'], key='terms:' + result['task_id'] + ':1'))
                result = self.service.get_task(actor, result['task_id'])
                result['search_enqueue'] = enqueue
            return HTTPStatus.ACCEPTED, result, replayed

        self._run(action)

    @platform_request
    def do_DELETE(self) -> None:  # noqa: N802
        if self.browse_only:
            self._json(503, {'code':'BROWSE_ONLY', 'message':'This validation instance is browse-only',
                             'retryable':False, 'trace_id':self._trace_id()})
            return
        path = urlsplit(self.path).path
        dialogue_match = DIALOGUE_SESSION_PATH.fullmatch(path)
        conversation_match = CONVERSATION_PATH.fullmatch(path)
        if dialogue_match:
            def delete_dialogue():
                if self.dialogue is None:
                    raise ServiceError('DIALOGUE_NOT_CONFIGURED', 'Conversation state is unavailable', 503)
                actor = self._actor()
                project_id, session_id = dialogue_match.groups()
                self.service.projects.require_access(actor, project_id, 'default', 'create')
                return HTTPStatus.OK, self.dialogue.store.delete(actor, project_id, session_id), False
            self._run(delete_dialogue)
            return
        if conversation_match:
            params = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            force = params.get('force', [''])[0].lower() == 'true'
            if force:
                self._run(lambda: (HTTPStatus.OK,
                                   self.service.hard_delete_conversation(
                                       self._actor(), conversation_match.group(1)),
                                   False))
                return
            self._run(lambda: (HTTPStatus.OK,
                               self.service.delete_conversation(self._actor(), conversation_match.group(1)),
                               False))
            return
        task_match = TASK_PATH.fullmatch(path)
        if task_match:
            params = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            force = params.get('force', [''])[0].lower() == 'true'
            if force:
                self._run(lambda: (HTTPStatus.OK,
                                   self.service.hard_delete_task(
                                       self._actor(), task_match.group(1)),
                                   False))
                return
            self._json(404, {'code': 'NOT_FOUND', 'message': 'task route requires force=true',
                             'retryable': False, 'trace_id': self._trace_id()})
            return
        self._json(404, {'code': 'NOT_FOUND', 'message': 'route was not found',
                         'retryable': False, 'trace_id': self._trace_id()})

    @platform_request
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == '/platform.js' and self.platform_browser is not None:
            from .platform_ui import PLATFORM_JS
            self._asset('text/javascript', PLATFORM_JS)
            return
        workspace_match = TASK_WORKSPACE_PATH.fullmatch(parsed.path)
        if workspace_match:
            def workspace_action():
                if parsed.query:
                    raise ServiceError('INVALID_SPEC', 'Workspace URL has no query parameters', 422)
                return HTTPStatus.OK, self._task_workspace(self._actor(), workspace_match.group(1)), False
            self._run(workspace_action)
            return
        dialogue_command = DIALOGUE_COMMAND_PATH.fullmatch(parsed.path)
        if dialogue_command:
            def dialogue_command_action():
                from urllib.parse import unquote
                actor = self._actor()
                project_id, key = dialogue_command.groups()
                if self.dialogue is None or getattr(self.dialogue, 'store', None) is None:
                    raise ServiceError('DIALOGUE_NOT_CONFIGURED', 'Conversation state is unavailable', 503)
                result = self._dialogue_command_receipt(actor, project_id, unquote(key))
                return HTTPStatus.OK, result, False
            self._run(dialogue_command_action)
            return
        command_match = TASK_COMMAND_PATH.fullmatch(parsed.path)
        if command_match:
            def command_action():
                from urllib.parse import unquote
                actor = self._actor()
                task_id, key = command_match.groups()
                result = self._task_command_receipt(actor, task_id, unquote(key))
                return HTTPStatus.OK, result, False
            self._run(command_action)
            return
        query_image_match = QUERY_IMAGES_PATH.fullmatch(parsed.path)
        if query_image_match and query_image_match.group(2):
            project_id, query_image_id, operation = query_image_match.groups()
            if operation == "preview":
                trace_id = self._trace_id()
                try:
                    if parsed.query or self.query_images is None:
                        raise ServiceError("NOT_FOUND", "参考图预览不存在", 404)
                    content_type, content = self.query_images.preview(
                        self._actor(), project_id, query_image_id)
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Cache-Control", "private, no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.end_headers()
                    self.wfile.write(content)
                except ServiceError as error:
                    self._json(error.status, error.as_dict(trace_id=trace_id))
                return
            def query_image_status_action():
                if parsed.query:
                    raise ServiceError("INVALID_SPEC", "参考图状态地址不接受查询参数", 422)
                if self.query_images is None:
                    raise ServiceError("QUERY_IMAGES_UNAVAILABLE", "参考图输入尚未启用", 503)
                return HTTPStatus.OK, self.query_images.get(
                    self._actor(), project_id, query_image_id), False
            self._run(query_image_status_action)
            return
        event_stream_match = TASK_EVENT_STREAM_PATH.fullmatch(parsed.path)
        if event_stream_match:
            trace_id = self._trace_id()
            try:
                self._stream_task_events(parsed, event_stream_match.group(1))
            except ServiceError as error:
                self._json(error.status, error.as_dict(trace_id=trace_id))
            return
        task_expansion_match = TASK_EXPANSION_JOBS_PATH.fullmatch(urlsplit(self.path).path)
        if task_expansion_match:
            def task_expansion_action():
                if urlsplit(self.path).query or task_expansion_match.group(3):
                    raise ServiceError('INVALID_SPEC', 'Invalid expansion job URL', 422)
                if self.expansion_jobs is None:
                    raise ServiceError('EXPANSION_JOBS_UNAVAILABLE', 'Expansion jobs are unavailable', 503)
                task_id, job_id, _ = task_expansion_match.groups()
                actor = self._actor()
                task = self.service.get_task(actor, task_id)
                return HTTPStatus.OK, self.expansion_jobs.get(
                    actor, task['project_id'], 'task:' + task_id, job_id), False
            self._run(task_expansion_action)
            return
        expansion_job_match = EXPANSION_JOBS_PATH.fullmatch(urlsplit(self.path).path)
        if expansion_job_match:
            def expansion_job_action():
                if urlsplit(self.path).query or expansion_job_match.group(4):
                    raise ServiceError('INVALID_SPEC', 'Invalid expansion job URL', 422)
                if self.expansion_jobs is None:
                    raise ServiceError('EXPANSION_JOBS_UNAVAILABLE', 'Expansion jobs are unavailable', 503)
                project_id, session_id, job_id, _ = expansion_job_match.groups()
                return HTTPStatus.OK, self.expansion_jobs.get(self._actor(), project_id, session_id, job_id), False
            self._run(expansion_job_action)
            return
        settings_match = MODEL_SETTINGS_PATH.fullmatch(urlsplit(self.path).path)
        if settings_match:
            self._run(lambda: self._model_settings_action(settings_match))
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        history_match = re.fullmatch(r'/v1/retrieval/projects/([^/]+)/chat-history', path)
        if history_match:
            def history_action():
                from .chat_history import page
                params = parse_qs(parsed.query, keep_blank_values=True)
                if set(params) - {'limit', 'cursor'} or any(len(v) != 1 for v in params.values()):
                    raise ServiceError('INVALID_SPEC', 'Invalid history parameters', 422)
                try:
                    limit = int(params.get('limit', ['20'])[0])
                except ValueError:
                    raise ServiceError('INVALID_SPEC', 'Invalid history limit', 422) from None
                return HTTPStatus.OK, page(self.service, self.dialogue, self._actor(),
                    history_match.group(1), limit=limit, cursor=params.get('cursor', [None])[0]), False
            self._run(history_action)
            return
        node_page_match = re.fullmatch(r'/v1/retrieval/tasks/([^/]+)/nodes/(packaging|refill)/items', path)
        if node_page_match:
            def node_page_action():
                params = parse_qs(parsed.query, keep_blank_values=True)
                if set(params) - {'limit', 'cursor'} or any(len(v) != 1 for v in params.values()):
                    raise ServiceError('INVALID_SPEC', 'invalid node page parameters', 422)
                try:
                    limit = int(params.get('limit', ['24'])[0])
                except ValueError:
                    raise ServiceError('INVALID_SPEC', 'invalid page size', 422) from None
                reader = getattr(self.service.repository, 'node_read_page', None)
                if reader is None:
                    raise ServiceError('READ_PROJECTION_REQUIRED', 'persistent node pages unavailable', 503)
                return HTTPStatus.OK, reader(self._actor(), *node_page_match.groups(),
                    limit=limit, cursor=params.get('cursor', [None])[0], projects=self.service.projects), False
            self._run(node_page_action)
            return
        delivery_match = re.fullmatch(r'/v1/retrieval/tasks/([^/]+)/delivery(?:/(package_[a-f0-9]{32})/download)?', path)
        if delivery_match:
            try:
                if parsed.query:
                    raise ServiceError('INVALID_SPEC', 'Delivery URL has no query parameters', 422)
                if self.delivery_service is None:
                    raise ServiceError('DELIVERY_UNAVAILABLE', 'Image delivery is not configured', 503)
                actor = self._actor()
                task_id, package_id = delivery_match.groups()
                if package_id is None:
                    self._json(200, self.delivery_service.status(actor, task_id))
                else:
                    content = self.delivery_service.download(actor, task_id, package_id)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/zip')
                    self.send_header('Content-Disposition', f'attachment; filename="{package_id}.zip"')
                    self.send_header('Content-Length', str(len(content)))
                    self.send_header('Cache-Control', 'private, no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.end_headers()
                    self.wfile.write(content)
            except ServiceError as error:
                self._json(error.status, error.as_dict(trace_id=self._trace_id()))
            return
        cleaning_preview_match = CLEANING_PREVIEW_PATH.fullmatch(path)
        if cleaning_preview_match:
            trace_id = self._trace_id()
            try:
                if parsed.query:
                    raise ServiceError('INVALID_RESULT_VIEW',
                                       'Cleaning preview URL has no query parameters', 422)
                if self.cleaning_service is None or not callable(
                        getattr(self.cleaning_service, 'candidate_preview', None)):
                    raise ServiceError('PREVIEW_UNAVAILABLE',
                                       'Cleaning candidate image is unavailable', 404)
                content_type, content = self.cleaning_service.candidate_preview(
                    self._actor(), *cleaning_preview_match.groups())
                self.send_response(HTTPStatus.OK)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'private, no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.end_headers()
                self.wfile.write(content)
            except ServiceError as error:
                self._json(error.status, error.as_dict(trace_id=trace_id))
            return
        if path == '/v1/build':
            if parsed.query:
                self._json(422, {'code': 'INVALID_SPEC', 'message': 'Build URL has no query parameters'})
            else:
                self._json(200, self.build_info)
            return
        dialogue_match = DIALOGUE_SESSION_PATH.fullmatch(path)
        if dialogue_match:
            def dialogue_action():
                if parsed.query:
                    raise ServiceError('INVALID_SPEC', 'Conversation URL has no query parameters', 422)
                if self.dialogue is None:
                    raise ServiceError('DIALOGUE_NOT_CONFIGURED', 'Conversation state is unavailable', 503)
                return HTTPStatus.OK, self.dialogue.snapshot(self._actor(), *dialogue_match.groups()), False
            self._run(dialogue_action)
            return
        dialogue_list_match = PROJECT_DIALOGUE_SESSIONS_PATH.fullmatch(path)
        if dialogue_list_match:
            def dialogue_list_action():
                if parsed.query:
                    raise ServiceError('INVALID_SPEC', 'Conversation URL has no query parameters', 422)
                if self.dialogue is None:
                    raise ServiceError('DIALOGUE_NOT_CONFIGURED', 'Conversation state is unavailable', 503)
                actor = self._actor()
                project_id = dialogue_list_match.group(1)
                self.service.projects.require_access(actor, project_id, 'default', 'read')
                return HTTPStatus.OK, {'items': self.dialogue.store.list(actor, project_id)}, False
            self._run(dialogue_list_action)
            return
        refill_match = REFILL_PATH.fullmatch(path)
        if refill_match:
            def refill_action():
                if parsed.query:
                    raise ServiceError('INVALID_RESULT_VIEW',
                                       'Refill preview URL has no query parameters', 422)
                if self.refill_service is None:
                    raise ServiceError('REFILL_NOT_CONFIGURED',
                                       'Confirmed refill planning is unavailable', 503)
                return HTTPStatus.OK, self.refill_service.preview(
                    self._actor(), refill_match.group(1)), False
            self._run(refill_action)
            return
        node_outputs_match = NODE_OUTPUTS_PATH.fullmatch(path)
        if node_outputs_match:
            def node_outputs_action():
                actor = self._actor()
                task_id, node_id = node_outputs_match.groups()
                if node_id != 'cleaning':
                    raise ServiceError('NODE_OUTPUT_NOT_SUPPORTED',
                                       'Only cleaning step outputs are available', 422)
                reader = self.cleaning_service or self.cleaning_reader
                if reader is None:
                    self.service.get_task(actor, task_id)
                    raise ServiceError('CLEANING_NOT_CONFIGURED',
                                       'Cleaning step outputs are unavailable', 503)
                try:
                    params = parse_qs(parsed.query, keep_blank_values=True,
                                      strict_parsing=True)
                    allowed = {'operation_id', 'step_execution_id', 'output_version',
                               'view_type', 'reason_code', 'rule_id', 'query', 'limit', 'cursor',
                               'as_of_sequence'}
                    if (set(params) - allowed or any(len(values) != 1
                            for values in params.values())
                            or not {'operation_id', 'step_execution_id',
                                    'output_version', 'view_type'} <= set(params)):
                        raise ValueError
                    limit = int(params.get('limit', ['20'])[0])
                    requested_sequence = (int(params['as_of_sequence'][0])
                                          if 'as_of_sequence' in params else None)
                except ValueError:
                    raise ServiceError('INVALID_STEP_OUTPUT_VIEW',
                                       'Invalid cleaning step output query', 422) from None
                page_reader = getattr(reader, 'step_output_read_page', None)
                # The projected reader authorizes and binds the page in one read
                # transaction. Only explicit event-watermark requests need the
                # wider snapshot/compatibility path below.
                if callable(page_reader) and requested_sequence is None:
                    try:
                        page = page_reader(actor, task_id,
                            operation_id=params['operation_id'][0],
                            step_execution_id=params['step_execution_id'][0],
                            output_version=params['output_version'][0],
                            view_type=params['view_type'][0],
                            reason_code=params.get('reason_code', [None])[0],
                            rule_id=params.get('rule_id', [None])[0],
                            query=params.get('query', [None])[0], limit=limit,
                            cursor=params.get('cursor', [None])[0])
                        return HTTPStatus.OK, page, False
                    except StepOutputCursorError as error:
                        raise ServiceError('STEP_OUTPUT_CURSOR_STALE', str(error), 409) from None
                    except ValueError as error:
                        raise ServiceError('INVALID_STEP_OUTPUT_VIEW', str(error), 422) from None
                    except ServiceError as error:
                        if (error.code != 'READ_PROJECTION_MISSING'
                                or getattr(reader, 'require_read_projection', False)):
                            raise
                snapshot = self._task_snapshot(actor, task_id,
                    include_cleaning_items=not callable(page_reader))
                if (requested_sequence is not None
                        and requested_sequence > snapshot['as_of_sequence']):
                    raise ServiceError('INVALID_EVENT_CURSOR',
                                       'as_of_sequence is ahead of task state', 422)
                data = snapshot['cleaning_view']
                progress = snapshot['progress']
                if (data.get('task_id') != task_id
                        or data.get('revision') != progress['revision']
                        or data.get('generation') != progress['run_generation']):
                    raise ServiceError('REVISION_CONFLICT',
                                       'Cleaning step output is no longer current', 409)
                try:
                    has_full_items = any('items' in step
                        for run in data.get('runs', ())
                        for step in run.get('steps', ()))
                    page_builder = (page_reader if callable(page_reader)
                                    and not has_full_items
                                    else lambda actor, task_id, **kwargs:
                                        build_step_output_page(data, **kwargs))
                    page = page_builder(actor, task_id,
                        operation_id=params['operation_id'][0],
                        step_execution_id=params['step_execution_id'][0],
                        output_version=params['output_version'][0],
                        view_type=params['view_type'][0],
                        reason_code=params.get('reason_code', [None])[0], limit=limit,
                        rule_id=params.get('rule_id', [None])[0],
                        query=params.get('query', [None])[0],
                        cursor=params.get('cursor', [None])[0],
                        as_of_sequence=snapshot['as_of_sequence'])
                except StepOutputCursorError as error:
                    raise ServiceError('STEP_OUTPUT_CURSOR_STALE', str(error), 409) from None
                except ValueError as error:
                    raise ServiceError('INVALID_STEP_OUTPUT_VIEW', str(error), 422) from None
                return HTTPStatus.OK, page, False
            self._run(node_outputs_action)
            return
        cleaning_match = re.fullmatch(r'/v1/retrieval/tasks/([^/]+)/cleaning/steps', path)
        if cleaning_match:
            def cleaning_steps_action():
                try:
                    params = parse_qs(parsed.query, keep_blank_values=True,
                                      strict_parsing=True)
                    if set(params) - {'summary'} or any(len(values) != 1
                            for values in params.values()) or params.get('summary', ['false'])[0] not in {
                                'true', 'false'}:
                        raise ValueError
                    summary_only = params.get('summary', ['false'])[0] == 'true'
                except ValueError:
                    raise ServiceError('INVALID_RESULT_VIEW',
                                       'Unexpected cleaning step query parameters', 422) from None
                actor = self._actor()
                task_id = cleaning_match.group(1)
                reader = self.cleaning_service or self.cleaning_reader
                if reader is None:
                    self.service.get_task(actor, task_id)
                    return HTTPStatus.OK, {'available': False, 'runs': [],
                        'note': '尚未产生可查看的清洗步骤结果。'}, False
                summary_reader = (getattr(reader, 'steps_summary', None)
                    if self.cleaning_service is not None else
                    getattr(reader, 'step_results_summary', None)) if summary_only else None
                if callable(summary_reader):
                    try:
                        result = summary_reader(actor, task_id)
                    except ServiceError as error:
                        if (error.code != 'READ_PROJECTION_MISSING'
                                or getattr(reader, 'require_read_projection', False)):
                            raise
                        result = (reader.steps(actor, task_id)
                                  if self.cleaning_service is not None else
                                  reader.step_results(actor, task_id))
                else:
                    result = (reader.steps(actor, task_id) if self.cleaning_service is not None
                              else reader.step_results(actor, task_id))
                if summary_only:
                    result = {**result, 'runs': [{**run, 'steps': [
                        {key: value for key, value in step.items() if key != 'items'}
                        for step in run.get('steps', ())]}
                        for run in result.get('runs', ())]}
                return HTTPStatus.OK, result, False
            self._run(cleaning_steps_action)
            return
        recompute_match = re.fullmatch(
            r'/v1/retrieval/tasks/([^/]+)/cleaning/recompute', path)
        if recompute_match:
            def recompute_action():
                if parsed.query:
                    raise ServiceError('INVALID_RESULT_VIEW', 'Unexpected query parameters', 422)
                if self.cleaning_service is None or not callable(
                        getattr(self.cleaning_service, 'incremental_recompute', None)):
                    raise ServiceError('CLEANING_NOT_CONFIGURED',
                                       'Incremental recomputation is unavailable', 503)
                return HTTPStatus.OK, self.cleaning_service.incremental_recompute(
                    self._actor(), recompute_match.group(1)), False
            self._run(recompute_action)
            return
        cleaning_status_match = re.fullmatch(
            r'/v1/retrieval/tasks/([^/]+)/cleaning/status', path)
        if cleaning_status_match:
            def cleaning_status_action():
                values = parse_qs(parsed.query, keep_blank_values=True)
                if set(values) != {'operation_id'} or len(values['operation_id']) != 1:
                    raise ServiceError('INVALID_CLEANING_REQUEST',
                                       'Exactly one operation_id is required', 422)
                actor = self._actor()
                task_id = cleaning_status_match.group(1)
                if self.cleaning_service is None or self.cleaning_dispatch is None:
                    raise ServiceError('CLEANING_NOT_CONFIGURED',
                                       'Background cleaning is unavailable', 503)
                self.cleaning_service.steps(actor, task_id)
                return HTTPStatus.OK, self.cleaning_dispatch.status(
                    actor, task_id, values['operation_id'][0]), False
            self._run(cleaning_status_action)
            return
        manifest_match = FIRST_SEARCH_MANIFEST_PATH.fullmatch(path)
        if manifest_match:
            def manifest_action():
                if parsed.query:
                    raise ServiceError('INVALID_RESULT_VIEW', 'Candidate manifest URL has no query parameters', 422)
                if self.first_search is None or not callable(
                        getattr(self.first_search, 'preview_manifest', None)):
                    raise ServiceError('CANDIDATE_MANIFEST_NOT_CONFIGURED',
                                       'Candidate manifest publication is unavailable', 503)
                return HTTPStatus.OK, self.first_search.preview_manifest(
                    self._actor(), manifest_match.group(1)), False
            self._run(manifest_action)
            return
        preview_match = FIRST_SEARCH_PREVIEW_PATH.fullmatch(path)
        if preview_match:
            trace_id = self._trace_id()
            try:
                params = parse_qs(parsed.query, keep_blank_values=True)
                if set(params) - {'ref'} or any(len(values) != 1 for values in params.values()):
                    raise ServiceError('INVALID_RESULT_VIEW', 'Invalid preview reference', 422)
                if not isinstance(self.first_search, LocalFirstSearch):
                    raise ServiceError('PREVIEW_UNAVAILABLE', 'Candidate image bytes are unavailable', 404)
                content_type, content = self.first_search.candidate_preview(
                    self._actor(), *preview_match.groups(), ref=params.get('ref', [None])[0])
                self.send_response(HTTPStatus.OK)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'private, no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.end_headers()
                self.wfile.write(content)
            except ServiceError as error:
                self._json(error.status, error.as_dict(trace_id=trace_id))
            return
        first_search_match = FIRST_SEARCH_GET_PATH.fullmatch(path)
        if first_search_match:
            def first_search_action():
                if self.first_search is None:
                    raise ServiceError('LOCAL_SEARCH_NOT_CONFIGURED', 'Local first search is not enabled', 503)
                actor = self._actor()
                task_id, view, alias = first_search_match.groups()
                view = view or alias
                params = parse_qs(parsed.query, keep_blank_values=True)
                options = {}
                if view in ('candidates', 'candidates/changes'):
                    if set(params) - {'limit', 'cursor', 'query'} or any(len(v) != 1 for v in params.values()):
                        raise ServiceError('INVALID_RESULT_VIEW', 'Invalid candidate page parameters', 422)
                    try:
                        options = dict(limit=int(params.get('limit', ['200'])[0]), cursor=params.get('cursor', [None])[0],
                                       query=params.get('query', [''])[0], changes=view.endswith('/changes'))
                    except ValueError:
                        raise ServiceError('INVALID_RESULT_VIEW', 'Invalid candidate page limit', 422) from None
                elif params:
                    raise ServiceError('INVALID_RESULT_VIEW', 'Search status has no query parameters', 422)
                result = (self.first_search.candidates(actor, task_id, **options) if view in ('candidates', 'candidates/changes')
                          else self.first_search.progress(actor, task_id) if view == 'progress'
                          else self.first_search.preview(actor, task_id))
                return HTTPStatus.OK, result, False
            self._run(first_search_action)
            return
        routing_match = ROUTING_PATH.fullmatch(path)
        if routing_match:
            def routing_action():
                actor = self._actor()
                self.service.get_task(actor, routing_match.group(1))
                if self.routing_preview is None:
                    raise ServiceError('ROUTING_NOT_CONFIGURED', '选库预览尚未配置目录及查询能力', 503)
                try:
                    params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                    if set(params) - {'branch_id'}:
                        raise ValueError()
                    branches = tuple(params['branch_id']) if 'branch_id' in params else None
                except ValueError:
                    raise ServiceError('INVALID_ROUTE_SELECTION', 'Only branch_id query parameters supported', 422) from None
                return HTTPStatus.OK, self.routing_preview.preview(actor, routing_match.group(1), branches), False
            self._run(routing_action)
            return
        retrieval_progress_match = RETRIEVAL_PROGRESS_PATH.fullmatch(path)
        if retrieval_progress_match:
            def retrieval_progress_action():
                actor = self._actor()
                task_id = retrieval_progress_match.group(1)
                self.service.get_task(actor, task_id)
                if parsed.query:
                    raise ServiceError('INVALID_RESULT_VIEW', 'Retrieval progress has no query parameters', 422)
                if self.plan_set_repository is None:
                    raise ServiceError('RETRIEVAL_PROGRESS_NOT_CONFIGURED',
                                       'Approved plan-set ledger is not configured', 503)
                return HTTPStatus.OK, read_retrieval_progress(
                    self.plan_set_repository, actor, task_id), False
            self._run(retrieval_progress_action)
            return
        agent_runs_match = AGENT_RUNS_PATH.fullmatch(path)
        if agent_runs_match:
            def agent_runs_action():
                actor = self._actor()
                task_id = agent_runs_match.group(1)
                self.service.get_task(actor, task_id)
                if parsed.query:
                    raise ServiceError('INVALID_RESULT_VIEW', 'Agent runs have no query parameters', 422)
                # Identifiers and status only; DSH sessions are audit records, not task state.
                runs = getattr(self.model_runtime, 'agent_runs', None)
                return HTTPStatus.OK, {'engine': self.model_runtime.status().get('engine'),
                                       'items': runs(actor.tenant_id, task_id) if runs else []}, False
            self._run(agent_runs_action)
            return
        task_events_match = TASK_EVENTS_PATH.fullmatch(path)
        if task_events_match:
            def task_events_action():
                actor = self._actor()
                task_id = task_events_match.group(1)
                try:
                    params = parse_qs(parsed.query, keep_blank_values=True,
                                      strict_parsing=True)
                    if set(params) - {"after_sequence", "limit", "compact"} or any(
                            len(values) != 1 for values in params.values()):
                        raise ValueError
                    if params.get("compact", ["false"])[0] not in ("true", "false"):
                        raise ValueError
                    after = int(params.get("after_sequence", ["0"])[0])
                    limit = int(params.get("limit", ["100"])[0])
                except ValueError:
                    raise ServiceError("INVALID_EVENT_CURSOR",
                                       "invalid task event query", 422) from None
                if params.get('compact') == ['true']:
                    task = self.service.task_read_summary(actor, task_id)
                    mutation = self.task_events.task_mutation_sequence(task_id)
                    if not self.task_events.can_replay(actor, task, mutation):
                        self._task_snapshot(actor, task_id)
                    return HTTPStatus.OK, {"schema_version": "task-events@1", "task_id": task_id,
                        **self.task_events.read(actor, task_id, after_sequence=after, limit=limit)}, False
                snapshot = self._task_snapshot(actor, task_id)
                page = self.task_events.read(
                    actor, task_id, after_sequence=after, limit=limit)
                return HTTPStatus.OK, {"schema_version": "task-events@1",
                    "task_id": task_id, **page,
                    "snapshot": {"progress": snapshot["progress"],
                                 "result_binding": snapshot["result_binding"],
                                 "executions": snapshot["executions"],
                                 "step_outputs": snapshot["step_outputs"]}}, False
            self._run(task_events_action)
            return
        if path == '/catalog':
            self._asset('text/html', CATALOG_HTML)
            return
        if path == '/catalog.js':
            self._asset('text/javascript', CATALOG_JS)
            return
        catalog_match = CATALOG_PATH.fullmatch(path)
        if catalog_match and catalog_match.group(2) is None:
            self._run(lambda: self._catalog_action(catalog_match))
            return
        if path in ("/", "/production"):
            self._asset("text/html", BOARD_HTML)
            return
        if path == "/production.css":
            self._asset("text/css", BOARD_CSS)
            return
        if path == "/production.js":
            self._asset("text/javascript", BOARD_JS)
            return
        if path == "/chat":
            self._asset("text/html", CHAT_HTML)
            return
        if path == "/chat.css":
            self._asset("text/css", CHAT_CSS)
            return
        if path == "/chat.js":
            self._asset("text/javascript", CHAT_JS)
            return
        match = TASK_PATH.fullmatch(path)
        draft_match = DRAFT_PATH.fullmatch(path)
        feedback_match = FEEDBACK_PATH.fullmatch(path)
        revisions_match = REVISIONS_PATH.fullmatch(path)
        task_summary_match = TASK_SUMMARY_PATH.fullmatch(path)
        read_binding_match = READ_BINDING_PATH.fullmatch(path)
        progress_match = PROGRESS_PATH.fullmatch(path)
        results_match = RESULTS_PATH.fullmatch(path)
        project_tasks_match = PROJECT_TASKS_PATH.fullmatch(path)
        project_conversations_match = PROJECT_CONVERSATIONS_PATH.fullmatch(path)
        conversation_match = CONVERSATION_PATH.fullmatch(path)
        branches_match = QUERY_BRANCHES_PATH.fullmatch(path)
        models_match = MODELS_PATH.fullmatch(path)
        if (match is None and not (draft_match and draft_match.group(2))
                and not (feedback_match and feedback_match.group(2))
                and revisions_match is None and read_binding_match is None
                and task_summary_match is None and progress_match is None and results_match is None
                and project_tasks_match is None
                and project_conversations_match is None and conversation_match is None and branches_match is None and models_match is None):
            self._json(404, {"code": "NOT_FOUND", "message": "route was not found", "retryable": False, "trace_id": self._trace_id()})
            return

        def action() -> tuple[int, dict[str, Any], bool]:
            if models_match:
                self.service.projects.require_access(self._actor(), models_match.group(1), 'default', 'read')
                return HTTPStatus.OK, {'items': self.model_runtime.catalog(),
                                      'defaults': self.model_runtime.settings.defaults() if getattr(self.model_runtime, 'settings', None) else {},
                                      'runtime': self.model_runtime.status()}, False
            if branches_match:
                actor = self._actor()
                task_id = branches_match.group(1)
                task, proposal = self.service.current_read_binding(actor, task_id)
                try:
                    result = build_query_branch_view(task, proposal)
                except ValueError:
                    raise ServiceError("STORAGE_CORRUPT", "query branch binding is invalid", 500) from None
                current = self.service.get_task(actor, task_id)
                if any(current[key] != task[key] for key in
                       ("confirmed_revision", "task_version", "run_generation")):
                    raise ServiceError("REVISION_CONFLICT", "task changed during preview; reload", 409)
                return HTTPStatus.OK, result, False
            if draft_match:
                return HTTPStatus.OK, self.service.get_draft(self._actor(), draft_match.group(1), draft_match.group(2)), False
            if feedback_match:
                return HTTPStatus.OK, self.service.get_feedback(
                    self._actor(), feedback_match.group(1), feedback_match.group(2)), False
            if revisions_match:
                return HTTPStatus.OK, {"items": list(self.service.list_revisions(
                    self._actor(), revisions_match.group(1)))}, False
            if task_summary_match:
                if parsed.query:
                    raise ServiceError("INVALID_REQUEST", "Unexpected query parameters", 422)
                return HTTPStatus.OK, self.service.task_read_summary(
                    self._actor(), task_summary_match.group(1)), False
            if read_binding_match:
                task, proposal = self.service.current_read_binding(
                    self._actor(), read_binding_match.group(1))
                return HTTPStatus.OK, {'task': task, 'proposal': proposal,
                    'revision': task['confirmed_revision'],
                    'proposal_digest': digest({'project_id': task['project_id'],
                                               'proposal': proposal})}, False
            if progress_match:
                actor = self._actor()
                return HTTPStatus.OK, self._task_snapshot(
                    actor, progress_match.group(1))["progress"], False
            if results_match:
                actor = self._actor()
                try:
                    params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                    if set(params) - {"view", "limit", "cursor"} or any(len(values) != 1 for values in params.values()):
                        raise ValueError
                    view_type = params.get("view", ["accepted"])[0]
                    limit = int(params.get("limit", ["20"])[0])
                    cursor = params.get("cursor", [None])[0]
                except (ValueError, TypeError):
                    raise ServiceError("INVALID_RESULT_VIEW", "result view query is invalid", 422) from None
                if self.result_reader is not None:
                    page_reader = getattr(self.result_reader, 'get_result_read_page', None)
                    if callable(page_reader):
                        try:
                            return HTTPStatus.OK, page_reader(actor, results_match.group(1),
                                view_type=view_type, limit=limit, cursor=cursor), False
                        except ServiceError as error:
                            if (error.code not in {'READ_PROJECTION_MISSING', 'NOT_FOUND'}
                                    or getattr(self.result_reader,
                                               'require_read_projection', False)
                                    and error.code == 'READ_PROJECTION_MISSING'):
                                raise
                        except ResultCursorError as error:
                            raise ServiceError("INVALID_CURSOR", str(error), 422) from None
                        except ValueError:
                            raise ServiceError("INVALID_RESULT_VIEW", "invalid result page parameters", 422) from None
                task, proposal = self.service.current_read_binding(
                    actor, results_match.group(1))
                result_set = None
                if self.result_reader is not None:
                    try:
                        reader = getattr(self.result_reader,
                                         'get_current_result_read_snapshot', None)
                        if callable(reader):
                            try:
                                result_set = reader(actor, task["task_id"])
                            except ServiceError as error:
                                if (error.code != 'READ_PROJECTION_MISSING'
                                        or getattr(self.result_reader,
                                                   'require_read_projection', False)):
                                    raise
                                result_set = self.result_reader.get_current_result_set(
                                    actor, task["task_id"])
                        else:
                            result_set = self.result_reader.get_current_result_set(
                                actor, task["task_id"])
                    except ServiceError as error:
                        if error.code != "NOT_FOUND":
                            raise
                try:
                    return HTTPStatus.OK, build_result_page(
                        task, proposal, result_set, view_type=view_type, limit=limit, cursor=cursor), False
                except ResultCursorError as error:
                    raise ServiceError("INVALID_CURSOR", str(error), 422) from None
                except ValueError:
                    raise ServiceError("STORAGE_CORRUPT", "current result view is invalid", 500) from None
            if project_tasks_match:
                if parsed.query:
                    try:
                        params = parse_qs(parsed.query, keep_blank_values=True,
                                          strict_parsing=True)
                        if (set(params) - {'limit', 'cursor'}
                                or any(len(values) != 1 for values in params.values())):
                            raise ValueError
                        limit = int(params.get('limit', ['20'])[0])
                        cursor = params.get('cursor', [None])[0]
                    except (ValueError, TypeError):
                        raise ServiceError('INVALID_CURSOR',
                                           'Task page query is invalid', 422) from None
                    return HTTPStatus.OK, self.service.list_tasks_page(
                        self._actor(), project_tasks_match.group(1),
                        limit=limit, cursor=cursor), False
                return HTTPStatus.OK, {"items": list(self.service.list_tasks(
                    self._actor(), project_tasks_match.group(1)))}, False
            if project_conversations_match:
                return HTTPStatus.OK, {"items": list(self.service.list_conversations(
                    self._actor(), project_conversations_match.group(1)))}, False
            if conversation_match:
                actor = self._actor()
                conversation = self.service.get_conversation(actor, conversation_match.group(1))
                task_messages = []
                clarification_messages = []
                if self.dialogue is not None and conversation['confirmations']:
                    latest = conversation['confirmations'][-1]
                    task_messages = self.dialogue.store.task_history(
                        actor, conversation['project_id'], latest['task_id'], limit=100)
                    clarification_messages = self.dialogue.store.task_origin(
                        actor, conversation['project_id'], conversation['confirmations'][0]['task_id'])
                executions = []
                runs_reader = getattr(self.model_runtime, 'agent_runs', None)
                for task_id in dict.fromkeys(item['task_id']
                                             for item in conversation['confirmations']):
                    self.service.get_task(actor, task_id)
                    executions.append({'task_id': task_id,
                        'runs': runs_reader(actor.tenant_id, task_id) if runs_reader else []})
                return HTTPStatus.OK, {**conversation, 'task_messages': task_messages,
                                       'clarification_messages': clarification_messages,
                                       'executions': executions}, False
            return HTTPStatus.OK, self.service.get_task(self._actor(), match.group(1)), False

        self._run(action)

    def log_message(self, format: str, *args: Any) -> None:
        # Platform deployment uses gateway access logs; never log raw URL/query,
        # credentials, prompts or upstream errors from this handler.
        if self.identity_provider is not None:
            return
        super().log_message(format, *args)


def create_handler(service: TaskService, result_reader: Any = None, model_runtime=None, catalog_config=None,
                   routing_preview=None, plan_set_repository=None, first_search=None, dialogue=None,
                   search_dispatch=None, build_info=None, cleaning_reader=None,
                   cleaning_service=None, cleaning_dispatch=None, delivery_service=None,
                   expansion_jobs=None, refill_service=None, task_events=None,
                   query_images=None, browse_only=False, identity_provider=None,
                   identity_mode="development", platform_browser=None) -> type[Handler]:
    """Bind one service instance per server, without global repository state."""
    class BoundHandler(Handler):
        pass

    if identity_mode not in ('development', 'platform'):
        raise ValueError('unknown identity mode')
    if identity_mode == 'platform':
        from .adapters.platform_access import PlatformProjectAccess
        if identity_provider is None or not isinstance(service.projects, PlatformProjectAccess):
            raise ValueError('platform mode requires verified identity and platform project authorization')
        if identity_provider.tenant_id != service.projects.tenant_id:
            raise ValueError('platform identity and project namespace must match')
    elif identity_provider is not None:
        raise ValueError('identity provider requires explicit platform mode')
    if platform_browser is not None and identity_mode != 'platform':
        raise ValueError('browser sessions require platform mode')
    BoundHandler.platform_browser = platform_browser
    BoundHandler.identity_provider = identity_provider
    BoundHandler.browse_only = browse_only
    BoundHandler.service = service
    BoundHandler.result_reader = result_reader if result_reader is not None else cleaning_reader
    BoundHandler.cleaning_reader = cleaning_reader
    BoundHandler.cleaning_service = cleaning_service
    BoundHandler.cleaning_dispatch = cleaning_dispatch
    BoundHandler.delivery_service = delivery_service
    BoundHandler.model_runtime = model_runtime or ModelRuntime()
    BoundHandler.catalog_config = catalog_config
    BoundHandler.routing_preview = routing_preview
    BoundHandler.plan_set_repository = plan_set_repository
    BoundHandler.first_search = first_search
    BoundHandler.search_dispatch = search_dispatch
    BoundHandler.refill_service = refill_service
    BoundHandler.dialogue = dialogue
    from .task_commands import TaskCommandStore
    BoundHandler.task_commands = TaskCommandStore(getattr(dialogue, 'store', None) or DialogueStore())
    BoundHandler.expansion_jobs = expansion_jobs
    BoundHandler.task_events = task_events or TaskEventFeed(
        getattr(getattr(service, 'repository', None), 'path', None))
    BoundHandler.query_images = query_images
    BoundHandler.build_info = build_info or {}
    return BoundHandler


def source_version(root: Path) -> tuple[str | None, bool | None]:
    """Capture source provenance once at startup; a running process must not follow later edits."""
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True,
                                text=True, timeout=3, check=True).stdout.strip()
        state = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=normal'], cwd=root,
                               capture_output=True, text=True, timeout=3, check=True).stdout
        return commit, not bool(state.strip())
    except (OSError, subprocess.SubprocessError):
        return None, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Local development only; simulated identity headers, no W3")
    parser.add_argument("--browse-only", action="store_true", help="disable business writes and worker recovery for isolated browsing validation")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--demo-project", action="store_true", help="grant tenant-a/user-a access to demo/default")
    parser.add_argument("--ledger", help="local SQLite ledger path; omitted means in-memory storage")
    parser.add_argument('--dialogue-ledger', help='separate SQLite ledger for unconfirmed conversation turns')
    parser.add_argument('--model-config', help='trusted model profile JSON path; keys are environment references')
    parser.add_argument('--model-settings-store', help='private SQLite model connection store (opt-in)')
    parser.add_argument('--model-key-file', help='owner-only encryption master key outside the repository')
    parser.add_argument('--model-allowed-url', action='append', default=[],
                        help='administrator-approved exact HTTPS Base URL; repeat for multiple gateways')
    parser.add_argument('--agent-runtime', choices=('dsh', 'direct'), default='dsh',
                        help='Planning runtime; ordinary dialogue uses direct model calls. Existing default remains dsh for binding compatibility.')
    parser.add_argument('--require-clean-source', action='store_true',
                        help='refuse to serve when source is not an unchanged Git commit')
    parser.add_argument('--dsh-python', help='Python executable with pinned requirements-dsh.lock installed')
    parser.add_argument('--dsh-home', help='private root for isolated DSH turns (or explicit DSH_HOME)')
    parser.add_argument('--catalog-source', help='trusted local existing-index inventory and explicit grants JSON')
    parser.add_argument('--catalog-ledger', help='independent local SQLite configuration file')
    parser.add_argument('--query-capabilities', help='trusted exact text-to-image index capability JSON')
    parser.add_argument('--plan-set-progress', action='store_true',
                        help='enable read-only approved-plan progress for local demo tasks')
    parser.add_argument('--local-first-search-fixture',
                        help='opt-in trusted local exact-vector fixture; requires DSH_LOCAL_CURSOR_SECRET')
    parser.add_argument('--local-first-search-legacy-fixture',
                        help='trusted prior LOCAL_EXACT fixture for already approved plans')
    parser.add_argument('--local-first-search-legacy-capabilities',
                        help='capabilities matching the prior LOCAL_EXACT fixture')
    parser.add_argument('--local-test-media-manifest',
                        help='validated local image manifest used by the real VLM cleaning worker')
    parser.add_argument('--require-task-read-projection', action='store_true',
                        help='fail closed when a published preview lacks its migrated read binding')
    parser.add_argument('--local-clip-model',
                        help='offline CLIP model directory for real embeddings of local test images')
    parser.add_argument('--clip-search-physical-indexes',
                        help='trusted physical registry for combined CLIP search')
    parser.add_argument('--clip-search-endpoints',
                        help='trusted combined CLIP endpoint declarations; URLs come from env')
    parser.add_argument('--clip-search-jobs', help='independent SQLite ledger for combined CLIP jobs')
    parser.add_argument('--clip-search-media-mappings',
                        help='trusted CLIP hit-to-media mapping JSON (optional simulation adapter)')
    args = parser.parse_args()
    if bool(args.model_settings_store) != bool(args.model_key_file):
        parser.error('--model-settings-store and --model-key-file must be supplied together')
    if args.model_settings_store and any(Path(args.model_settings_store).resolve() == Path(p).resolve()
            for p in (args.ledger, args.dialogue_ledger, args.catalog_ledger, args.model_key_file) if p):
        parser.error('model settings store must be independent from all ledgers and master key')
    if bool(args.catalog_source) != bool(args.catalog_ledger):
        parser.error('--catalog-source and --catalog-ledger must be supplied together')
    if args.query_capabilities and not args.catalog_source:
        parser.error('--query-capabilities requires --catalog-source and --catalog-ledger')
    if args.catalog_ledger and args.ledger and Path(args.catalog_ledger).resolve() == Path(args.ledger).resolve():
        parser.error('catalog ledger must be independent from task ledger')
    if args.dialogue_ledger and any(Path(args.dialogue_ledger).resolve() == Path(path).resolve()
                                    for path in (args.ledger, args.catalog_ledger) if path):
        parser.error('dialogue ledger must be independent from task and catalog ledgers')
    if args.plan_set_progress and not (args.ledger and args.catalog_source and args.demo_project):
        parser.error('--plan-set-progress requires --ledger, --catalog-source and --demo-project')
    if args.local_first_search_fixture and not (args.ledger and args.catalog_source
            and args.catalog_ledger and args.query_capabilities and args.demo_project):
        parser.error('--local-first-search-fixture requires --ledger, --catalog-source, '
                     '--catalog-ledger, --query-capabilities and --demo-project')
    if args.local_test_media_manifest and not (args.local_first_search_fixture
            and (args.model_config or args.model_settings_store)):
        parser.error('--local-test-media-manifest requires --local-first-search-fixture '
                     'and configured model profiles or model settings')
    if args.local_clip_model and not (args.local_first_search_fixture and args.local_test_media_manifest):
        parser.error('--local-clip-model requires --local-first-search-fixture '
                     'and --local-test-media-manifest')
    clip_values = (args.clip_search_physical_indexes, args.clip_search_endpoints, args.clip_search_jobs)
    if any(clip_values) and not all(clip_values):
        parser.error('combined CLIP search requires --clip-search-physical-indexes, '
                     '--clip-search-endpoints and --clip-search-jobs together')
    if args.browse_only and all(clip_values):
        parser.error('--browse-only currently supports the local fixture adapter only')
    if args.clip_search_media_mappings and not all(clip_values):
        parser.error('--clip-search-media-mappings requires combined CLIP search configuration')
    if all(clip_values) and (args.local_first_search_fixture or not (args.ledger and args.catalog_source
            and args.catalog_ledger and args.query_capabilities and args.demo_project)):
        parser.error('combined CLIP search is mutually exclusive with the local fixture and requires '
                     '--ledger, --catalog-source, --catalog-ledger, --query-capabilities and --demo-project')
    if args.clip_search_jobs and any(Path(args.clip_search_jobs).resolve() == Path(path).resolve()
            for path in (args.ledger, args.dialogue_ledger, args.catalog_ledger,
                         args.model_settings_store) if path):
        parser.error('combined CLIP job ledger must be independent from other stores')
    grants = [("tenant-a", "user-a", "demo", "default")] if args.demo_project else []
    repository = SQLiteTaskRepository(Path(args.ledger)) if args.ledger else InMemoryTaskRepository()
    runtime = load_model_runtime(args.model_config) if args.model_config else ModelRuntime()
    if (args.model_config or args.model_settings_store) and args.agent_runtime == 'dsh':
        from .adapters.dsh.runtime import DshModelRuntime
        home = args.dsh_home or os.environ.get('DSH_HOME')
        if not args.dsh_python or not home:
            parser.error('DSH requires --dsh-python and --dsh-home (or explicit DSH_HOME)')
        runtime = DshModelRuntime(runtime.profiles.values(), python=args.dsh_python, home=home)
        runtime.check_installation()
        recovered = runtime.recover() if not args.browse_only else {'runs':0,'audits':0}
        if any(recovered.values()):
            print(f"DSH recovery: closed {recovered['runs']} indexed and {recovered['audits']} "
                  "audited run(s) as interrupted", file=sys.stderr)
    if args.model_settings_store:
        from .model_settings import ModelSettings
        runtime.settings = ModelSettings(args.model_settings_store, args.model_key_file,
            [p.config.base_url for p in runtime.profiles.values()] + args.model_allowed_url)
    catalog = build_local_catalog(args.catalog_source, args.catalog_ledger, runtime) if args.catalog_source else None
    from .query_images import QueryImageService
    projects = LocalProjectAccess(grants)
    query_images = QueryImageService(projects, Path(args.ledger) if args.ledger else None)
    service = TaskService(repository, projects, query_images)
    dialogue = DialogueCoordinator(DialogueStore(Path(args.dialogue_ledger) if args.dialogue_ledger else None),
                                   runtime.conversation_runtime(), service.projects)
    expansion_jobs = ExpansionJobs(Path(args.dialogue_ledger) if args.dialogue_ledger else None,
                                   runtime, dialogue, service.projects, service)
    routing = LocalRoutingPreview(service, catalog, args.catalog_source, args.query_capabilities) if args.query_capabilities else None
    plan_sets = build_local_demo_progress_repository(Path(args.ledger), projects=service.projects,
        source_path=args.catalog_source) if args.plan_set_progress else None
    first_search = None
    if args.local_first_search_fixture:
        secret = os.environ.get('DSH_LOCAL_CURSOR_SECRET', '').encode()
        if len(secret) < 32:
            parser.error('DSH_LOCAL_CURSOR_SECRET must contain at least 32 bytes')
        first_search = LocalFirstSearch(service, catalog, ledger_path=Path(args.ledger),
            source_path=args.catalog_source, capabilities_path=args.query_capabilities,
            fixture_path=args.local_first_search_fixture, cursor_secret=secret,
            legacy_fixture_path=args.local_first_search_legacy_fixture,
            legacy_capabilities_path=args.local_first_search_legacy_capabilities,
            clip_model_path=args.local_clip_model, start=not args.browse_only)
        plan_sets = first_search.repository
    elif all(clip_values):
        secret = os.environ.get('DSH_LOCAL_CURSOR_SECRET', '').encode()
        if len(secret) < 32:
            parser.error('DSH_LOCAL_CURSOR_SECRET must contain at least 32 bytes')
        from .adapters.retrieval.clip_search import load_clip_search_endpoints
        from .first_search_clip import CLIPFirstSearch
        from .physical_index_registry import load_physical_indexes
        registry = load_physical_indexes(args.clip_search_physical_indexes)
        endpoints = load_clip_search_endpoints(args.clip_search_endpoints, registry)
        first_search = CLIPFirstSearch(service, catalog, source_path=args.catalog_source,
            capabilities_path=args.query_capabilities, registry=registry, endpoints=endpoints,
            jobs_path=args.clip_search_jobs, cursor_secret=secret,
            media_mappings_path=args.clip_search_media_mappings)
    commit, clean = source_version(Path(__file__).resolve().parents[2])
    if args.require_clean_source and (not commit or clean is not True):
        parser.error('--require-clean-source requires a clean Git checkout')
    build_info = {'commit': commit, 'source_clean': clean,
                  'started_at': datetime.now(timezone.utc).isoformat(),
                  'model_engine': runtime.status().get('engine'),
                  'conversation_engine': dialogue.runtime.status().get('engine'),
                  'retrieval_mode': (getattr(first_search, 'mode', 'local_fixture')
                                     if first_search else 'unconfigured'),
                  'identity_mode': 'demo' if args.demo_project else 'external',
                  'browse_only':args.browse_only}
    search_dispatch = (SearchDispatchCoordinator(repository, first_search)
                       if not args.browse_only and first_search is not None and isinstance(repository, SQLiteTaskRepository)
                       else None)
    refill_runtime = (build_refill_runtime(args.ledger, service, first_search)
                      if not args.browse_only and args.ledger and first_search is not None else None)
    cleaning_reader = None
    cleaning_service = None
    cleaning_dispatch = None
    delivery_service = None
    refill_service = None
    if isinstance(first_search, LocalFirstSearch):
        from .adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
        cleaning_reader = SQLiteMultiRuleApprovalRepository(args.ledger,
            projects=service.projects, authorization=first_search._authority())
        cleaning_reader.require_read_projection = args.require_task_read_projection
        if args.local_test_media_manifest:
            from .cleaning_dispatch import CleaningDispatchCoordinator
            from .confirmed_cleaning import ConfirmedCleaningService
            from .local_test_media import LocalTestMediaCorpus
            from .vlm_registry import ConfirmedTaskVLMRegistry
            gateway = LocalTestMediaCorpus(args.local_test_media_manifest, service)
            first_search.media_gateway = gateway
            cleaning_service = ConfirmedCleaningService(
                cleaning_reader, registry_provider=ConfirmedTaskVLMRegistry(runtime),
                gateway=gateway,
                require_read_projection=args.require_task_read_projection)
            cleaning_dispatch = CleaningDispatchCoordinator(
                cleaning_reader, cleaning_service, start=not args.browse_only)
            from .delivery import ImageDeliveryService
            delivery_service = ImageDeliveryService(cleaning_reader, gateway)
    if refill_runtime is not None and cleaning_reader is not None:
        from .refill_service import ConfirmedRefillService
        refill_service = ConfirmedRefillService(repository, cleaning_reader, refill_runtime)
        refill_runtime.enable_auto(cleaning_reader, refill_service)
        if cleaning_service is not None and cleaning_dispatch is not None:
            refill_runtime.enable_auto_intake(
                repository, first_search, cleaning_service, cleaning_dispatch,
                refill_service)
    handler = create_handler(service, model_runtime=runtime, catalog_config=catalog,
                             routing_preview=routing, plan_set_repository=plan_sets,
                             first_search=first_search, dialogue=dialogue, expansion_jobs=expansion_jobs,
                             search_dispatch=search_dispatch, build_info=build_info,
                             cleaning_reader=cleaning_reader,
                             cleaning_service=cleaning_service,
                             delivery_service=delivery_service,
                             cleaning_dispatch=cleaning_dispatch,
                             refill_service=refill_service,
                             query_images=query_images, browse_only=args.browse_only)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    if not args.browse_only:
        handler.task_commands.recover_interrupted()
        expansion_jobs.recover()
    storage = f"SQLite ledger {Path(args.ledger).absolute()}" if args.ledger else "memory storage"
    print(f"LOCAL ONLY: http://127.0.0.1:{server.server_port}; {storage}, simulated identity", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        handler.task_commands.close()
        if search_dispatch:
            search_dispatch.close()
        if refill_runtime:
            refill_runtime.close()
        if cleaning_dispatch:
            cleaning_dispatch.close()
        if first_search:
            first_search.close()
        dialogue.store.close()
        if runtime.settings:
            runtime.settings.close()


if __name__ == "__main__":
    main()
