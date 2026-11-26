from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dsh_retrieval.agent_management import AgentManagement, InstalledAgent
from dsh_retrieval.adapters.harness.local import LocalHarnessAdapter
from dsh_retrieval.api import create_handler
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.harness.contracts import AdapterRegistry, RuntimeRelease
from dsh_retrieval.harness.roles import DEFAULT_ROLES
from dsh_retrieval.models import Actor
from dsh_retrieval.query_spec import digest
from dsh_retrieval.service import TaskService, InMemoryTaskRepository
from dsh_retrieval.adapters.projects import LocalProjectAccess


class AgentManagementTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'catalog.sqlite'
        self.actor = Actor('tenant', 'owner')
        self.available = True; self.qualified = True; self.granted = True
        class Adapter(LocalHarnessAdapter):
            def probe(inner):
                return replace(inner.describe(), available=self.available,
                    capabilities=inner.describe().capabilities | {'model_call_admission'})
        adapter = Adapter('installed', lambda *args: None, artifact_digest=digest({'code': 1}))
        self.registry = AdapterRegistry(); self.registry.register('installed', adapter)
        self.item = InstalledAgent('team-search@1', 'Team Search', 'v1', DEFAULT_ROLES['retrieval'],
            RuntimeRelease('runtime@1', 'installed', adapter.artifact_digest), 'connection@1')
        self.second = replace(self.item, implementation_ref='team-search@2', version='v2',
                              runtime=replace(self.item.runtime, release_ref='runtime@2'))
        self.probes = []
        def authorize(actor, project, operation):
            if actor != self.actor or project not in ('demo', 'other'):
                raise ServiceError('ACCESS_DENIED', 'Denied', 403)
        def release_access(actor, project, item):
            if not self.granted or project != 'demo': raise ServiceError('ACCESS_DENIED', 'Release denied', 403)
        def qualify(*args):
            self.probes.append(args[2].implementation_ref)
            return 'evidence@1' if self.qualified else None
        self.options = dict(installed={v.implementation_ref: v for v in (self.item,self.second)},
            roles=DEFAULT_ROLES, adapters=self.registry, authorize=authorize,
            authorize_release=release_access, qualify=qualify)
        self.service = AgentManagement(self.path, **self.options)

    def ready(self, item=None):
        item = item or self.item
        body = {'implementation_ref': item.implementation_ref}
        self.service.register(self.actor, 'demo', 'register-'+item.version, body)
        return self.service.publish(self.actor, 'demo', 'publish-'+item.version, body)

    def bind(self, ref='team-search@1', version=0, key='bind'):
        return self.service.bind(self.actor, 'demo', key,
            {'expected_version': version, 'routes': {'retrieval': ref}})

    def test_restart_replay_and_project_route_switch(self):
        self.ready(); value, replayed = self.bind()
        self.assertFalse(replayed); self.assertEqual(value['version'], 1)
        self.service = AgentManagement(self.path, **self.options)
        self.assertEqual(self.bind(), (value, True))
        task = SimpleNamespace(project_id='demo')
        old = self.service.project_routes(self.actor, task)
        self.ready(self.second); self.bind(self.second.implementation_ref, 1, 'switch')
        self.assertEqual(old, {'retrieval': 'runtime@1'})
        self.assertEqual(self.service.project_routes(self.actor, task), {'retrieval': 'runtime@2'})
        self.assertEqual(len(self.service.discover(self.actor,'demo')['implementations']), 2)
        self.assertEqual(self.service.project_routes(self.actor,SimpleNamespace(project_id='other')), {})

    def test_registration_cannot_install_code_or_publish_itself(self):
        for body in ({'implementation_ref':'unknown'}, {'implementation_ref':self.item.implementation_ref,'endpoint':'https://bad'}):
            with self.assertRaises(ServiceError): self.service.register(self.actor,'demo','bad',body)
        self.service.register(self.actor,'demo','reg',{'implementation_ref':self.item.implementation_ref})
        with self.assertRaises(ServiceError): self.bind()
        self.qualified = False
        with self.assertRaises(ServiceError): self.ready()
        self.assertEqual(self.service.discover(self.actor,'demo')['implementations'][0]['status'],'registered')
        self.qualified = True; self.available = False
        with self.assertRaises(ServiceError): self.ready()
        self.available = True; self.ready()
        with self.assertRaises(ServiceError):
            self.service.bind(self.actor,'demo','wrong-role',{'expected_version':0,'routes':{'cleaning':self.item.implementation_ref}})

    def test_tenant_project_and_release_authorization(self):
        self.ready(); self.bind()
        with self.assertRaises(ServiceError): self.service.discover(Actor('another','owner'),'demo')
        with self.assertRaises(ServiceError):
            self.service.register(self.actor,'other','reg',{'implementation_ref':self.item.implementation_ref})
        with self.assertRaises(ServiceError):
            self.service.bind(self.actor,'other','bind',{'expected_version':0,'routes':{'retrieval':self.item.implementation_ref}})
        self.granted = False
        with self.assertRaises(ServiceError): self.service.project_routes(self.actor,SimpleNamespace(project_id='demo'))

    def test_changed_installed_artifact_fails_without_fallback(self):
        self.ready(); self.bind()
        self.options['installed'] = {self.item.implementation_ref:replace(self.item, version='changed')}
        changed = AgentManagement(self.path, **self.options)
        with self.assertRaises(ServiceError): changed.project_routes(self.actor,SimpleNamespace(project_id='demo'))
        self.assertFalse(changed.discover(self.actor,'demo')['implementations'][0]['installed'])

    def test_idempotency_and_concurrent_binding_compare_and_swap(self):
        self.ready()
        with ThreadPoolExecutor(2) as pool:
            def change(key):
                try: return self.bind(key=key)[0]['version']
                except ServiceError as error: return error.code
            results = list(pool.map(change, ('one','two')))
        self.assertCountEqual(results, [1, 'AGENT_BINDING_CONFLICT'])
        with self.assertRaises(ServiceError) as error:
            self.service.register(self.actor,'demo','register-v1',{'implementation_ref':self.second.implementation_ref})
        self.assertEqual(error.exception.code,'IDEMPOTENCY_CONFLICT')
        self.ready(); self.assertEqual(len(self.probes),1)

    def test_write_permission_and_model_admission_are_required(self):
        body = {'implementation_ref':self.item.implementation_ref}
        self.service.register(self.actor,'demo','reg',body)
        original = self.service.authorize
        def authorize(actor, project, operation):
            original(actor,project,operation)
            if operation != 'read': raise ServiceError('ACCESS_DENIED','Management denied',403)
        self.service.authorize = authorize
        self.assertEqual(len(self.service.discover(self.actor,'demo')['implementations']),1)
        with self.assertRaises(ServiceError): self.service.publish(self.actor,'demo','pub',body)
        self.assertEqual(self.probes,[])
        self.service.authorize = original
        adapter = self.registry._adapters['installed']
        adapter.probe = adapter.describe  # No model admission capability.
        with self.assertRaises(ServiceError): self.service.publish(self.actor,'demo','pub',body)
        self.assertEqual(self.probes,[])

    def test_role_semantics_must_match_installed_runner(self):
        options = dict(self.options)
        options['roles'] = {'retrieval':replace(DEFAULT_ROLES['retrieval'], output_contract='different@1')}
        with self.assertRaises(ServiceError): AgentManagement(self.path, **options)

    def test_http_register_publish_bind_discover_and_denial(self):
        app = TaskService(InMemoryTaskRepository(), LocalProjectAccess())
        server = ThreadingHTTPServer(('127.0.0.1',0),create_handler(app,agent_management=self.service))
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}/v1/retrieval/projects/demo/agent-management'
        def request(suffix='', body=None, user='owner', key='http'):
            headers = {'X-Tenant-Id':'tenant','X-User-Id':user,'Content-Type':'application/json','Idempotency-Key':key}
            req = Request(base+suffix,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            try:
                with urlopen(req) as reply: return reply.status,json.load(reply),reply.headers
            except HTTPError as error: return error.code,json.load(error),error.headers
        body = {'implementation_ref':self.item.implementation_ref}
        self.assertEqual(request('/register',body,key='reg')[0],200)
        self.assertEqual(request('/publish',body,key='pub')[0],200)
        self.assertEqual(request('/bindings',{'expected_version':0,'routes':{'retrieval':self.item.implementation_ref}},key='bind')[0],200)
        self.assertEqual(request()[1]['binding']['version'],1)
        self.assertEqual(request(user='intruder')[0],403)
        self.assertEqual(request('/register',body,key='')[0],400)


class ManagedPlanningTest(unittest.TestCase):
    def test_managed_binding_preserves_queued_runtime_and_checks_runner_digest(self):
        import test_agent_framework as fixtures
        from dsh_retrieval.adapters.harness.composition import build_agent_planning_service
        f = fixtures.AgentFrameworkTest(); f.setUp(); self.addCleanup(f.doCleanups)
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        registry = AdapterRegistry()
        for key, adapter in f.installed.items(): registry.register(key,adapter)
        items = [InstalledAgent(ref, ref, 'v1', DEFAULT_ROLES['retrieval'],
            RuntimeRelease(ref, conf['adapter_id'], conf['artifact_digest']), 'connection@1')
            for ref, conf in f.config['releases'].items()]
        management = AgentManagement(Path(temp.name)/'catalog.sqlite', installed={i.implementation_ref:i for i in items},
            roles=DEFAULT_ROLES, adapters=registry, authorize=lambda a,p,o:f.domain.service.projects.require_access(a,p,'default','read'),
            authorize_release=lambda *args:None, qualify=lambda *args:'fixture-evidence@1')
        for item in items:
            body={'implementation_ref':item.implementation_ref}
            management.register(f.actor,'demo','reg-'+item.implementation_ref,body)
            management.publish(f.actor,'demo','pub-'+item.implementation_ref,body)
        def bind(ref, version):
            management.bind(f.actor,'demo','bind-'+str(version),{'expected_version':version,'routes':{'retrieval':ref}})
        def build():
            app=build_agent_planning_service(f.domain.service,f.config,authority=f.authority,
                context_provider=lambda a,t,r:{'role':r,'snapshot_version':1},
                model_environment=lambda a,r:{}, model_binding=lambda a,r:f.domain.stage.model_binding_digest,
                adapters_provider=lambda a:f.installed,agent_management=management,lease_seconds=1)
            self.addCleanup(app.close)
            return app
        bind('fixture-release',0)
        app=build(); f.f.app=app
        view=f.f.prepare(); f.f.act(view,'confirm')
        runner=app._runner(f.actor)
        dispatch=runner.dispatch; runner.dispatch=lambda *args,**kwargs:False
        f.f.act(view,'start'); runner.dispatch=dispatch
        bind('alternate-release',1)
        restarted=build(); f.f.app=restarted
        f.f.act(view,'start','restart',app=restarted)
        self.assertEqual(f.f.completed(view)['status'],'finished')
        self.assertEqual(f.calls,['fixture-release'])
        self.assertEqual(restarted.capabilities(f.actor,f.task.task_id)['roles'][0]['runtime_release_ref'],'alternate-release')
        pending=f.f.prepare('pending')
        bind('fixture-release',2)
        with self.assertRaises(ServiceError) as error: f.f.act(pending,'confirm')
        self.assertEqual(error.exception.code,'AGENT_STAGE_CONFIGURATION_CHANGED')
        altered = SimpleNamespace(roles=runner.roles, routes=runner.routes, releases=dict(runner.releases))
        altered.releases['fixture-release']=replace(runner.releases['fixture-release'],artifact_digest=digest({'other':1}))
        with self.assertRaises(ServiceError): management.validate_runner(altered)
