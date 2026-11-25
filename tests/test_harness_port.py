from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

import test_agent_contracts as domain_fixtures
from dsh_retrieval.agent_contracts import AgentStageTask, ProcessingCapability
from dsh_retrieval.adapters.harness.local import LocalHarnessAdapter
from dsh_retrieval.adapters.harness.process import ProcessHarnessAdapter
from dsh_retrieval.adapters.harness.dsh_legacy import LegacyDshAdapter
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.harness.contracts import (AdapterRegistry, RoleDefinition, RuntimeRelease,
                                            HarnessInvocation, RuntimeHandle)
from dsh_retrieval.harness.runner import HarnessRunner, load_role_routes
from dsh_retrieval.harness.tools import ToolBroker, ToolCatalog, ToolDefinition, LocalTools, McpTools
from dsh_retrieval.harness.stage_tools import StageAuthority, stage_catalog
from dsh_retrieval.models import RunStatus
from dsh_retrieval.multi_rule_contracts import DetectionStep
from dsh_retrieval.query_spec import digest


class HarnessPortTest(unittest.TestCase):
    def setUp(self):
        # Reuse domain fixture, not the test methods.
        domain = domain_fixtures.AgentContractsTest(); domain.setUp()
        self.actor, self.task, self.spec, self.binding = domain.actor, domain.task, domain.spec, domain.binding
        self.context = {'authorized': 'current-stage'}
        self.stage = replace(domain.stage, input_digest=digest(self.context))
        self.current = self.stage
        self.calls = 0
        self.remaining_calls = 5
        self.detector = DetectionStep('tag', 'watermark@1', 'validator@1', 'evidence@1')
        self.cap = ProcessingCapability(self.detector, 'image', frozenset({'tag'}), frozenset({'boolean'}))
        def authority(invocation):
            self.calls += 1
            return StageAuthority(self.actor, self.task, self.current, self.spec, self.remaining_calls,
                                  (self.binding,), (self.cap,), ('facts-1',))
        self.catalog, self.guard = stage_catalog(authority)
        self.role = RoleDefinition('retrieval@1', 'retrieval', 'Plan retrieval using approved tools.',
            ('get_stage_context', 'submit_retrieval_strategy', 'submit_stage_report'), 'strategy@1')
        self.cleaning = RoleDefinition('cleaning@1', 'cleaning', 'Plan processing using approved tools.',
            ('get_stage_context', 'submit_processing_plan', 'submit_stage_report'), 'processing@1')
        self.release = RuntimeRelease('local-release', 'local', digest({'artifact': 1}))
        self.deadline = time.time() + 10
        self.proposal = {'proposal': {'action': 'rewrite_query', 'binding_id': 'binding',
            'method': 'vector', 'query': '客厅', 'evidence_refs': ['facts-1'], 'reason': '提高相关性'}}

    def invocation(self, **changes):
        return replace(HarnessInvocation(self.stage, 'run', 'attempt', self.role, self.release,
            'model-profile', 'plan', self.deadline, json.dumps(self.context)), **changes)

    def broker(self, invocation=None):
        return ToolBroker(invocation or self.invocation(), self.catalog, guard=self.guard)

    def runner(self, driver):
        registry = AdapterRegistry(); registry.register(driver.adapter_id, driver)
        release = replace(self.release, adapter_id=driver.adapter_id, artifact_digest=driver.artifact_digest)
        return HarnessRunner(registry, {'retrieval': release, 'cleaning': release},
            {'retrieval': self.role, 'cleaning': self.cleaning}, self.catalog, guard=self.guard, model_binding=lambda _: self.stage.model_binding_digest)

    def start(self, runner, **changes):
        args = dict(run_id='run', attempt_id='attempt', model_profile_ref='model-profile',
                    task_kind='plan', deadline=self.deadline, context_json=json.dumps(self.context))
        args.update(changes)
        return runner.start(self.stage, **args)

    def finish(self, runner, handle):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            events = runner.poll(handle)
            if events and events[-1].kind in ('finished', 'failed', 'cancelled'): return events
            time.sleep(.01)
        self.fail('adapter did not reach a terminal event')

    def test_local_and_mcp_share_t03_policy_and_receipt(self):
        broker = self.broker()
        first = LocalTools(broker).call('submit_retrieval_strategy', self.proposal)
        response = McpTools(broker).dispatch({'id': 1, 'method': 'tools/call',
            'params': {'name': 'submit_retrieval_strategy', 'arguments': self.proposal}})
        self.assertEqual(json.loads(response['result']['content'][0]['text']), first)
        self.assertEqual(len(broker.receipts.records), 1)
        changed = deepcopy(self.proposal); changed['proposal']['query'] = '另一查询'
        with self.assertRaises(ServiceError) as error: broker.call('submit_retrieval_strategy', changed)
        self.assertEqual(error.exception.code, 'IDEMPOTENCY_CONFLICT')
        self.task.run_generation += 1
        with self.assertRaises(ServiceError): broker.call('submit_retrieval_strategy', self.proposal)

    def test_tool_allowlist_schema_and_role_guard(self):
        broker = self.broker()
        for tool, args in [('bash', {}), ('submit_processing_plan', {}),
                           ('get_stage_context', {'tenant_id': 'spoof'}),
                           ('submit_retrieval_strategy', {'proposal': []})]:
            with self.subTest(tool=tool), self.assertRaises(ServiceError): broker.call(tool, args)
        response = McpTools(broker).dispatch({'id': 2, 'method': 'tools/call',
                                              'params': {'name': 'bash', 'arguments': {}}})
        self.assertTrue(response['result']['isError'])

    def test_missing_budget_and_unreleased_capability_rejected_by_shared_handler(self):
        broker = self.broker()
        bad = deepcopy(self.proposal); bad['proposal']['binding_id'] = 'unapproved'
        with self.assertRaises(ServiceError): broker.call('submit_retrieval_strategy', bad)
        self.remaining_calls = 0
        with self.assertRaises(ServiceError) as caught:
            broker.call('submit_retrieval_strategy', self.proposal)
        self.assertEqual(caught.exception.code, 'AGENT_BUDGET_EXCEEDED')
        report = broker.call('submit_stage_report', {'proposal': {
            'outcome': 'stopped', 'summary': '额度用尽', 'recommendation': 'stop', 'evidence_refs': ['facts-1']}})
        self.assertEqual(report['status'], 'report_validated')
        self.remaining_calls = 5
        self.current = replace(self.stage, role='cleaning')
        invocation = self.invocation(stage=self.current, role=self.cleaning)
        clean = self.broker(invocation)
        result = clean.call('submit_processing_plan', {'proposal': {'steps': [
            {'requirement_id': 'watermark-tag', 'step_id': 'tag'}]}})
        self.assertFalse(result['execution_allowed'])
        with self.assertRaises(ServiceError): clean.call('submit_processing_plan', {'proposal': {'steps': [
            {'requirement_id': 'watermark-tag', 'step_id': 'arbitrary-module'}]}})

    def test_cancellation_and_revocation_fence_cached_reads(self):
        broker = self.broker(); broker.call('get_stage_context', {})
        self.task.run_status = RunStatus.PAUSED
        with self.assertRaises(ServiceError): broker.call('get_stage_context', {})
        self.task.run_status = RunStatus.RUNNING
        broker.cancel()
        with self.assertRaises(ServiceError): broker.call('get_stage_context', {})

    def test_input_digest_and_semantic_runtime_bindings_are_separate(self):
        with self.assertRaises(ServiceError): self.invocation(context_json='{"changed": true}')
        first = self.invocation()
        second = replace(first, runtime=replace(first.runtime, artifact_digest=digest({'artifact': 2})))
        self.assertEqual(first.stage, second.stage)
        self.assertNotEqual(first.invocation_digest, second.invocation_digest)
        self.assertEqual(self.stage.model_binding_digest, second.stage.model_binding_digest)

    def test_local_lifecycle_requires_broker_output_not_driver_return(self):
        for submit in (True, False):
            def driver(invocation, tools, stopped):
                if submit: tools.call('submit_retrieval_strategy', self.proposal)
                return 'completed'
            runner = self.runner(LocalHarnessAdapter('local', driver, artifact_digest=self.release.artifact_digest))
            handle = self.start(runner)
            events = self.finish(runner, handle)
            self.assertEqual(events[-1].kind, 'finished' if submit else 'failed')
            cursor = events[-1].cursor
            self.assertEqual(runner.poll(handle, cursor), ())
            self.assertEqual(handle, self.start(runner))
            runner.close(handle)

    def test_process_transport_runs_same_domain_tools_and_replays_native_calls(self):
        driver = ProcessHarnessAdapter('process', (sys.executable,
            str(Path(__file__).parent / 'fixtures' / 'harness_worker.py')), version='fixture@1', artifact_digest=self.release.artifact_digest)
        runner = self.runner(driver); handle = self.start(runner)
        events = self.finish(runner, handle)
        self.assertEqual(events[-1].kind, 'finished', events)
        self.assertEqual(len(runner.receipts.records), 2)  # context and one proposal
        self.assertEqual(runner.outputs(handle)[0]['arguments'], self.proposal)
        self.assertEqual([e.cursor for e in events], list(range(1, len(events) + 1)))
        runner.close(handle)
        self.assertEqual(runner.outputs(handle)[0]['arguments'], self.proposal)

    def test_worker_text_malformed_and_no_tool_output_do_not_succeed(self):
        for kind in ('malformed', 'no_output'):
            driver = ProcessHarnessAdapter('process', (sys.executable,
                str(Path(__file__).parent / 'fixtures' / 'harness_worker.py')), version='fixture@1', artifact_digest=self.release.artifact_digest)
            runner = self.runner(driver); handle = self.start(runner, task_kind=kind)
            events = self.finish(runner, handle)
            self.assertEqual(events[-1].kind, 'failed')
            self.assertNotIn('raw-provider-secret', str(events))
            runner.close(handle)

    def test_deadline_cancel_and_handle_tamper(self):
        release = threading.Event()
        def driver(invocation, tools, stop):
            release.wait(2)
            tools.call('submit_retrieval_strategy', self.proposal)
        runner = self.runner(LocalHarnessAdapter('local', driver, artifact_digest=self.release.artifact_digest))
        handle = self.start(runner, deadline=time.time() + .1)
        events = self.finish(runner, handle)
        self.assertEqual(events[-1].code, 'HARNESS_DEADLINE')
        release.set(); runner.close(handle)
        self.assertEqual(len(runner.receipts.records), 0)
        with self.assertRaises(ServiceError): runner.poll(replace(handle, invocation_digest=digest({})))

    def test_route_changes_do_not_rebind_existing_attempt(self):
        adapter = LocalHarnessAdapter('local', lambda inv, tools, stop:
                                       tools.call('submit_retrieval_strategy', self.proposal), artifact_digest=self.release.artifact_digest)
        runner = self.runner(adapter)
        first = self.start(runner); self.finish(runner, first)
        runner.routes['retrieval'] = replace(self.release, release_ref='next-release')
        self.assertEqual(self.start(runner), first)
        next_run = self.start(runner, attempt_id='attempt2')
        self.finish(runner, next_run)
        self.assertNotEqual(next_run.runtime_digest, first.runtime_digest)
        with self.assertRaises(ServiceError): self.start(runner, model_profile_ref='changed')
        runner.close(first); runner.close(next_run)

    def test_missing_capabilities_and_unknown_registration_fail_closed(self):
        registry = AdapterRegistry()
        with self.assertRaises(ServiceError): registry.resolve(self.release)
        registry.register('local', LocalHarnessAdapter('local', lambda *a: None, artifact_digest=self.release.artifact_digest))
        with self.assertRaises(ServiceError): registry.register('local', LocalHarnessAdapter('local', lambda *a: None, artifact_digest=self.release.artifact_digest))
        with self.assertRaises(ServiceError): registry.resolve(replace(self.release,
            required_capabilities=self.release.required_capabilities | {'hard_model_budget'}))

    def test_legacy_worker_bridge_with_sdk_double_keeps_business_handler_shared(self):
        self.sdk_double_bridge(candidate=False)

    def test_candidate_sdk_uses_same_bridge_and_explicit_keyword_constructor(self):
        self.sdk_double_bridge(candidate=True)

    def sdk_double_bridge(self, *, candidate):
        version = '0.2.2rc2' if candidate else '0.2.1rc2'
        with tempfile.TemporaryDirectory(prefix='hs-', dir='/tmp') as root:
            root = Path(root)
            (root / 'deepseek_harness.py').write_text((Path(__file__).parent / 'fixtures' / 'harness_sdk_double.py').read_text())
            for package in ('deepseek_harness_sdk', 'deepseek_harness_runtime_bin'):
                dist = root / (package + '-' + version + '.dist-info'); dist.mkdir()
                (dist / 'METADATA').write_text('Metadata-Version: 2.1\nName: ' + package + '\nVersion: ' + version + '\n')
            adapter = LegacyDshAdapter(python=sys.executable, home=root / 'home', model_environment=lambda _: {
                'RETRIEVAL_MODEL_KEY': 'test-placeholder', 'RETRIEVAL_MODEL_URL': 'https://example.invalid',
                'RETRIEVAL_MODEL_ID': 'test-model'})
            if candidate:
                from dsh_retrieval.adapters.harness.dsh import DshAdapter
                from dsh_retrieval.adapters.harness.dsh_releases import WHEEL_DSH_022RC2
                adapter = DshAdapter(python=sys.executable, home=root / 'home',
                    model_environment=adapter.model_environment, release=WHEEL_DSH_022RC2, adapter_id='dsh-candidate')
            original_environment = adapter._environment
            def environment():
                env = original_environment()
                env['PYTHONPATH'] = str(root) + ':' + env['PYTHONPATH']
                return env
            adapter._environment = environment
            self.assertTrue(adapter.probe().available)  # SDK double, not real release validation.
            runner = self.runner(adapter); handle = self.start(runner)
            events = self.finish(runner, handle)
            self.assertEqual(events[-1].kind, 'finished', events)
            self.assertEqual(len(runner.receipts.records), 2)
            runner.close(handle)

    def test_manifest_and_model_mismatches_are_rejected_before_start(self):
        adapter = LocalHarnessAdapter('local', lambda *a: None, artifact_digest=self.release.artifact_digest)
        runner = self.runner(adapter)
        runner.routes['retrieval'] = replace(self.release, artifact_digest=digest({'wrong': 1}))
        with self.assertRaises(ServiceError): self.start(runner)
        runner.routes['retrieval'] = self.release
        runner.model_binding = lambda _: digest({'different-model': 1})
        with self.assertRaises(ServiceError): self.start(runner)

    def test_mixed_role_routes_share_business_contracts(self):
        adapter = LocalHarnessAdapter('local', lambda inv, tools, stop:
            tools.call('submit_retrieval_strategy', self.proposal), artifact_digest=self.release.artifact_digest)
        runner = self.runner(adapter)
        first = self.start(runner); self.finish(runner, first)
        process = ProcessHarnessAdapter('process', (sys.executable,
            str(Path(__file__).parent / 'fixtures' / 'harness_worker.py')), version='fixture@1',
            artifact_digest=digest({'process': 1}))
        runner.registry.register('process', process)
        runner.routes['cleaning'] = RuntimeRelease('process-release', 'process', process.artifact_digest)
        self.stage = replace(self.stage, role='cleaning'); self.current = self.stage
        second = self.start(runner, attempt_id='cleaning')
        self.assertEqual(self.finish(runner, second)[-1].kind, 'finished')
        self.assertNotEqual(first.runtime_digest, second.runtime_digest)
        runner.close(first); runner.close(second)

    def test_production_tools_and_unregistered_config_are_rejected(self):
        catalog = ToolCatalog()
        tool = ToolDefinition('execute', '1', frozenset({'retrieval'}), 'execute',
            json.dumps({'type': 'object'}), 'execution')
        catalog.register(tool, lambda *a: self.fail('execution must never run'))
        inv = self.invocation(role=replace(self.role, tool_ids=('execute',)))
        with self.assertRaises(ServiceError): ToolBroker(inv, catalog, guard=self.guard)
        routes = load_role_routes({'retrieval': 'local-release', 'cleaning': 'local-release'},
                                  {'local-release': self.release})
        self.assertEqual(routes['retrieval'], self.release)
        for config in ({'retrieval': 'local-release'}, {'retrieval': 'local-release', 'cleaning': 'arbitrary.module'}):
            with self.assertRaises(ServiceError): load_role_routes(config, {'local-release': self.release})

    def test_process_cancel_fences_and_closes_worker(self):
        process = ProcessHarnessAdapter('process', (sys.executable,
            str(Path(__file__).parent / 'fixtures' / 'harness_worker.py')), version='fixture@1',
            artifact_digest=self.release.artifact_digest)
        runner = self.runner(process); handle = self.start(runner, task_kind='hang')
        self.assertEqual(runner.cancel(handle), 'acknowledged')
        self.assertEqual(self.finish(runner, handle)[-1].kind, 'cancelled')
        with self.assertRaises(ServiceError): runner.outputs(handle)
        runner.close(handle)
        self.assertFalse(process._get(handle).thread.is_alive())

    def test_release_configuration_cannot_import_code_or_fall_back_to_wheel(self):
        from dsh_retrieval.adapters.harness.composition import build_planning_runner
        from dsh_retrieval.adapters.harness.dsh import DshAdapter
        from dsh_retrieval.adapters.harness.dsh_releases import CANDIDATE_DSH
        with self.assertRaises(ServiceError):
            build_planning_runner({'schema_version': 'harness-routes@1', 'releases': {
                'runtime': {'sdk_release': 'arbitrary.module'}}, 'roles': {}},
                authority=lambda _: None, model_environment=lambda _: {}, model_binding=lambda _: None)
        with tempfile.TemporaryDirectory() as home:
            with self.assertRaises(ValueError):
                DshAdapter(python=sys.executable, home=home, model_environment=lambda _: {},
                           release=CANDIDATE_DSH, adapter_id='candidate')

    def test_legacy_probe_does_not_install_or_fallback(self):
        with tempfile.TemporaryDirectory() as home:
            adapter = LegacyDshAdapter(python='/unavailable/python', home=home, model_environment=lambda _: {})
            self.assertFalse(adapter.probe().available)
            self.assertEqual(adapter.probe().reason_code, 'DSH_RELEASE_UNAVAILABLE')


if __name__ == '__main__': unittest.main()
