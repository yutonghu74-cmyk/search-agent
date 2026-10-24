from __future__ import annotations

import json
import os
import re
import uuid
import argparse
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


TASK_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)$")
DRAFT_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/drafts(?:/([^/]+))?$")
FEEDBACK_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/feedback(?:/([^/]+))?$")
FEEDBACK_SUGGESTION_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/feedback-suggestions$")
CONFIRMATION_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/confirmations$")
REVISIONS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/revisions$")
PROGRESS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/progress$")
RESULTS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/results$")
PROJECT_TASKS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/tasks$")
PROJECT_CONVERSATIONS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/conversations$")
CONVERSATION_PATH = re.compile(r"^/v1/retrieval/conversations/([^/]+)$")
EXPANSIONS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/expansions$")
QUERY_BRANCHES_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/query-branches$")
MODELS_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/models$")
CATALOG_PATH = re.compile(r"^/v1/retrieval/projects/([^/]+)/catalog-config(?:/(preview|confirm))?$")
ROUTING_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/query-routing$")
RETRIEVAL_PROGRESS_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/retrieval-progress$")
FIRST_SEARCH_GET_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/first-search(?:/(candidates))?$")
FIRST_SEARCH_POST_PATH = re.compile(r"^/v1/retrieval/tasks/([^/]+)/first-search/(prepare|confirm|tick)$")


class Handler(BaseHTTPRequestHandler):
    server_version = "dsh-retrieval-p0"
    service: TaskService
    result_reader: Any = None
    model_runtime: Any = None
    catalog_config: Any = None
    routing_preview: Any = None
    plan_set_repository: Any = None
    first_search: Any = None

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
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
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
            status, body, replayed = action()
            body["trace_id"] = trace_id
            self._json(status, body, replayed=replayed)
        except ServiceError as error:
            self._json(error.status, error.as_dict(trace_id=trace_id))

    def do_POST(self) -> None:  # noqa: N802
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
        confirmation_match = CONFIRMATION_PATH.fullmatch(self.path)
        expansion_match = EXPANSIONS_PATH.fullmatch(self.path)
        if (self.path != "/v1/retrieval/tasks" and draft_match is None
                and not (feedback_match and feedback_match.group(2) is None)
                and suggestion_match is None and confirmation_match is None and expansion_match is None):
            self._json(404, {"code": "NOT_FOUND", "message": "route was not found", "retryable": False, "trace_id": self._trace_id()})
            return

        def action() -> tuple[int, dict[str, Any], bool]:
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
            if expansion_match:
                actor = self._actor()
                body = self._body()
                selection = body.pop('model_selection', None)
                if selection is not None and (not isinstance(selection, dict) or set(selection) != {'llm', 'vlm'}):
                    raise ServiceError('INVALID_SPEC', 'Select LLM and VLM profile IDs', 422)
                edited = set(body) == {"parent_label", "groups", "dimension_id"}
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
                        result = self.model_runtime.expand(body['raw_request'], selection['llm'], selection['vlm'])
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
                    self._actor(), feedback_match.group(1), self.headers.get("Idempotency-Key", ""), self._body())
                return HTTPStatus.OK, result, replayed
            if confirmation_match:
                result, replayed = self.service.confirm_feedback(
                    self._actor(), confirmation_match.group(1), self.headers.get("Idempotency-Key", ""), self._body())
                return HTTPStatus.ACCEPTED, result, replayed
            result, replayed = self.service.create_task(self._actor(), self.headers.get("Idempotency-Key", ""), self._body())
            return HTTPStatus.ACCEPTED, result, replayed

        self._run(action)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        path = parsed.path
        first_search_match = FIRST_SEARCH_GET_PATH.fullmatch(path)
        if first_search_match:
            def first_search_action():
                if parsed.query:
                    raise ServiceError('INVALID_RESULT_VIEW', 'Local search view has no query parameters', 422)
                if self.first_search is None:
                    raise ServiceError('LOCAL_SEARCH_NOT_CONFIGURED', 'Local first search is not enabled', 503)
                actor = self._actor()
                task_id, view = first_search_match.groups()
                result = (self.first_search.candidates(actor, task_id) if view == 'candidates'
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
        if path == "/":
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
                and revisions_match is None and progress_match is None and results_match is None and project_tasks_match is None
                and project_conversations_match is None and conversation_match is None and branches_match is None and models_match is None):
            self._json(404, {"code": "NOT_FOUND", "message": "route was not found", "retryable": False, "trace_id": self._trace_id()})
            return

        def action() -> tuple[int, dict[str, Any], bool]:
            if models_match:
                self.service.projects.require_access(self._actor(), models_match.group(1), 'default', 'read')
                return HTTPStatus.OK, {'items': self.model_runtime.catalog()}, False
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
                try:
                    return HTTPStatus.OK, build_task_progress(task, proposal).to_view(), False
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
                return HTTPStatus.OK, self.service.get_conversation(
                    self._actor(), conversation_match.group(1)), False
            return HTTPStatus.OK, self.service.get_task(self._actor(), match.group(1)), False

        self._run(action)

    def log_message(self, format: str, *args: Any) -> None:
        # Production logging will be structured and redacted; avoid request-body logging here.
        super().log_message(format, *args)


