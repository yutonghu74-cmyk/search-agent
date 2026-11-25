"""Platform search using the existing approval, candidate and refill ledgers."""
from dataclasses import asdict
from contextlib import contextmanager
import hashlib
import json
import threading

from .adapters.task_storage.plan_sets import SQLitePlanSetApprovalRepository
from .candidate_browsing import CandidateBrowser
from .candidate_contracts import candidate_identity
from .configured_catalog import build_configured_routing
from .errors import ServiceError
from .first_search_local import LocalFirstSearch
from .integration_models import AccessContext
from .models import Actor
from .query_spec import digest
from .retrieval_contracts import RetrievalScope
from .text_catalog_config import CatalogConfigScope


class PlatformCatalogSource:
    def __init__(self, resources):
        self.resources = resources

    def inventory(self, scope):
        actor = Actor(scope.tenant_id, scope.owner_id)
        r = self.resources
        allowed = r.allowed_versions(actor, scope.project_id)
        return tuple(lib for lib in r.libraries(actor, scope.project_id)
                     if lib.library_id in {ref.library_id for ref in allowed})

    def authorize(self, scope, action, library_id):
        libraries = self.inventory(scope)
        return action == 'configure' or (action in ('search', 'discover') and
                                        library_id in {lib.library_id for lib in libraries})


class PlatformQueryEncoding:
    def __init__(self, resources, encode_query, capabilities, encode_reference=None):
        self.resources, self.encoder, self.capabilities = resources, encode_query, capabilities
        self.reference_encoder = encode_reference

    def can_encode_query(self, context, text, binding):
        self.resources.require_access(context, 'library:' + binding.library_id, 'search')
        return bool(text) and any(cap.binding == binding for cap in self.capabilities())

    def can_encode_reference(self, context, query_input, binding):
        self.resources.require_access(context, 'library:' + binding.library_id, 'search')
        return self.reference_encoder is not None and any(cap.binding == binding for cap in self.capabilities())

    def encode_reference(self, context, query_input, binding):
        if not self.can_encode_reference(context, query_input, binding):
            raise ServiceError('QUERY_UNSUPPORTED', 'verified reference-image encoder unavailable', 422)
        result = self.reference_encoder(context, query_input, binding)
        self.resources.require_access(context, 'library:' + binding.library_id, 'search')
        return result

    def encode_query(self, context, text, binding):
        if not self.can_encode_query(context, text, binding):
            raise ServiceError('QUERY_UNSUPPORTED', 'query encoder binding is unavailable', 422)
        result = self.encoder(context, text, binding)
        self.resources.require_access(context, 'library:' + binding.library_id, 'search')
        return result


