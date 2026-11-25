"""Opt-in admission and projections for the existing Chat/candidate windows."""
from dataclasses import replace
from hashlib import sha256
import json

from .candidate_browsing import CandidateBrowser
from .candidate_contracts import merge_manifest_items
from .agent_job_reviews import collection_review
from .errors import ServiceError, invalid
from .integration_models import AccessContext, IndexBinding
from .processing_requirements import identifier
from .query_spec import digest
from .service import require_key


def agent_task(task):
    return 'agent_requirements' in json.loads(task.confirmed_proposal_json)['query_spec']


class RetrievalAgentWindow:
    """Explicit deployment choice; it never upgrades legacy drafts or approves jobs."""
    def __init__(self, service, jobs, dispatcher, *, enabled_projects, model_profile_ref,
                 sources_provider, cursor_secret, max_model_calls=2, max_seconds=60, chat_entry='conversation'):
        if (type(enabled_projects) is not frozenset or not enabled_projects
                or any(not isinstance(p, str) or not p for p in enabled_projects)
                or not identifier(model_profile_ref) or not callable(sources_provider)
                or type(cursor_secret) is not bytes or len(cursor_secret) < 32
                or type(max_model_calls) is not int or not 1 <= max_model_calls <= 100
                or type(max_seconds) is not int or not 1 <= max_seconds <= 300
                or chat_entry not in ('conversation', 'candidate') or dispatcher.jobs is not jobs):
            raise ValueError('explicit projects, profile, authorized sources, worker and cursor secret required')
        expected = 'sqlite:' + str(service.repository.path.resolve()) if getattr(service.repository, 'path', None) else None
        if expected is None or jobs.links.storage_identity != expected:
            raise ValueError('admission requires the same persistent business/job ledger')
        self.service, self.jobs, self.dispatcher = service, jobs, dispatcher
        self.workflow = jobs.registry.capability('retrieval')
        self.enabled_projects, self.model_profile_ref = enabled_projects, model_profile_ref
        self.chat_entry = chat_entry
        self.sources_provider, self.max_model_calls, self.max_seconds = sources_provider, max_model_calls, max_seconds
        self.tokens = CandidateBrowser(service.repository.path, cursor_secret)

    def _sources(self, actor, project_id):
        bindings = self.sources_provider(actor, project_id)
        if (type(bindings) is not tuple or not 1 <= len(bindings) <= 64
                or any(type(b) is not IndexBinding or b not in self.workflow.compiler.capabilities for b in bindings)
                or len(set(bindings)) != len(bindings)):
            raise ServiceError('AGENT_SOURCES_UNAVAILABLE', 'Authorized released text sources required', 503)
        return bindings

    def profile(self, actor, project_id):
        self.service.projects.require_access(actor, project_id, 'default', 'read')
        result = {'schema_version': 'retrieval-agent-window@1', 'enabled': project_id in self.enabled_projects,
                  'chat_entry': self.chat_entry}
        if not result['enabled']: return result
        sources = self._sources(actor, project_id)
        worker_ready = (self.dispatcher._thread is not None and self.dispatcher._thread.is_alive()
                        and not self.dispatcher._closed.is_set())
        page_size = min(self.workflow.batch_size,
            min(self.workflow.compiler.capabilities[b].max_batch_size for b in sources))
        result.update(available=worker_ready, output_kind='unverified_candidates', max_pages=self.workflow.max_pages,
            scope_ref=digest((actor.tenant_id, actor.user_id, project_id)),
            page_size=page_size, max_candidates=page_size*self.workflow.max_pages,
            library_ids=sorted({b.library_id for b in sources}), model_profile_ref=self.model_profile_ref,
            max_model_calls=self.max_model_calls, max_seconds=self.max_seconds,
            actions=['rewrite_query', 'switch_source']+(['paginate'] if self.workflow.max_pages > 1 else []), methods=['text', 'vector'])
        confirmed = getattr(self, 'confirmed_terms', None)
        if confirmed is not None:
            result['confirmed_terms_execution'] = confirmed.policy(actor, project_id)
            if result['confirmed_terms_execution'].get('candidate_intake') is not None:
                result['candidate_review_execution'] = confirmed.policy(actor, project_id, candidate_intake='manual')
        coordinator = getattr(self, 'coordinator', None)
        if coordinator is not None:
            result['coordinator'] = {'enabled': True, 'workflow_ref': coordinator.contract_ref,
                'max_model_calls': coordinator.policy.model_calls, 'max_seconds': coordinator.policy.max_seconds}
        if not worker_ready: result['reason_code'] = 'AGENT_WORKER_UNAVAILABLE'
        return result

    def require(self, actor, project_id, proposal, *, task=None):
        profile = self.profile(actor, project_id)
        if not profile['enabled']:
            raise ServiceError('AGENT_EXECUTION_UNAVAILABLE', 'Agent retrieval is not enabled for this project', 422)
        if not profile['available']:
            raise ServiceError('AGENT_WORKER_UNAVAILABLE', 'Persistent Agent dispatcher is unavailable', 503)
        spec = proposal['query_spec']; req = spec.get('agent_requirements', {})
        autonomy = req.get('autonomy', {})
        if (proposal['dimension_id'] != 'default' or spec['media_type'] != 'image'
                or req.get('schema_version') != 'agent-requirements@2' or req.get('processing') != []
                or spec.get('grouped_expansion') or spec.get('query_input') or spec.get('source_constraints')
                or spec.get('rules') or spec.get('cleaning_conditions')
                or spec['cross_batch_dedup']['scope'] != 'none'
                or spec['refill_policy']['mode'] != 'input_only'
                or any(mode != 'manual' for mode in proposal['node_modes'].values())
                or not 1 <= spec['target_count'] <= profile['max_candidates'] or len(spec['raw_request']) > 2000
                or spec['target_count'] > profile['page_size'] and 'paginate' not in autonomy.get('actions', ())
                or autonomy.get('max_rounds') != 1 or autonomy.get('max_no_progress_rounds') != 1
                or not set(autonomy.get('actions', ())) <= set(profile['actions'])
                or not set(autonomy.get('methods', ())) <= set(profile['methods'])
                or not 1 <= autonomy.get('max_calls', 0) <= self.max_model_calls
                or not 1 <= autonomy.get('max_seconds', 0) <= self.max_seconds
                or not autonomy.get('library_ids')
                or not set(autonomy['library_ids']) <= set(profile['library_ids'])):
            raise ServiceError('AGENT_SCOPE_UNSUPPORTED',
                'Only bounded image/text candidate retrieval is admitted; explicit pagination scope is required for multiple pages', 422)
        runner = self.workflow.planning._runner(actor)
        if task is not None:
            runtime = self.workflow.planning._runtime(actor, task, 'retrieval', runner)
        else:
            runtime = runner.routes.get('retrieval')
            if runtime is None: raise ServiceError('AGENT_ROLE_UNAVAILABLE', 'Retrieval role is unavailable', 503)
            runtime = replace(runtime, required_capabilities=runtime.required_capabilities | {'model_call_admission'})
        runner.model_binding(self.model_profile_ref)
        runner.registry.resolve(runtime)  # installation/capability probe only; no model calls

    def ensure_job(self, actor, task_id, key):
        require_key(key)
        task = self.service.repository.get(actor, task_id)
        if not agent_task(task): raise ServiceError('AGENT_REQUIREMENTS_REQUIRED', 'Explicit Agent request required', 409)
        ref = self.jobs.links.first_in_scope(actor, task_id)
        if ref: return self.jobs.get(actor, task_id, ref)
        proposal = json.loads(task.confirmed_proposal_json)
        self.require(actor, task.project_id, proposal, task=task)
        req = proposal['query_spec']['agent_requirements']
        body = {'capability': 'retrieval', 'planning': {'model_profile_ref': self.model_profile_ref,
            'model_calls': req['autonomy']['max_calls'], 'expected_revision': task.confirmed_revision,
            'expected_generation': task.run_generation, 'expected_task_version': task.task_version}}
        stable_key = 'window_' + digest((task_id, task.confirmed_revision, task.run_generation))[7:]
        return self.jobs.prepare(actor, task_id, stable_key, body)[0]

    def _current(self, actor, task_id):
        task = self.service.repository.get(actor, task_id)
        self.service.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
        if not agent_task(task): raise ServiceError('AGENT_REQUIREMENTS_REQUIRED', 'Explicit Agent request required', 409)
        ref = self.jobs.links.first_in_scope(actor, task_id)
        view = self.jobs.get(actor, task_id, ref) if ref else None
        snapshot = self.jobs.links.get(actor, task_id, ref) if ref else None
        return task, view, snapshot

    def workspace(self, actor, task_id):
        task, job, snapshot = self._current(actor, task_id)
        operation = snapshot['operation'] if snapshot else None
        raw = sum(len(op.page.hits) for op in snapshot['operations'] if op.page is not None) if snapshot else 0
        status = job['status'] if job else 'ready'
        text = ({'waiting_confirmation': '等待发起人确认', 'ready': '等待启动', 'queued': '排队中',
                 'running': '检索进行中', 'completed': '候选清单已交付，未清洗', 'failed': '检索未完成',
                 'unknown': '执行结果待核对，不自动重试', 'cancelled': '已取消，旧结果不能发布'})[status]
        nodes = [{'node': name, 'label': label, 'node_status': 'pending', 'gate_status': 'blocked',
                  'display_status': '本次不执行', 'detail': '独立检索仅交付未经清洗的候选。'}
                 for name, label in [('expansion', '检索规划'), ('retrieval', '检索候选'),
                                     ('cleaning', '清洗'), ('refill', '补数'), ('packaging', '打包')]]
        for node in nodes[:2]:
            node.update(display_status=text, detail=text, node_status='running' if status in ('running', 'queued')
                else 'succeeded' if status == 'completed' else 'failed' if status in ('failed', 'unknown')
                else 'cancelled' if status == 'cancelled' else 'pending')
        progress = {'task_id': task_id, 'revision': task.confirmed_revision, 'run_generation': task.run_generation,
            'task_version': task.task_version, 'nodes': nodes,
            'counts': {'requested': task.target_count, 'returned': raw, 'accepted': None, 'gap': None}}
        binding = {'task_id': task_id, 'revision': task.confirmed_revision,
                   'generation': task.run_generation, 'task_version': task.task_version}
        return {**task.to_summary(), 'raw_request': task.raw_request, 'target_count': task.target_count,
                'agent_retrieval': True, 'agent_job': job, 'progress': progress,
                'workflow': {'binding': binding, 'summary': text, 'nodes': nodes, 'available_actions': []},
                'candidate_only': True, 'candidate_count': raw, 'accepted_count': None}

    def _proposals(self, actor, task_id):
        task, job, snapshot = self._current(actor, task_id)
        if job is None or job['status'] == 'cancelled':
            raise ServiceError('AGENT_JOB_NOT_READY', 'Current candidate source unavailable', 409)
        proposals = self.jobs.links.candidate_proposals(actor, task_id, job['job_ref'])
        return task, job, snapshot, proposals

    @staticmethod
    def _binding(actor, task, job, proposal_digest):
        return [actor.tenant_id, actor.user_id, task.project_id, task.task_id, task.confirmed_revision,
                task.run_generation, task.task_version, job['job_ref'], proposal_digest]

    @staticmethod
    def _source_digest(snapshot, proposals):
        if snapshot['links']['schema_version'] == 'agent-job-links@2':
            return collection_review(snapshot, proposals)['proposal_digest']
        return proposals[0].proposal_digest

    def candidates(self, actor, task_id, *, limit=200, cursor=None, query='', changes=False):
        if (type(limit) is not int or not 1 <= limit <= 200 or type(changes) is not bool
                or not isinstance(query, str) or len(query) > 1000):
            raise invalid('bounded candidate page required')
        task, job, snapshot, proposals = self._proposals(actor, task_id)
        if not proposals:
            if cursor: raise ServiceError('CANDIDATE_VERSION_CHANGED', 'Candidate source changed', 409)
            return {'task_id': task_id, 'items': [], 'raw_hit_count': 0, 'returned': 0, 'verified_count': 0,
                    'next_cursor': None, 'changes_cursor': None, 'query_terms': [], 'accepted_count': None}
        binding = self._binding(actor, task, job, self._source_digest(snapshot, proposals))
        offset = 0
        if cursor:
            saved = self.tokens.decode(cursor)
            if saved.get('kind') != 'agent-page' or saved.get('binding') != binding or saved.get('query') != query:
                raise ServiceError('CANDIDATE_VERSION_CHANGED', 'Candidate scope changed', 409)
            offset = saved.get('offset')
            if type(offset) is not int or offset < 0: raise invalid('invalid candidate cursor')
        merged = merge_manifest_items(proposals)
        all_rows = [(item, hit) for item in merged for hit in item.hits]
        terms = job['query_terms']
        rows = list(enumerate(all_rows, 1)) if not query or query in terms or query == task.raw_request else []
        selected = rows[offset:offset + limit]
        version = proposals[0].plan_digest
        items = []
        for sequence, (item, hit) in selected:
            preview = self.tokens.token({'kind': 'agent-preview', 'binding': binding, 'candidate_id': item.candidate_id})
            items.append({'candidate_id': item.candidate_id, 'asset_version': item.media.asset_version,
                'content_digest': item.media.content_digest, 'media_unit_id': item.media.media_unit_id,
                'status': 'resolved', 'preview_status': 'available', 'preview_ref': preview,
                'plan_id': snapshot['approved'].request.plan_id, 'branch_id': snapshot['approved'].request.plan_id,
                'query_text': terms[0], 'query_role': 'primary', 'source_term': terms[0],
                'library_id': hit.binding.library_id, 'binding_id': hit.binding.binding_id,
                'index_version': hit.binding.index_version, 'metric': hit.binding.metric,
                'vector_id': hit.vector_id, 'score': hit.score, 'hit_sequence': sequence, 'search_version': version})
        next_offset = offset + len(selected)
        token = self.tokens.token({'kind': 'agent-page', 'binding': binding, 'query': query, 'offset': next_offset})
        return {'schema_version': 'agent-window-candidates@1', 'task_id': task_id, 'items': items,
            'raw_hit_count': len(all_rows), 'returned': len(items), 'verified_count': len(merged),
            'resolved_count': len(merged), 'accepted_count': None, 'search_version': version,
            'as_of_sequence': len(all_rows), 'next_cursor': token if next_offset < len(rows) else None,
            'changes_cursor': token, 'query_terms': terms, 'truncated': next_offset < len(rows),
            'note': '仅为授权候选，尚未执行清洗规则；不计入有效数量。'}

    def candidate_preview(self, actor, task_id, candidate_id, *, ref=None):
        task, job, snapshot, proposals = self._proposals(actor, task_id)
        saved = self.tokens.decode(ref)
        if (not proposals or saved.get('kind') != 'agent-preview'
                or saved.get('binding') != self._binding(actor, task, job, self._source_digest(snapshot, proposals))
                or saved.get('candidate_id') != candidate_id):
            raise ServiceError('CANDIDATE_VERSION_CHANGED', 'Candidate preview source changed', 409)
        item = next((i for i in merge_manifest_items(proposals) if i.candidate_id == candidate_id), None)
        if item is None: raise ServiceError('NOT_FOUND', 'Candidate not found', 404)
        materializer = self.workflow.materializer
        context = AccessContext(actor, task_id, 'candidate_preview', 'current')
        materializer.authorization.require_access(context, item.media.authorized_media_ref, 'read')
        with materializer.gateway.open_read(context, item.media.authorized_media_ref, max_bytes=10_000_000) as stream:
            content = stream.read(10_000_001)
        if type(content) is not bytes or len(content) > 10_000_000 or 'sha256:' + sha256(content).hexdigest() != item.media.content_digest:
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Candidate content changed', 409)
        materializer.authorization.require_access(context, item.media.authorized_media_ref, 'read')
        latest_task, latest_job, latest_snapshot, latest = self._proposals(actor, task_id)
        if not latest or self._binding(actor, latest_task, latest_job, self._source_digest(latest_snapshot, latest)) != saved['binding']:
            raise ServiceError('CANDIDATE_VERSION_CHANGED', 'Candidate scope changed during read', 409)
        mime = 'image/png' if content.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if content.startswith(b'\xff\xd8\xff') else None
        if mime is None: raise ServiceError('PREVIEW_UNAVAILABLE', 'Image preview unavailable', 404)
        return mime, content