def create_handler(service: TaskService, result_reader: Any = None, model_runtime=None, catalog_config=None,
                   routing_preview=None, plan_set_repository=None, first_search=None) -> type[Handler]:
    """Bind one service instance per server, without global repository state."""
    class BoundHandler(Handler):
        pass

    BoundHandler.service = service
    BoundHandler.result_reader = result_reader
    BoundHandler.model_runtime = model_runtime or ModelRuntime()
    BoundHandler.catalog_config = catalog_config
    BoundHandler.routing_preview = routing_preview
    BoundHandler.plan_set_repository = plan_set_repository
    BoundHandler.first_search = first_search
    return BoundHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Local development only; simulated identity headers, no W3")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--demo-project", action="store_true", help="grant tenant-a/user-a access to demo/default")
    parser.add_argument("--ledger", help="local SQLite ledger path; omitted means in-memory storage")
    parser.add_argument('--model-config', help='trusted model profile JSON path; keys are environment references')
    parser.add_argument('--catalog-source', help='trusted local existing-index inventory and explicit grants JSON')
    parser.add_argument('--catalog-ledger', help='independent local SQLite configuration file')
    parser.add_argument('--query-capabilities', help='trusted exact text-to-image index capability JSON')
    parser.add_argument('--plan-set-progress', action='store_true',
                        help='enable read-only approved-plan progress for local demo tasks')
    parser.add_argument('--local-first-search-fixture',
                        help='opt-in trusted local exact-vector fixture; requires DSH_LOCAL_CURSOR_SECRET')
    args = parser.parse_args()
    if bool(args.catalog_source) != bool(args.catalog_ledger):
        parser.error('--catalog-source and --catalog-ledger must be supplied together')
    if args.query_capabilities and not args.catalog_source:
        parser.error('--query-capabilities requires --catalog-source and --catalog-ledger')
    if args.catalog_ledger and args.ledger and Path(args.catalog_ledger).resolve() == Path(args.ledger).resolve():
        parser.error('catalog ledger must be independent from task ledger')
    if args.plan_set_progress and not (args.ledger and args.catalog_source and args.demo_project):
        parser.error('--plan-set-progress requires --ledger, --catalog-source and --demo-project')
    if args.local_first_search_fixture and not (args.ledger and args.catalog_source
            and args.catalog_ledger and args.query_capabilities and args.demo_project):
        parser.error('--local-first-search-fixture requires --ledger, --catalog-source, '
                     '--catalog-ledger, --query-capabilities and --demo-project')
    grants = [("tenant-a", "user-a", "demo", "default")] if args.demo_project else []
    repository = SQLiteTaskRepository(Path(args.ledger)) if args.ledger else InMemoryTaskRepository()
    runtime = load_model_runtime(args.model_config) if args.model_config else ModelRuntime()
    catalog = build_local_catalog(args.catalog_source, args.catalog_ledger, runtime) if args.catalog_source else None
    service = TaskService(repository, LocalProjectAccess(grants))
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
            fixture_path=args.local_first_search_fixture, cursor_secret=secret)
        plan_sets = first_search.repository
    handler = create_handler(service, model_runtime=runtime, catalog_config=catalog,
                             routing_preview=routing, plan_set_repository=plan_sets,
                             first_search=first_search)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    storage = f"SQLite ledger {Path(args.ledger).absolute()}" if args.ledger else "memory storage"
    print(f"LOCAL ONLY: http://127.0.0.1:{server.server_port}; {storage}, simulated identity", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
