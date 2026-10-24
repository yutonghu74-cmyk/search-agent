"""Local-only, read-only route discovery with explicit capability registrations."""
from dataclasses import asdict
import json
from pathlib import Path

from .configured_catalog import build_configured_routing
from .errors import ServiceError
from .integration_models import IndexBinding
from .local_catalog import LocalCatalogSource
from .physical_query_preparation import TextQueryCapability
from .query_spec import digest
from .retrieval_contracts import RetrievalScope, validate_scope
from .text_catalog_config import CatalogConfigScope


def load_capabilities(path):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError()
        value = json.loads(raw)
        if (set(value) != {'schema', 'items'} or value['schema'] != 'text-query-capabilities@1'
                or not isinstance(value['items'], list) or len(value['items']) > 256):
            raise ValueError()
        result, seen = [], set()
        for item in value['items']:
            if set(item) != {'binding', 'media_type', 'filter_template_ref', 'max_batch_size'}:
                raise ValueError()
            binding = IndexBinding(**item['binding'])
            if (not all(isinstance(v, str) and 0 < len(v.strip()) <= 128 for v in
                        (binding.binding_id, binding.library_id, binding.index_version, binding.encoder_ref, binding.metric))
                    or type(binding.vector_dimension) is not int or not 1 <= binding.vector_dimension <= 65536
                    or binding.binding_id in seen or item['media_type'] != 'image'
                    or not isinstance(item['filter_template_ref'], str)
                    or not 1 <= len(item['filter_template_ref'].strip()) <= 128
                    or type(item['max_batch_size']) is not int or not 1 <= item['max_batch_size'] <= 100):
                raise ValueError()
            seen.add(binding.binding_id)
            result.append(TextQueryCapability(binding, item['media_type'],
                item['filter_template_ref'], item['max_batch_size']))
        return tuple(sorted(result, key=lambda entry: entry.binding.binding_id))
    except (OSError, ValueError, TypeError, KeyError):
        raise ServiceError('QUERY_CAPABILITIES_UNAVAILABLE', 'Trusted query capabilities invalid or unavailable', 503) from None


class _NoEncoding:
    def encode_query(self, *args):
        raise ServiceError('QUERY_EXECUTION_DISABLED', 'Preview cannot encode or execute', 409)


class LocalRoutingPreview:
    def __init__(self, service, configs, source_path, capabilities_path):
        self.service, self.configs = service, configs
        self.source = LocalCatalogSource(source_path)
        self.capabilities_path = capabilities_path
        load_capabilities(capabilities_path)

    def _source_digest(self):
        libraries, grants = self.source.load()
        return digest({'libraries': [asdict(lib) for lib in libraries], 'grants': grants})

    def preview(self, actor, task_id, branch_ids=None):
        task = self.service.repository.get(actor, task_id)
        self.service.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
        scope = RetrievalScope.from_task(task)
        validate_scope(actor, task, scope)
        capabilities = load_capabilities(self.capabilities_path)
        source_digest = self._source_digest()
        config_scope = CatalogConfigScope(actor.tenant_id, actor.user_id, task.project_id)
        source = self.source
        snapshot = self._source_digest

        class Authorization:
            def require_access(self, context, ref, action):
                if snapshot() != source_digest:
                    raise ServiceError('AUTHORIZATION_CHANGED', 'Local catalog authorization changed', 409)
                if (context.actor != actor or context.task_id != task_id
                        or context.authorization_version != source_digest
                        or not ref.startswith('library:')
                        or not source.authorize(config_scope, action, ref[len('library:'):])):
                    raise ServiceError('ACCESS_DENIED', 'Library access denied', 403)

        router = build_configured_routing(tasks=self.service.repository, projects=self.service.projects,
            configs=self.configs, authorization=Authorization(), embeddings=_NoEncoding(), capabilities=capabilities)
        preview = router.discover(actor, task_id, expected_scope=scope,
            authorization_version=source_digest, branch_ids=branch_ids)
        if self._source_digest() != source_digest or load_capabilities(self.capabilities_path) != capabilities:
            raise ServiceError('ROUTING_CHANGED', 'Catalog or capabilities changed during preview', 409)
        validate_scope(actor, self.service.repository.get(actor, task_id), scope)
        self.service.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
        return {**preview.to_view(), 'execution_enabled': False,
                'note': '仅为配置与能力匹配预览，未调用编码器、未批准、未执行向量检索；不代表连接健康已验证。'}
