from __future__ import annotations

import json
import os
import re
import sys
import uuid
import argparse
import subprocess
from datetime import datetime, timezone
from contextlib import nullcontext
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .errors import ServiceError
from .models import Actor
from .service import InMemoryTaskRepository, TaskService
from .adapters.projects import LocalProjectAccess
from .adapters.task_storage.sqlite import SQLiteTaskRepository
from .chat_ui import CHAT_CSS, CHAT_HTML, CHAT_JS
from .production_board_ui import BOARD_CSS, BOARD_HTML, BOARD_JS
from .progress_models import build_task_progress
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
FOLLOWUP_INTENT_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/followup-intent$")
CONFIRMATION_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/confirmations$")
REVISIONS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/revisions$")
PROGRESS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/progress$")
RESULTS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/results$")
PROJECT_TASKS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/tasks$")
PROJECT_CONVERSATIONS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/conversations$")
CONVERSATION_PATH = re.compile(r"^/v1/retrieval/conversations/([^/]+)$")
EXPANSIONS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/expansions$")
EXPANSION_JOBS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/dialogue-sessions/(dlg_[A-Za-z0-9]+)/expansion-jobs(?:/(expjob_[a-f0-9]{32})(?:/(retry))?)?$")
TASK_EXPANSION_JOBS_PATH = re.compile(r"^/v1/retrieval/tasks/(rt_[a-f0-9]{32})/expansion-jobs(?:/(expjob_[a-f0-9]{32})(?:/(retry))?)?$")
INTENT_PREVIEW_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/intent-preview$")
DIALOGUE_TURN_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/dialogue-turn$")
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
FIRST_SEARCH_GET_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/(?:first-search(?:/(candidates(?:/changes)?|progress))?|(?P<candidate_view>candidates(?:/changes)?))$")
FIRST_SEARCH_PREVIEW_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/(?:first-search/)?candidates/(candidate_[a-f0-9]{64})/preview$")
FIRST_SEARCH_POST_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/first-search/(prepare|confirm|tick)$")
FIRST_SEARCH_MANIFEST_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/first-search/manifest$")
CLEANING_POST_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/cleaning/(prepare|confirm|execute)$")
CLEANING_PREVIEW_PATH = re.compile(
    r"^/v1/retrieval/tasks/([^/]+)/cleaning/candidates/(candidate_[a-f0-9]{64})/preview$")
REFILL_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/refill$")


