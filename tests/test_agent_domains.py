from dataclasses import asdict, dataclass, replace
import json
import time
import unittest
from pathlib import Path
import sys

import test_harness_port as fixtures
from dsh_retrieval.adapters.harness.local import LocalHarnessAdapter
from dsh_retrieval.adapters.harness.process import ProcessHarnessAdapter
from dsh_retrieval.adapters.harness.composition import build_planning_runner
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.harness.agents import AgentDomain, AgentDomainRegistry, build_registered_agent_runner
from dsh_retrieval.harness.codec import dump_invocation, load_invocation, StageCodecRegistry
from dsh_retrieval.harness.contracts import AdapterRegistry, RoleDefinition, RuntimeRelease, HarnessInvocation
from dsh_retrieval.harness.stage_tools import StageAuthority
from dsh_retrieval.harness.tools import ToolCatalog, ToolDefinition
from dsh_retrieval.models import Actor
from dsh_retrieval.query_spec import digest


@dataclass(frozen=True)
class ProductionStage:
    task_ref: str
    role: str
    input_digest: str
    model_binding_digest: str
    revision: int = 1

    @property
    def stage_digest(self): return digest(asdict(self))


@dataclass(frozen=True)
class TrainingStage(ProductionStage):
    pass


class AgentDomainTest(unittest.TestCase):
    def plugin(self, role='data_production', stage_type=ProductionStage):
        context = {'dataset_ref': 'synthetic-dataset@1', 'task_ref': 'synthetic-task'}
        stage = stage_type('synthetic-task', role, digest(context), digest({'model': 1}))
        tool_id = 'submit_' + role + '_plan'
        definition = RoleDefinition(role + '@1', role, 'Plan using registered tools.', (tool_id,), role + '-plan@1')
        catalog = ToolCatalog()
        catalog.register(ToolDefinition(tool_id, '1', frozenset({role}), 'proposal',
            json.dumps({'type': 'object', 'properties': {'task_ref': {'type': 'string'}},
                        'required': ['task_ref'], 'additionalProperties': False}), 'Submit plan'),
            lambda inv, args: {'proposal_digest': digest(args), 'execution_allowed': False})
        calls = []
        def guard(inv):
            calls.append(inv.stage)
            if inv.stage != stage:
                raise ServiceError('REVISION_CONFLICT', 'Synthetic current task changed', 409)
        domain = AgentDomain(role + '-domain@1', role + '-task@1', stage_type=stage_type,
            roles=(definition,), catalog=catalog, guard=guard)
        def driver(inv, tools, stopped):
            tools.call(tool_id, {'task_ref': inv.stage.task_ref})
        adapter = LocalHarnessAdapter(role + '-implementation', driver, artifact_digest=digest({'code': role}))
        adapters = AdapterRegistry(); adapters.register(adapter.adapter_id, adapter)
        release = RuntimeRelease(role + '-runtime@1', adapter.adapter_id, adapter.artifact_digest)
        runner = build_registered_agent_runner(domain=domain, routes={role: release.release_ref},
            releases={release.release_ref: release}, adapters=adapters, model_binding=lambda _: stage.model_binding_digest)
        return domain, runner, stage, context, calls, adapters, release

    def start(self, runner, stage, context, **overrides):
        kwargs = dict(run_id='run', attempt_id='attempt', model_profile_ref='model', task_kind='plan',
            deadline=time.time()+5, context_json=json.dumps(context))
        kwargs.update(overrides)
        handle = runner.start(stage, **kwargs)
        self.addCleanup(lambda: runner.close(handle))
        return handle

    def finish(self, runner, handle):
        stop = time.monotonic()+4
        while time.monotonic()<stop:
            events = runner.poll(handle)
            if events and events[-1].kind in ('finished','failed','cancelled'):
                self.assertEqual(events[-1].kind,'finished',events)
                return runner.outputs(handle)
            time.sleep(.005)
        self.fail('Agent fixture did not finish')

    def test_production_and_training_plugins_use_the_existing_runtime_ports(self):
        for role, stage_type in (('data_production',ProductionStage),('training',TrainingStage)):
            with self.subTest(role=role):
                domain,runner,stage,context,calls,*_ = self.plugin(role,stage_type)
                registry = AgentDomainRegistry(); registry.register(domain)
                self.assertIs(registry.resolve(domain.domain_ref), domain)
                output = self.finish(runner, self.start(runner,stage,context))
                self.assertFalse(output[0]['receipt']['execution_allowed'])
                self.assertTrue(calls)

    def test_role_registration_does_not_allow_foreign_tasks_or_execution(self):
        domain,runner,stage,context,*_ = self.plugin('training',TrainingStage)
        foreign = ProductionStage(stage.task_ref,stage.role,stage.input_digest,stage.model_binding_digest)
        with self.assertRaises(ServiceError) as error: self.start(runner,foreign,context)
        self.assertEqual(error.exception.code,'AGENT_DOMAIN_MISMATCH')
        with self.assertRaises(ServiceError): self.start(runner,stage,context,task_kind='execute')

    def test_training_cannot_use_the_retrieval_journal_or_codec(self):
        domain,runner,stage,context,calls,adapters,release = self.plugin('training',TrainingStage)
        journal = type('RetrievalJournal',(),{'task_contract_ref':'retrieval-cleaning-stage@1'})()
        with self.assertRaises(ServiceError) as error:
            build_registered_agent_runner(domain=domain,routes={'training':release.release_ref},
                releases={release.release_ref:release},adapters=adapters,model_binding=lambda _:stage.model_binding_digest,
                journal=journal,actor=Actor('tenant','owner'))
        self.assertEqual(error.exception.code,'AGENT_JOURNAL_DOMAIN_MISMATCH')
        invocation = HarnessInvocation(stage,'run','attempt',domain.roles['training'],release,'model','plan',
            time.time()+5,json.dumps(context))
        with self.assertRaises(ServiceError): dump_invocation(invocation)

    def test_unknown_duplicate_domains_and_incomplete_routes_reject(self):
        domain,runner,stage,context,calls,adapters,release = self.plugin()
        registry=AgentDomainRegistry();registry.register(domain)
        with self.assertRaises(ServiceError): registry.register(domain)
        with self.assertRaises(ServiceError): registry.resolve('not-installed@1')
        for routes in ({},{'data_production':'missing'},{'data_production':release.release_ref,'training':release.release_ref}):
            with self.assertRaises(ServiceError):
                build_registered_agent_runner(domain=domain,routes=routes,releases={release.release_ref:release},
                    adapters=adapters,model_binding=lambda _:stage.model_binding_digest)

    def test_domain_tool_isolation_and_execution_gate(self):
        domain,*_ = self.plugin()
        role=RoleDefinition('bad@1','training','Plan.',('submit_data_production_plan',),'training-plan@1')
        with self.assertRaises(ServiceError):
            AgentDomain('bad-domain@1','training-task@1',stage_type=TrainingStage,roles=(role,),
                catalog=domain.catalog,guard=lambda _:None)
        catalog=ToolCatalog();catalog.register(ToolDefinition('train','1',frozenset({'training'}),'execute',
            json.dumps({'type':'object'}),'Train'),lambda *args:self.fail('must not execute'))
        role=replace(role,tool_ids=('train',))
        with self.assertRaises(ServiceError):
            AgentDomain('bad-domain@1','training-task@1',stage_type=TrainingStage,roles=(role,),catalog=catalog,guard=lambda _:None)

    def cleaning_runner(self, *, wrong_digest=False, missing=False, extra=None):
        f=fixtures.HarnessPortTest();f.setUp()
        stage=replace(f.stage,role='cleaning');f.current=stage
        def authority(inv):
            return StageAuthority(f.actor,f.task,f.current,f.spec,5,(f.binding,),(f.cap,),('facts-1',))
        def driver(inv,tools,stop):
            tools.call('get_stage_context',{})
            tools.call('submit_processing_plan',{'proposal':{'steps':[{'requirement_id':'watermark-tag','step_id':'tag'}]}})
        adapter=LocalHarnessAdapter('independent-cleaner',driver,artifact_digest=digest({'implementation':'custom-agent@1'}))
        item={'provider':'registered','adapter_id':adapter.adapter_id,
              'artifact_digest':digest({'wrong':1}) if wrong_digest else adapter.artifact_digest}
        if extra:item.update(extra)
        config={'schema_version':'harness-routes@2','releases':{'custom-agent@1':item},
                'roles':{'retrieval':'custom-agent@1','cleaning':'custom-agent@1'}}
        runner=build_planning_runner(config,authority=authority,model_environment=lambda _: {},
            model_binding=lambda _:stage.model_binding_digest,
            installed_adapters={} if missing else {adapter.adapter_id:adapter})
        return f,runner,stage,config,adapter

    def test_independent_cleaning_agent_uses_the_existing_business_tools(self):
        f,runner,stage,*_=self.cleaning_runner()
        output=self.finish(runner,self.start(runner,stage,f.context))
        self.assertEqual(output[0]['receipt']['status'],'proposal_validated')
        self.assertEqual(output[0]['tool_id'],'submit_processing_plan')

    def test_independent_agent_registration_digest_and_no_dynamic_import(self):
        with self.assertRaises(ServiceError): self.cleaning_runner(missing=True)
        with self.assertRaises(ServiceError): self.cleaning_runner(extra={'module':'untrusted.module'})
        f,runner,stage,*_=self.cleaning_runner(wrong_digest=True)
        with self.assertRaises(ServiceError) as error:self.start(runner,stage,f.context)
        self.assertEqual(error.exception.code,'HARNESS_CAPABILITY_UNAVAILABLE')

    def test_broad_role_names_do_not_expand_the_retrieval_task_contract(self):
        f=fixtures.HarnessPortTest();f.setUp()
        with self.assertRaises(ServiceError):replace(f.stage,role='training')

    def test_installed_domain_codec_roundtrip_does_not_create_retrieval_scope(self):
        domain,runner,stage,context,calls,adapters,release=self.plugin('training',TrainingStage)
        inv=HarnessInvocation(stage,'run','attempt',domain.roles['training'],release,'model','plan',
            time.time()+5,json.dumps(context))
        codecs=StageCodecRegistry()
        codecs.register(domain.task_contract_ref,TrainingStage,encode=asdict,decode=lambda value:TrainingStage(**value))
        payload=dump_invocation(inv,stage_codecs=codecs)
        restored=load_invocation(payload,stage_codecs=codecs)
        self.assertEqual(restored,inv)
        self.assertIs(type(restored.stage),TrainingStage)
        self.assertFalse(hasattr(restored.stage,'scope'))
        with self.assertRaises(ServiceError):load_invocation(payload)
        changed=json.loads(payload);changed['stage_contract_ref']='uninstalled.module'
        with self.assertRaises(ServiceError):load_invocation(json.dumps(changed),stage_codecs=codecs)

    def test_stage_codec_duplicates_and_wrong_types_are_rejected(self):
        codecs=StageCodecRegistry()
        codecs.register('training@1',TrainingStage,encode=asdict,decode=lambda value:object())
        with self.assertRaises(ServiceError):codecs.decode('training@1',{})
        with self.assertRaises(ServiceError):
            codecs.register('training@2',TrainingStage,encode=asdict,decode=lambda value:TrainingStage(**value))

    def test_independent_process_can_use_a_training_domain_without_retrieval_types(self):
        domain,_,stage,context,*_=self.plugin('training',TrainingStage)
        path=Path(__file__).parent/'fixtures'/'domain_agent_worker.py'
        adapter=ProcessHarnessAdapter('training-process',(sys.executable,str(path)),
            artifact_digest=digest({'worker':path.read_text()}),version='fixture@1')
        adapters=AdapterRegistry();adapters.register(adapter.adapter_id,adapter)
        release=RuntimeRelease('training-process@1',adapter.adapter_id,adapter.artifact_digest)
        runner=build_registered_agent_runner(domain=domain,routes={'training':release.release_ref},
            releases={release.release_ref:release},adapters=adapters,model_binding=lambda _:stage.model_binding_digest)
        outputs=self.finish(runner,self.start(runner,stage,context))
        self.assertEqual(outputs[0]['tool_id'],'submit_training_plan')
        self.assertFalse(outputs[0]['receipt']['execution_allowed'])
