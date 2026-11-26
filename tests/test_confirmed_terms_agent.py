from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from http.server import ThreadingHTTPServer
import json
import os
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_first_search_local as fixtures
from draft_helpers import BODY
from dsh_retrieval.adapters.task_storage.harness import SQLiteHarnessJournal
from dsh_retrieval.agent_jobs import AgentJobRegistry, AgentJobService, AgentJobDispatcher
from dsh_retrieval.api import create_handler
from dsh_retrieval.confirmed_terms_agent import ConfirmedTermsLinks, ConfirmedTermsRetrievalAgent
from dsh_retrieval.confirmed_terms_contract import WORKFLOW
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import preview_edited_groups
from dsh_retrieval.query_branches import compile_query_branches
from dsh_retrieval.query_spec import digest
from dsh_retrieval.retrieval_contracts import RetrievalScope
from dsh_retrieval.search_budget import SearchBudget
from dsh_retrieval.search_dispatch import SearchDispatchCoordinator
from dsh_retrieval.chat_ui import CHAT_JS


class ConfirmedTermsAgentTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.FirstSearchLocalTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.service, self.engine, self.actor = self.f.tasks, self.f.local, self.f.actor
        self.revoked = False
        self.journal = SQLiteHarnessJournal(self.service.repository, self.f.projects)
        self.links = ConfirmedTermsLinks(self.journal)
        self.agent = ConfirmedTermsRetrievalAgent(self.service, self.engine, self.links, self.sources)
        registry = AgentJobRegistry(); registry.register(self.agent)
        self.jobs = AgentJobService(registry=registry, links=self.links)
        self.worker = AgentJobDispatcher(self.jobs, start=False); self.addCleanup(self.worker.close)
        self.agent.notify = self.worker.notify
        self.window = SimpleNamespace(confirmed_terms=self.agent, jobs=self.jobs)
        self.service.agent_execution = self.window
        self.dispatch = SearchDispatchCoordinator(self.service.repository, self.engine, start=False,
            confirmed_terms_agent=self.agent)
        self.addCleanup(self.dispatch.close)
        handler = create_handler(self.service, first_search=self.engine, search_dispatch=self.dispatch)
        self.addCleanup(handler.task_commands.close)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), thread.join(2)))
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def automatic_intake(self):
        from test_multi_rule_pipeline import MultiRulePipelineTests
        from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
        from dsh_retrieval.confirmed_cleaning import ConfirmedCleaningService
        from dsh_retrieval.cleaning_dispatch import CleaningDispatchCoordinator
        from contextlib import contextmanager
        from io import BytesIO
        fixture = MultiRulePipelineTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        repo = SQLiteMultiRuleApprovalRepository(self.f.ledger, projects=self.f.projects,
                                                 authorization=self.engine._authority())
        class Gateway:
            reads = 0
            @contextmanager
            def open_read(gateway, context, ref, *, max_bytes):
                gateway.reads += 1
                manifest = self.engine.repository.get_current_candidate_manifest(self.actor, context.task_id)
                media = next(item.media for item in manifest.proposal.items if item.media.authorized_media_ref == ref)
                yield BytesIO(media.media_unit_id.encode())
        cleaning = ConfirmedCleaningService(repo, registry=fixture.registry, gateway=Gateway())
        dispatcher = CleaningDispatchCoordinator(repo, cleaning, start=False)
        self.addCleanup(dispatcher.close)
        self.agent.configure_intake(cleaning, dispatcher)
        return cleaning, dispatcher, fixture.detector

    def auto_body(self, *, review=False):
        body = self.body()
        body['query_spec']['raw_request'] = '狗和猫共5张'
        body['query_spec']['grouped_expansion'] = preview_edited_groups(None, [
            {'primary_label':'狗','auxiliary_terms':['柯基']},
            {'primary_label':'猫','auxiliary_terms':['橘猫']}])['plan']
        body['query_spec']['retrieval_execution'] = self.agent.policy(self.actor, 'demo',
            candidate_intake='manual' if review else 'auto')
        body['query_spec']['rules'] = [{'rule_id':'no-watermark', 'predicate_ref':'watermark.present@1',
            'operator':'eq','value':False,'hardness':'hard','origin':'user_confirmed'}]
        body['node_modes'].update(retrieval='auto', cleaning='auto')
        return body

    def create_auto(self, *, review=False):
        draft = self.draft(self.auto_body(review=review))
        status, task = self.confirm(draft)
        self.assertEqual(status, 202, task)
        return task, task['search_enqueue']['job_ref']

    def wait_intake(self, task_id, ref):
        for _ in range(200):
            self.worker.drain()
            view = self.jobs.get(self.actor, task_id, ref)
            if view['status'] in ('completed', 'waiting_confirmation', 'failed', 'unknown', 'cancelled'):
                return view
            time.sleep(.01)
        self.fail('automatic intake did not reach a durable checkpoint')

    def test_auto_intake_fixes_candidates_and_queues_real_cleaning_without_extra_owner_action(self):
        cleaning, dispatcher, detector = self.automatic_intake()
        task, ref = self.create_auto(); tid = task['task_id']
        view = self.wait_intake(tid, ref)
        self.assertEqual(view['status'], 'completed', view)
        self.assertIsNone(view['confirmation'])
        self.assertEqual(view['candidate_intake'], 'auto')
        self.assertIsNotNone(view['cleaning_operation_id'])
        pending = cleaning.repository.pending_approved_operations()
        self.assertEqual([p['operation_id'] for p in pending], [view['cleaning_operation_id']])
        self.assertEqual(detector.calls, 0)
        dispatcher._drain()
        self.assertEqual(cleaning.repository.cleaning_dispatch_state(self.actor, tid, view['cleaning_operation_id']), 'completed')
        self.assertGreater(detector.calls, 0)
        calls = detector.calls
        recovered = ConfirmedTermsRetrievalAgent(self.service, self.engine, ConfirmedTermsLinks(self.journal), self.sources)
        recovered.configure_intake(cleaning, dispatcher)
        recovered.advance(self.actor, tid, ref); dispatcher._drain()
        self.assertEqual(detector.calls, calls)
        self.assertEqual(recovered.artifacts(self.actor, tid, ref)['items'][0]['ref'], self.links.get(self.actor, tid, ref)['links']['manifest_ref'])

    def test_input_only_delegation_freezes_first_pages_without_exhausting_all_cursors(self):
        capabilities = json.loads(Path(self.engine.capabilities_path).read_text())
        for item in capabilities['items']:
            item['max_batch_size'] = 1
        path = self.f.root / 'one-hit-capabilities.json'
        path.write_text(json.dumps(capabilities))
        self.engine.capabilities_path = path
        cleaning, dispatcher, detector = self.automatic_intake()
        task, ref = self.create_auto()
        tid = task['task_id']
        self.agent.advance(self.actor, tid, ref)
        deadline = time.monotonic() + 3
        while not self.engine.progress(self.actor, tid)['initial_window_complete']:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        progress = self.engine.progress(self.actor, tid)
        self.assertTrue(progress['initial_window_complete'])
        self.assertTrue(all(member['committed_pages'] == 1 for member in progress['members']))
        self.assertTrue(any(member['status'] == 'more_available' for member in progress['members']))
        self.assertFalse(progress['pagination_stopped'])
        view = self.wait_intake(tid, ref)
        self.assertEqual(view['status'], 'completed', view)
        self.assertEqual(len(cleaning.repository.pending_approved_operations()), 1)
        dispatcher._drain()
        self.assertGreater(detector.calls, 0)
        calls = detector.calls
        self.agent.advance(self.actor, tid, ref)
        dispatcher._drain()
        self.assertEqual(detector.calls, calls)

    def test_optional_candidate_review_still_waits_then_queues_cleaning(self):
        cleaning, _, detector = self.automatic_intake()
        task, ref = self.create_auto(review=True); tid=task['task_id']
        view = self.wait_intake(tid, ref)
        self.assertEqual(view['status'], 'waiting_confirmation')
        self.assertEqual(cleaning.repository.pending_approved_operations(), ())
        self.assertEqual(detector.calls, 0)
        self.jobs.act(self.actor, tid, ref, 'confirm', 'review-and-clean',
                      {k:v for k,v in view['confirmation'].items() if k!='candidate_count'} | {'action':'confirm'})
        self.assertEqual(len(cleaning.repository.pending_approved_operations()), 1)
        self.assertEqual(self.jobs.get(self.actor, tid, ref)['status'], 'completed')

    def test_auto_intake_budget_exhaustion_keeps_manifest_but_never_approves_cleaning(self):
        cleaning, _, _ = self.automatic_intake()
        original = cleaning.prepare
        def costly(*args):
            return {**original(*args), 'estimated_vlm_calls': 10}
        cleaning.prepare = costly
        body = self.auto_body();body['query_spec']['refill_limits'] = {
            'max_rounds':None,'max_vlm_calls':1,'no_progress_rounds':2}
        draft=self.draft(body);status, task=self.confirm(draft);self.assertEqual(status,202,task)
        view=self.wait_intake(task['task_id'],task['search_enqueue']['job_ref'])
        self.assertEqual((view['status'],view['code']), ('failed','VLM_CALL_LIMIT_REACHED'))
        self.assertEqual(cleaning.repository.pending_approved_operations(), ())
        self.assertIsNotNone(self.engine.repository.get_current_candidate_manifest(self.actor, task['task_id']))

    def test_auto_intake_cancel_or_source_revocation_cannot_approve_cleaning(self):
        cleaning, _, _ = self.automatic_intake()
        task,ref=self.create_auto();self.revoked=True
        view=self.wait_intake(task['task_id'],ref)
        self.assertEqual(view['status'],'failed')
        self.assertEqual(cleaning.repository.pending_approved_operations(), ())

    def test_auto_cleaning_crash_after_approval_replays_same_operation(self):
        from unittest.mock import patch
        cleaning, dispatcher, _ = self.automatic_intake()
        task, ref=self.create_auto();tid=task['task_id']
        self.agent.advance(self.actor,tid,ref)
        deadline = time.monotonic() + 3
        while not self.engine.progress(self.actor,tid)['initial_window_complete']:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        original=self.links.update
        def crash(*args,**kwargs):
            if kwargs.get('status')=='completed': raise RuntimeError('crash after cleaning approval')
            return original(*args,**kwargs)
        with patch.object(self.links,'update',side_effect=crash):
            with self.assertRaises(RuntimeError): self.agent.advance(self.actor,tid,ref)
        operations=cleaning.repository.pending_approved_operations()
        self.assertEqual(len(operations),1)
        recovered=ConfirmedTermsRetrievalAgent(self.service,self.engine,ConfirmedTermsLinks(self.journal),self.sources)
        recovered.configure_intake(cleaning,dispatcher)
        recovered.advance(self.actor,tid,ref)
        self.assertEqual(recovered.get(self.actor,tid,ref)['cleaning_operation_id'],operations[0]['operation_id'])
        self.assertEqual(len(cleaning.repository.pending_approved_operations()),1)

    def test_revocation_during_cleaning_preparation_prevents_approval(self):
        cleaning, _, _ = self.automatic_intake()
        original=cleaning.prepare
        def revoke(*args):
            result=original(*args);self.revoked=True;return result
        cleaning.prepare=revoke
        task,ref=self.create_auto()
        view=self.wait_intake(task['task_id'],ref)
        self.assertEqual((view['status'],view['code']),('failed','ACCESS_DENIED'))
        self.assertEqual(cleaning.repository.pending_approved_operations(),())

    def test_cancellation_during_preparation_fences_automatic_cleaning(self):
        cleaning, _, _ = self.automatic_intake()
        original=cleaning.prepare
        task,ref=self.create_auto();tid=task['task_id']
        def cancel(*args):
            result=original(*args)
            current=self.service.repository.get(self.actor,tid)
            self.jobs.act(self.actor,tid,ref,'cancel','cancel-before-clean',
                {'action':'cancel','expected_task_version':current.task_version})
            return result
        cleaning.prepare=cancel
        view=self.wait_intake(tid,ref)
        self.assertEqual(view['status'],'cancelled')
        self.assertEqual(cleaning.repository.pending_approved_operations(),())

    def test_new_delegation_requires_explicit_cleaning_mode_and_rules(self):
        from dsh_retrieval.proposals import normalize_proposal
        self.automatic_intake()
        body=self.auto_body();body['node_modes']['cleaning']='manual'
        with self.assertRaises(ServiceError): normalize_proposal(body)
        body=self.auto_body();body['query_spec'].pop('rules')
        with self.assertRaises(ServiceError): normalize_proposal(body)
        body=self.auto_body()
        self.assertEqual(normalize_proposal(body)['node_modes']['refill'],'manual')
        self.assertEqual(body['query_spec']['refill_policy']['mode'],'input_only')

    def test_initial_detection_reservation_is_visible_without_recounting_on_replay(self):
        from dsh_retrieval.confirmed_terms_contract import initial_cleaning_calls
        cleaning, _, _ = self.automatic_intake()
        original=cleaning.prepare
        cleaning.prepare=lambda *args: {**original(*args),'estimated_vlm_calls':3}
        task,ref=self.create_auto();tid=task['task_id']
        view=self.wait_intake(tid,ref)
        self.assertEqual(view['status'],'completed',view)
        with closing(self.service.repository._connect()) as db:
            self.assertEqual(initial_cleaning_calls(db,tid,1,1),3)
            self.assertEqual(initial_cleaning_calls(db,tid,1,2),0)
        self.agent.advance(self.actor,tid,ref)
        with closing(self.service.repository._connect()) as db:
            self.assertEqual(initial_cleaning_calls(db,tid,1,1),3)

    def test_old_delegation_remains_manual_after_automatic_service_is_installed(self):
        body=self.body()  # @1 is already frozen before configuring a new backend.
        cleaning, _, _ = self.automatic_intake()
        draft=self.draft(body);status,task=self.confirm(draft);self.assertEqual(status,202,task)
        view=self.wait_intake(task['task_id'],task['search_enqueue']['job_ref'])
        self.assertEqual(view['status'],'waiting_confirmation')
        self.assertEqual(cleaning.repository.pending_approved_operations(), ())

    def sources(self, actor, project):
        self.f.projects.require_access(actor, project, 'default', 'read')
        if self.revoked: raise ServiceError('ACCESS_DENIED', 'synthetic source revoked', 403)
        return tuple(c.binding for c in self.engine._fixture()[5])

    def call(self, path, body=None, *, key='request', user='user-a'):
        request = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
            headers={'X-Tenant-Id':'tenant-a', 'X-User-Id':user, 'Content-Type':'application/json', 'Idempotency-Key':key})
        try:
            with urlopen(request, timeout=5) as response: return response.status, json.load(response)
        except HTTPError as error:
            with error: return error.code, json.load(error)

    def body(self):
        value = deepcopy(BODY)
        value['query_spec']['raw_request'] = '狗3张、猫2张'
        value['query_spec']['target_count'] = value['query_spec']['refill_policy']['target_count'] = 5
        value['query_spec']['grouped_expansion'] = preview_edited_groups(None, [
            {'primary_label':'狗', 'auxiliary_terms':['柯基'], 'delivery_target':3},
            {'primary_label':'猫', 'auxiliary_terms':['橘猫'], 'delivery_target':2}])['plan']
        value['query_spec']['retrieval_execution'] = self.agent.policy(self.actor, 'demo')
        return value

    def draft(self, body=None):
        status, draft = self.call('/v1/retrieval/projects/demo/drafts', body or self.body(), key='terms-draft')
        self.assertEqual(status, 200, draft)
        return draft

    def confirm(self, draft):
        return self.call('/v1/retrieval/tasks', dict(draft_id=draft['draft_id'],
            expected_draft_version=draft['draft_version'], proposal_digest=draft['proposal_digest'], action='confirm'), key='terms-confirm')

    def create(self):
        draft = self.draft(); status, task = self.confirm(draft)
        self.assertEqual(status, 202, task)
        return draft, task, task['search_enqueue']['job_ref']

    def wait_review(self, task_id, ref):
        for _ in range(150):
            self.worker.drain()
            view = self.jobs.get(self.actor, task_id, ref)
            if view['status'] == 'waiting_confirmation': return view
            if view['status'] in ('failed', 'unknown'): self.fail(str(view))
            time.sleep(.01)
        self.fail('candidate review not reached')

    def test_public_confirmation_routes_all_terms_and_group_quotas_to_execution_agent(self):
        draft, task, ref = self.create(); tid = task['task_id']
        self.assertEqual(task['search_enqueue']['backend'], 'confirmed-terms-agent')
        self.assertEqual(self.jobs.get(self.actor, tid, ref)['status'], 'queued')
        self.assertIsNone(self.engine.repository.get_current(self.actor, tid))
        _, binding = self.call(f'/v1/retrieval/tasks/{tid}/read-binding')
        original = binding['proposal']['query_spec']
        expected = compile_query_branches(original)['branches']
        review = self.wait_review(tid, ref)
        self.assertEqual(review['query_terms'], [b['query_text'] for b in expected])
        approved = self.engine.repository.get_current(self.actor, tid)
        self.assertEqual({m.branch_id for m in approved.members}, {b['branch_id'] for b in expected})
        self.assertEqual([g['delivery_target'] for g in original['grouped_expansion']['groups']], [3, 2])
        before = self.engine.search_budget.snapshot(approved.scope)
        status, replay = self.confirm(draft)
        self.assertEqual(status, 202, replay); self.assertEqual(replay['search_enqueue']['job_ref'], ref)
        self.worker.drain()
        self.assertEqual(self.engine.search_budget.snapshot(approved.scope)['calls_reserved'], before['calls_reserved'])
        _, workspace = self.call(f'/v1/retrieval/tasks/{tid}/workspace')
        self.assertEqual(workspace['retrieval_agent_job']['job_ref'], ref)
        # The original explicit candidate gate remains usable from the unchanged Chat.
        c = review['confirmation']
        status, manifest = self.call(f'/v1/retrieval/tasks/{tid}/first-search/manifest',
            {k:v for k,v in c.items() if k in ('proposal_digest','expected_task_version')} | {'action':'confirm'}, key='candidate-confirm')
        self.assertEqual(status, 202, manifest)
        self.worker.drain(); self.assertEqual(self.jobs.get(self.actor, tid, ref)['status'], 'completed')
        artifact = self.jobs.artifacts(self.actor, tid, ref)['items'][0]
        self.assertEqual(artifact['ref'], manifest['manifest_ref'])
        self.assertIsNone(self.jobs.get(self.actor, tid, ref)['accepted_count'])
        # Reopening the job cannot regenerate terms, approve, or search again.
        recovered = ConfirmedTermsRetrievalAgent(self.service, self.engine, ConfirmedTermsLinks(self.journal), self.sources)
        self.assertEqual(recovered.get(self.actor, tid, ref)['status'], 'completed')
        recovered.advance(self.actor, tid, ref)
        self.assertEqual(recovered.artifacts(self.actor, tid, ref)['items'][0]['ref'], artifact['ref'])
        self.assertEqual(self.engine.search_budget.snapshot(approved.scope)['calls_reserved'], before['calls_reserved'])

    def test_delegation_is_explicit_and_cannot_forge_or_expand_backend_policy(self):
        body = self.body(); policy = body['query_spec']['retrieval_execution']
        policy['max_calls'] += 1
        policy['policy_digest'] = digest({k:v for k,v in policy.items() if k!='policy_digest'})
        draft = self.draft(body)
        status, value = self.confirm(draft)
        self.assertEqual(status, 409, value)
        with closing(self.service.repository._connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM task_read_summaries').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_job_links').fetchone()[0], 0)

    def test_revocation_between_review_and_confirmation_does_not_create_a_task(self):
        draft = self.draft(); self.revoked = True
        status, value = self.confirm(draft)
        self.assertEqual(status, 403, value)
        self.assertEqual(self.links.pending(), ())

    def test_owner_cancel_fences_pending_execution_and_old_replies(self):
        _, task, ref = self.create(); tid = task['task_id']
        body = {'action':'cancel','expected_task_version':task['task_version']}
        cancelled, replayed = self.jobs.act(self.actor, tid, ref, 'cancel', 'cancel', body)
        self.assertFalse(replayed); self.assertEqual(cancelled['status'], 'cancelled')
        self.assertTrue(self.jobs.act(self.actor, tid, ref, 'cancel', 'cancel', body)[1])
        self.worker.drain(); self.agent.advance(self.actor, tid, ref)
        self.assertIsNone(self.engine.repository.get_browse_current(self.actor, tid))
        with self.assertRaises(ServiceError): self.links.update(self.actor, tid, ref, status='completed')
        with self.assertRaises(ServiceError): self.agent.artifacts(self.actor, tid, ref)

    def test_reads_and_legacy_start_cannot_bypass_the_agent_and_other_owner_is_denied(self):
        _, task, ref = self.create(); tid = task['task_id']
        for path in [f'/v1/retrieval/tasks/{tid}/agent-jobs/{ref}', f'/v1/retrieval/tasks/{tid}/workspace']:
            status, value = self.call(path); self.assertEqual(status, 200, value)
        self.assertIsNone(self.engine.repository.get_current(self.actor, tid))
        status, value = self.call(f'/v1/retrieval/tasks/{tid}/first-search/tick', {'max_requests':1})
        self.assertEqual(status, 409, value)
        status, _ = self.call(f'/v1/retrieval/tasks/{tid}/agent-jobs/{ref}', user='other')
        self.assertIn(status, (403, 404))

    def test_network_unknown_is_not_resent_after_reopening_executor(self):
        _, task, ref = self.create(); tid = task['task_id']
        original = self.engine._adapter_for_plan; calls = []
        def adapter(*args):
            result = original(*args)
            def fail(*args):
                calls.append(True)
                raise ServiceError('NETWORK_UNAVAILABLE', 'synthetic ambiguous transport', 503, True)
            result.search = fail
            return result
        self.engine._adapter_for_plan = adapter
        self.worker.drain()
        for _ in range(100):
            self.worker.drain()
            view = self.agent.get(self.actor, tid, ref)
            if view['status'] == 'unknown': break
            time.sleep(.01)
        self.assertEqual(view['status'], 'unknown'); self.assertEqual(len(calls), 1)
        recovered = self.f.make_local(start=False)
        with self.assertRaises(ServiceError) as error: recovered.tick(self.actor, tid, max_requests=1)
        self.assertEqual(error.exception.code, 'AGENT_SEARCH_OUTCOME_UNKNOWN')
        self.worker.drain(); self.assertEqual(len(calls), 1)
        self.assertIsNone(view['accepted_count'])

    def test_term_edits_keep_the_agent_route_and_supersede_old_job_callbacks(self):
        _, task, old_ref = self.create(); tid = task['task_id']
        plan = preview_edited_groups(None, [
            {'primary_label':'狗','auxiliary_terms':['金毛'],'delivery_target':4},
            {'primary_label':'猫','auxiliary_terms':['橘猫'],'delivery_target':2}])['plan']
        draft, _ = self.service.save_feedback(self.actor, tid, 'edit-terms', {
            'text':'狗改为4张并查金毛，猫2张不变','scope':'all','expected_revision':1,
            'expected_task_version':task['task_version'],'grouped_expansion':plan,'target_count':6})
        self.assertIn('retrieval_execution', draft['proposal']['query_spec'])
        self.service.confirm_feedback(self.actor, tid, 'confirm-edit', {
            'draft_id':draft['draft_id'],'expected_draft_version':draft['draft_version'],
            'proposal_digest':draft['proposal_digest'],'expected_task_version':task['task_version'],'action':'confirm'})
        result = self.dispatch.dispatch_task(tid)
        self.assertEqual(result['backend'], 'confirmed-terms-agent')
        self.assertNotEqual(result['job_ref'], old_ref)
        self.assertEqual(self.agent.get(self.actor, tid, old_ref)['status'], 'cancelled')
        self.agent.advance(self.actor, tid, old_ref)
        with self.assertRaises(ServiceError): self.links.update(self.actor, tid, old_ref, status='completed')
        review = self.wait_review(tid, result['job_ref'])
        self.assertTrue(any('金毛' in term for term in review['query_terms']))
        self.assertFalse(any('柯基' in term for term in review['query_terms']))

    def test_confirmation_rechecks_scope_inside_the_feedback_transaction(self):
        _, task, ref = self.create(); tid = task['task_id']
        plan = preview_edited_groups(None, [
            {'primary_label':'狗','auxiliary_terms':['金毛'],'delivery_target':3},
            {'primary_label':'猫','auxiliary_terms':['橘猫'],'delivery_target':2}])['plan']
        draft, _ = self.service.save_feedback(self.actor, tid, 'change', {
            'text':'狗组换金毛','scope':'all','expected_revision':1,
            'expected_task_version':task['task_version'],'grouped_expansion':plan,'target_count':5})
        original = self.agent.require; calls = []
        def revoke_after_precheck(*args, **kwargs):
            if calls: self.revoked = True
            calls.append(True); return original(*args, **kwargs)
        self.agent.require = revoke_after_precheck
        with self.assertRaises(ServiceError) as error:
            self.service.confirm_feedback(self.actor, tid, 'confirm-change', {
                'draft_id':draft['draft_id'],'expected_draft_version':draft['draft_version'],
                'proposal_digest':draft['proposal_digest'],'expected_task_version':task['task_version'],'action':'confirm'})
        self.assertEqual(error.exception.code, 'ACCESS_DENIED'); self.assertEqual(len(calls), 2)
        current = self.service.repository.get(self.actor, tid)
        self.assertEqual((current.confirmed_revision,current.run_generation,current.task_version),(1,1,task['task_version']))
        self.assertEqual(self.agent.get(self.actor, tid, ref)['status'], 'queued')

    def test_original_chat_confirmation_passes_the_complete_policy_and_only_polls(self):
        self.assertIsNotNone(shutil.which('node'), 'Node required for original Chat wiring')
        body = self.body(); plan = body['query_spec']['grouped_expansion']
        proposal = {'raw':body['query_spec']['raw_request'],'count':5,'plan':plan,'lines':'狗 | 柯基\n猫 | 橘猫'}
        source = CHAT_JS[CHAT_JS.index('function draftBody('):CHAT_JS.index('function groupPreviewBody(')]
        source += CHAT_JS[CHAT_JS.index('async function confirmGuidedExpansion('):CHAT_JS.index('async function guidedStartSearch(')]
        script = r"""
const assert=require('node:assert/strict');const proposal=JSON.parse(process.env.PROPOSAL),policy=JSON.parse(process.env.POLICY);
const state={proposedExpansion:proposal,task:null,resumeDraft:null};const fields=new Map(),paths=[];
const $=id=>{if(!fields.has(id))fields.set(id,{value:'',classList:{add(){},remove(){}}});return fields.get(id)};
async function api(path,options={}){
 paths.push([path,options.method||'GET']);
 const r=await fetch(process.env.API+path,{method:options.method||'GET',headers:{'X-Tenant-Id':'tenant-a','X-User-Id':'user-a','Content-Type':'application/json','Idempotency-Key':options.keyValue||'node-read'},body:options.body?JSON.stringify(options.body):undefined});
 const v=await r.json();if(!r.ok)throw Error(JSON.stringify(v));return v;
}
let sequence=0,polls=0;const store={setItem(){},removeItem(){}};
const confirm=new Function('state','$','api','key','setBusy','sessionStorage','localStorage','refreshTaskSidebar','retireDraftActions','message','refreshHistory','guidedStartSearch','guidedTick','timeline',
 process.env.SOURCE+';return confirmGuidedExpansion')(state,$,api,()=> 'node-'+(++sequence),()=>{},store,store,
 async()=>{},()=>{},()=>{},async()=>{},()=>{throw Error('legacy start bypass')},async()=>{polls++},{querySelectorAll(){return []}});
(async()=>{
 await confirm(proposal,{value:proposal.lines,disabled:false},{enabled:false,libraries:[],limits:{max_rounds:null,max_vlm_calls:null,no_progress_rounds:2}},policy);
 assert(state.task,'task confirmation failed');assert.equal(state.task.search_enqueue.backend,'confirmed-terms-agent');assert.equal(polls,1);
 assert(!paths.some(([path,method])=>method==='POST'&&/first-search|agent-jobs/.test(path)));
 const binding=await api('/v1/retrieval/tasks/'+state.task.task_id+'/read-binding');
 assert.deepEqual(binding.proposal.query_spec.retrieval_execution,policy);assert.deepEqual(binding.proposal.query_spec.grouped_expansion,proposal.plan);
 console.log(JSON.stringify({task_id:state.task.task_id,job_ref:state.task.search_enqueue.job_ref}));
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node','-e',script], env={**os.environ,'SOURCE':source,'API':self.base,
            'PROPOSAL':json.dumps(proposal),'POLICY':json.dumps(body['query_spec']['retrieval_execution'])},
            capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        created=json.loads(result.stdout)
        self.assertEqual(self.jobs.get(self.actor,created['task_id'],created['job_ref'])['status'],'queued')
        self.assertIsNone(self.engine.repository.get_current(self.actor,created['task_id']))

    def test_original_conversation_delete_purges_execution_job_and_reopen_accepts_storage(self):
        _, task, ref = self.create()
        # This is an isolated synthetic ledger, not a live user deletion.
        self.service.hard_delete_conversation(self.actor, task['conversation_id'])
        with closing(self.service.repository._connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_job_links WHERE job_ref=?',(ref,)).fetchone()[0],0)
        reopened=type(self.service.repository)(self.f.ledger)
        self.assertEqual(reopened.pending_search_dispatches(),self.service.repository.pending_search_dispatches())

    def test_confirmed_budget_is_pinned_before_dispatch_and_survives_larger_runtime_limits(self):
        _, task, ref = self.create()
        root = self.service.repository.get(self.actor,task['task_id']); scope=RetrievalScope.from_task(root)
        initial=self.engine.search_budget.snapshot(scope)
        self.assertEqual(initial['calls_reserved'],0); self.assertEqual(initial['hit_slots_reserved'],0)
        larger=replace(self.engine.search_limits,max_calls=initial['max_calls']+100,max_hit_slots=initial['max_hit_slots']+1000)
        reopened=SearchBudget(self.f.ledger,larger)
        self.assertEqual(reopened.snapshot(scope)['max_calls'],initial['max_calls'])
        self.assertEqual(reopened.snapshot(scope)['max_hit_slots'],initial['max_hit_slots'])
        self.assertEqual(self.agent.get(self.actor,root.task_id,ref)['status'],'queued')

    def test_committed_owner_manifest_resolves_late_unknown_without_new_search(self):
        _, task, ref=self.create(); tid=task['task_id']; review=self.wait_review(tid,ref)
        approved=self.engine.repository.get_current(self.actor,tid)
        before=self.engine.search_budget.snapshot(approved.scope)['calls_reserved']
        self.links.update(self.actor,tid,ref,status='unknown',code='AGENT_SEARCH_OUTCOME_UNKNOWN')
        c=review['confirmation']
        manifest,_=self.engine.confirm_manifest(self.actor,tid,key='owner-fix',action='confirm',
            proposal_digest=c['proposal_digest'],expected_task_version=c['expected_task_version'])
        self.assertEqual(self.agent.get(self.actor,tid,ref)['status'],'completed')
        self.assertEqual(self.agent.artifacts(self.actor,tid,ref)['items'][0]['ref'],manifest['manifest_ref'])
        self.assertEqual(self.engine.search_budget.snapshot(approved.scope)['calls_reserved'],before)
        # Crash after the original output committed but before linking dispatch:
        # recovery must use that receipt before considering another queue send.
        self.links.update(self.actor,tid,ref,status='queued',dispatch_result=None)
        self.agent.advance(self.actor,tid,ref)
        self.assertEqual(self.links.get(self.actor,tid,ref)['links']['status'],'completed')
        self.assertEqual(self.engine.search_budget.snapshot(approved.scope)['calls_reserved'],before)

    def test_page_publication_failure_does_not_resend_network_or_report_empty_success(self):
        _,task,ref=self.create();tid=task['task_id'];calls=[]
        original=self.engine.repository.commit_page
        def fail(*args,**kwargs):
            calls.append(True);raise ServiceError('STORAGE_UNAVAILABLE','synthetic lost page publication',503,True)
        self.engine.repository.commit_page=fail
        self.addCleanup(lambda:setattr(self.engine.repository,'commit_page',original))
        self.worker.drain()
        for _ in range(100):
            self.worker.drain();view=self.agent.get(self.actor,tid,ref)
            if view['status']=='unknown':break
            time.sleep(.01)
        self.assertEqual(view['status'],'unknown');self.assertEqual(len(calls),1)
        self.engine.repository.commit_page=original
        recovered=self.f.make_local(start=False)
        with self.assertRaises(ServiceError) as error:recovered.tick(self.actor,tid,max_requests=1)
        self.assertEqual(error.exception.code,'AGENT_SEARCH_OUTCOME_UNKNOWN')
        self.assertIsNone(view['accepted_count']);self.assertEqual(self.agent.artifacts(self.actor,tid,ref)['items'],[])

    def test_explicit_workflow_api_reuses_job_and_owner_confirms_original_candidates(self):
        _,task,ref=self.create();tid=task['task_id']
        status,replay=self.call(f'/v1/retrieval/tasks/{tid}/agent-jobs/prepare',{
            'capability':'retrieval','workflow_ref':WORKFLOW,'planning':{'expected_revision':1,
                'expected_generation':1,'expected_task_version':task['task_version']}},key='external-adopt')
        self.assertEqual(status,200,replay);self.assertEqual(replay['job_ref'],ref)
        self.assertIsNone(self.engine.repository.get_current(self.actor,tid))
        view=self.wait_review(tid,ref);c=view['confirmation']
        status,confirmed=self.call(f'/v1/retrieval/tasks/{tid}/agent-jobs/{ref}/confirm',{
            'action':'confirm',**{k:c[k] for k in ('checkpoint','proposal_digest','expected_task_version')}},key='owner-candidates')
        self.assertEqual(status,200,confirmed);self.assertEqual(confirmed['status'],'completed')
        self.assertTrue(self.jobs.artifacts(self.actor,tid,ref)['items'][0]['manifest_ref'])

    def test_known_empty_pages_do_not_create_an_empty_input_manifest_or_confirmation(self):
        _,task,ref=self.create();tid=task['task_id'];original=self.engine._adapter_for_plan
        def adapter(*args):
            result=original(*args);search=result.search
            result.search=lambda *args:replace(search(*args),hits=(),next_cursor=None,stop_reason='exhausted')
            return result
        self.engine._adapter_for_plan=adapter
        for _ in range(100):
            self.worker.drain();view=self.agent.get(self.actor,tid,ref)
            if view['status']=='completed':break
            time.sleep(.01)
        self.assertEqual(view['status'],'completed');self.assertEqual(view['code'],'NO_CANDIDATES')
        self.assertIsNone(view['accepted_count']);self.assertFalse(view['artifacts_ready'])
        self.assertEqual(self.agent.artifacts(self.actor,tid,ref)['items'],[])
        status,workspace=self.call(f'/v1/retrieval/tasks/{tid}/workspace')
        self.assertEqual(status,200,workspace);self.assertEqual(workspace['workflow']['state'],'retrieval_empty')
        self.assertNotIn('confirm_candidates',[a['id'] for a in workspace['workflow']['available_actions']])


if __name__ == '__main__': unittest.main()
