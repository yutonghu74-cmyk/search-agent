"""Opt-in combined CLIP first-search flow with persistent background jobs."""

from dataclasses import asdict, replace
import json
import threading

from .adapters.retrieval.clip_search import CLIPTextSearchClient, CLIPTextSearchRequest
from .clip_search_jobs import CLIPJobMember, CLIPSearchExecutor, SQLiteCLIPSearchJobs
from .candidate_contracts import candidate_identity
from .clip_media_mapping import load_clip_media_mappings
from .errors import ServiceError
from .integration_models import AccessContext
from .local_catalog import LocalCatalogSource
from .models import Actor
from .query_spec import digest
from .retrieval_contracts import RetrievalScope
from .routing_preview import LocalRoutingPreview
from .text_catalog_config import CatalogConfigScope


class CLIPFirstSearch:
    mode = "CLIP_COMBINED"

    def __init__(self, service, configs, *, source_path, capabilities_path,
                 registry, endpoints, jobs_path, cursor_secret, opener=None,
                 media_mappings_path=None, search_limits=None):
        self.service = service
        self.source = LocalCatalogSource(source_path)
        self.routing = LocalRoutingPreview(service, configs, source_path, capabilities_path)
        self.endpoints = {endpoint.binding.binding_id: endpoint for endpoint in endpoints}
        self.jobs = SQLiteCLIPSearchJobs(jobs_path)
        from .candidate_browsing import CandidateBrowser
        self.browser = CandidateBrowser(jobs_path, cursor_secret)
        self.client = CLIPTextSearchClient(registry, self._authority(), endpoints,
            cursor_secret=cursor_secret, opener=opener)
        self.media_mappings = (load_clip_media_mappings(media_mappings_path, registry)
                               if media_mappings_path else None)
        from .search_scheduling import SearchLimits, FairSearchQueue
        from .search_budget import SearchBudget
        self.search_limits = search_limits or SearchLimits()
        self.search_budget = SearchBudget(getattr(service.repository, 'path', jobs_path), self.search_limits)
        self.executor = CLIPSearchExecutor(service.repository, self.jobs, self.client, budget=self.search_budget, continuation=self._dispatch_pause, initial_selector=self._initial_selector)
        self.scheduler = FairSearchQueue(self._activation, self.search_limits, name='clip-search')
        self._worker = self.scheduler.workers[0]
        self._recovery_worker = threading.Thread(target=self._recover_jobs, daemon=True, name='clip-search-recovery')
        self._recovery_worker.start()

    def _recover_jobs(self):
        try:
            items = []
            for job_id in self.jobs.recoverable():
                job = self.jobs.job(job_id)
                if any(m.windowed for m in job.members) and getattr(self, 'cleaning_reader', None) is None:
                    continue
                items.append((Actor(job.scope.tenant_id, job.scope.owner_user_id), job_id))
            self.scheduler.recover(items)
        except ServiceError:
            return

    def _authority(self):
        source = self.source

        class Authority:
            def require_access(self, context, resource_ref, action):
                if (type(context) is not AccessContext
                        or context.purpose not in ("search", "retrieval", "refill")
                        or not resource_ref.startswith("library:")
                        or action not in ("discover", "search")
                        or (context.purpose == "refill" and action != "search")):
                    raise ServiceError("ACCESS_DENIED", "CLIP search access denied", 403)
                scope = CatalogConfigScope(context.actor.tenant_id, context.actor.user_id, "demo")
                library_id = resource_ref[len("library:"):]
                if not source.authorize(scope, action, library_id):
                    raise ServiceError("ACCESS_DENIED", "CLIP search access denied", 403)

        return Authority()

    def close(self):
        self.scheduler.close()
        self._recovery_worker.join()

    def enqueue_job(self, job_id):
        job = self.jobs.job(job_id)
        self.scheduler.enqueue(Actor(job.scope.tenant_id, job.scope.owner_user_id), job_id)

    def enqueue(self, actor, task_id):
        task = self.service.repository.get(actor, task_id)
        job_id = self.jobs.current_for_scope(RetrievalScope.from_task(task))
        if job_id is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'Combined CLIP job is not prepared', 409)
        self.enqueue_job(job_id)

    def _activation(self, actor, job_id):
        from .search_scheduling import SearchRetry
        try:
            result = self.executor.tick(job_id, max_requests=1)
        except ServiceError as error:
            if error.code == 'SEARCH_BUSY':
                return SearchRetry()
            raise
        if result['status'] in ('complete', 'failed', 'superseded', 'paused'):
            return False
        if any(row['status'] == 'retry_pending' for row in result['outcomes']):
            return SearchRetry(0.5)
        return bool(result['network_attempts'])

    def preview(self, actor, task_id):
        view = self.routing.preview(actor, task_id)
        for route in view["routes"]:
            route["options"] = [option for option in route["options"]
                                if option["binding_id"] in self.endpoints]
            route["availability"] = "available" if route["options"] else "no_available_index"
        return {**view, "execution_enabled": True, "mode": self.mode,
                "execution_limits": {"max_calls": self.search_limits.max_calls, "max_hit_slots": self.search_limits.max_hit_slots, "scope": "task_revision_generation"},
                "source_policy": "smart_authorized",
                "note": "组合式 CLIP 文本检索；素材读取和清洗尚未接入。"}

    def _members(self, actor, task_id):
        task = self.service.repository.get(actor, task_id)
        scope = RetrievalScope.from_task(task)
        view = self.preview(actor, task_id)
        unavailable = [route for route in view["routes"] if not route["options"]]
        if unavailable:
            return scope, view, None, "unavailable"
        members = []
        for route in view["routes"]:
            for option in route["options"]:
                endpoint = self.endpoints[option["binding_id"]]
                if self._progressive(task) and endpoint.binding.library_id not in json.loads(
                        task.confirmed_proposal_json)['query_spec']['refill_policy']['approved_library_ids']:
                    continue
                plan_id = "clipplan_" + digest({"scope": asdict(scope), "branch": route["branch_id"],
                    "query": route["query_text"], "binding": option,
                    "filter": endpoint.default_filter_template_ref})[7:]
                request = CLIPTextSearchRequest(plan_id, scope.generation, endpoint.binding,
                    route["query_text"], endpoint.default_filter_template_ref,
                    min(25, option["max_batch_size"], max(1, 100 // len(view["routes"]))) if
                    self._progressive(task) else min(25, option["max_batch_size"]))
                members.append(CLIPJobMember(route["branch_id"], request,
                    endpoint.shard_paths if endpoint.declared_shards else (), endpoint.declared_shards,
                    self._progressive(task)))
        if any(not any(m.branch_id == r['branch_id'] for m in members) for r in view['routes']):
            return scope, view, None, 'unavailable'
        if len(members) > 64:
            return scope, view, None, "needs_selection"
        return scope, view, tuple(members), "ready"

    def auto_start(self, actor, task_id, *, key):
        if not isinstance(key, str) or not key:
            raise ServiceError("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key required", 400)
        scope, view, members, status = self._members(actor, task_id)
        if status != "ready":
            return {"status": status, "queries": [route["query_text"] for route in view["routes"]
                                                    if len(route["options"]) != 1]}
        authorization_version = self.routing._source_digest()
        job_id, replayed = self.jobs.create(scope, authorization_version, members)
        self.enqueue_job(job_id)
        return {"status": "queued", "replayed": replayed, "job_id": job_id,
                "source_policy": "smart_authorized",
                "source_count": len({member.request.binding.binding_id for member in members}),
                "members": len(members)}

    def _progressive(self, task):
        if getattr(self, 'cleaning_reader', None) is None:
            return False
        spec = json.loads(task.confirmed_proposal_json)
        policy = spec['query_spec']['refill_policy']
        return (task.media_type == 'image' and policy['mode'] == 'maintain_target'
                and policy['progression'] == 'auto' and spec['node_modes']['refill'] == 'auto')

    def _initial_selector(self, task, job):
        if not any(m.windowed for m in job.members):
            return None
        if getattr(self, 'cleaning_reader', None) is None:
            raise ServiceError('SEARCH_GATE_NOT_CONFIGURED', 'Windowed job requires business cleaning reader', 503)
        seen, selected = set(), set()
        for member in job.members:
            if member.branch_id not in seen:
                seen.add(member.branch_id)
                selected.add(member.request.plan_id)
        return frozenset(selected)

    def _window_ready(self, task, job, snapshot):
        if snapshot['status'] == 'complete':
            return True
        selected = self._initial_selector(task, job)
        return (bool(selected) and snapshot['status'] not in ('failed', 'superseded')
                and all(r['pages'] >= 1 and r['last_error'] is None
                        for r in snapshot['members'] if r['plan_id'] in selected))

    def _candidate_input(self, task, job, snapshot):
        from .clip_candidate_input import build_clip_input
        from .clip_search_jobs import _match_from_dict
        store = getattr(self, 'candidate_manifests', None)
        actor = Actor(task.tenant_id, task.owner_user_id)
        seen = store.seen_candidate_ids(actor, task.task_id, job.job_id) if store else frozenset()
        rows = []
        for row in snapshot['items']:
            match = _match_from_dict({k: v for k, v in row.items() if k not in ('branch_id', 'plan_id')})
            resolution = self.media_mappings.resolve(match) if self.media_mappings else None
            if resolution and resolution.media and candidate_identity(resolution.media) in seen:
                continue
            rows.append(row)
        return build_clip_input(task, job, rows, self.media_mappings)

    def preview_manifest(self, actor, task_id):
        from .clip_candidate_input import build_clip_input
        task = self.service.repository.get(actor, task_id)
        scope = RetrievalScope.from_task(task)
        job_id = self.jobs.current_for_browse(scope)
        if job_id is None:
            raise ServiceError('PLAN_NOT_APPROVED', 'Combined CLIP job is not prepared', 409)
        job = self.jobs.job(job_id)
        snapshot = self.jobs.view(job_id)
        if not self._window_ready(task, job, snapshot):
            raise ServiceError('SEARCH_IN_PROGRESS', 'Candidate window must finish before review', 409)
        context = AccessContext(actor, task_id, 'retrieval', job.authorization_version)
        def authorize():
            self.service.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
            for library in sorted({m.request.binding.library_id for m in job.members}):
                self.client.authorization.require_access(context, 'library:' + library, 'search')
        authorize()
        proposal, skipped = self._candidate_input(task, job, snapshot)
        authorize()
        if (RetrievalScope.from_task(self.service.repository.get(actor, task_id)) != scope
                or self.jobs.view(job_id) != snapshot):
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Candidate source changed during review', 409)
        store = getattr(self, 'candidate_manifests', None)
        if store is not None:
            store.put_proposal(actor, proposal)
        return {**proposal.view(), 'schema_version': 'first-search-manifest-proposal@1',
                'verified_count': len(proposal.items), 'skipped_count': skipped,
                'confirmed': False, 'confirmation_supported': store is not None,
                'note': ('候选输入待发起人确认；确认不代表清洗批准。' if store is not None else
                         '已映射输入快照仅供审阅；组合来源确认账本未配置。')}

    def configure_cleaning_reader(self, repository):
        self.configure_candidate_manifests(repository.manifests)
        self.cleaning_reader = repository
        self._recover_jobs()

    def _dispatch_pause(self, task, phase):
        reader = getattr(self, 'cleaning_reader', None)
        if reader is None:
            actor = Actor(task.tenant_id, task.owner_user_id)
            job_id = self.jobs.current_for_browse(RetrievalScope.from_task(task))
            if job_id and any(m.windowed for m in self.jobs.job(job_id).members):
                raise ServiceError('SEARCH_GATE_NOT_CONFIGURED', 'Windowed job requires business cleaning reader', 503)
            return None
        if task.active_cleaning_operation_id:
            return 'CLEANING_IN_PROGRESS'
        actor = Actor(task.tenant_id, task.owner_user_id)
        try:
            result = reader.get_current_result_set(actor, task.task_id)
        except ServiceError as error:
            if error.code == 'NOT_FOUND':
                if phase == 'initial':
                    job_id = self.jobs.current_for_browse(RetrievalScope.from_task(task))
                    if job_id and self._window_ready(task, self.jobs.job(job_id), self.jobs.view(job_id)):
                        return 'CANDIDATE_WINDOW_READY'
                return None
            raise
        if (result.get('generation') != task.run_generation
                or result.get('target_count') != task.target_count
                or type(result.get('accepted_count')) is not int
                or result['accepted_count'] < 0):
            raise ServiceError('RESULT_SCOPE_CHANGED', 'Current effective count is unavailable', 409)
        if result['accepted_count'] >= task.target_count:
            return 'TARGET_MET'
        # Once a business result exists, only approved refill reservations own
        # further pages; the original first-search worker must not race them.
        return 'AWAIT_REFILL' if phase == 'initial' else None

    def configure_candidate_manifests(self, repository):
        """Trusted composition only: repository owns project and media authorization."""
        from .adapters.task_storage.candidates import SQLiteCandidateManifestRepository
        if type(repository) is not SQLiteCandidateManifestRepository:
            raise TypeError('Existing persistent candidate repository required')
        if repository.tasks.path != self.service.repository.path:
            raise ValueError('Candidate and task ledger must match')
        repository.combined_source_validator = self._validate_combined_manifest
        self.candidate_manifests = repository

    def _validate_combined_manifest(self, task, actor, proposal, *, reading=False, delivery_read=False):
        from .retrieval_contracts import validate_scope
        from .clip_candidate_input import build_clip_input
        validate_scope(actor, replace(task, task_version=proposal.scope.task_version)
                       if reading else task, proposal.scope, reading=delivery_read)
        if reading and task.task_version < proposal.scope.task_version:
            raise ServiceError('REVISION_CONFLICT', 'Candidate task version regressed', 409)
        job = self.jobs.job(proposal.operation_id)
        if self.jobs.current_for_browse(RetrievalScope.from_task(task)) != job.job_id:
            raise ServiceError('PLAN_NOT_CURRENT', 'Combined candidate source superseded', 409)
        context = AccessContext(actor, task.task_id, 'retrieval', job.authorization_version)
        for library in sorted({m.request.binding.library_id for m in job.members}):
            self.client.authorization.require_access(context, 'library:' + library, 'search')
        snapshot = self.jobs.view(job.job_id)
        # Retain the exact frozen input even after later refill pages are appended.
        hits = {digest(asdict(h)) for item in proposal.items for h in item.hits}
        rows = [r for r in snapshot['items'] if digest(r['hit']) in hits]
        source_task = replace(task, task_version=proposal.scope.task_version)
        rebuilt, _ = build_clip_input(source_task, job, rows, self.media_mappings)
        if rebuilt.items != proposal.items:
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Frozen combined candidates changed', 409)
        if not reading:
            full, _ = self._candidate_input(task, job, snapshot)
            if not self._window_ready(task, job, snapshot) or full != proposal:
                raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Combined candidate preview changed', 409)

    def confirm_manifest(self, actor, task_id, *, proposal_digest, expected_task_version, key, action):
        store = getattr(self, 'candidate_manifests', None)
        if store is None:
            raise ServiceError('CANDIDATE_MANIFEST_NOT_CONFIGURED', 'Persistent candidate authorization required', 503)
        committed = store.replay_confirmation(actor, task_id, key=key,
            proposal_digest=proposal_digest, expected_task_version=expected_task_version, action=action)
        if committed is not None:
            return {**committed.view(), 'confirmed': True,
                    'verified_count': len(committed.proposal.items)}, True
        task = self.service.repository.get(actor, task_id)
        job_id = self.jobs.current_for_browse(RetrievalScope.from_task(task))
        from .clip_candidate_input import build_clip_input
        proposal, _ = self._candidate_input(task, self.jobs.job(job_id), self.jobs.view(job_id))
        if proposal.proposal_digest != proposal_digest:
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Review candidate input again', 409)
        if not proposal.items:
            raise ServiceError('CANDIDATE_MANIFEST_EMPTY', 'No verified candidates', 409)
        manifest, replayed = store.confirm(actor, proposal, key=key,
            expected_task_version=expected_task_version, action=action)
        return {**manifest.view(), 'confirmed': True, 'verified_count': len(proposal.items)}, replayed

    def get_manifest(self, actor, task_id, manifest_ref):
        store = getattr(self, 'candidate_manifests', None)
        if store is None:
            raise ServiceError('CANDIDATE_MANIFEST_NOT_CONFIGURED', 'Persistent candidate authorization required', 503)
        return store.get(actor, task_id, manifest_ref).view()

    def prepare(self, actor, task_id, **kwargs):
        raise ServiceError("COMBINED_SEARCH_AUTO_ONLY",
                           "Combined CLIP selection is not yet available", 409)

    def confirm(self, actor, task_id, **kwargs):
        raise ServiceError("COMBINED_SEARCH_AUTO_ONLY",
                           "Combined CLIP starts from confirmed terms", 409)

    def tick(self, actor, task_id, *, max_requests):
        task = self.service.repository.get(actor, task_id)
        job_id = self.jobs.current_for_browse(RetrievalScope.from_task(task))
        if job_id is None:
            raise ServiceError("PLAN_NOT_APPROVED", "Combined CLIP job is not prepared", 409)
        return {**self.executor.tick(job_id, max_requests=max_requests), "mode": self.mode}

    def progress(self, actor, task_id):
        task = self.service.repository.get(actor, task_id)
        scope = RetrievalScope.from_task(task)
        job_id = self.jobs.current_for_browse(scope)
        if job_id is None:
            return {"schema_version": "clip-search-progress@1", "task_id": task_id,
                    "revision": scope.revision, "generation": scope.generation,
                    "task_version": scope.task_version, "status": "unapproved",
                    "pagination_stopped": False, "members": []}
        view = self.jobs.view(job_id)
        return {"schema_version": "clip-search-progress@1", "task_id": task_id,
                "revision": scope.revision, "generation": scope.generation,
                "task_version": scope.task_version, "status": "approved",
                "job_status": view["status"], "coverage": view["coverage"],
                "pause_reason": self._dispatch_pause(task, "initial"),
                "initial_window_complete": self._window_ready(task, self.jobs.job(job_id), view),
                "execution_budget": self.search_budget.snapshot(scope),
                "execution_queue": self.scheduler.status(actor, job_id),
                "pagination_stopped": view["status"] in ("complete", "failed", "superseded"),
                "members": view["members"], "job_id": job_id}

    def candidates(self, actor, task_id, *, limit=200, cursor=None, query='', changes=False):
        task = self.service.repository.get(actor, task_id)
        scope = RetrievalScope.from_task(task)
        job_id = self.jobs.current_for_browse(scope)
        if job_id is None:
            raise ServiceError("PLAN_NOT_APPROVED", "Combined CLIP job is not prepared", 409)
        job = self.jobs.job(job_id)
        requests = {member.request.plan_id: member.request for member in job.members}
        from .query_branches import compile_query_branches
        logical = compile_query_branches(json.loads(task.confirmed_proposal_json)["query_spec"])
        branches = {branch["branch_id"]: branch for branch in logical["branches"]}
        from .clip_search_jobs import _match_from_dict
        rows, pagination = self.browser.page(actor, task_id, job_id, limit=limit,
            cursor=cursor, query=query, changes=changes,
            branches=[key for key, branch in branches.items() if branch['query_text'] == query] if query else None)
        typed = [(row[2], row[1], _match_from_dict(json.loads(row[3]))) for row in rows]
        context = AccessContext(actor, task_id, "retrieval", job.authorization_version)
        libraries = sorted({match.hit.binding.library_id for _, _, match in typed[:200]})
        for library_id in libraries:
            self.client.authorization.require_access(context, "library:" + library_id, "search")
        items = []
        for branch_id, plan_id, match in typed[:200]:
            hit, binding = match.hit, match.hit.binding
            request, branch = requests[plan_id], branches[branch_id]
            resolution = self.media_mappings.resolve(match) if self.media_mappings else None
            media = resolution.media if resolution else None
            status = resolution.status if resolution else "mapping_missing"
            items.append({"plan_id": request.plan_id,
                "branch_id": branch_id, "query_text": request.query_text,
                "query_role": branch["query_role"], "source_term": branch["source_term"],
                "library_id": binding.library_id, "binding_id": binding.binding_id,
                "index_version": binding.index_version, "vector_id": hit.vector_id,
                "score": hit.score, "metric": binding.metric, "status": status,
                "candidate_id": candidate_identity(media) if media else None,
                "media_unit_id": media.media_unit_id if media else None,
                "asset_version": media.asset_version if media else None,
                "content_digest": media.content_digest if media else None,
                "preview_status": "unavailable", "filename": match.filename,
                "full_path": match.full_path, "web_path": match.web_path,
                "folder": match.folder, "dataset_name": match.dataset_name,
                "dataset_version": match.dataset_version, "shard": match.shard})
        for library_id in libraries:
            self.client.authorization.require_access(context, "library:" + library_id, "search")
        sources = sorted({(member.request.binding.library_id,
                           member.request.binding.binding_id,
                           member.request.binding.index_version)
                          for member in job.members})
        fresh = RetrievalScope.from_task(self.service.repository.get(actor, task_id))
        if self.jobs.current_for_browse(fresh) != job_id:
            raise ServiceError('CANDIDATE_VERSION_CHANGED', 'Search changed; reload', 409)
        for row, item in zip(rows, items):
            item['hit_sequence'] = row[0]
            item['search_version'] = job_id
        return {"schema_version": "clip-search-candidates@1", "task_id": task_id,
                "mode": self.mode, **pagination, "query_terms": sorted({b['query_text'] for b in branches.values()}),
                "returned": len(items),
                "accepted_count": None, "source_policy": "smart_authorized",
                "sources": [{"library_id": library_id, "binding_id": binding_id,
                             "index_version": index_version}
                            for library_id, binding_id, index_version in sources],
                "items": items,
                "resolved_count": sum(item["status"] == "resolved" for item in items),
                "verified_count": sum(item["status"] == "resolved"
                                      and item["content_digest"] is not None for item in items),
                "note": "仅映射成功的命中具有稳定候选 ID；尚无授权预览或清洗判定。"}
