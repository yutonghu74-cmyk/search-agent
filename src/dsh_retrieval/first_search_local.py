"""Opt-in local first-search flow. Explicit word rules are not learned embeddings."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import threading
import unicodedata

from .adapters.retrieval.local_vector import LocalVectorRecord, LocalVectorRetrieval
from .adapters.retrieval.simulated import ApprovedFixture
from .adapters.task_storage.plan_sets import SQLitePlanSetApprovalRepository
from .configured_catalog import build_configured_routing
from .candidate_contracts import (CandidateManifestProposal, ManifestItem,
                                  candidate_identity)
from .errors import ServiceError
from .conversation_timing import timed_call
from .pipeline_timing import measured, bind as bind_timing
from .integration_models import AccessContext, MediaRecord, QueryVector
from .local_catalog import LocalCatalogSource
from .multi_branch_contracts import plan_origin_groups
from .physical_query_preparation import TextQueryCapability
from .query_routing import QueryRouteChoice
from .query_spec import digest

MANIFEST_HIT_LIMIT = 100
from .retrieval_contracts import RetrievalScope
from .retrieval_coordinator import RetrievalCoordinator
from .retrieval_progress import read_retrieval_progress
from .routing_preview import load_capabilities
from .text_catalog_config import CatalogConfigScope


class LocalFirstSearch:
    def __init__(self, service, configs, *, ledger_path, source_path,
                 capabilities_path, fixture_path, cursor_secret,
                 legacy_fixture_path=None, legacy_capabilities_path=None,
                 clip_model_path=None, start=True, search_limits=None):
        if not isinstance(cursor_secret, bytes) or len(cursor_secret) < 32:
            raise ValueError('Local cursor secret must contain at least 32 bytes')
        self.service, self.configs = service, configs
        self.source = LocalCatalogSource(source_path)
        self.capabilities_path = capabilities_path
        self.fixture_path = Path(fixture_path)
        self.legacy_fixture_path = Path(legacy_fixture_path) if legacy_fixture_path else None
        self.legacy_capabilities_path = legacy_capabilities_path
        if bool(self.legacy_fixture_path) != bool(self.legacy_capabilities_path):
            raise ValueError('Legacy fixture and capabilities must be supplied together')
        self.cursor_secret = cursor_secret
        self.clip_encoder = None
        self.mode = 'LOCAL_CLIP' if clip_model_path else 'LOCAL_EXACT'
        if clip_model_path:
            from .local_clip import LocalCLIPEncoder
            self.clip_encoder = LocalCLIPEncoder(clip_model_path)
        from .candidate_browsing import CandidateBrowser
        self.browser = CandidateBrowser(ledger_path, cursor_secret)
        self._fixture()  # startup validation; each operation reloads trusted input
        if self.legacy_fixture_path:
            self._fixture(self.legacy_fixture_path, self.legacy_capabilities_path)
        self.repository = SQLitePlanSetApprovalRepository(ledger_path,
            projects=service.projects, authorization=self._authority())
        self._setup_search_execution(ledger_path, search_limits, start=start)

    def _setup_search_execution(self, ledger_path, limits, *, start):
        from .search_scheduling import SearchLimits, FairSearchQueue
        from .search_budget import SearchBudget
        self.search_limits = limits or SearchLimits()
        self.search_budget = SearchBudget(ledger_path, self.search_limits)
        self._retry_rounds = {}
        self.scheduler = FairSearchQueue(self._activation, self.search_limits, start=start)
        self._worker = self.scheduler.workers[0]  # compatibility for browse-only inspection
        self._recovery_worker = None
        if start:
            self._recovery_worker = threading.Thread(target=self._recover_searches,
                daemon=True, name='search-recovery')
            self._recovery_worker.start()

    def _recover_searches(self):
        try:
            self.scheduler.recover(self.repository.recoverable())
        except ServiceError:
            # Shutdown/authorization failure leaves durable approvals unchanged.
            return

    def encode_query(self, text, binding):
        if self.clip_encoder is None:
            raise ServiceError('QUERY_UNSUPPORTED', 'Text encoder is unavailable', 422)
        return self.clip_encoder.encode_query(text, binding)

    def encode_reference(self, context, query_input, binding):
        if self.clip_encoder is None or self.service.query_images is None:
            raise ServiceError('QUERY_UNSUPPORTED', 'Reference-image encoder is unavailable', 422)
        task = self.service.repository.get(context.actor, context.task_id)
        content = self.service.query_images.content(
            context.actor, task.project_id, query_input['query_image_id'], query_input['digest'])
        return self.clip_encoder.encode_image_bytes(content, binding)

    def close(self):
        self.scheduler.close()
        if self._recovery_worker is not None:
            self._recovery_worker.join()

    def enqueue(self, actor, task_id):
        self.scheduler.enqueue(actor, task_id)

    def _activation(self, actor, task_id):
        """One page per turn; yield between branches, tasks and users."""
        task = self.service.repository.get(actor, task_id)
        identity = (actor, task_id, task.confirmed_revision, task.run_generation)
        if (self._automatic_refill(task) or task.media_type == 'video') and self._first_window_committed(actor, task_id):
            self._retry_rounds.pop(identity, None)
            return False
        try:
            result = self.tick(actor, task_id, max_requests=1)
        except ServiceError as error:
            if error.code == 'SEARCH_BUSY':
                from .search_scheduling import SearchRetry
                return SearchRetry()
            self._retry_rounds.pop(identity, None)
            raise
        if any(row.get('code') == 'SEARCH_BUSY' for row in result['outcomes']):
            from .search_scheduling import SearchRetry
            return SearchRetry()
        if result['pagination_stopped'] or ((self._automatic_refill(task) or task.media_type == 'video')
                                           and self._initial_window_complete(result)):
            self._retry_rounds.pop(identity, None)
            return False
        if any(row['status'] == 'retry_pending' for row in result['outcomes']):
            retries = self._retry_rounds.get(identity, 0) + 1
            if retries >= 3:
                self._retry_rounds.pop(identity, None)
                return False
            self._retry_rounds[identity] = retries
            from .search_scheduling import SearchRetry
            return SearchRetry(0.25 * 2 ** retries)
        self._retry_rounds.pop(identity, None)
        return bool(result['network_attempts'])

    @staticmethod
    def _automatic_refill(task):
        try:
            proposal = json.loads(task.confirmed_proposal_json)
            policy = proposal['query_spec']['refill_policy']
            return (policy['mode'] == 'maintain_target'
                    and policy['progression'] == 'auto'
                    and proposal['node_modes']['refill'] == 'auto')
        except (TypeError, ValueError, KeyError):
            return False

    @staticmethod
    def _initial_window_complete(progress):
        members = [m for m in progress.get('members', ()) if not m.get('initial_deferred')]
        return bool(members) and all(
            member.get('committed_pages', 0) >= 1 and not member.get('pending')
            for member in members)

    def _first_window_committed(self, actor, task_id):
        members = self._mark_initial_members(actor, task_id,
            read_retrieval_progress(self.repository, actor, task_id)['members'])
        return self._initial_window_complete({'members': members})

    def _initial_ids(self, actor, task_id, approved=None):
        task = self.service.repository.get(actor, task_id)
        if task.media_type != 'image' or not self._automatic_refill(task):
            return None
        approved = approved or self.repository.get_browse_current(actor, task_id)
        if approved is None:
            return None
        from .recall_strategy import initial_plan_ids
        return initial_plan_ids(approved)

    def _mark_initial_members(self, actor, task_id, members):
        selected = self._initial_ids(actor, task_id)
        if selected is None:
            return members
        return [{**member, 'initial_deferred': member['plan_id'] not in selected}
                for member in members]

    def _fixture(self, fixture_path=None, capabilities_path=None):
        try:
            raw = (fixture_path or self.fixture_path).read_bytes()
            if len(raw) > 8_000_000:
                raise ValueError('fixture too large')
            value = json.loads(raw)
            if (set(value) not in ({'schema', 'query_vectors', 'records', 'filter_templates', 'max_depth'},
                                   {'schema', 'query_vectors', 'query_rules', 'records', 'filter_templates', 'max_depth'})
                    or value['schema'] != 'local-first-search@1'
                    or not isinstance(value['query_vectors'], list) or len(value['query_vectors']) > 512
                    or not isinstance(value.get('query_rules', []), list) or len(value.get('query_rules', [])) > 64
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
            rules, aliases = [], {library.library_id: set() for library in libraries}
            for item in value.get('query_rules', []):
                if (set(item) != {'binding_id', 'tokens', 'values'} or item['binding_id'] not in bindings
                        or not isinstance(item['tokens'], list) or not 1 <= len(item['tokens']) <= 64
                        or not isinstance(item['values'], list)):
                    raise ValueError('invalid query rule')
                binding = bindings[item['binding_id']]
                tokens = tuple(unicodedata.normalize('NFKC', token).casefold().strip()
                               for token in item['tokens'] if isinstance(token, str))
                if (len(tokens) != len(item['tokens']) or any(not token or len(token) > 80 for token in tokens)
                        or len(set(tokens)) != len(tokens)):
                    raise ValueError('invalid query rule tokens')
                rules.append((binding, tokens, QueryVector(binding.encoder_ref, tuple(item['values']))))
                aliases[binding.library_id].update(tokens)
            records = []
            for item in value['records']:
                if (set(item) - {'search_text'} != {'binding_id', 'vector_id', 'values', 'scope', 'media'}
                        or item['binding_id'] not in bindings or not isinstance(item['values'], list)
                        or not isinstance(item['media'], list) or len(item['media']) > 4):
                    raise ValueError('invalid record')
                media = []
                for entry in item['media']:
                    if set(entry) != set(MediaRecord.__dataclass_fields__):
                        raise ValueError('invalid media mapping')
                    media.append(MediaRecord(**entry))
                records.append(LocalVectorRecord(bindings[item['binding_id']], item['vector_id'],
                                                 tuple(item['values']), item['scope'], tuple(media),
                                                 item.get('search_text', '')))
            templates = {key: frozenset(scopes) for key, scopes in value['filter_templates'].items()
                         if isinstance(scopes, list) and len(scopes) <= 64}
            if len(templates) != len(value['filter_templates']):
                raise ValueError('invalid filter templates')
            capabilities = load_capabilities(capabilities_path or self.capabilities_path)
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
                filter_templates=templates, cursor_secret=self.cursor_secret,
                query_rules=tuple(rules), query_encoder=self
                if fixture_path is None else None)
            if self.clip_encoder is not None and fixture_path is None:
                if any(binding.encoder_ref != self.clip_encoder.encoder_ref
                       or binding.vector_dimension != self.clip_encoder.dimension
                       for binding in bindings.values()):
                    raise ValueError('CLIP fixture/model mismatch')
            return (libraries, vectors, tuple(records), templates, value['max_depth'],
                    capabilities, adapter, aliases, tuple(rules))
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise ServiceError('LOCAL_SEARCH_UNAVAILABLE', 'Trusted local search fixture invalid', 503) from None

    def _authority(self):
        source = self.source
        fixture_path = self.fixture_path
        from .local_media_index import LocalMediaIndex
        media_index = LocalMediaIndex(fixture_path)

        class Authority:
            def require_access(self, context, resource_ref, action):
                if not isinstance(context, AccessContext):
                    raise ServiceError('ACCESS_DENIED', 'Local search context required', 403)
                if (context.purpose == 'refill'
                        and (not resource_ref.startswith('library:') or action != 'search')):
                    raise ServiceError('ACCESS_DENIED', 'Local refill search access denied', 403)
                if (context.purpose == 'cleaning_input'
                        and (not resource_ref.startswith('media:') or action != 'read')):
                    raise ServiceError('ACCESS_DENIED', 'Cleaning access is read-only media access', 403)
                if context.purpose not in ('search', 'retrieval', 'refill', 'cleaning_input'):
                    raise ServiceError('ACCESS_DENIED', 'Local search context required', 403)
                scope = CatalogConfigScope(context.actor.tenant_id, context.actor.user_id, 'demo')
                if resource_ref.startswith('library:'):
                    library_id = resource_ref[len('library:'):]
                    if action not in ('discover', 'search') or not source.authorize(scope, 'search', library_id):
                        raise ServiceError('ACCESS_DENIED', 'Library access denied', 403)
                    return
                if action == 'read' and resource_ref.startswith('media:'):
                    try:
                        matching = media_index.bindings_for(resource_ref)
                        libraries, _ = source.load()
                        binding_to_library = {b.binding_id: b.library_id for lib in libraries for b in lib.bindings}
                        if matching and all(source.authorize(scope, 'search', binding_to_library[b])
                                            for b in matching):
                            return
                    except (OSError, ValueError, KeyError, TypeError):
                        raise ServiceError('AUTH_UNAVAILABLE', 'Local media authority unavailable', 503) from None
                raise ServiceError('ACCESS_DENIED', 'Media access denied', 403)

        return Authority()

    def _local_configs(self, aliases):
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
                            'aliases': sorted({lib.library_id, *aliases[lib.library_id]})} for lib in libraries]
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
        libraries, vectors, records, templates, depth, capabilities, adapter, aliases, rules = self._fixture()
        router = build_configured_routing(tasks=self.service.repository,
            projects=self.service.projects, configs=self._local_configs(aliases),
            authorization=self._authority(), embeddings=adapter, capabilities=capabilities)
        return router, (libraries, vectors, records, templates, depth, adapter, rules)

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
        _, fixture, preview = self._preview(actor, task_id)
        adapter = fixture[5]
        context = AccessContext(actor, task_id, 'search', preview.authorization_version)
        task = self.service.repository.get(actor, task_id)
        query_input = json.loads(task.confirmed_proposal_json)['query_spec'].get('query_input')
        view = preview.to_view()
        for route, row in zip(preview.routes, view['routes']):
            row['options'] = [option for option, capability in zip(row['options'], route.options)
                              if (adapter.can_encode_reference(context, query_input, capability.binding)
                                  if query_input and query_input.get('type') == 'reference_image' else
                                  adapter.can_encode_query(context, route.query_text, capability.binding))]
            row['availability'] = ('available' if row['options'] else
                                   'query_encoding_unavailable' if route.options else
                                   'no_available_index')
        return {**view, 'execution_enabled': True, 'mode': self.mode,
                'execution_limits': {'max_calls': self.search_limits.max_calls,
                    'max_hit_slots': self.search_limits.max_hit_slots,
                    'scope': 'task_revision_generation'},
                'source_policy': 'smart_authorized',
                'note': ('本地测试图片的真实 CLIP 向量召回；候选仍须授权读取及 VLM 复判。'
                         if self.clip_encoder else
                         '仅本地显式词规则和测试向量；未批准、未执行、未接真实 Milvus/模型/图片。')}

    @measured('search_preparation')
    def prepare(self, actor, task_id, *, routing_digest, choices):
        router, _, preview = self._preview(actor, task_id)
        if routing_digest != preview.routing_digest or not isinstance(choices, list):
            raise ServiceError('ROUTING_CHANGED', 'Routing preview changed or choices invalid', 409)
        try:
            selected = tuple(QueryRouteChoice(**choice) for choice in choices)
        except (TypeError, KeyError):
            raise ServiceError('INVALID_ROUTE_SELECTION', 'Invalid route choices', 422) from None
        if {choice.branch_id for choice in selected} != {route.branch_id for route in preview.routes}:
            raise ServiceError('BRANCH_SELECTION_INCOMPLETE', 'Select an index for every query term', 422)
        epoch = self.repository.next_epoch(actor, task_id)
        plan = router.prepare(actor, task_id, preview=preview, choices=selected, epoch=epoch)
        task = self.service.repository.get(actor, task_id)
        if task.media_type == 'video':
            # Manual and automatic approvals share the same frozen input and
            # call limits. Never approve a first window that cannot be fixed.
            hits = sum(member.plan.request.batch_size for member in plan.members)
            if hits > MANIFEST_HIT_LIMIT:
                raise ServiceError('INPUT_LIMIT', 'Initial video window exceeds candidate manifest capacity', 422)
            spec = json.loads(task.confirmed_proposal_json)['query_spec']
            limit = spec.get('refill_limits', {}).get('max_vlm_calls')
            if limit is not None and hits * len(spec['video_conditions']['clauses']) > limit:
                raise ServiceError('VIDEO_CALL_BUDGET', 'Initial video window exceeds confirmed call budget', 422)
        self.repository.put_proposal(actor, plan)
        return {'schema_version': 'local-first-search-proposal@1', 'task_id': task_id,
                'proposal_digest': plan.proposal_digest, 'expected_task_version': plan.scope.task_version,
                'epoch': plan.epoch, 'mode': self.mode,
                'members': [{'branch_id': member.branch_id,
                             'library_id': member.plan.request.binding.library_id,
                             'binding_id': member.plan.request.binding.binding_id,
                             'index_version': member.plan.request.binding.index_version,
                             'batch_size': member.plan.request.batch_size} for member in plan.members],
                'note': '待发起人确认；确认后仍需显式点击执行。本地测试向量，不是合格素材数。'}

    @measured('search_dispatch')
    def auto_start(self, actor, task_id, *, key, allowed_bindings=None):
        """Prepare, approve and queue only an unambiguous authorized plan."""
        task = self.service.repository.get(actor, task_id)
        try:
            current = self.repository.get_current(actor, task_id)
        except ServiceError as error:
            if error.code != 'REVISION_CONFLICT':
                raise
            current = None
        if (current is not None and current.scope.revision == task.confirmed_revision
                and current.scope.generation == task.run_generation):
            if allowed_bindings is not None and any(m.plan.request.binding.binding_id not in allowed_bindings for m in current.members):
                raise ServiceError('AGENT_SOURCE_NOT_APPROVED', 'Plan exceeds confirmed source scope', 409)
            self.enqueue(actor, task_id)
            return {'status': 'queued', 'replayed': True,
                    'source_policy': 'smart_authorized',
                    'source_count': len({member.plan.request.binding.binding_id
                                         for member in current.members}),
                    'set_digest': current.proposal_digest}
        preview = self.preview(actor, task_id)
        if allowed_bindings is not None:
            for route in preview['routes']:
                route['options'] = [o for o in route['options'] if o['binding_id'] in allowed_bindings]
        unavailable = [route for route in preview['routes'] if not route['options']]
        if unavailable:
            return {'status': 'unavailable', 'queries': [route['query_text'] for route in unavailable]}
        # Keep the first window and every later refill page proportional to the
        # requested target. A target of one should not approve eight VLM-bound
        # candidates per refill position before its execution budget is known.
        batch_cap = (min(8, max(1, task.target_count * 2))
                     if self._automatic_refill(task) else 8)
        options = [(route, option) for route in preview['routes'] for option in route['options']]
        if self._automatic_refill(task):
            allowed = set(json.loads(task.confirmed_proposal_json)['query_spec']['refill_policy']['approved_library_ids'])
            options = [(route, option) for route, option in options if option['library_id'] in allowed]
            if any(not any(r['branch_id'] == route['branch_id'] for r, _ in options)
                   for route in preview['routes']):
                return {'status': 'unavailable', 'reason': 'approved_source_scope',
                        'queries': [route['query_text'] for route in preview['routes']]}

        if self._automatic_refill(task) and options:
            # The automatic first window is fixed as one candidate manifest,
            # which holds at most 100 hits. Split that budget across members:
            # 19 branches x 8 per page once stranded a real task at INPUT_LIMIT.
            batch_cap = min(batch_cap, max(1, MANIFEST_HIT_LIMIT // len(options)))
        if task.media_type == 'video' and options:
            spec = json.loads(task.confirmed_proposal_json)['query_spec']
            limit = spec.get('refill_limits', {}).get('max_vlm_calls')
            if limit is not None:
                per_candidate = len(spec['video_conditions']['clauses'])
                if limit < len(options) * per_candidate:
                    raise ServiceError('VIDEO_CALL_BUDGET', 'Initial video window exceeds confirmed call budget', 422)
                batch_cap = min(batch_cap, max(1, limit // (len(options) * per_candidate)))
        choices = [{'branch_id': route['branch_id'], 'binding_id': option['binding_id'],
                    'batch_size': min(batch_cap, option['max_batch_size'])}
                   for route, option in options]
        if len(choices) > 64:
            return {'status': 'needs_selection', 'reason': 'source_limit',
                    'queries': [route['query_text'] for route in preview['routes']]}
        proposal = self.prepare(actor, task_id, routing_digest=preview['routing_digest'], choices=choices)
        approved, replayed = self.confirm(actor, task_id,
            proposal_digest=proposal['proposal_digest'],
            expected_task_version=proposal['expected_task_version'],
            action='confirm', key=key)
        self.enqueue(actor, task_id)
        return {'status': 'queued', 'replayed': replayed,
                'source_policy': 'smart_authorized',
                'source_count': len({member['binding_id'] for member in proposal['members']}),
                'set_digest': approved['set_digest'], 'members': len(proposal['members'])}

    def confirm(self, actor, task_id, *, proposal_digest, expected_task_version, action, key):
        approved, replay = self.repository.confirm(actor, task_id, proposal_digest=proposal_digest,
            expected_task_version=expected_task_version, action=action, key=key)
        return {'task_id': task_id, 'set_digest': approved.proposal_digest,
                'epoch': approved.epoch, 'task_version': approved.scope.task_version,
                'mode': self.mode, 'status': 'approved',
                'execution_limits': {'max_calls': self.search_limits.max_calls,
                                     'max_hit_slots': self.search_limits.max_hit_slots},
                'note': '已确认整组计划；可由持久化后台执行器继续。'}, replay

    def _adapter_for_plan(self, actor, task_id, approved):
        libraries, vectors, records, templates, depth, _, _, _, rules = self._fixture()
        using_legacy = bool(self.legacy_fixture_path and any(
                member.plan.request.filter_template_ref not in templates
                for member in approved.members))
        if using_legacy:
            libraries, vectors, records, templates, depth, _, _, _, rules = self._fixture(
                self.legacy_fixture_path, self.legacy_capabilities_path)
        if any(member.plan.request.filter_template_ref not in templates for member in approved.members):
            raise ServiceError('INDEX_SNAPSHOT_EXPIRED', 'Local fixture changed since approval', 409)
        task = self.service.repository.get(actor, task_id)
        spec = json.loads(task.confirmed_proposal_json)['query_spec']
        target_count = spec['target_count']
        member_count = len(approved.members)
        # A configured local-media gateway means this search feeds the fixed
        # candidate manifest, which accepts at most 100 raw hits. Keep ordinary
        # retrieval fixtures at their declared depth, but give the Chat/VLM path
        # a bounded five-times refill buffer divided across all approved members.
        automatic_refill = self._automatic_refill(task)
        candidate_budget = (depth * member_count if automatic_refill else
                            min(100, max(member_count, target_count * 5))
                            if getattr(self, 'media_gateway', None) is not None
                            else depth * member_count)
        base_depth, remainder = divmod(candidate_budget, member_count)
        context = AccessContext(actor, task_id, 'retrieval', 'current')
        from .query_branches import compile_query_branches
        logical = compile_query_branches(spec)
        branches = {b['branch_id']: b['query_text'] for b in logical['branches']}
        # Existing approved cursors retain their original ranking/window policy.
        # Only a plan prepared with this compiler preview opts into the new one.
        current_policy = all(member.logical_preview_digest == logical['preview_digest']
                             for member in approved.members)
        return LocalVectorRetrieval(self._authority(), libraries=libraries, query_vectors=vectors,
            approved=tuple(ApprovedFixture(context, member.plan.request, (), min(
                depth, base_depth + (index < remainder)))
                for index, member in enumerate(approved.members)), records=records,
            filter_templates=templates, cursor_secret=self.cursor_secret,
            query_rules=rules, query_encoder=None if using_legacy else self,
            query_texts={member.plan.request.plan_id: branches[member.branch_id]
                         for member in approved.members}
            if current_policy and (spec.get('query_input') or {}).get('type') != 'reference_image' else {},
            progressive=current_policy and automatic_refill and task.media_type == 'image')

    @measured('search_activation')
    def tick(self, actor, task_id, *, max_requests):
        approved = self.repository.get_current(actor, task_id)
        if approved is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'Explicit whole-set approval required', 409)
        gateway = self._adapter_for_plan(actor, task_id, approved)
        task = self.service.repository.get(actor, task_id)
        strict = 'retrieval_execution' in json.loads(task.confirmed_proposal_json)['query_spec']
        result = RetrievalCoordinator(self.repository, gateway, budget=self.search_budget, strict_unknown=strict).tick(actor, task_id,
            max_requests=max_requests, plan_ids=self._initial_ids(actor, task_id, approved))
        return {**result, 'mode': self.mode,
                'note': '仅记录原始检索页；未清洗、未计算有效素材数。'}

    def progress(self, actor, task_id):
        reader = getattr(self.service.repository, 'get_task_read_binding', self.service.repository.get)
        task = reader(actor, task_id)
        progress = read_retrieval_progress(self.repository, actor, task_id, browsing=task.media_type == 'video')
        progress['members'] = self._mark_initial_members(actor, task_id, progress['members'])
        if any('initial_deferred' in m for m in progress['members']):
            progress['source_strategy'] = {
                'policy': 'ranked_first_index_per_query@1',
                'initial_indexes': sum(not m['initial_deferred'] for m in progress['members']),
                'deferred_indexes': sum(m['initial_deferred'] for m in progress['members']),
                'ranking_evidence': 'authorized_catalog_order'}
        progress['initial_window_complete'] = (
            progress['pagination_stopped'] or (
                (self._automatic_refill(task) or task.media_type == 'video')
                and bool(progress['members'])
                and all(member['committed_pages'] >= 1
                        and member['status'] != 'pending'
                        for member in progress['members'] if not member.get('initial_deferred'))))
        approved = self.repository.get_browse_current(actor, task_id)
        if approved is not None:
            if any(progress.get(name) != value for name, value in (('revision', approved.scope.revision), ('generation', approved.scope.generation), ('task_version', approved.scope.task_version))):
                raise ServiceError('PLAN_NOT_CURRENT', 'Search progress scope changed', 409)
            progress['execution_budget'] = self.search_budget.snapshot(approved.scope)
            progress['execution_queue'] = self.scheduler.status(actor, task_id)
        return progress

    def recall(self, actor, task_id):
        from .retrieval_recall import read_recall
        return read_recall(self, actor, task_id)

    def candidates(self, actor, task_id, *, limit=200, cursor=None, query='', changes=False):
        approved = self.repository.get_browse_current(actor, task_id)
        if approved is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'Explicit whole-set approval required', 409)
        from .query_branches import compile_query_branches
        from .adapters.task_storage.sqlite import decode
        task = self.service.repository.get(actor, task_id)
        logical = compile_query_branches(json.loads(task.confirmed_proposal_json)['query_spec'])
        branches = {branch['branch_id']: branch for branch in logical['branches']}
        rows, pagination = self.browser.page(actor, task_id, approved.proposal_digest,
            limit=limit, cursor=cursor, query=query, changes=changes,
            branches=[key for key, branch in branches.items() if branch['query_text'] == query] if query else None)
        raw_hit_count = pagination['raw_hit_count']
        sourced_hits = tuple((row[1], decode(json.loads(row[3]))) for row in rows)
        members = {member.plan.request.plan_id: member for member in approved.members}
        if any(plan_id not in members or members[plan_id].branch_id not in branches
               for plan_id, _ in sourced_hits):
            raise ServiceError('STORAGE_CORRUPT', 'Candidate source branch missing', 500)
        hits = tuple(hit for _, hit in sourced_hits)
        adapter = self._adapter_for_plan(actor, task_id, approved)
        context = AccessContext(actor, task_id, 'retrieval', 'current')
        resolutions = adapter.resolve_hits(context, hits[:200])
        if len(resolutions) != min(len(hits), 200):
            raise ServiceError('STORAGE_CORRUPT', 'Candidate resolution count changed', 500)
        if self.repository.get_browse_current(actor, task_id) != approved:
            raise ServiceError('PLAN_NOT_CURRENT', 'Approved set changed during candidate read', 409)
        sources = sorted({(member.plan.request.binding.library_id,
                           member.plan.request.binding.binding_id,
                           member.plan.request.binding.index_version)
                          for member in approved.members})
        result = {'schema_version': 'local-first-search-candidates@1', 'task_id': task_id,
                'mode': self.mode, 'raw_hit_count': raw_hit_count, 'returned': len(resolutions),
                'truncated': raw_hit_count > len(resolutions), 'accepted_count': None,
                'resolved_count': sum(result.status == 'resolved' for result in resolutions),
                'verified_count': sum(result.status == 'resolved' and result.media is not None
                                      and result.media.content_digest is not None
                                      for result in resolutions),
                'source_policy': 'smart_authorized',
                'sources': [{'library_id': library_id, 'binding_id': binding_id,
                             'index_version': index_version}
                            for library_id, binding_id, index_version in sources],
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
                           'candidate_id': candidate_identity(result.media) if result.media else None,
                           'media_unit_id': result.media.media_unit_id if result.media else None,
                           'asset_version': result.media.asset_version if result.media else None,
                           'content_digest': result.media.content_digest if result.media else None,
                           'preview_status': ('available' if result.status == 'resolved'
                                              and result.media is not None
                                              and result.media.content_digest is not None
                                              and result.media.authorized_media_ref in getattr(
                                                  getattr(self, 'media_gateway', None), 'by_ref', {})
                                              and self.media_gateway.by_ref[
                                                  result.media.authorized_media_ref][1] == result.media.content_digest
                                              else 'unavailable')}
                          for (plan_id, _), result in zip(sourced_hits, resolutions)],
                'note': '仅授权后的原始候选；可预览原图不代表清洗通过，不能作为有效素材数量。'}
        result.update(pagination)
        result['query_terms'] = sorted({branch['query_text'] for branch in branches.values()})
        for row, item in zip(rows, result['items']):
            item['hit_sequence'] = row[0]
            item['search_version'] = approved.proposal_digest
            item['preview_ref'] = self.browser.preview_ref(actor, task_id, approved.proposal_digest, row[0], item['candidate_id'])
        self.browser.remember_many(approved.proposal_digest,
            ((row[0], item['candidate_id']) for row, item in zip(rows, result['items'])))
        return result

    def candidate_count(self, actor, task_id):
        """Return the committed raw hit count without resolving or mutating candidates."""
        approved = self.repository.get_browse_current(actor, task_id)
        if approved is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'Explicit whole-set approval required', 409)
        _, pagination = self.browser.page(
            actor, task_id, approved.proposal_digest, limit=1)
        if self.repository.get_browse_current(actor, task_id) != approved:
            raise ServiceError('PLAN_NOT_CURRENT', 'Approved set changed during candidate read', 409)
        return pagination['raw_hit_count']

    def candidate_preview(self, actor, task_id, candidate_id, *, ref=None):
        """Read only a committed, currently authorized local-test candidate."""
        gateway = getattr(self, 'media_gateway', None)
        if gateway is None:
            raise ServiceError('PREVIEW_UNAVAILABLE', 'Candidate image bytes are unavailable', 404)
        approved = self.repository.get_browse_current(actor, task_id)
        if approved is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'Explicit whole-set approval required', 409)
        from .adapters.task_storage.sqlite import decode
        hit = decode(json.loads(self.browser.locate(actor, task_id, approved.proposal_digest, candidate_id, ref)))
        hits = (hit,)
        adapter = self._adapter_for_plan(actor, task_id, approved)
        context = AccessContext(actor, task_id, 'retrieval', 'current')
        for result in adapter.resolve_hits(context, hits):
            media = result.media
            if (result.status != 'resolved' or media is None
                    or candidate_identity(media) != candidate_id):
                continue
            if (not media.content_digest
                    or media.authorized_media_ref not in gateway.by_ref
                    or gateway.by_ref[media.authorized_media_ref][1] != media.content_digest):
                break
            preview_context = AccessContext(actor, task_id, 'candidate_preview', 'current')
            thumbnail = getattr(gateway, 'thumbnail', None)
            if callable(thumbnail):
                content = thumbnail(preview_context, media.authorized_media_ref,
                                    max_bytes=10 * 1024 * 1024)
            else:
                with gateway.open_read(preview_context, media.authorized_media_ref,
                                       max_bytes=10 * 1024 * 1024) as stream:
                    content = stream.read()
            if self.repository.get_browse_current(actor, task_id) != approved:
                raise ServiceError('PLAN_NOT_CURRENT', 'Approved set changed during preview read', 409)
            content_type = ('image/jpeg' if content.startswith(b'\xff\xd8\xff')
                            else 'image/png' if content.startswith(b'\x89PNG\r\n\x1a\n')
                            else None)
            if content_type is None:
                raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Candidate image changed', 409)
            return content_type, content
        raise ServiceError('PREVIEW_UNAVAILABLE', 'Candidate image bytes are unavailable', 404)

    @measured('candidate_validation')
    def _candidate_manifest_proposal(self, actor, task_id):
        task = self.service.repository.get(actor, task_id)
        bind_timing(task_id=task_id, revision=task.confirmed_revision,
                    generation=task.run_generation, task_version=task.task_version)
        approved = self.repository.get_browse_current(actor, task_id)
        if approved is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'Search plan is not approved', 409)
        downstream = task.task_version > approved.scope.task_version
        progress = (None if downstream else
                    read_retrieval_progress(self.repository, actor, task_id))
        if progress is not None:
            progress['members'] = self._mark_initial_members(actor, task_id, progress['members'])
        if (progress is not None and not progress['pagination_stopped']
                and not ((self._automatic_refill(task) or task.media_type == 'video')
                         and bool(progress['members'])
                         and all(member['committed_pages'] >= 1
                                 and member['status'] != 'pending'
                                 for member in progress['members'] if not member.get('initial_deferred')))):
            raise ServiceError('SEARCH_IN_PROGRESS', 'Search must finish before fixing candidates', 409)
        pages = self.repository.list_committed_pages(
            actor, approved, allow_downstream=downstream)
        sourced_hits = tuple(hit for _, page in pages for hit in page.hits)
        adapter = self._adapter_for_plan(actor, task_id, approved)
        context = AccessContext(actor, task_id, 'retrieval', 'current')
        resolutions = timed_call('candidate_resolution', adapter.resolve_hits, context, sourced_hits)
        if len(resolutions) != len(sourced_hits):
            raise ServiceError('STORAGE_CORRUPT', 'Candidate resolution count changed', 500)
        seen = (self.repository.candidate_manifest_candidate_ids(actor, task_id, approved)
                if downstream else frozenset())
        grouped = {}
        origin_groups = plan_origin_groups(task, approved)
        hit_groups = {}
        for plan_id, page in pages:
            for hit in page.hits:
                hit_groups.setdefault(digest(asdict(hit)), set()).update(origin_groups[plan_id])
        quota_task = any(origin_groups.values())
        query_input = json.loads(task.confirmed_proposal_json)['query_spec'].get('query_input')
        excluded_digest = (query_input['digest'] if query_input
                           and query_input.get('type') == 'reference_image'
                           and query_input['exclude_exact_content'] else None)
        for resolution in resolutions:
            media = resolution.media
            if (resolution.status != 'resolved' or media is None
                    or media.content_digest is None
                    or media.content_digest == excluded_digest):
                continue
            candidate_id = candidate_identity(media)
            if candidate_id in seen:
                continue
            old = grouped.get(candidate_id)
            if old is None:
                grouped[candidate_id] = [media, [resolution.hit]]
            elif old[0] != media:
                raise ServiceError('MAPPING_CONFLICT', 'Candidate identity maps to multiple versions', 409)
            else:
                old[1].append(resolution.hit)
        if task.media_type == 'video':
            from .whole_video import require_whole_video_records
            try:
                require_whole_video_records(tuple(media for media, _ in grouped.values()))
            except ValueError as exc:
                raise ServiceError('MEDIA_MAPPING_CONFLICT', 'Original video identities conflict', 409) from exc
        # Frozen here so every later check, count and rebuild sees the same groups.
        items = []
        for media, hits in grouped.values():
            groups = tuple(sorted(set().union(*(hit_groups[digest(asdict(hit))] for hit in hits))))
            # Hit only by the whole-request query: no group to attribute it to
            # (§8.6), so it is not worth a model call it could never count for.
            if groups or not quota_task:
                items.append(ManifestItem(media, tuple(hits), groups))
        if len(items) > 100 or sum(len(item.hits) for item in items) > 100:
            raise ServiceError('INPUT_LIMIT', 'Candidate manifest supports at most 100 items/hits', 422)
        proposal = CandidateManifestProposal(
            RetrievalScope.from_task(task), self.repository.candidate_source_id(approved),
            approved.proposal_digest, tuple(items))
        # A concurrent plan/page change must not survive either side of mapping.
        if self.repository.get_browse_current(actor, task_id) != approved:
            raise ServiceError('PLAN_NOT_CURRENT', 'Search plan changed during candidate mapping', 409)
        return proposal

    def preview_manifest(self, actor, task_id):
        published = self.repository.get_current_candidate_manifest(actor, task_id)
        task = self.service.repository.get(actor, task_id)
        if published is not None and published.proposal.scope.task_version == task.task_version:
            return {**published.view(), 'schema_version': 'first-search-manifest@1',
                    'verified_count': len(published.proposal.items),
                    'confirmed': True,
                    'note': '候选清单已固定；尚未批准或启动清洗。'}
        proposal = self._candidate_manifest_proposal(actor, task_id)
        self.repository.put_candidate_proposal(actor, proposal)
        return {**proposal.view(), 'schema_version': 'first-search-manifest-proposal@1',
                'verified_count': len(proposal.items),
                'confirmed': False,
                'note': '仅固定已映射且内容摘要完整的候选；尚未开始清洗。'}

    def confirm_manifest(self, actor, task_id, *, proposal_digest,
                         expected_task_version, key, action):
        proposal = self._candidate_manifest_proposal(actor, task_id)
        if proposal.proposal_digest != proposal_digest:
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Candidate preview changed; review again', 409)
        if not proposal.items:
            raise ServiceError('CANDIDATE_MANIFEST_EMPTY',
                               'No verified candidates are available to fix', 409)
        manifest, replayed = self.repository.confirm_candidate_manifest(
            actor, proposal, key=key, expected_task_version=expected_task_version,
            action=action)
        return {**manifest.view(), 'schema_version': 'first-search-manifest@1',
                'verified_count': len(manifest.proposal.items),
                'note': '候选清单已固定；尚未批准或启动清洗。'}, replayed

    def get_manifest(self, actor, task_id, manifest_ref):
        return self.repository.get_candidate_manifest(actor, task_id, manifest_ref).view()
