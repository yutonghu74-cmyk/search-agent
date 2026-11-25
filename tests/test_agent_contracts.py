from copy import deepcopy
from dataclasses import replace
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from draft_helpers import BODY, confirmation, make_service
from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.agent_contracts import (
    AgentStageTask, RetrievalStrategyProposal, ProcessingStepBinding,
    ProcessingCapability, ProcessingPlanProposal, StageFeedback, AgentStageReport,
    validate_agent_proposal, validate_stage_report, validate_agent_report,
)
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import IndexBinding
from dsh_retrieval.models import Actor, Task, RunStatus
from dsh_retrieval.multi_rule_contracts import DetectionStep
from dsh_retrieval.processing_requirements import normalize_agent_requirements
from dsh_retrieval.proposals import normalize_proposal
from dsh_retrieval.query_spec import digest
from dsh_retrieval.retrieval_contracts import RetrievalScope


def requirements():
    return {
        'schema_version': 'agent-requirements@1',
        'processing': [{'requirement_id': 'watermark-tag', 'purpose': 'tag',
            'instruction': '识别水印', 'source_text': '客厅图片', 'group_id': None,
            'rule_ref': None, 'output_field': 'watermark',
            'output_schema': {'version': 'watermark@1', 'type': 'boolean', 'values': [], 'required': False},
            'unknown_policy': 'allow_unknown'}],
        'autonomy': {'actions': ['rewrite_query', 'switch_source', 'paginate', 'retry'],
            'library_ids': ['lib-a'], 'methods': ['vector'], 'max_rounds': 3,
            'max_calls': 10, 'max_seconds': 60, 'max_no_progress_rounds': 2}}


def body():
    value = deepcopy(BODY)
    value['query_spec']['refill_policy'].update(
        mode='maintain_target', approved_library_ids=['lib-a'])
    value['query_spec']['agent_requirements'] = requirements()
    return value