class Handler(BaseHTTPRequestHandler):
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
        tenant_id = self.headers.get("X-Tenant-Id", "").strip()
        user_id = self.headers.get("X-User-Id", "").strip()
        if not tenant_id or not user_id:
            raise ServiceError("UNAUTHENTICATED", "trusted gateway identity is required", 401)
        return Actor(tenant_id, user_id)

    def _json(self, status: int, body: dict[str, Any], *, replayed: bool = False) -> None:
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        if replayed:
            self.send_header("Idempotent-Replayed", "true")
        self.end_headers()
        self.wfile.write(encoded)

    def _asset(self, content_type: str, value: str) -> None:
        encoded = value.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
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

    def _run(self, action: Any) -> None:
        trace_id = self._trace_id()
        try:
            scope = getattr(self.model_runtime, 'request_scope', None)
            settings = getattr(self.model_runtime, 'settings', None)
            with (settings.scope(self.headers.get('X-Tenant-Id', ''), self.headers.get('X-User-Id', ''))
                  if settings else nullcontext()), (scope(self.headers.get('X-Tenant-Id', ''), self.headers.get('X-User-Id', ''),
                        trace_id, urlsplit(self.path).path) if scope else nullcontext()):
                status, body, replayed = action()
            body["trace_id"] = trace_id
            self._json(status, body, replayed=replayed)
        except ServiceError as error:
            self._json(error.status, error.as_dict(trace_id=trace_id))

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

    def do_POST(self) -> None:  # noqa: N802
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
                return HTTPStatus.OK, self.dialogue.turn(self._actor(), dialogue_match.group(1), self._body()), False
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
                if operation == 'prepare' and set(body) == {'manifest_ref'}:
                    return HTTPStatus.OK, self.cleaning_service.prepare(
                        actor, task_id, body['manifest_ref']), False
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
                required = {'text', 'llm_id', 'expected_revision', 'expected_task_version'}
                if (not required <= set(body) or set(body) - required - {'draft_preview', 'recent_messages'}
                        or not isinstance(body['text'], str) or not isinstance(body['llm_id'], str)
                        or type(body['expected_revision']) is not int
                        or type(body['expected_task_version']) is not int):
                    raise ServiceError('INVALID_SPEC', 'Bound follow-up classification request required', 422)
                task_id = followup_match.group(1)
                task = self.service.get_task(actor, task_id)
                if (task['confirmed_revision'] != body['expected_revision']
                        or task['task_version'] != body['expected_task_version']):
                    raise ServiceError('REVISION_CONFLICT', 'Task changed; reload before classifying', 409)
                revisions = self.service.list_revisions(actor, task_id)
                revision = next((item for item in revisions
                                 if item['revision'] == task['confirmed_revision']), None)
                spec = revision['proposal']['query_spec'] if revision else None
                if not spec:
                    raise ServiceError('STORAGE_CORRUPT', 'Confirmed request unavailable', 500)
                groups = (spec.get('grouped_expansion') or {}).get('groups', [])
                current = [{'primary_label': group['primary_label'],
                            'auxiliary_terms': group['auxiliary_terms']} for group in groups]
                target_count = spec['target_count']
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
                if self.dialogue is not None:
                    messages = self.dialogue.store.task_history(actor, task['project_id'], task_id)
                retrieval_context = {'status': 'unknown'}
                if self.first_search is not None:
                    try:
                        progress = self.first_search.progress(actor, task_id)
                        if progress['status'] == 'unapproved':
                            routes = self.first_search.preview(actor, task_id)['routes']
                            retrieval_context = {'status': 'unapproved', 'unavailable_queries': [
                                {'query': row['query_text'], 'reason': row['availability']}
                                for row in routes if not row['options']][:10]}
                        elif (progress.get('revision') == task['confirmed_revision']
                              and progress.get('generation') == task['run_generation']):
                            members = progress['members']
                            retrieval_context = {'status': progress['status'],
                                'pagination_stopped': progress['pagination_stopped'],
                                'committed_pages': sum(member['committed_pages'] for member in members),
                                'stop_reasons': sorted({member['stop_reason'] for member in members if member['stop_reason']}),
                                'mode': 'local_test', 'cleaning_completed': False}
                    except ServiceError as error:
                        if error.status in (401, 403, 404):
                            raise
                        retrieval_context = {'status': 'unavailable', 'reason': error.code}
                scope = getattr(self.model_runtime, 'business_scope', None)
                with (scope(project_id=task['project_id'], task_id=task_id,
                            task_version=task['task_version'], revision=task['confirmed_revision'],
                            generation=task['run_generation']) if scope else nullcontext()):
                    result = self.model_runtime.classify_followup(
                        spec['raw_request'], current, body['text'], body['llm_id'],
                        task_context={'task_id': task_id, 'stage': task['stage'], 'run_status': task['run_status'],
                                      'target_count': target_count, 'has_unconfirmed_preview': preview is not None,
                                      'retrieval_progress': retrieval_context},
                        recent_messages=messages)
                after = self.service.get_task(actor, task_id)
                if (after['confirmed_revision'] != task['confirmed_revision']
                        or after['task_version'] != task['task_version']):
                    raise ServiceError('REVISION_CONFLICT', 'Task changed during classification', 409)
                if self.dialogue is not None:
                    self.dialogue.store.append_task_turn(actor, task['project_id'], task_id, task['task_version'],
                                                         body['text'], result.get('reply') or '本轮理解：' + result['kind'])
                return HTTPStatus.OK, {'schema_version': 'followup-intent@1',
                                       'task_id': task_id, 'base_revision': task['confirmed_revision'],
                                       'base_task_version': task['task_version'], **result}, False
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

    def do_DELETE(self) -> None:  # noqa: N802
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

    def do_GET(self) -> None:  # noqa: N802
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
        cleaning_match = re.fullmatch(r'/v1/retrieval/tasks/([^/]+)/cleaning/steps', path)
        if cleaning_match:
            def cleaning_steps_action():
                if parsed.query:
                    raise ServiceError('INVALID_RESULT_VIEW', 'Unexpected query parameters', 422)
                actor = self._actor()
                task_id = cleaning_match.group(1)
                reader = self.cleaning_service or self.cleaning_reader
                if reader is None:
                    self.service.get_task(actor, task_id)
                    return HTTPStatus.OK, {'available': False, 'runs': [],
                        'note': '尚未产生可查看的清洗步骤结果。'}, False
                result = (reader.steps(actor, task_id) if self.cleaning_service is not None
                          else reader.step_results(actor, task_id))
                return HTTPStatus.OK, result, False
            self._run(cleaning_steps_action)
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
        progress_match = PROGRESS_PATH.fullmatch(path)
        results_match = RESULTS_PATH.fullmatch(path)
        project_tasks_match = PROJECT_TASKS_PATH.fullmatch(path)
        project_conversations_match = PROJECT_CONVERSATIONS_PATH.fullmatch(path)
        conversation_match = CONVERSATION_PATH.fullmatch(path)
        branches_match = QUERY_BRANCHES_PATH.fullmatch(path)
        models_match = MODELS_PATH.fullmatch(path)
        if (match is None and not (draft_match and draft_match.group(2))
                and not (feedback_match and feedback_match.group(2))
                and revisions_match is None and progress_match is None and results_match is None
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
                task = self.service.get_task(actor, task_id)
                revisions = self.service.list_revisions(actor, task_id)
                proposal = next((item["proposal"] for item in revisions
                                 if item["revision"] == task["confirmed_revision"]), None)
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
            if progress_match:
                actor = self._actor()
                task = self.service.get_task(actor, progress_match.group(1))
                revisions = self.service.list_revisions(actor, progress_match.group(1))
                proposal = next((item["proposal"] for item in revisions
                                 if item["revision"] == task["confirmed_revision"]), None)
                result_set = None
                if self.result_reader is not None:
                    try:
                        result_set = self.result_reader.get_current_result_set(actor, task["task_id"])
                    except ServiceError as error:
                        if error.code != "NOT_FOUND":
                            raise
                raw_returned = None
                if self.first_search is not None:
                    try:
                        raw_returned = self.first_search.candidate_count(actor, task["task_id"])
                    except ServiceError as error:
                        if error.code not in ("PLAN_NOT_APPROVED", "PLAN_NOT_CURRENT"):
                            raise
                try:
                    return HTTPStatus.OK, build_task_progress(
                        task, proposal, result_set, raw_returned=raw_returned).to_view(), False
                except ValueError:
                    raise ServiceError("STORAGE_CORRUPT", "task progress binding is invalid", 500) from None
            if results_match:
                actor = self._actor()
                task = self.service.get_task(actor, results_match.group(1))
                revisions = self.service.list_revisions(actor, results_match.group(1))
                proposal = next((item["proposal"] for item in revisions
                                 if item["revision"] == task["confirmed_revision"]), None)
                try:
                    params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                    if set(params) - {"view", "limit", "cursor"} or any(len(values) != 1 for values in params.values()):
                        raise ValueError
                    view_type = params.get("view", ["accepted"])[0]
                    limit = int(params.get("limit", ["20"])[0])
                    cursor = params.get("cursor", [None])[0]
                except (ValueError, TypeError):
                    raise ServiceError("INVALID_RESULT_VIEW", "result view query is invalid", 422) from None
                result_set = None
                if self.result_reader is not None:
                    try:
                        result_set = self.result_reader.get_current_result_set(actor, task["task_id"])
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
                return HTTPStatus.OK, {**conversation, 'task_messages': task_messages,
                                       'clarification_messages': clarification_messages}, False
            return HTTPStatus.OK, self.service.get_task(self._actor(), match.group(1)), False

        self._run(action)

    def log_message(self, format: str, *args: Any) -> None:
        # Production logging will be structured and redacted; avoid request-body logging here.
        super().log_message(format, *args)


def create_handler(service: TaskService, result_reader: Any = None, model_runtime=None, catalog_config=None,
                   routing_preview=None, plan_set_repository=None, first_search=None, dialogue=None,
                   search_dispatch=None, build_info=None, cleaning_reader=None,
                   cleaning_service=None, cleaning_dispatch=None, delivery_service=None,
                   expansion_jobs=None, refill_service=None) -> type[Handler]:
    """Bind one service instance per server, without global repository state."""
    class BoundHandler(Handler):
        pass

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
    BoundHandler.expansion_jobs = expansion_jobs
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
                        help='DSH is the default; direct is an explicit legacy diagnostic mode')
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
        recovered = runtime.recover()
        if any(recovered.values()):
            print(f"DSH recovery: closed {recovered['runs']} indexed and {recovered['audits']} "
                  "audited run(s) as interrupted", file=sys.stderr)
    if args.model_settings_store:
        from .model_settings import ModelSettings
        runtime.settings = ModelSettings(args.model_settings_store, args.model_key_file,
            [p.config.base_url for p in runtime.profiles.values()] + args.model_allowed_url)
    catalog = build_local_catalog(args.catalog_source, args.catalog_ledger, runtime) if args.catalog_source else None
    service = TaskService(repository, LocalProjectAccess(grants))
    dialogue = DialogueCoordinator(DialogueStore(Path(args.dialogue_ledger) if args.dialogue_ledger else None),
                                   runtime, service.projects)
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
            clip_model_path=args.local_clip_model)
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
                  'retrieval_mode': (getattr(first_search, 'mode', 'local_fixture')
                                     if first_search else 'unconfigured'),
                  'identity_mode': 'demo' if args.demo_project else 'external'}
    search_dispatch = (SearchDispatchCoordinator(repository, first_search)
                       if first_search is not None and isinstance(repository, SQLiteTaskRepository)
                       else None)
    refill_runtime = (build_refill_runtime(args.ledger, service, first_search)
                      if args.ledger and first_search is not None else None)
    cleaning_reader = None
    cleaning_service = None
    cleaning_dispatch = None
    delivery_service = None
    refill_service = None
    if isinstance(first_search, LocalFirstSearch):
        from .adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
        cleaning_reader = SQLiteMultiRuleApprovalRepository(args.ledger,
            projects=service.projects, authorization=first_search._authority())
        if args.local_test_media_manifest:
            from .cleaning_dispatch import CleaningDispatchCoordinator
            from .confirmed_cleaning import ConfirmedCleaningService
            from .local_test_media import LocalTestMediaCorpus
            from .vlm_registry import ConfirmedTaskVLMRegistry
            gateway = LocalTestMediaCorpus(args.local_test_media_manifest, service)
            first_search.media_gateway = gateway
            cleaning_service = ConfirmedCleaningService(
                cleaning_reader, registry_provider=ConfirmedTaskVLMRegistry(runtime),
                gateway=gateway)
            cleaning_dispatch = CleaningDispatchCoordinator(
                cleaning_reader, cleaning_service)
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
                             refill_service=refill_service)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    expansion_jobs.recover()
    storage = f"SQLite ledger {Path(args.ledger).absolute()}" if args.ledger else "memory storage"
    print(f"LOCAL ONLY: http://127.0.0.1:{server.server_port}; {storage}, simulated identity", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
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
