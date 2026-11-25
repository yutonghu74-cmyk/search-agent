"""Independent role deployment and backend project routing over durable stages."""
from dataclasses import replace
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_agent_stage_api as fixtures
from dsh_retrieval.adapters.harness.composition import build_agent_planning_service, build_planning_runner
from dsh_retrieval.adapters.task_storage.agent_retrieval import SQLiteAgentRetrievalHandoffs
from dsh_retrieval.adapters.task_storage.retrieval import SQLiteRetrievalPlanRepository
from dsh_retrieval.agent_retrieval import AgentRetrievalService, AgentRetrievalExecutor
from dsh_retrieval.api import create_handler
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.harness.contracts import RuntimeRelease
from dsh_retrieval.harness.stage_tools import StageAuthority
from dsh_retrieval.models import Actor
from dsh_retrieval.integration_models import QueryVector, SearchHit, SearchPage
from dsh_retrieval.physical_query_preparation import TextQueryCapability
from dsh_retrieval.query_spec import digest
from dsh_retrieval.search_budget import SearchBudget
from dsh_retrieval.search_scheduling import SearchLimits


class AgentFrameworkTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AgentStageApiTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.domain, self.actor, self.task = self.f.domain, self.f.actor, self.f.task
        self.routes = {'demo': {'retrieval': 'fixture-release'}}
        self.calls, self.probes = [], []

        def drive(inv, tools, stopped):
            self.calls.append(inv.runtime.release_ref)
            tools.admit_model_call(1)
            tools.call('submit_retrieval_strategy', self.domain.proposal)

        parent = type(self.f.adapter)
        probes = self.probes
        class Adapter(parent):
            def probe(self):
                probes.append(self.adapter_id)
                return super().probe()
        self.old = Adapter('fixture', drive, artifact_digest=self.domain.release.artifact_digest)
        self.new = Adapter('alternate', drive, artifact_digest=digest({'alternate': 1}))
        self.installed = {'fixture': self.old, 'alternate': self.new}
        self.config = {'schema_version': 'harness-routes@2', 'releases': {
            'fixture-release': {'provider': 'registered', 'adapter_id': 'fixture', 'artifact_digest': self.old.artifact_digest},
            'alternate-release': {'provider': 'registered', 'adapter_id': 'alternate', 'artifact_digest': self.new.artifact_digest}},
            'roles': {'retrieval': 'fixture-release'}}
        self.app = self.build()
        self.f.app = self.app
        self.addCleanup(self.app.close)

    def authority(self, actor, inv):
        task = self.domain.repo.get(actor, inv.stage.scope.task_id)
        return StageAuthority(actor, task, inv.stage,
            json.loads(task.confirmed_proposal_json)['query_spec'], 10, (self.domain.binding,), (), ('facts-1',))

    def build(self):
        return build_agent_planning_service(self.domain.service, self.config,
            authority=self.authority, context_provider=lambda actor, task, role: {'role': role, 'snapshot_version': 1},
            model_environment=lambda actor, ref: {}, model_binding=lambda actor, ref: self.domain.stage.model_binding_digest,
            adapters_provider=lambda actor: self.installed,
            project_routes=lambda actor, task: dict(self.routes[task.project_id]), lease_seconds=1)

    def test_retrieval_only_configuration_never_requires_a_cleaning_adapter(self):
        config = dict(self.config, releases={'fixture-release': self.config['releases']['fixture-release']})
        runner = build_planning_runner(config, authority=lambda inv: self.authority(self.actor, inv),
            model_environment=lambda ref: {}, model_binding=lambda ref: self.domain.stage.model_binding_digest,
            installed_adapters={'fixture': self.old})
        self.assertEqual(set(runner.roles), {'retrieval'})
        self.assertEqual(set(runner.routes), {'retrieval'})
        view = self.f.prepare()
        self.f.act(view, 'confirm'); self.f.act(view, 'start')
        self.assertEqual(self.f.completed(view)['status'], 'finished')
        self.assertEqual(self.calls, ['fixture-release'])
        body = dict(self.f.body, role='cleaning')
        with self.assertRaises(ServiceError) as error:
            self.app.prepare(self.actor, self.task.task_id, 'cleaning', body)
        self.assertEqual(error.exception.code, 'AGENT_ROLE_UNAVAILABLE')

    def test_discovery_is_authorized_read_only_and_does_not_probe_or_create_runs(self):
        view = self.app.capabilities(self.actor, self.task.task_id)
        self.assertEqual([r['role'] for r in view['roles']], ['retrieval'])
        self.assertEqual(view['roles'][0]['availability'], 'configured_not_probed')
        self.assertFalse(view['roles'][0]['execution_allowed'])
        for field in ('context_json', 'system_prompt', 'artifact_digest', 'python', 'home'):
            self.assertNotIn(field, json.dumps(view))
        self.assertEqual(self.probes, [])
        self.assertEqual(self.calls, [])
        with self.domain.journal._tx() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM harness_stage_drafts').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM harness_attempts').fetchone()[0], 0)
        with self.assertRaises(ServiceError):
            self.app.capabilities(Actor('tenant', 'other'), self.task.task_id)

    def test_binding_change_invalidates_unstarted_review_and_new_requests_use_new_release(self):
        view = self.f.prepare()
        self.routes['demo']['retrieval'] = 'alternate-release'
        with self.assertRaises(ServiceError) as error:
            self.f.act(view, 'confirm')
        self.assertEqual(error.exception.code, 'AGENT_STAGE_CONFIGURATION_CHANGED')
        view = self.f.prepare('new-review')
        self.assertEqual(view['runtime_release_ref'], 'alternate-release')
        self.f.act(view, 'confirm'); self.f.act(view, 'start')
        self.assertEqual(self.f.completed(view)['status'], 'finished')
        self.assertEqual(self.calls, ['alternate-release'])

    def test_queued_attempt_keeps_persisted_release_across_binding_change_and_restart(self):
        view = self.f.prepare()
        self.f.act(view, 'confirm')
        runner = self.app._runner(self.actor)
        dispatch = runner.dispatch
        runner.dispatch = lambda *args, **kwargs: False
        self.f.act(view, 'start')
        runner.dispatch = dispatch
        self.assertEqual(self.calls, [])
        self.routes['demo']['retrieval'] = 'alternate-release'
        restarted = self.build(); self.addCleanup(restarted.close)
        self.f.app = restarted
        self.f.act(view, 'start', 'restart', app=restarted)
        self.assertEqual(self.f.completed(view)['status'], 'finished')
        self.assertEqual(self.calls, ['fixture-release'])
        self.assertEqual(restarted.capabilities(self.actor, self.task.task_id)['roles'][0]['runtime_release_ref'], 'alternate-release')

    def test_disabled_or_unknown_project_routes_do_not_fall_back(self):
        self.routes['demo'] = {}
        self.assertEqual(self.app.capabilities(self.actor, self.task.task_id)['roles'], [])
        with self.assertRaises(ServiceError) as error: self.f.prepare()
        self.assertEqual(error.exception.code, 'AGENT_ROLE_UNAVAILABLE')
        for routes in ({'training': 'fixture-release'}, {'cleaning': 'fixture-release'}, {'retrieval': 'uninstalled'}):
            self.routes['demo'] = routes
            with self.assertRaises(ServiceError): self.f.prepare()
        self.assertEqual(self.calls, [])

    def test_same_actor_uses_each_tasks_authorized_project_binding(self):
        projects = self.domain.service.projects
        class Projects:
            def require_access(_, actor, project_id, dimension_id, action):
                if project_id == 'second': return None
                return projects.require_access(actor, project_id, dimension_id, action)
        self.domain.service.projects = Projects()
        second = replace(self.task, task_id='second-task', project_id='second')
        self.domain.repo.create(self.actor, 'second-task', digest({'second': 1}), second)
        self.routes['second'] = {'retrieval': 'alternate-release'}
        app = self.build(); self.addCleanup(app.close)
        first_view = app.capabilities(self.actor, self.task.task_id)
        second_view = app.capabilities(self.actor, second.task_id)
        self.assertEqual(first_view['roles'][0]['runtime_release_ref'], 'fixture-release')
        self.assertEqual(second_view['roles'][0]['runtime_release_ref'], 'alternate-release')
        view, _ = app.prepare(self.actor, second.task_id, 'second-prepare', self.f.body)
        self.assertEqual(view['runtime_release_ref'], 'alternate-release')
        with self.assertRaises(ServiceError): app.capabilities(Actor('different-tenant', 'owner'), second.task_id)

    def test_old_schema_keeps_full_role_contract_and_invalid_routes_are_rejected(self):
        for config in (dict(self.config, schema_version='harness-routes@1'),
                       dict(self.config, roles={}), dict(self.config, roles={'training': 'fixture-release'})):
            with self.assertRaises(ServiceError):
                build_planning_runner(config, authority=lambda inv: self.authority(self.actor, inv),
                    model_environment=lambda ref: {}, model_binding=lambda ref: self.domain.stage.model_binding_digest,
                    installed_adapters=self.installed)
        with self.assertRaises(ServiceError):
            RuntimeRelease('bad', 'fixture', 'not-a-digest')

    def test_cleaning_can_be_composed_separately_without_enabling_retrieval(self):
        config = dict(self.config, roles={'cleaning': 'fixture-release'},
            releases={'fixture-release': self.config['releases']['fixture-release']})
        runner = build_planning_runner(config, authority=lambda inv: self.authority(self.actor, inv),
            model_environment=lambda ref: {}, model_binding=lambda ref: self.domain.stage.model_binding_digest,
            installed_adapters={'fixture': self.old})
        self.assertEqual(set(runner.roles), {'cleaning'})
        self.assertEqual(set(runner.routes), {'cleaning'})
        self.assertEqual(self.calls, [])

    def test_retrieval_only_intent_plans_and_executes_without_a_cleaning_request(self):
        self.config['roles']['cleaning'] = 'fixture-release'
        def retrieval_only(task):
            proposal = json.loads(task.confirmed_proposal_json)
            proposal['query_spec']['agent_requirements'].update(schema_version='agent-requirements@2', processing=[])
            task.confirmed_proposal_json = json.dumps(proposal)
        # Trusted fixture, not a public bypass of the formal TaskService gate.
        self.domain.mutate_task(retrieval_only)
        self.assertEqual([r['role'] for r in self.app.capabilities(self.actor, self.task.task_id)['roles']], ['retrieval'])
        with self.assertRaises(ServiceError) as error:
            self.app.prepare(self.actor, self.task.task_id, 'no-cleaning-request', dict(self.f.body, role='cleaning'))
        self.assertEqual(error.exception.code, 'AGENT_ROLE_UNAVAILABLE')
        view = self.f.prepare(); self.f.act(view, 'confirm'); self.f.act(view, 'start')
        self.assertEqual(self.f.completed(view)['status'], 'finished')
        row, review = self.app.store.get(self.actor, self.task.task_id, view['stage_draft_id'])
        handle = self.app.journal.handle(self.app.store.invocation(row, review))
        class Authorization:
            def require_access(_, *args): pass
        retrieval = SQLiteRetrievalPlanRepository(self.domain.repo.path,
            projects=self.domain.service.projects, authorization=Authorization())
        handoffs = SQLiteAgentRetrievalHandoffs(journal=self.app.journal, retrieval=retrieval,
            authority=lambda inv: self.authority(self.actor, inv),
            context_provider=lambda actor, task, role: {'role': role, 'snapshot_version': 1})
        class Embeddings:
            def encode_query(_, context, query, binding):
                return QueryVector(binding.encoder_ref, (.1,) * binding.vector_dimension)
        compiler = AgentRetrievalService(handoffs=handoffs, embeddings=Embeddings(),
            capabilities=(TextQueryCapability(self.domain.binding, 'image', 'filter@1', 10),))
        plan = compiler.prepare(self.actor, handle, epoch=1, batch_size=2)
        approved, _ = retrieval.confirm(self.actor, self.task.task_id, plan.request.plan_id, key='search-confirm',
            action='confirm', proposal_digest=plan.proposal_digest, expected_task_version=plan.scope.task_version)
        calls = []
        class Gateway:
            def search(_, context, request):
                calls.append(request)
                return SearchPage((SearchHit(request.binding, 'synthetic-hit', .9),), None, 'exhausted', 'snapshot', 'snapshot')
        executor = AgentRetrievalExecutor(retrieval=retrieval, gateway=Gateway(),
            budget=SearchBudget(self.domain.repo.path, SearchLimits(max_calls=2, max_hit_slots=4)))
        result = executor.execute_page(self.actor, self.task.task_id,
            plan_id=approved.request.plan_id, proposal_digest=approved.proposal_digest)
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(result['operation'].page.hits), 1)
        self.assertEqual(self.calls, ['fixture-release'])
        self.assertIsNone(self.domain.repo.get(self.actor, self.task.task_id).active_cleaning_operation_id)

    def test_http_discovery_does_not_accept_writes_query_claims_or_foreign_owners(self):
        handler = create_handler(self.domain.service, agent_planning=self.app)
        self.addCleanup(handler.task_commands.store.connection.close)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(timeout=2)))
        base = f'http://127.0.0.1:{server.server_port}/v1/retrieval/tasks/{self.task.task_id}/agent-capabilities'
        def call(suffix='', method='GET', user='owner'):
            request = Request(base + suffix, method=method,
                headers={'X-Tenant-Id': 'tenant', 'X-User-Id': user})
            try:
                with urlopen(request, timeout=3) as response:
                    return response.status, json.load(response)
            except HTTPError as response:
                return response.code, json.load(response)
        code, body = call()
        self.assertEqual(code, 200, body)
        self.assertEqual([item['role'] for item in body['roles']], ['retrieval'])
        self.assertEqual(call('?project_id=other')[0], 422)
        self.assertEqual(call(method='POST')[0], 404)
        self.assertEqual(call(user='other')[0], 404)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.probes, [])