class AgentContractsTest(unittest.TestCase):
    def setUp(self):
        self.spec = normalize_proposal(body())['query_spec']
        self.req = self.spec['agent_requirements']
        self.actor = Actor('tenant', 'owner')
        self.task = Task('task', 'tenant', 'owner', 'request', 'image', 10, 'test', False,
                         'manual', digest({}), confirmed_revision=1, project_id='demo',
                         confirmed_proposal_json=json.dumps({'query_spec': self.spec}),
                         run_status=RunStatus.RUNNING)
        self.stage = AgentStageTask(RetrievalScope.from_task(self.task), 'op', 'retrieval',
            digest({'input': 1}), digest(self.req), digest({'approval': 1}), 'policy@1', digest({'model': 1}), 'budget')
        self.binding = IndexBinding('binding', 'lib-a', 'v1', 'encoder@1', 4, 'cosine')
        self.strategy = RetrievalStrategyProposal(self.stage.stage_digest, 'rewrite_query',
            self.binding, 'vector', '客厅', ('facts-1',), '提高相关性')

    def check(self, *, stage=None, proposal=None, **overrides):
        options = dict(current=stage or self.stage, requirements=self.req, spec=self.spec,
                       remaining_calls=5, requested_calls=1,
                       authorized_bindings=(self.binding,), evidence_refs=('facts-1',))
        options.update(overrides)
        validate_agent_proposal(self.actor, self.task, stage or self.stage,
                                proposal or self.strategy, **options)

    def test_legacy_normalization_and_digest_unchanged(self):
        normalized = normalize_proposal(deepcopy(BODY))
        self.assertNotIn('agent_requirements', normalized['query_spec'])
        self.assertEqual(normalize_proposal(normalized), normalized)
        self.assertEqual(digest(normalized), digest(normalize_proposal(deepcopy(BODY))))
        service = make_service(InMemoryTaskRepository())
        result, _ = service.create_task(self.actor, 'legacy', confirmation(service, self.actor))
        self.assertEqual(result['confirmed_revision'], 1)

    def test_draft_roundtrip_and_confirmation_fails_without_runtime(self):
        service = make_service(InMemoryTaskRepository())
        request = confirmation(service, self.actor, body=body())
        draft = service.get_draft(self.actor, 'demo', request['draft_id'])
        self.assertEqual(draft['proposal']['query_spec']['agent_requirements'], self.req)
        with self.assertRaises(ServiceError) as error:
            service.create_task(self.actor, 'new-mode', request)
        self.assertEqual(error.exception.code, 'AGENT_EXECUTION_UNAVAILABLE')
        self.assertIsNone(service.get_draft(self.actor, 'demo', request['draft_id'])['confirmed_task_id'])

    def test_requirement_changes_affect_digest_and_do_not_mutate_input(self):
        raw = body(); previous = deepcopy(raw)
        first = normalize_proposal(raw)
        self.assertEqual(raw, previous)
        raw['query_spec']['agent_requirements']['autonomy']['max_calls'] = 11
        self.assertNotEqual(digest(first), digest(normalize_proposal(raw)))
        first['query_spec']['agent_requirements']['autonomy']['max_calls'] = 50
        self.assertEqual(raw['query_spec']['agent_requirements']['autonomy']['max_calls'], 11)

    def test_explicit_v2_allows_retrieval_without_fake_processing_but_does_not_open_execution(self):
        raw = body()
        old = normalize_proposal(raw)
        req = raw['query_spec']['agent_requirements']
        req.update(schema_version='agent-requirements@2', processing=[])
        normalized = normalize_proposal(raw)
        self.assertEqual(normalized['query_spec']['agent_requirements'], req)
        self.assertEqual(normalize_proposal(normalized), normalized)
        self.assertEqual(normalize_proposal(body()), old)
        self.assertNotEqual(digest(normalized), digest(old))
        service = make_service(InMemoryTaskRepository())
        request = confirmation(service, self.actor, body=raw)
        with self.assertRaises(ServiceError) as error:
            service.create_task(self.actor, 'retrieval-only', request)
        self.assertEqual(error.exception.code, 'AGENT_EXECUTION_UNAVAILABLE')
        self.assertIsNone(service.get_draft(self.actor, 'demo', request['draft_id'])['confirmed_task_id'])
        for change in ({'schema_version': 'agent-requirements@1'}, {'schema_version': 'agent-requirements@3'}):
            with self.assertRaises(ServiceError): normalize_agent_requirements(req | change, normalized['query_spec'])
        for field in ('actions', 'library_ids'):
            missing = deepcopy(req); missing['autonomy'][field] = []
            with self.assertRaises(ServiceError): normalize_agent_requirements(missing, normalized['query_spec'])

    def test_all_purposes_and_same_fact_for_filter_and_tag(self):
        value = body()
        value['query_spec']['rules'] = [{'rule_id': 'watermark', 'predicate_ref': 'watermark.present@1',
            'operator': 'eq', 'value': False, 'hardness': 'hard', 'origin': 'user_explicit'}]
        items = value['query_spec']['agent_requirements']['processing']
        for purpose in ('filter', 'rank', 'analyze'):
            item = deepcopy(items[0]); item.update(requirement_id=purpose, purpose=purpose, output_field=purpose)
            if purpose == 'filter':
                item.update(rule_ref='watermark', output_field=None, output_schema=None, unknown_policy='hold_for_review')
            if purpose == 'rank': item['output_schema']['type'] = 'number'
            items.append(item)
        self.assertEqual(len(normalize_proposal(value)['query_spec']['agent_requirements']['processing']), 4)

    def test_malformed_or_unsafe_requirements_rejected(self):
        mutations = [lambda r: r.update(tenant_id='spoof'),
            lambda r: r['autonomy'].update(library_ids=['forbidden']),
            lambda r: r['autonomy'].update(max_calls=True),
            lambda r: r['autonomy'].update(actions=['shell']),
            lambda r: r['processing'][0].update(purpose=[]),
            lambda r: r['processing'][0].update(group_id='missing'),
            lambda r: r['processing'][0].update(source_text='invented quote'),
            lambda r: r['processing'][0].update(backend_ref='/tmp/code.py'),
            lambda r: r['processing'][0]['output_schema'].update(required=True),
            lambda r: r['processing'].append(deepcopy(r['processing'][0])),
            lambda r: r['processing'][0].update(purpose='filter', rule_ref='missing'),
            lambda r: r['processing'][0]['output_schema'].update(type='enum', values=[])]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                req = requirements(); mutate(req)
                with self.assertRaises(ServiceError): normalize_agent_requirements(req, self.spec)

    def test_valid_retrieval_proposal_and_physical_binding_check(self):
        self.check()
        for change in (dict(library_id='outside'), dict(encoder_ref='other'), dict(index_version='v2')):
            with self.subTest(change=change), self.assertRaises(ServiceError):
                self.check(proposal=replace(self.strategy, binding=replace(self.binding, **change)))
        for change in (dict(action='shell'), dict(method='text'), dict(evidence_refs=('unknown',)), dict(query='')):
            with self.subTest(change=change), self.assertRaises(ServiceError):
                self.check(proposal=replace(self.strategy, **change))

    def test_authority_staleness_and_budget_guards(self):
        for options in (dict(current=None), dict(remaining_calls=0), dict(requested_calls=11),
                        dict(requested_calls=True), dict(spec={}), dict(requirements=requirements() | {'autonomy': {}})):
            with self.subTest(options=options), self.assertRaises(ServiceError): self.check(**options)
        for change in (dict(operation_id='other'), dict(input_digest=digest({'new': 1})),
                       dict(approval_digest=digest({'new': 1})), dict(model_binding_digest=digest({'new': 1}))):
            with self.subTest(change=change), self.assertRaises(ServiceError):
                self.check(stage=replace(self.stage, **change), current=self.stage)
        for scope in (replace(self.stage.scope, tenant_id='spoof'), replace(self.stage.scope, generation=2),
                      replace(self.stage.scope, task_version=2), replace(self.stage.scope, revision=2)):
            with self.subTest(scope=scope), self.assertRaises(ServiceError): self.check(stage=replace(self.stage, scope=scope))
        self.actor = Actor('tenant', 'other')
        with self.assertRaises(ServiceError): self.check()

    def test_cancel_pause_archive_block_stage(self):
        for status in (RunStatus.PAUSED, RunStatus.CANCELLED, RunStatus.COMPLETED):
            self.task.run_status = status
            with self.subTest(status=status), self.assertRaises(ServiceError): self.check()

    def test_processing_requires_exact_released_capabilities_and_full_coverage(self):
        stage = replace(self.stage, role='cleaning')
        detector = DetectionStep('tag', 'watermark@1', 'validator@1', 'evidence@1')
        step = ProcessingStepBinding('watermark-tag', detector)
        plan = ProcessingPlanProposal(stage.stage_digest, stage.requirements_digest, (step,))
        cap = ProcessingCapability(detector, 'image', frozenset({'tag'}), frozenset({'boolean'}))
        self.check(stage=stage, proposal=plan, capabilities=(cap,))
        for caps in ((), (replace(cap, media_type='video'),), (replace(cap, purposes=frozenset({'filter'})),),
                     (replace(cap, output_types=frozenset({'text'})),)):
            with self.subTest(caps=caps), self.assertRaises(ServiceError):
                self.check(stage=stage, proposal=plan, capabilities=caps)
        for steps in ((), (step, step), (replace(step, detector=replace(detector, backend_ref='arbitrary@1')),)):
            with self.subTest(steps=steps), self.assertRaises(ServiceError):
                self.check(stage=stage, proposal=replace(plan, steps=steps), capabilities=(cap,))

    def test_sqlite_draft_survives_restart_without_formal_execution(self):
        from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
        with TemporaryDirectory() as root:
            path = Path(root) / 'isolated.sqlite3'
            first = make_service(SQLiteTaskRepository(path))
            draft, replayed = first.save_draft(self.actor, 'demo', 'agent-draft', body())
            self.assertFalse(replayed)
            second = make_service(SQLiteTaskRepository(path))
            restored = second.get_draft(self.actor, 'demo', draft['draft_id'])
            self.assertEqual(restored, draft)
            repeated, replayed = second.save_draft(self.actor, 'demo', 'agent-draft', body())
            self.assertTrue(replayed)
            self.assertEqual(repeated, draft)
            request = dict(draft_id=draft['draft_id'], expected_draft_version=1,
                           proposal_digest=draft['proposal_digest'], action='confirm')
            with self.assertRaises(ServiceError): second.create_task(self.actor, 'blocked', request)
            self.assertIsNone(second.get_draft(self.actor, 'demo', draft['draft_id'])['confirmed_task_id'])

    def test_chinese_enum_labels_are_preserved(self):
        req = requirements()
        req['processing'][0]['output_schema'].update(type='enum', values=['白天', '夜晚'])
        self.assertEqual(normalize_agent_requirements(req, self.spec), req)
        req['processing'][0]['output_schema']['values'].append('白天')
        with self.assertRaises(ServiceError): normalize_agent_requirements(req, self.spec)

    def test_filter_scope_and_unknown_cannot_weaken_existing_rule(self):
        spec = deepcopy(self.spec)
        spec['rules'] = [{'rule_id': 'watermark', 'hardness': 'soft'}]
        req = requirements()
        req['processing'][0].update(purpose='filter', rule_ref='watermark',
            output_field=None, output_schema=None, unknown_policy='hold_for_review')
        with self.assertRaises(ServiceError): normalize_agent_requirements(req, spec)
        spec['rules'][0]['hardness'] = 'hard'
        normalize_agent_requirements(req, spec)
        req['processing'][0]['unknown_policy'] = 'allow_unknown'
        with self.assertRaises(ServiceError): normalize_agent_requirements(req, spec)

    def test_final_report_rechecks_authority_without_execution_budget(self):
        report = AgentStageReport(self.stage.stage_digest, 'stopped', '额度用尽', 'stop', ('facts-1',))
        options = dict(current=self.stage, requirements=self.req, spec=self.spec, evidence_refs=('facts-1',))
        validate_agent_report(self.actor, self.task, self.stage, report, **options)
        self.task.run_generation += 1
        with self.assertRaises(ServiceError):
            validate_agent_report(self.actor, self.task, self.stage, report, **options)

    def test_feedback_unknown_is_not_zero_yield_and_reports_cannot_write_counts(self):
        for status in ('pending', 'failed', 'unknown'):
            StageFeedback(self.stage.stage_digest, 'facts-1', status, 3, None, None, 1, 1)
            with self.assertRaises(ServiceError):
                StageFeedback(self.stage.stage_digest, 'facts-1', status, 3, 0, 10, 1, 1)
        StageFeedback(self.stage.stage_digest, 'facts-1', 'completed', 3, 1, 9, 1, 1)
        report = AgentStageReport(self.stage.stage_digest, 'needs_confirmation', '需调整条件', 'change_request', ('facts-1',))
        validate_stage_report(report, self.stage, evidence_refs=('facts-1',))
        for bad in (replace(report, outcome='ready'), replace(report, evidence_refs=('other',))):
            with self.assertRaises(ServiceError): validate_stage_report(bad, self.stage, evidence_refs=('facts-1',))


if __name__ == '__main__':
    unittest.main()
