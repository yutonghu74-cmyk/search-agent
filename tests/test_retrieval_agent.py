"""Plan/confirm/execute continuity through one retrieval owner and real ledgers."""
from copy import deepcopy
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from dsh_retrieval.dialogue import DialogueCoordinator, DialogueStore, _new_dialogue_state
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_jobs import ExpansionJobs
from dsh_retrieval.expansion_models import preview_edited_groups
from dsh_retrieval.models import Actor
from dsh_retrieval.retrieval_agent import RetrievalAgent
import test_confirmed_terms_agent as fixtures


class Models:
    def __init__(self):
        self.calls = 0
        self.version = 'initial'

    def binding(self, llm, vlm):
        return {'llm': llm, 'vlm': vlm, 'version': self.version}

    def expand_chunk(self, *args, **kwargs):
        self.calls += 1
        return preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': ['柯基']},
            {'primary_label': '猫', 'auxiliary_terms': ['橘猫']}])

    def bind(self, result, binding, **kwargs):
        return result


class RetrievalAgentTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ConfirmedTermsAgentTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.actor = self.f.actor; self.service = self.f.service
        self.initial_tasks = len(self.service.list_tasks(self.actor, 'demo'))
        # Keep a separate durable planning ledger, not a formal task/run ledger.
        import tempfile
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'dialogue.sqlite'
        self.models = Models(); self.store = DialogueStore(self.path); self.addCleanup(self.store.close)
        self.dialogue = DialogueCoordinator(self.store, self.models, self.f.f.projects)
        self.state = {**_new_dialogue_state(), 'subject': '狗、猫', 'media_type': 'image',
                      'target_count': 5, 'raw_request': '狗3张、猫2张', 'effective_request': '狗3张、猫2张',
                      'stage': 'terms', 'question': 'review_terms',
                      'messages': [{'role': 'user', 'text': '狗3张、猫2张'}]}
        self.session, self.version = self.store.save(self.actor, 'demo', None, 0, self.state)
        self.queue = ExpansionJobs(self.path, self.models, self.dialogue, self.f.f.projects, self.service)
        self.addCleanup(self.queue.close)
        self.agent = RetrievalAgent(self.queue, self.service, self.f.agent)
        self.f.window.confirmed_terms = self.agent
        self.f.dispatch.confirmed_terms_agent = self.agent
        handler = self.f.server.RequestHandlerClass
        handler.dialogue = self.dialogue; handler.expansion_jobs = self.queue; handler.retrieval_terms = self.agent
        self.root = f'/v1/retrieval/projects/demo/dialogue-sessions/{self.session}/retrieval-agent/terms'

    def plan(self, **changes):
        current = self.dialogue.snapshot(self.actor, 'demo', self.session)
        body = {'dialogue_session_id': self.session, 'dialogue_version': current['version'],
                'raw_request': current['request']['effective_request'], 'dimension_id': 'default',
                'target_count': current['request']['target_count'],
                'model_selection': {'llm': 'llm', 'vlm': 'vlm'}}
        body.update(changes)
        status, job = self.f.call(self.root, body)
        self.assertEqual(status, 202, job)
        for _ in range(100):
            job = self.agent.get(self.actor, 'demo', self.session, job['job_id'])
            if job['status'] == 'completed':break
            time.sleep(.01)
        self.assertEqual(job['status'], 'completed', job)
        return job

    def confirmation(self, job):
        proposal = self.f.body()
        proposal['query_spec']['raw_request'] = job['raw_request']
        proposal['query_spec']['target_count'] = job['target_count']
        proposal['query_spec']['refill_policy']['target_count'] = job['target_count']
        proposal['query_spec']['grouped_expansion'] = deepcopy(job['result']['plan'])
        return {**job['confirmation'], 'proposal': proposal}

    def turn(self, message, interpretation=None):
        from test_dialogue import proposal
        current = self.dialogue.snapshot(self.actor, 'demo', self.session)
        with patch.object(self.models, 'interpret_dialogue_turn', create=True,
                          return_value=interpretation or proposal('update')):
            return self.dialogue.turn(self.actor, 'demo', {
                'session_id': self.session, 'expected_version': current['version'],
                'message': message, 'llm_id': 'llm', 'phase': 'terms',
                'generate_terms_when_complete': True})

    def structured(self):
        from dsh_retrieval.requirement_draft import project_draft, render_draft
        draft = {'schema_version': 'requirement-draft@1', 'categories': [
            {'category_id': 'dog', 'label': '狗', 'scene': '狗吃饭', 'target_count': 3,
             'conditions': [], 'evidence': {}},
            {'category_id': 'cat', 'label': '猫', 'scene': '猫睡觉', 'target_count': 2,
             'conditions': [], 'evidence': {}}]}
        self.state = project_draft(self.state, draft)
        self.state['effective_request'] = render_draft(self.state)
        self.version = self.store.save(self.actor, 'demo', self.session, self.version, self.state)[1]

    def receipt_count(self):
        return self.queue.db.execute('SELECT COUNT(*) FROM retrieval_agent_confirmations').fetchone()[0]

    def test_total_count_reuses_terms_and_confirms_latest_backend_version(self):
        self.state.update(raw_request='狗和猫共5张', effective_request='狗和猫共5张')
        self.version = self.store.save(self.actor, 'demo', self.session, self.version, self.state)[1]
        original = self.plan(); calls = self.models.calls
        turn = self.turn('改成20张')
        self.assertEqual(turn['planning_change'], 'quantity')
        updated = self.plan()
        self.assertEqual(self.models.calls, calls)
        self.assertEqual(updated['status'], 'completed')
        self.assertEqual(updated['result']['plan'], original['result']['plan'])
        self.assertEqual(updated['reuse']['source_job_id'], original['job_id'])
        self.assertEqual(updated['confirmation']['dialogue_version'], turn['version'])
        self.assertEqual(self.plan()['job_id'], updated['job_id'])
        self.assertEqual(len(self.service.list_tasks(self.actor, 'demo')), self.initial_tasks)
        with self.assertRaises(ServiceError):
            self.agent.confirm(self.actor, 'demo', self.session, original['job_id'], 'old', self.confirmation(original))
        task, _ = self.agent.confirm(self.actor, 'demo', self.session, updated['job_id'], 'new', self.confirmation(updated))
        self.assertEqual(task['planning']['job_ref'], updated['job_id'])
        self.assertEqual(self.service.repository.get(self.actor, task['task_id']).target_count, 20)

    def test_category_count_and_condition_changes_preserve_other_scope_and_restart(self):
        from test_dialogue import proposal
        self.structured(); original = self.plan(); calls = self.models.calls
        turn = self.turn('狗改成30张，其他不变', proposal('update', requirement_edits=[
            {'op': 'set_count', 'targets': ['dog'], 'count': 30, 'source_text': '狗改成30张'}]))
        self.assertEqual(turn['planning_change'], 'quantity')
        updated = self.plan()
        groups = updated['result']['plan']['groups']
        self.assertEqual([(g['primary_label'], g['delivery_target']) for g in groups], [('狗', 30), ('猫', 2)])
        self.assertEqual([g['auxiliary_terms'] for g in groups],
                         [g['auxiliary_terms'] for g in original['result']['plan']['groups']])
        turn = self.turn('狗不要水印', proposal('update', requirement_edits=[
            {'op': 'add_condition', 'targets': ['dog'], 'text': '不要水印', 'source_text': '狗不要水印'}]))
        self.assertEqual(turn['planning_change'], 'cleaning')
        cleaned = self.plan()
        self.assertEqual(self.models.calls, calls)
        self.assertEqual(cleaned['result']['plan'], updated['result']['plan'])
        self.assertIn('本类条件：不要水印', cleaned['raw_request'])
        restored = ExpansionJobs(self.path, self.models, self.dialogue, self.f.f.projects, self.service)
        try:
            agent = RetrievalAgent(restored, self.service)
            readback = agent.get(self.actor, 'demo', self.session)
            for key in ('job_id', 'dialogue_version', 'result', 'reuse', 'status'):
                self.assertEqual(readback[key], cleaned[key])
        finally:
            restored.close()
        body = self.confirmation(cleaned)
        task, _ = self.agent.confirm(self.actor, 'demo', self.session, cleaned['job_id'], 'cleaned', body)
        saved = self.service.repository.get(self.actor, task['task_id'])
        spec = json.loads(saved.confirmed_proposal_json)['query_spec']
        self.assertIn('本类条件：不要水印', spec['raw_request'])
        self.assertEqual(spec['target_count'], 32)

    def test_count_plus_cleaning_and_partial_allocation_never_guess_group_quotas(self):
        from test_dialogue import proposal
        self.structured(); original = self.plan(); calls = self.models.calls
        turn = self.turn('猫要10张且不要文字', proposal('update', requirement_edits=[
            {'op': 'set_count', 'targets': ['cat'], 'count': 10, 'source_text': '猫要10张'},
            {'op': 'add_condition', 'targets': ['cat'], 'text': '不要文字', 'source_text': '不要文字'}]))
        self.assertEqual(turn['planning_change'], 'quantity_and_cleaning')
        updated = self.plan()
        self.assertEqual(self.models.calls, calls)
        self.assertEqual([g['delivery_target'] for g in updated['result']['plan']['groups']], [3, 10])
        self.assertNotEqual(updated['result']['plan']['plan_digest'], original['result']['plan']['plan_digest'])
        # A model's taxonomy cannot stand in for an exact category-to-group map.
        state = self.store.read(self.actor, 'demo', self.session)
        state['requirement_draft']['categories'][0]['label'] = '小狗'
        state['subject'] = '小狗吃饭、猫睡觉'
        state['effective_request'] = None
        state['planning_change'] = 'quantity'  # Even an erroneous marker cannot bypass the fact diff.
        self.store.save(self.actor, 'demo', self.session, turn['version'], state)
        calls = self.models.calls
        self.assertIsNone(self.plan()['reuse']); self.assertGreater(self.models.calls, calls)

    def test_scene_and_term_strategy_changes_do_not_reuse(self):
        from test_dialogue import proposal
        self.structured(); self.plan(); calls = self.models.calls
        turn = self.turn('狗改为奔跑', proposal('update', requirement_edits=[
            {'op': 'update_category', 'targets': ['dog'], 'scene': '狗奔跑', 'source_text': '狗改为奔跑'}]))
        self.assertIsNone(turn['planning_change']); updated = self.plan()
        self.assertIsNone(updated['reuse']); self.assertGreater(self.models.calls, calls)
        calls = self.models.calls
        turn = self.turn('狗要40张，检索词换一批', proposal('update', requirement_edits=[
            {'op': 'set_count', 'targets': ['dog'], 'count': 40, 'source_text': '狗要40张'}],
            expansion_instruction='检索词换一批'))
        self.assertIsNone(turn['planning_change']); self.plan()
        self.assertGreater(self.models.calls, calls)

    def test_explicit_regeneration_model_change_and_unknown_condition_do_not_reuse(self):
        from test_dialogue import proposal
        self.plan(); self.turn('改成20张'); calls = self.models.calls
        self.assertIsNone(self.plan(regeneration_key='explicit')['reuse'])
        self.assertGreater(self.models.calls, calls)
        self.turn('改成30张'); calls = self.models.calls; self.models.version = 'new-model-config'
        self.assertIsNone(self.plan()['reuse']); self.assertGreater(self.models.calls, calls)
        turn = self.turn('换到公园场景', proposal('update', constraints='公园场景'))
        self.assertIsNone(turn['planning_change']); calls = self.models.calls
        self.assertIsNone(self.plan()['reuse']); self.assertGreater(self.models.calls, calls)

    def test_textual_agreement_rebases_confirmation_without_approving_or_expanding(self):
        from test_dialogue import proposal
        original = self.plan(); calls = self.models.calls
        turn = self.turn('就按这些词', proposal('confirm_terms'))
        self.assertEqual(turn['planning_change'], 'unchanged')
        updated = self.plan()
        self.assertEqual(updated['result']['plan'], original['result']['plan'])
        self.assertEqual(self.models.calls, calls)
        self.assertEqual(self.receipt_count(), 0)
        self.assertEqual(len(self.service.list_tasks(self.actor, 'demo')), self.initial_tasks)
        self.assertEqual(updated['confirmation']['dialogue_version'], turn['version'])

    def test_common_cleaning_condition_add_and_remove_keep_terms(self):
        from test_dialogue import proposal
        original = self.plan(); calls = self.models.calls
        for message, constraint in [('不要水印', '不要水印'), ('水印也可以', '')]:
            turn = self.turn(message, proposal('update', constraints=constraint))
            self.assertEqual(turn['planning_change'], 'cleaning')
            updated = self.plan()
            self.assertEqual(updated['result']['plan'], original['result']['plan'])
            self.assertEqual(self.models.calls, calls)
            self.assertEqual(updated['raw_request'], turn['request']['effective_request'])

    def test_legacy_plan_and_injected_reuse_cannot_bypass_generation_or_scope(self):
        original = self.plan()
        state = self.queue._load(original['job_id'])[1]; state.pop('request_facts')
        self.queue._save(original['job_id'], state)
        self.turn('改成20张'); calls = self.models.calls
        self.assertIsNone(self.plan()['reuse']); self.assertGreater(self.models.calls, calls)
        with self.assertRaises(ServiceError):
            self.agent.start(self.actor, 'demo', {'reuse': True})
        with self.assertRaises(ServiceError):
            self.agent.get(Actor('tenant-a', 'other'), 'demo', self.session)

    def test_same_agent_pauses_for_confirmation_and_resumes_original_plan(self):
        self.f.automatic_intake()
        job = self.plan()
        self.assertEqual(job['agent'], self.agent.descriptor('planning'))
        self.assertEqual(job['phase'], 'waiting_confirmation')
        self.assertEqual(len(self.service.list_tasks(self.actor, 'demo')), self.initial_tasks)
        body = self.confirmation(job)
        body['proposal']['node_modes'].update(retrieval='auto', cleaning='auto')
        body['proposal']['query_spec']['rules'] = [{'rule_id': 'no-watermark', 'predicate_ref': 'watermark.present@1',
            'operator': 'eq', 'value': False, 'hardness': 'hard', 'origin': 'user_confirmed'}]
        status, result = self.f.call(self.root + '/' + job['job_id'] + '/confirm', body)
        self.assertEqual(status, 202, result)
        self.assertEqual(result['agent']['workflow_ref'], job['agent']['workflow_ref'])
        self.assertEqual(result['planning']['job_ref'], job['job_id'])
        ref = result['search_enqueue']['job_ref']
        view = self.f.wait_intake(result['task_id'], ref)
        self.assertTrue(view['artifacts_ready'], view)
        self.assertEqual(view['planning']['plan_digest'], job['result']['plan']['plan_digest'])
        self.assertEqual(view['agent']['workflow_ref'], job['agent']['workflow_ref'])
        status, continued = self.f.call(self.root + '/' + job['job_id'])
        self.assertEqual(status, 200, continued)
        self.assertEqual(continued['task_id'], result['task_id'])
        self.assertEqual(continued['planning_job_ref'], job['job_id'])
        self.assertEqual(continued['execution']['job_ref'], ref)
        self.assertIsNone(continued['confirmation'])
        newer = self.agent.execution_context(self.actor, result['task_id'], 'retrieval',
                                            plan_digest='sha256:' + '0' * 64)
        self.assertIsNone(newer['planning'])
        self.assertEqual(self.models.calls, 1)  # Approval/execution do not expand again.

    def test_confirmation_replays_after_archive_and_process_reopen(self):
        job = self.plan(); body = self.confirmation(job)
        first, replayed = self.agent.confirm(self.actor, 'demo', self.session, job['job_id'], 'click-one', body)
        self.assertFalse(replayed)
        with self.assertRaises(ServiceError):self.dialogue.snapshot(self.actor, 'demo', self.session)
        reopened = ExpansionJobs(self.path, self.models, self.dialogue, self.f.f.projects, self.service)
        self.addCleanup(reopened.close)
        agent = RetrievalAgent(reopened, self.service, self.f.agent)
        result, replayed = agent.confirm(self.actor, 'demo', self.session, job['job_id'], 'click-two', body)
        self.assertTrue(replayed); self.assertEqual(result['task_id'], first['task_id'])
        self.assertEqual(len(self.service.list_tasks(self.actor, 'demo')), self.initial_tasks + 1)
        altered = deepcopy(body); altered['proposal']['query_spec']['raw_request'] += '不要水印'
        with self.assertRaisesRegex(ServiceError, 'different settings'):
            agent.confirm(self.actor, 'demo', self.session, job['job_id'], 'click-three', altered)

    def test_crash_after_task_commit_recovers_without_duplicate_task(self):
        job = self.plan(); body = self.confirmation(job)
        original = self.service.create_task
        def interrupted(*args):
            original(*args)
            raise RuntimeError('simulated lost acknowledgement')
        with patch.object(self.service, 'create_task', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'lost acknowledgement'):
                self.agent.confirm(self.actor, 'demo', self.session, job['job_id'], 'first', body)
        result, replayed = self.agent.confirm(self.actor, 'demo', self.session, job['job_id'], 'retry', body)
        self.assertTrue(replayed); self.assertIn('task_id', result)
        self.assertEqual(len(self.service.list_tasks(self.actor, 'demo')), self.initial_tasks + 1)

    def test_lost_task_acknowledgement_cannot_archive_a_newer_request(self):
        job = self.plan(); body = self.confirmation(job)
        original = self.service.create_task
        def interrupted(*args):
            original(*args)
            raise RuntimeError('simulated lost acknowledgement')
        with patch.object(self.service, 'create_task', side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.agent.confirm(self.actor, 'demo', self.session, job['job_id'], 'first', body)
        self.store.save(self.actor, 'demo', self.session, self.version, {**self.state, 'target_count': 6})
        task, replayed = self.agent.confirm(self.actor, 'demo', self.session, job['job_id'], 'retry', body)
        self.assertTrue(replayed)
        self.assertIn('task_id', task)
        self.assertEqual(self.dialogue.snapshot(self.actor, 'demo', self.session)['request']['target_count'], 6)
        self.assertEqual(len(self.service.list_tasks(self.actor, 'demo')), self.initial_tasks + 1)

    def test_stale_session_changed_plan_and_changed_models_are_rejected(self):
        job = self.plan(); body = self.confirmation(job)
        altered = deepcopy(body); altered['proposal']['query_spec']['raw_request'] = '猫5张'
        with self.assertRaises(ServiceError):self.agent.confirm(self.actor, 'demo', self.session, job['job_id'], 'raw', altered)
        self.models.version = 'rotated'
        with self.assertRaisesRegex(ServiceError, 'configuration changed'):
            self.agent.confirm(self.actor, 'demo', self.session, job['job_id'], 'model', body)
        self.models.version = 'initial'
        self.store.save(self.actor, 'demo', self.session, self.version, {**self.state, 'target_count': 6})
        with self.assertRaises(ServiceError):self.agent.confirm(self.actor, 'demo', self.session, job['job_id'], 'stale', body)
        self.assertEqual(self.receipt_count(), 0);self.assertEqual(len(self.service.list_tasks(self.actor, 'demo')), self.initial_tasks)

    def test_foreign_owner_and_nonconfirmation_cannot_create_task(self):
        job = self.plan(); body = self.confirmation(job)
        bad = {**body, 'action': 'continue'}
        for actor, request in [(self.actor, bad), (Actor('tenant-a', 'other'), body)]:
            with self.assertRaises(ServiceError):self.agent.confirm(actor, 'demo', self.session, job['job_id'], 'forbidden', request)
        self.assertEqual(self.receipt_count(), 0);self.assertEqual(len(self.service.list_tasks(self.actor, 'demo')), self.initial_tasks)

    def test_source_revocation_rejects_approval_before_business_draft(self):
        job = self.plan(); self.f.revoked = True
        with self.assertRaisesRegex(ServiceError, 'revoked'):
            self.agent.confirm(self.actor, 'demo', self.session, job['job_id'], 'revoked', self.confirmation(job))
        self.assertEqual(self.receipt_count(), 0);self.assertEqual(len(self.service.list_tasks(self.actor, 'demo')), self.initial_tasks)

    def test_disabled_project_cannot_resume_a_plan(self):
        job = self.plan(); body = self.confirmation(job)
        self.agent.enabled_projects = frozenset()
        with self.assertRaisesRegex(ServiceError, 'not enabled'):
            self.agent.confirm(self.actor, 'demo', self.session, job['job_id'], 'disabled', body)
        self.assertEqual(self.receipt_count(), 0)
        self.assertEqual(len(self.service.list_tasks(self.actor, 'demo')), self.initial_tasks)

    def test_factory_keeps_management_and_plan_confirmation_routes_together(self):
        import test_agent_management as managed_fixtures
        from dsh_retrieval.api import create_handler
        management = managed_fixtures.AgentManagementTest()
        management.setUp(); self.addCleanup(management.doCleanups)
        management.actor = self.actor
        management.ready(); management.bind()
        handler = create_handler(self.service, first_search=self.f.engine,
            search_dispatch=self.f.dispatch, dialogue=self.dialogue,
            expansion_jobs=self.queue, agent_management=management.service)
        self.addCleanup(handler.task_commands.close)
        self.f.server.RequestHandlerClass = handler
        status, catalog = self.f.call('/v1/retrieval/projects/demo/agent-management')
        self.assertEqual(status, 200, catalog)
        self.assertEqual(catalog['binding']['version'], 1)
        self.assertEqual(self.f.call('/v1/retrieval/projects/demo/agent-management', user='other')[0], 403)
        job = self.plan()
        status, task = self.f.call(self.root + '/' + job['job_id'] + '/confirm', self.confirmation(job))
        self.assertEqual(status, 202, task)
        self.assertEqual(task['planning']['job_ref'], job['job_id'])
        self.assertEqual(task['agent']['workflow_ref'], 'retrieval-agent@1')
        status, catalog_after = self.f.call('/v1/retrieval/projects/demo/agent-management')
        self.assertEqual(status, 200, catalog_after)
        self.assertEqual(catalog_after['binding'], catalog['binding'])
        handler.agent_management = None
        status, unavailable = self.f.call('/v1/retrieval/projects/demo/agent-management')
        self.assertEqual(status, 503, unavailable)
        self.assertEqual(unavailable['code'], 'AGENT_MANAGEMENT_UNAVAILABLE')

    def test_regeneration_invalidates_older_plan_even_without_dialogue_edit(self):
        old = self.plan(); body = self.confirmation(old)
        self.agent.start(self.actor, 'demo', {'dialogue_session_id': self.session, 'dialogue_version': self.version,
            'raw_request': '狗3张、猫2张', 'dimension_id': 'default', 'target_count': 5,
            'model_selection': {'llm': 'llm', 'vlm': 'vlm'}, 'regeneration_key': 'new-plan'})
        with self.assertRaisesRegex(ServiceError, 'latest complete retrieval plan'):
            self.agent.confirm(self.actor, 'demo', self.session, old['job_id'], 'old-click', body)
        self.assertEqual(self.receipt_count(), 0)
        self.assertEqual(len(self.service.list_tasks(self.actor, 'demo')), self.initial_tasks)

    def test_concurrent_clicks_create_one_task(self):
        import threading
        job = self.plan(); body = self.confirmation(job); results = []; errors = []
        barrier = threading.Barrier(2)
        def click(key):
            try:
                barrier.wait(timeout=3)
                results.append(self.agent.confirm(self.actor, 'demo', self.session, job['job_id'], key, body))
            except Exception as error:errors.append(error)
        threads = [threading.Thread(target=click, args=(key,)) for key in ('first', 'second')]
        for thread in threads:thread.start()
        for thread in threads:thread.join(10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len({r[0]['task_id'] for r in results}), 1)
        self.assertEqual(sorted(r[1] for r in results), [False, True])
        self.assertEqual(len(self.service.list_tasks(self.actor, 'demo')), self.initial_tasks + 1)


class RetrievalAgentBrowserRoutingTest(unittest.TestCase):
    def test_new_plan_confirmation_resumes_agent_without_creating_an_unrelated_draft(self):
        import os
        import shutil
        import subprocess
        from dsh_retrieval.chat_ui import CHAT_JS
        if not shutil.which('node'):self.skipTest('Node required')
        source = CHAT_JS[CHAT_JS.index('function draftBody('):CHAT_JS.index('function groupPreviewBody(')]
        source += CHAT_JS[CHAT_JS.index('async function confirmGuidedExpansion('):CHAT_JS.index('async function guidedStartSearch(')]
        script = r"""
const assert=require('node:assert/strict');
const proposal={raw:'狗5张',count:5,plan:{groups:[],plan_digest:'reviewed'},lines:'狗',jobId:'expjob_one',
 confirmation:{action:'confirm',plan_digest:'reviewed',dialogue_version:1}};
const state={proposedExpansion:proposal,dialogueId:'dlg_one',task:null,resumeDraft:null};
const fields={'new-request':{classList:{add(){}}},'feedback-box':{classList:{remove(){}}},status:{textContent:''}};
const events=[];const execution={candidate_intake:'auto'};
const api=async(path,options)=>{assert.equal(path,'/plans/dlg_one/expjob_one/confirm');
 assert.equal(options.body.action,'confirm');assert.equal(options.body.plan_digest,'reviewed');
 assert.equal(options.body.proposal.query_spec.raw_request,'狗5张');
 assert.deepEqual(options.body.proposal.query_spec.retrieval_execution,execution);
 events.push('resume');return {task_id:'rt_one',search_enqueue:{status:'queued'}}};
const fn=new Function('state','$','api','key','setBusy','sessionStorage','localStorage','refreshTaskSidebar','retireDraftActions','message','refreshHistory','guidedTick','expansionJobPath',
 process.env.SOURCE+';return confirmGuidedExpansion')(
 state,id=>fields[id],api,()=> 'click',()=>{}, {removeItem(){}},{setItem(){}},async()=>{},()=>{},()=>{},async()=>{},async()=>events.push('execution'),s=>'/plans/'+s);
(async()=>{await fn(proposal,{value:'狗',disabled:false},{enabled:false,libraries:[],limits:{}},execution);
 assert.deepEqual(events,['resume','execution']);assert.equal(state.task.task_id,'rt_one');assert.equal(state.dialogueId,null);
})().catch(e=>{console.error(e);process.exit(1)});
"""
        run = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
