"""Opt-in local first-search flow. No Milvus, learned embedding or media bytes."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from .adapters.retrieval.local_vector import LocalVectorRecord, LocalVectorRetrieval
from .adapters.retrieval.simulated import ApprovedFixture
from .adapters.task_storage.plan_sets import SQLitePlanSetApprovalRepository
from .configured_catalog import build_configured_routing
from .errors import ServiceError
from .integration_models import AccessContext, MediaRecord, QueryVector
from .local_catalog import LocalCatalogSource
from .physical_query_preparation import TextQueryCapability
from .query_routing import QueryRouteChoice
from .query_spec import digest
from .retrieval_contracts import RetrievalScope
from .retrieval_coordinator import RetrievalCoordinator
from .routing_preview import load_capabilities
from .text_catalog_config import CatalogConfigScope


class LocalFirstSearch:
    def __init__(self, service, configs, *, ledger_path, source_path,
                 capabilities_path, fixture_path, cursor_secret):
        if not isinstance(cursor_secret, bytes) or len(cursor_secret) < 32:
            raise ValueError('Local cursor secret must contain at least 32 bytes')
        self.service, self.configs = service, configs
        self.source = LocalCatalogSource(source_path)
        self.local_configs = self._local_configs()
        self.capabilities_path = capabilities_path
        self.fixture_path = Path(fixture_path)
        self.cursor_secret = cursor_secret
        self._fixture()  # startup validation; each operation reloads trusted input
        self.repository = SQLitePlanSetApprovalRepository(ledger_path,
            projects=service.projects, authorization=self._authority())

    def _fixture(self):
        try:
            raw = self.fixture_path.read_bytes()
            if len(raw) > 1_000_000:
                raise ValueError('fixture too large')
            value = json.loads(raw)
            if (set(value) != {'schema', 'query_vectors', 'records', 'filter_templates', 'max_depth'}
                    or value['schema'] != 'local-first-search@1'
                    or not isinstance(value['query_vectors'], list) or len(value['query_vectors']) > 512
                    or not isinstance(value['records'], list) or len(value['records']) > 1000
                    or not isinstance(value['filter_templates'], dict)
                    or type(value['max_depth']) is not int or not 1 <= value['max_depth'] <= 1000):
                raise ValueError('invalid fixture shape')
            libraries, _ = self.source.load()
            bindings = {binding.binding_id: binding for library in libraries for binding in library.bindings}
            vectors = {}
            for item in value['query_vectors']:
                if set(item) != {'binding_id', 'text', 'values'} or item['binding_id'] not in bindings:
                    raise ValueError('invalid query vector')
                binding = bindings[item['binding_id']]
                if not isinstance(item['text'], str) or not item['text'].strip() or not isinstance(item['values'], list):
                    raise ValueError('invalid query text/vector')
                key = (binding, item['text'])
                if key in vectors:
                    raise ValueError('duplicate query vector')
                vectors[key] = QueryVector(binding.encoder_ref, tuple(item['values']))
            records = []
            for item in value['records']:
                if (set(item) != {'binding_id', 'vector_id', 'values', 'scope', 'media'}
                        or item['binding_id'] not in bindings or not isinstance(item['values'], list)
                        or not isinstance(item['media'], list) or len(item['media']) > 4):
                    raise ValueError('invalid record')
                media = []
                for entry in item['media']:
                    if set(entry) != set(MediaRecord.__dataclass_fields__):
                        raise ValueError('invalid media mapping')
                    media.append(MediaRecord(**entry))
                records.append(LocalVectorRecord(bindings[item['binding_id']], item['vector_id'],
                                                 tuple(item['values']), item['scope'], tuple(media)))
            templates = {key: frozenset(scopes) for key, scopes in value['filter_templates'].items()
                         if isinstance(scopes, list) and len(scopes) <= 64}
            if len(templates) != len(value['filter_templates']):
                raise ValueError('invalid filter templates')
            capabilities = load_capabilities(self.capabilities_path)
            if any(cap.binding not in {b for lib in libraries for b in lib.bindings}
                   or cap.filter_template_ref not in templates for cap in capabilities):
                raise ValueError('capability/fixture mismatch')
            # Freeze the exact local corpus and mappings into every prepared
            # request. Restart with changed fixture must reject the old plan.
            version = hashlib.sha256(raw).hexdigest()[:24]
            templates = {key + '@' + version: scopes for key, scopes in templates.items()}
            capabilities = tuple(TextQueryCapability(cap.binding, cap.media_type,
                cap.filter_template_ref + '@' + version, cap.max_batch_size) for cap in capabilities)
            adapter = LocalVectorRetrieval(self._authority(), libraries=libraries,
                query_vectors=vectors, approved=(), records=tuple(records),
                filter_templates=templates, cursor_secret=self.cursor_secret)
            return libraries, vectors, tuple(records), templates, value['max_depth'], capabilities, adapter
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise ServiceError('LOCAL_SEARCH_UNAVAILABLE', 'Trusted local search fixture invalid', 503) from None

    def _authority(self):
        source = self.source
        fixture_path = self.fixture_path

        class Authority:
            def require_access(self, context, resource_ref, action):
                if not isinstance(context, AccessContext) or context.purpose not in ('search', 'retrieval'):
                    raise ServiceError('ACCESS_DENIED', 'Local search context required', 403)
                scope = CatalogConfigScope(context.actor.tenant_id, context.actor.user_id, 'demo')
                if resource_ref.startswith('library:'):
                    library_id = resource_ref[len('library:'):]
                    if action not in ('discover', 'search') or not source.authorize(scope, 'search', library_id):
                        raise ServiceError('ACCESS_DENIED', 'Library access denied', 403)
                    return
                if action == 'read' and resource_ref.startswith('media:'):
                    try:
                        data = json.loads(fixture_path.read_bytes())
                        matching = [(entry['binding_id'], media) for entry in data['records']
                                    for media in entry['media'] if media['authorized_media_ref'] == resource_ref]
                        libraries, _ = source.load()
                        binding_to_library = {b.binding_id: b.library_id for lib in libraries for b in lib.bindings}
                        if matching and all(source.authorize(scope, 'search', binding_to_library[b])
                                            for b, _ in matching):
                            return
                    except (OSError, ValueError, KeyError, TypeError):
                        raise ServiceError('AUTH_UNAVAILABLE', 'Local media authority unavailable', 503) from None
                raise ServiceError('ACCESS_DENIED', 'Media access denied', 403)

        return Authority()

    def _local_configs(self):
        """Use saved configuration when present; otherwise expose only local grants.

        The fallback is confined to the explicit LOCAL_EXACT mode and never
        writes a user's configuration or approves a search plan.
        """
        source, real = self.source, self.configs

        class Configs:
            def read(self, scope):
                saved = real.read(scope)
                if saved is not None:
                    return saved
                libraries = tuple(lib for lib in source.inventory(scope)
                                  if source.authorize(scope, 'search', lib.library_id))
                entries = [{'library_id': lib.library_id, 'description': lib.description,
                            'aliases': [lib.library_id]} for lib in libraries]
                source_digest = digest([asdict(lib) for lib in libraries])
                return {'version': 1, 'proposal_digest': source_digest,
                        'catalog_digest': source_digest, 'entries': entries}

            def configured_libraries(self, scope):
                if real.read(scope) is not None:
                    return real.configured_libraries(scope)
                return tuple(lib for lib in source.inventory(scope)
                             if source.authorize(scope, 'search', lib.library_id))

        return Configs()

    def _router(self):
        libraries, vectors, records, templates, depth, capabilities, adapter = self._fixture()
        router = build_configured_routing(tasks=self.service.repository,
            projects=self.service.projects, configs=self.local_configs,
            authorization=self._authority(), embeddings=adapter, capabilities=capabilities)
        return router, (libraries, vectors, records, templates, depth)

    def _source_digest(self):
        libraries, grants = self.source.load()
        return digest({'source': grants, 'bindings': [asdict(lib) for lib in libraries]})

    def _preview(self, actor, task_id):
        task = self.service.repository.get(actor, task_id)
        self.service.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
        if (task.project_id, task.dimension_id) != ('demo', 'default'):
            raise ServiceError('PROJECT_ACCESS_DENIED', 'Local demo project required', 403)
        router, fixture = self._router()
        source_version = self._source_digest()
        preview = router.discover(actor, task_id, expected_scope=RetrievalScope.from_task(task),
                                  authorization_version=source_version)
        if self._source_digest() != source_version:
            raise ServiceError('ROUTING_CHANGED', 'Local source changed during preview', 409)
        return router, fixture, preview

    def preview(self, actor, task_id):
        _, _, preview = self._preview(actor, task_id)
        return {**preview.to_view(), 'execution_enabled': True, 'mode': 'LOCAL_EXACT',
                'note': '仅本地测试向量；未批准、未执行、未接真实 Milvus/模型/图片。'}

    def prepare(self, actor, task_id, *, routing_digest, choices):
        router, _, preview = self._preview(actor, task_id)
        if routing_digest != preview.routing_digest or not isinstance(choices, list):
            raise ServiceError('ROUTING_CHANGED', 'Routing preview changed or choices invalid', 409)
        try:
            selected = tuple(QueryRouteChoice(**choice) for choice in choices)
        except (TypeError, KeyError):
            raise ServiceError('INVALID_ROUTE_SELECTION', 'Invalid route choices', 422) from None
        current = self.repository.get_current(actor, task_id)
        epoch = 1 if current is None else current.epoch + 1
        plan = router.prepare(actor, task_id, preview=preview, choices=selected, epoch=epoch)
        self.repository.put_proposal(actor, plan)
        return {'schema_version': 'local-first-search-proposal@1', 'task_id': task_id,
                'proposal_digest': plan.proposal_digest, 'expected_task_version': plan.scope.task_version,
                'epoch': plan.epoch, 'mode': 'LOCAL_EXACT',
                'members': [{'branch_id': member.branch_id,
                             'library_id': member.plan.request.binding.library_id,
                             'binding_id': member.plan.request.binding.binding_id,
                             'index_version': member.plan.request.binding.index_version,
                             'batch_size': member.plan.request.batch_size} for member in plan.members],
                'note': '待发起人确认；确认后仍需显式点击执行。本地测试向量，不是合格素材数。'}

    def confirm(self, actor, task_id, *, proposal_digest, expected_task_version, action, key):
        approved, replay = self.repository.confirm(actor, task_id, proposal_digest=proposal_digest,
            expected_task_version=expected_task_version, action=action, key=key)
        return {'task_id': task_id, 'set_digest': approved.proposal_digest,
                'epoch': approved.epoch, 'task_version': approved.scope.task_version,
                'mode': 'LOCAL_EXACT', 'status': 'approved',
                'note': '已确认整组计划；尚未执行检索，需显式启动。'}, replay

    def _adapter_for_plan(self, actor, task_id, approved):
        libraries, vectors, records, templates, depth, _, _ = self._fixture()
        if any(member.plan.request.filter_template_ref not in templates for member in approved.members):
            raise ServiceError('INDEX_SNAPSHOT_EXPIRED', 'Local fixture changed since approval', 409)
        context = AccessContext(actor, task_id, 'retrieval', 'current')
        return LocalVectorRetrieval(self._authority(), libraries=libraries, query_vectors=vectors,
            approved=tuple(ApprovedFixture(context, member.plan.request, (), depth)
                           for member in approved.members), records=records,
            filter_templates=templates, cursor_secret=self.cursor_secret)

    def tick(self, actor, task_id, *, max_requests):
        approved = self.repository.get_current(actor, task_id)
        if approved is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'Explicit whole-set approval required', 409)
        gateway = self._adapter_for_plan(actor, task_id, approved)
        result = RetrievalCoordinator(self.repository, gateway).tick(actor, task_id,
            max_requests=max_requests)
        return {**result, 'mode': 'LOCAL_EXACT',
                'note': '仅记录原始检索页；未清洗、未计算有效素材数。'}

    def candidates(self, actor, task_id):
        approved = self.repository.get_current(actor, task_id)
        if approved is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'Explicit whole-set approval required', 409)
        pages = self.repository.list_committed_pages(actor, approved)
        from .query_branches import compile_query_branches
        task = self.service.repository.get(actor, task_id)
        logical = compile_query_branches(json.loads(task.confirmed_proposal_json)['query_spec'])
        branches = {branch['branch_id']: branch for branch in logical['branches']}
        members = {member.plan.request.plan_id: member for member in approved.members}
        sourced_hits = tuple((plan_id, hit) for plan_id, page in pages for hit in page.hits)
        if any(plan_id not in members or members[plan_id].branch_id not in branches
               for plan_id, _ in sourced_hits):
            raise ServiceError('STORAGE_CORRUPT', 'Candidate source branch missing', 500)
        hits = tuple(hit for _, hit in sourced_hits)
        adapter = self._adapter_for_plan(actor, task_id, approved)
        context = AccessContext(actor, task_id, 'retrieval', 'current')
        resolutions = adapter.resolve_hits(context, hits[:200])
        if len(resolutions) != min(len(hits), 200):
            raise ServiceError('STORAGE_CORRUPT', 'Candidate resolution count changed', 500)
        if self.repository.get_current(actor, task_id) != approved:
            raise ServiceError('PLAN_NOT_CURRENT', 'Approved set changed during candidate read', 409)
        return {'schema_version': 'local-first-search-candidates@1', 'task_id': task_id,
                'mode': 'LOCAL_EXACT', 'raw_hit_count': len(hits), 'returned': len(resolutions),
                'truncated': len(hits) > 200, 'accepted_count': None,
                'items': [{'plan_id': plan_id,
                           'branch_id': members[plan_id].branch_id,
                           'query_text': branches[members[plan_id].branch_id]['query_text'],
                           'query_role': branches[members[plan_id].branch_id]['query_role'],
                           'source_term': branches[members[plan_id].branch_id]['source_term'],
                           'library_id': result.hit.binding.library_id,
                           'binding_id': result.hit.binding.binding_id,
                           'index_version': result.hit.binding.index_version,
                           'vector_id': result.hit.vector_id, 'score': result.hit.score,
                           'metric': result.hit.binding.metric, 'status': result.status,
                           'media_unit_id': result.media.media_unit_id if result.media else None,
                           'asset_version': result.media.asset_version if result.media else None,
                           'preview_status': 'unavailable'} for (plan_id, _), result in zip(sourced_hits, resolutions)],
                'note': '仅授权后的原始候选；没有缩略图或清洗判定，不能作为有效素材数量。'}