class PlatformFirstSearch(LocalFirstSearch):
    """Reuse workflow semantics; replace every local data/identity dependency."""
    mode = 'PLATFORM'

    def __init__(self, service, configs, *, resources, ledger_path, cursor_secret,
                 capabilities, encode_query, encode_reference=None, start=True, search_limits=None):
        self.service, self.configs, self.resources = service, configs, resources
        self.source = PlatformCatalogSource(resources)
        self.capabilities = capabilities
        self.encoder = PlatformQueryEncoding(resources, encode_query, capabilities, encode_reference)
        self.clip_encoder = None
        self.media_gateway = resources
        self.cursor_secret = cursor_secret
        self.browser = CandidateBrowser(ledger_path, cursor_secret)
        self.repository = SQLitePlanSetApprovalRepository(ledger_path,
            projects=service.projects, authorization=resources)
        self._setup_search_execution(ledger_path, search_limits, start=start)

    def _authority(self):
        return self.resources

    def _preview(self, actor, task_id):
        task = self.service.repository.get(actor, task_id)
        self.service.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
        scope = CatalogConfigScope(actor.tenant_id, actor.user_id, task.project_id)
        libraries = self.source.inventory(scope)
        capabilities = tuple(self.capabilities())
        version = digest({'libraries': [asdict(lib) for lib in libraries], 'capabilities': [asdict(cap) for cap in capabilities]})
        query = json.loads(task.confirmed_proposal_json)['query_spec']
        if task.media_type == 'video':
            registered = self.resources.whole_video_bindings
            verified = set(registered()) if callable(registered) else set()
            capabilities = tuple(cap for cap in capabilities if cap.binding in verified)
            if query['refill_policy']['mode'] == 'maintain_target':
                permitted = set(query['refill_policy']['approved_library_ids'])
                capabilities = tuple(cap for cap in capabilities if cap.binding.library_id in permitted)
        configs = self._local_configs({lib.library_id: set() for lib in libraries})
        router = build_configured_routing(tasks=self.service.repository,
            projects=self.service.projects, configs=configs, authorization=self.resources,
            embeddings=self.encoder, capabilities=capabilities)
        preview = router.discover(actor, task_id, expected_scope=RetrievalScope.from_task(task),
                                  authorization_version=version)
        if digest({'libraries': [asdict(lib) for lib in self.source.inventory(scope)], 'capabilities': [asdict(cap) for cap in self.capabilities()]}) != version:
            raise ServiceError('AUTHORIZATION_CHANGED', 'platform directory changed during planning', 409)
        return router, (None, None, None, None, None, self.encoder), preview

    def preview(self, actor, task_id):
        view = super().preview(actor, task_id)
        view['note'] = '平台授权范围内的检索计划；候选仍须清洗及交付权限校验。'
        return view

    def prepare(self, actor, task_id, **kwargs):
        view = super().prepare(actor, task_id, **kwargs)
        view['note'] = '待发起人确认；检索输出仅为原始候选。'
        return view

    def auto_start(self, actor, task_id, *, key):
        view = super().auto_start(actor, task_id, key=key)
        if self.service.repository.get(actor, task_id).media_type == 'video':
            view['target_media_type'] = 'video'
        return view

    def _adapter_for_plan(self, actor, task_id, approved):
        self.service.get_task(actor, task_id)
        return self.resources

    def candidates(self, actor, task_id, **kwargs):
        result = super().candidates(actor, task_id, **kwargs)
        for item in result['items']:
            item['preview_status'] = 'available' if item['status'] == 'resolved' and item['content_digest'] else 'unavailable'
        return result

    def candidate_preview(self, actor, task_id, candidate_id, *, ref=None):
        approved = self.repository.get_browse_current(actor, task_id)
        if approved is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'approved search required', 409)
        from .adapters.task_storage.sqlite import decode
        hit = decode(json.loads(self.browser.locate(actor, task_id, approved.proposal_digest, candidate_id, ref)))
        context = AccessContext(actor, task_id, 'candidate_preview', 'current')
        item = self.resources.resolve_hits(context, (hit,))[0]
        if item.status != 'resolved' or item.media is None or candidate_identity(item.media) != candidate_id:
            raise ServiceError('PREVIEW_UNAVAILABLE', 'candidate media unavailable', 404)
        with self.resources.open_read(context, item.media.authorized_media_ref, 10 * 1024 * 1024) as stream:
            content = stream.read()
        if 'sha256:' + hashlib.sha256(content).hexdigest() != item.media.content_digest:
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'candidate content changed', 409)
        if self.repository.get_browse_current(actor, task_id) != approved:
            raise ServiceError('PLAN_NOT_CURRENT', 'search changed during preview', 409)
        content_type = ('image/jpeg' if content.startswith(b'\xff\xd8\xff') else
                        'image/png' if content.startswith(b'\x89PNG\r\n\x1a\n') else None)
        if content_type is None:
            raise ServiceError('PREVIEW_UNAVAILABLE', 'unsupported image type', 409)
        return content_type, content


    @contextmanager
    def open_candidate_video(self, actor, task_id, candidate_id, *, ref=None):
        from .adapters.task_storage.sqlite import decode
        from .video_media import open_verified_video, inspect_video_file
        task = self.service.repository.get(actor, task_id)
        if task.media_type != 'video':
            raise ServiceError('FEATURE_NOT_SUPPORTED', 'Video preview requires a video task', 422)
        approved = self.repository.get_browse_current(actor, task_id)
        if approved is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'Approved search required', 409)
        hit = decode(json.loads(self.browser.locate(actor, task_id, approved.proposal_digest, candidate_id, ref)))
        context = AccessContext(actor, task_id, 'candidate_preview', 'current')
        item = self.resources.resolve_hits(context, (hit,))[0]
        if item.status != 'resolved' or item.media is None or candidate_identity(item.media) != candidate_id:
            raise ServiceError('PREVIEW_UNAVAILABLE', 'Video candidate unavailable', 404)
        def guard():
            if self.repository.get_browse_current(actor, task_id) != approved:
                raise ServiceError('PLAN_NOT_CURRENT', 'Video search plan changed', 409)
            self.resources.require_access(context, item.media.authorized_media_ref, 'read')
        with open_verified_video(self.resources, context, item.media.authorized_media_ref,
                item.media.content_digest, policy=self.resources.video_policy, guard=guard) as file:
            metadata = inspect_video_file(file, inspector=self.resources.video_inspector,
                file_inspector=self.resources.video_file_inspector, policy=self.resources.video_policy)
            guard()
            yield file, metadata, guard
