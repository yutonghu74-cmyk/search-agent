"""Opt-in combined CLIP first-search flow with persistent background jobs."""

from dataclasses import asdict
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
                 media_mappings_path=None):
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
        self.executor = CLIPSearchExecutor(service.repository, self.jobs, self.client)
        self._condition = threading.Condition()
        self._pending = set(self.jobs.recoverable())
        self._closed = False
        self._worker = threading.Thread(target=self._work, name="clip-first-search", daemon=True)
        self._worker.start()
        if self._pending:
            with self._condition:
                self._condition.notify_all()

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
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._worker.join(timeout=5)

    def enqueue_job(self, job_id):
        with self._condition:
            if self._closed:
                raise ServiceError("SEARCH_EXECUTOR_STOPPED", "Background search executor is stopped", 503)
            self._pending.add(job_id)
            self._condition.notify()

    def enqueue(self, actor, task_id):
        task = self.service.repository.get(actor, task_id)
        job_id = self.jobs.current_for_scope(RetrievalScope.from_task(task))
        if job_id is None:
            raise ServiceError("PLAN_NOT_APPROVED", "Combined CLIP job is not prepared", 409)
        self.enqueue_job(job_id)

    def _work(self):
        while True:
            with self._condition:
                while not self._pending and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                job_id = self._pending.pop()
            try:
                for _ in range(64):
                    result = self.executor.tick(job_id, max_requests=8)
                    if result["status"] in ("complete", "failed", "superseded"):
                        break
                    if not result["network_attempts"]:
                        break
            except ServiceError:
                continue

    def preview(self, actor, task_id):
        view = self.routing.preview(actor, task_id)
        for route in view["routes"]:
            route["options"] = [option for option in route["options"]
                                if option["binding_id"] in self.endpoints]
            route["availability"] = "available" if route["options"] else "no_available_index"
        return {**view, "execution_enabled": True, "mode": self.mode,
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
                plan_id = "clipplan_" + digest({"scope": asdict(scope), "branch": route["branch_id"],
                    "query": route["query_text"], "binding": option,
                    "filter": endpoint.default_filter_template_ref})[7:]
                request = CLIPTextSearchRequest(plan_id, scope.generation, endpoint.binding,
                    route["query_text"], endpoint.default_filter_template_ref,
                    min(25, option["max_batch_size"]))
                members.append(CLIPJobMember(route["branch_id"], request))
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

    def prepare(self, actor, task_id, **kwargs):
        raise ServiceError("COMBINED_SEARCH_AUTO_ONLY",
                           "Combined CLIP selection is not yet available", 409)

    def confirm(self, actor, task_id, **kwargs):
        raise ServiceError("COMBINED_SEARCH_AUTO_ONLY",
                           "Combined CLIP starts from confirmed terms", 409)

    def tick(self, actor, task_id, *, max_requests):
        task = self.service.repository.get(actor, task_id)
        job_id = self.jobs.current_for_scope(RetrievalScope.from_task(task))
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
                "job_status": view["status"],
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
