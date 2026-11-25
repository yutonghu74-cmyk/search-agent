"""Explicit deployment bridge for the existing LOCAL_CLIP development service."""
from contextlib import nullcontext
from dataclasses import asdict, replace
import json
from pathlib import Path

from .adapters.harness.composition import build_agent_planning_service, build_retrieval_agent_jobs, enable_retrieval_agent_window
from .adapters.retrieval.local_vector import LocalVectorRetrieval
from .adapters.retrieval.simulated import ApprovedFixture
from .adapters.retrieval.materialization import LocalCandidateMaterializer
from .adapters.task_storage.agent_retrieval import SQLiteAgentRetrievalHandoffs
from .adapters.task_storage.candidates import SQLiteCandidateManifestRepository
from .adapters.task_storage.retrieval import SQLiteRetrievalPlanRepository
from .agent_jobs import AgentJobDispatcher
from .agent_retrieval import AgentRetrievalService, AgentRetrievalExecutor
from .errors import ServiceError
from .harness.stage_tools import StageAuthority
from .query_spec import digest
from .text_catalog_config import CatalogConfigScope


class LocalRetrievalDeployment:
    def __init__(self, service, first_search, runtime, configuration, *, cleaning_service=None, cleaning_dispatch=None):
        required = {'schema_version','harness','projects','model_profile_ref','batch_size','max_pages',
                    'max_model_calls','max_seconds','workspace'}
        if (type(configuration) is not dict or not required <= set(configuration)
                or set(configuration) - required - {'chat_entry', 'coordinator'}
                or configuration.get('chat_entry', 'conversation') not in ('conversation', 'candidate')
                or configuration['schema_version'] != 'local-retrieval-agent@1'
                or configuration['projects'] != ['demo'] or not isinstance(configuration['workspace'],str)
                or not Path(configuration['workspace']).is_absolute()
                or first_search.mode != 'LOCAL_CLIP' or getattr(first_search,'media_gateway',None) is None):
            raise ValueError('explicit LOCAL_CLIP deployment with existing authorized media required')
        self.service,self.search,self.runtime = service,first_search,runtime
        self.configuration = configuration
        self.capabilities = first_search._fixture()[5]
        authority = first_search._authority()
        deployment = self
        class Access:
            def require_access(self, context, resource_ref, action):
                # The business ledger validates the task scope before calling this
                # resource port. Reentering its SQLite transaction here can deadlock
                # a concurrent journal writer. This bridge serves demo only; media
                # reads additionally validate their task through the existing gateway.
                service.projects.require_access(context.actor,'demo','default','read')
                purpose = context.purpose
                if purpose in ('materialization','candidate_manifest','candidate_preview'):
                    purpose = 'retrieval' if resource_ref.startswith('library:') else 'cleaning_input'
                authority.require_access(replace(context,purpose=purpose),resource_ref,action)
        self.authorization = Access()
        self.retrieval = SQLiteRetrievalPlanRepository(service.repository.path,
            projects=service.projects,authorization=self.authorization)
        manifests = SQLiteCandidateManifestRepository(service.repository.path,
            projects=service.projects,authorization=self.authorization)
        class Gateway:
            def encode_query(self, context, text, binding):
                deployment.authorization.require_access(context,'library:'+binding.library_id,'search')
                return first_search.encode_query(text,binding)
            def search(self, context, request):
                approved=deployment.retrieval.get_current(context.actor,context.task_id)
                if approved is None or replace(request,cursor=None) != approved.request:
                    raise ServiceError('PLAN_NOT_CURRENT','Current approved Agent request required',409)
                libraries,vectors,records,templates,depth,*_=first_search._fixture()
                adapter=LocalVectorRetrieval(deployment.authorization,libraries=libraries,query_vectors=vectors,
                    approved=(ApprovedFixture(context,approved.request,(),depth),),records=records,
                    filter_templates=templates,cursor_secret=first_search.cursor_secret)
                return adapter.search(context,request)
            def resolve_hits(self, context, hits):
                return first_search._fixture()[6].resolve_hits(replace(context,purpose='retrieval'),hits)
            def open_read(self, context, ref, *, max_bytes):
                deployment.authorization.require_access(context,ref,'read')
                return first_search.media_gateway.open_read(replace(context,purpose='cleaning_input'),ref,max_bytes=max_bytes)
        self.gateway=Gateway()
        self.planning=build_agent_planning_service(service,configuration['harness'],authority=self.stage_authority,
            context_provider=self.context,model_environment=self.model_environment,model_binding=self.model_binding)
        try:
            handoffs=SQLiteAgentRetrievalHandoffs(journal=self.planning.journal,retrieval=self.retrieval,
                authority=lambda inv:self.stage_authority(self._actor(inv),inv),
                context_provider=self.context)
            compiler=AgentRetrievalService(handoffs=handoffs,embeddings=self.gateway,capabilities=self.capabilities)
            executor=AgentRetrievalExecutor(retrieval=self.retrieval,gateway=self.gateway,budget=first_search.search_budget)
            materializer=LocalCandidateMaterializer(ledger=self.retrieval,media=self.gateway,gateway=self.gateway,
                authorization=self.authorization,workspace_root=Path(configuration['workspace']))
            self.jobs=build_retrieval_agent_jobs(planning=self.planning,compiler=compiler,executor=executor,
                materializer=materializer,manifests=manifests,batch_size=configuration['batch_size'],max_pages=configuration['max_pages'])
            from .confirmed_terms_agent import ConfirmedTermsLinks, ConfirmedTermsRetrievalAgent, RoutedJobLinks
            confirmed_links = ConfirmedTermsLinks(self.planning.journal)
            self.confirmed_terms = ConfirmedTermsRetrievalAgent(service, first_search, confirmed_links, self.sources)
            if cleaning_service is not None or cleaning_dispatch is not None:
                self.confirmed_terms.configure_intake(cleaning_service, cleaning_dispatch)
            self.jobs.registry.register(self.confirmed_terms, default=False)
            self.jobs.links = RoutedJobLinks(self.jobs.links, confirmed_links)
            self.worker=AgentJobDispatcher(self.jobs)
            self.confirmed_terms.notify = self.worker.notify
            try:
                self.window=enable_retrieval_agent_window(service,self.jobs,self.worker,
                    enabled_projects=frozenset(configuration['projects']),model_profile_ref=configuration['model_profile_ref'],
                    sources_provider=self.sources,cursor_secret=first_search.cursor_secret,
                    chat_entry=configuration.get('chat_entry', 'conversation'),
                    max_model_calls=configuration['max_model_calls'],max_seconds=configuration['max_seconds'])
                self.window.confirmed_terms = self.confirmed_terms
            except Exception:
                self.worker.close();raise
        except Exception:
            self.planning.close();raise

    @staticmethod
    def _actor(inv):
        from .models import Actor
        return Actor(inv.stage.scope.tenant_id,inv.stage.scope.owner_user_id)

    def sources(self, actor, project_id):
        self.service.projects.require_access(actor,project_id,'default','read')
        if project_id not in self.configuration['projects']:
            raise ServiceError('ACCESS_DENIED','Project not enabled for local retrieval',403)
        capabilities=self.search._fixture()[5]
        if capabilities != self.capabilities:
            raise ServiceError('INDEX_SNAPSHOT_EXPIRED','Released source configuration changed; restart and review',409)
        scope=CatalogConfigScope(actor.tenant_id,actor.user_id,project_id)
        return tuple(c.binding for c in capabilities if self.search.source.authorize(scope,'search',c.binding.library_id))

    def context(self, actor, task, role):
        spec=json.loads(task.confirmed_proposal_json)['query_spec']
        allowed=spec['agent_requirements']['autonomy']['library_ids']
        bindings=tuple(b for b in self.sources(actor,task.project_id) if b.library_id in allowed)
        evidence=self.evidence(task,bindings)
        return {'schema_version':'local-retrieval-context@1','query_spec':spec,
            'authorized_bindings':[asdict(b) for b in bindings],
            'evidence':evidence,
            'submission_contract':{'proposal':{'action':'rewrite_query','binding_id':'one authorized binding_id',
                'method':'text','query':'query text based on the request','evidence_refs':[evidence[0]['ref']],
                'reason':'brief strategy reasoning'}},
            'instructions':'先读取上下文，再通过 submit_retrieval_strategy 提交一份策略；仅使用已授权来源。此阶段不执行搜索。'}

    def stage_authority(self, actor, inv):
        task=self.service.repository.get(actor,inv.stage.scope.task_id)
        spec=json.loads(task.confirmed_proposal_json)['query_spec']
        allowed=spec['agent_requirements']['autonomy']['library_ids']
        bindings=tuple(b for b in self.sources(actor,task.project_id) if b.library_id in allowed)
        return StageAuthority(actor,task,inv.stage,spec,spec['agent_requirements']['autonomy']['max_calls'],bindings,
            evidence_refs=tuple(e['ref'] for e in self.evidence(task,bindings)))

    @staticmethod
    def evidence(task, bindings):
        return [{'ref':'request_'+digest(task.confirmed_proposal_json)[7:],'kind':'confirmed_request'}]+[
            {'ref':'source_'+digest(asdict(b))[7:],'kind':'authorized_source','binding_id':b.binding_id} for b in bindings]

    def profile(self, actor, ref):
        if ref != self.configuration['model_profile_ref']:
            raise ServiceError('MODEL_NOT_CONFIGURED','Deployment profile required',422)
        with self.runtime.settings.scope(actor.tenant_id,actor.user_id) if self.runtime.settings else nullcontext():
            return self.runtime.select(ref,'llm')

    def model_binding(self, actor, ref):
        p=self.profile(actor,ref)
        return digest({'profile':p.profile_id,'model':p.config.model,'url':p.config.base_url,
                       'credential_binding':digest(p.config.api_key)})

    def model_environment(self, actor, ref):
        p=self.profile(actor,ref)
        return {'RETRIEVAL_MODEL_KEY':p.config.api_key,'RETRIEVAL_MODEL_URL':p.config.base_url,
                'RETRIEVAL_MODEL_ID':p.config.model,'RETRIEVAL_MAX_TOKENS':'8000'}

    def close(self):
        self.worker.close()
        if getattr(self, 'coordinator', None): self.coordinator.close()
        self.planning.close()


def load_local_retrieval_deployment(path, service, first_search, runtime, *, cleaning_service=None, cleaning_dispatch=None):
    raw=Path(path).read_bytes()
    if len(raw)>1_000_000: raise ValueError('Deployment config exceeds limit')
    return LocalRetrievalDeployment(service,first_search,runtime,json.loads(raw),
        cleaning_service=cleaning_service, cleaning_dispatch=cleaning_dispatch)
