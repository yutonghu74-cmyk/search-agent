from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import unittest
from unittest.mock import patch

import test_agent_jobs as fixtures
from dsh_retrieval.adapters.retrieval.local_vector import LocalVectorRecord
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import Library, AccessContext
from dsh_retrieval.models import Actor
from dsh_retrieval.local_agent_runtime import LocalRetrievalDeployment
from dsh_retrieval.physical_query_preparation import TextQueryCapability
from dsh_retrieval.adapters.harness.composition import build_planning_runner
from dsh_retrieval.agent_jobs import AgentJobDispatcher


class LocalAgentRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.AgentJobsTest();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.allowed=True
        binding=replace(self.f.domain.binding,metric='COSINE');self.f.domain.binding=binding
        library=Library(binding.library_id,'synthetic','image',(binding,))
        self.caps=(TextQueryCapability(binding,'image','filter@1',10),)
        self.fixture=((library,),{},(LocalVectorRecord(binding,'synthetic-vector',(.1,)*binding.vector_dimension,'public',(self.f.media,)),),
            {'filter@1':frozenset({'public'})},10,self.caps,None,{},())
        source=SimpleNamespace(authorize=lambda scope,action,library:self.allowed)
        self.search=SimpleNamespace(mode='LOCAL_CLIP',media_gateway=self.f,cursor_secret=b'synthetic-deployment-secret-32bytes',
            _authority=lambda:self.f,_fixture=lambda:self.fixture,source=source,search_budget=self.f.budget,search_limits=self.f.budget.limits,
            encode_query=lambda text,binding:self.f.encode_query(None,text,binding))
        self.config={'schema_version':'local-retrieval-agent@1','harness':{},'projects':['demo'],
            'model_profile_ref':'model','batch_size':2,'max_pages':3,'max_model_calls':2,'max_seconds':60,
            'workspace':str(self.f.domain.repo.path.parent/'deployment-media')}
        self.profile=SimpleNamespace(profile_id='model',config=SimpleNamespace(model='synthetic',base_url='https://models.example.test/v1',api_key='synthetic-secret'))
        self.runtime=SimpleNamespace(settings=None,select=lambda ref,kind:self.profile)

    def deploy(self):
        with patch('dsh_retrieval.local_agent_runtime.build_agent_planning_service',return_value=self.f.f.app):
            deployment=LocalRetrievalDeployment(self.f.domain.service,self.search,self.runtime,self.config)
        self.addCleanup(deployment.close)
        return deployment

    def test_cli_deployment_wires_existing_ledger_encoder_gateway_and_atomic_candidate_delivery(self):
        deployment=self.deploy();self.f.jobs=deployment.jobs;self.f.dispatch=deployment.worker
        deployment.planning.context_provider=deployment.context
        deployment.planning.runner_factory=lambda actor:build_planning_runner(self.f.f.config,
            authority=lambda inv:deployment.stage_authority(actor,inv),model_environment=lambda ref:{},
            model_binding=lambda ref:self.f.domain.stage.model_binding_digest,
            journal=deployment.planning.journal,actor=actor,installed_adapters=self.f.f.installed)
        self.f.domain.proposal['proposal']['evidence_refs']=[deployment.context(self.f.actor,
            self.f.domain.repo.get(self.f.actor,self.f.task.task_id),'retrieval')['evidence'][0]['ref']]
        # Mapping uses the existing authorized local mapping adapter.
        adapter=SimpleNamespace(resolve_hits=self.f.resolve_hits)
        self.fixture=(*self.fixture[:6],adapter,*self.fixture[7:])
        view=self.f.candidate_review();finished=self.f.confirm(view)
        self.assertEqual(finished['status'],'completed')
        artifacts=deployment.jobs.artifacts(self.f.actor,self.f.task.task_id,view['job_ref'])
        self.assertEqual(artifacts['candidate_count'],1);self.assertIsNone(artifacts['accepted_count'])
        self.assertIs(self.f.domain.service.agent_execution,deployment.window)
        self.assertEqual(deployment.window.profile(self.f.actor,'demo')['chat_entry'], 'conversation')

    def test_sources_are_current_authorized_and_model_secrets_stay_out_of_context(self):
        deployment=self.deploy();task=self.f.domain.repo.get(self.f.actor,self.f.task.task_id)
        self.assertTrue(deployment.sources(self.f.actor,'demo'))
        with self.assertRaises(ServiceError):
            deployment.authorization.require_access(AccessContext(Actor('tenant','other'),task.task_id,
                'retrieval','current'),'library:'+self.f.domain.binding.library_id,'search')
        context=deployment.context(self.f.actor,task,'retrieval')
        self.assertNotIn('synthetic-secret',str(context))
        before=deployment.model_binding(self.f.actor,'model');self.profile.config.api_key='rotated-synthetic'
        self.assertNotEqual(before,deployment.model_binding(self.f.actor,'model'))
        self.allowed=False;self.assertEqual(deployment.sources(self.f.actor,'demo'),())
        with self.assertRaises(ServiceError):deployment.window.profile(self.f.actor,'demo')

    def test_invalid_modes_and_unreleased_fixture_changes_fail_closed(self):
        self.search.mode='LOCAL_EXACT'
        with self.assertRaises(ValueError):self.deploy()
        self.search.mode='LOCAL_CLIP';deployment=self.deploy()
        self.fixture=(*self.fixture[:5],(),*self.fixture[6:])
        with self.assertRaises(ServiceError) as caught:deployment.sources(self.f.actor,'demo')
        self.assertEqual(caught.exception.code,'INDEX_SNAPSHOT_EXPIRED')
