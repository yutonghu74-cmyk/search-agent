from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from http.server import ThreadingHTTPServer
import json
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_agent_jobs as fixtures
from draft_helpers import BODY
from dsh_retrieval.adapters.harness.composition import enable_retrieval_agent_window
from dsh_retrieval.agent_jobs import AgentJobDispatcher
from dsh_retrieval.api import create_handler
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.proposals import normalize_proposal
from dsh_retrieval.query_spec import digest
from dsh_retrieval.search_dispatch import SearchDispatchCoordinator


class AgentRetrievalWindowTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AgentJobsTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.actor, self.service, self.repo = self.f.actor, self.f.domain.service, self.f.domain.repo
        self.worker = AgentJobDispatcher(self.f.jobs, start=True, poll_seconds=.05)
        self.addCleanup(self.worker.close)
        self.source_denied = False
        def sources(actor, project):
            self.service.projects.require_access(actor, project, 'default', 'read')
            if self.source_denied: raise ServiceError('ACCESS_DENIED', 'synthetic source revoked', 403)
            return (self.f.domain.binding,)
        self.window = enable_retrieval_agent_window(self.service, self.f.jobs, self.worker,
            enabled_projects=frozenset({'demo'}), model_profile_ref='model', sources_provider=sources,
            cursor_secret=b'synthetic-agent-window-cursor-secret', chat_entry='candidate')
        self.legacy_calls = []
        calls = self.legacy_calls
        class Legacy:
            def auto_start(_, *args, **kwargs):
                calls.append('auto_start'); return {'status': 'queued'}
        self.legacy = Legacy()
        handler = create_handler(self.service, first_search=self.legacy)
        self.addCleanup(handler.task_commands.store.connection.close)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), thread.join(timeout=2)))
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def call(self, path, body=None, *, key='request', user='owner', binary=False):
        request = Request(self.base+path, headers={'X-Tenant-Id': 'tenant', 'X-User-Id': user,
            'Content-Type': 'application/json', 'Idempotency-Key': key},
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read() if binary else json.load(response)
        except HTTPError as error:
            with error: return error.code, json.load(error)

    def body(self):
        value = deepcopy(BODY)
        spec = value['query_spec']; spec.update(raw_request='找 2 张客厅图片', target_count=2)
        spec['refill_policy']['target_count'] = 2
        spec['agent_requirements'] = {'schema_version': 'agent-requirements@2', 'processing': [],
            'autonomy': {'library_ids': [self.f.domain.binding.library_id], 'methods': ['vector'],
                'actions': ['rewrite_query'], 'max_rounds': 1, 'max_no_progress_rounds': 1,
                'max_calls': 2, 'max_seconds': 60}}
        return value

    def draft(self, body=None, key='window-draft'):
        code, draft = self.call('/v1/retrieval/projects/demo/drafts', body or self.body(), key=key)
        self.assertEqual(code, 200, draft)
        return draft

    def confirm_task(self, draft, key='root-confirm'):
        return self.call('/v1/retrieval/tasks', {'action': 'confirm', 'draft_id': draft['draft_id'],
            'expected_draft_version': draft['draft_version'], 'proposal_digest': draft['proposal_digest']}, key=key)

    def create(self):
        draft = self.draft()
        code, result = self.confirm_task(draft)
        self.assertEqual(code, 202, result)
        return draft, result

    def workspace(self, task_id):
        code, value = self.call(f'/v1/retrieval/tasks/{task_id}/workspace')
        self.assertEqual(code, 200, value)
        return value

    def confirm_job(self, task_id, view):
        c = view['confirmation']
        code, result = self.call(f'/v1/retrieval/tasks/{task_id}/agent-jobs/{view["job_ref"]}/confirm',
            {'action': 'confirm', 'checkpoint': c['checkpoint'], 'proposal_digest': c['proposal_digest'],
             'expected_task_version': c['expected_task_version']}, key='confirm-'+c['checkpoint'])
        self.assertEqual(code, 200, result)
        return result

    def wait(self, task_id, phase):
        for _ in range(180):
            value = self.workspace(task_id); job = value['agent_job']
            if job and job['phase'] == phase: return value
            if job and job['status'] == 'failed': self.fail(str(job))
            time.sleep(.01)
        self.fail('window did not reach '+phase)

    def candidates_ready(self):
        draft, task = self.create(); task_id = task['task_id']
        planning = self.workspace(task_id)['agent_job']; self.confirm_job(task_id, planning)
        code, result = self.call(f'/v1/retrieval/tasks/{task_id}/agent-jobs/{planning["job_ref"]}/start',
            {'action': 'start', **planning['planning_review']}, key='start')
        self.assertEqual(code, 202, result)
        retrieval = self.wait(task_id, 'retrieval'); self.confirm_job(task_id, retrieval['agent_job'])
        return draft, task, self.wait(task_id, 'candidates')

    def task_count(self):
        with closing(self.repo._connect()) as db:
            return len(self.repo._load(db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())._tasks)

    def test_conversation_entry_keeps_ordinary_confirmation_and_agent_api_independent(self):
        self.window.chat_entry = 'conversation'
        code, profile = self.call('/v1/retrieval/projects/demo/agent-retrieval')
        self.assertEqual(code, 200); self.assertTrue(profile['enabled'])
        self.assertEqual(profile['chat_entry'], 'conversation')
        body = deepcopy(BODY)
        from dsh_retrieval.expansion_models import preview_edited_groups
        body['query_spec']['grouped_expansion'] = preview_edited_groups(None, [
            {'primary_label': '客厅', 'auxiliary_terms': ['明亮的客厅', '客厅里的沙发', '带窗户的客厅']}])['plan']
        draft = self.draft(body, key='conversation-draft')
        code, task = self.confirm_task(draft, key='conversation-confirm')
        self.assertEqual(code, 202, task)
        self.assertIn('auto_start', self.legacy_calls)
        code, binding = self.call(f"/v1/retrieval/tasks/{task['task_id']}/read-binding")
        self.assertEqual(code, 200)
        self.assertNotIn('agent_requirements', binding['proposal']['query_spec'])
        self.assertEqual(binding['proposal']['query_spec']['grouped_expansion'], body['query_spec']['grouped_expansion'])
        self.assertEqual((self.f.encodings, self.f.searches, len(self.f.f.calls)), (0, 0, 0))
        # The new independent capability remains available through explicit API admission.
        self.legacy_calls.clear()
        _, independent = self.create()
        self.assertTrue(self.workspace(independent['task_id'])['agent_retrieval'])
        self.assertEqual(self.legacy_calls, [])

    def test_real_public_task_confirmation_enters_same_window_without_legacy_dispatch(self):
        code, profile = self.call('/v1/retrieval/projects/demo/agent-retrieval')
        self.assertEqual(code, 200); self.assertTrue(profile['enabled']); self.assertTrue(profile['available'])
        draft = self.draft()
        self.assertEqual(self.task_count(), 1)  # preexisting fixture only
        self.assertEqual((self.f.encodings, self.f.searches, len(self.f.f.calls)), (0, 0, 0))
        code, task = self.confirm_task(draft)
        self.assertEqual(code, 202, task)
        self.assertEqual(task['search_enqueue']['backend'], 'retrieval-agent')
        self.assertEqual(self.task_count(), 2)
        view = self.workspace(task['task_id'])
        self.assertTrue(view['agent_retrieval']); self.assertEqual(view['agent_job']['status'], 'waiting_confirmation')
        self.assertEqual(view['raw_request'], '找 2 张客厅图片'); self.assertEqual(view['target_count'], 2)
        self.assertEqual((self.f.encodings, self.f.searches, len(self.f.f.calls)), (0, 0, 0))
        self.assertEqual(self.legacy_calls, [])
        self.assertEqual(tuple(self.repo.pending_search_dispatches()), ())
        code, page = self.call('/chat', binary=True)
        self.assertEqual(code, 200); self.assertIn(b'prepare-task', page)

    def test_same_task_window_candidates_preview_and_artifact_delivery(self):
        draft, task, view = self.candidates_ready(); task_id = task['task_id']
        code, candidates = self.call(f'/v1/retrieval/tasks/{task_id}/candidates?limit=60&query=')
        self.assertEqual(code, 200, candidates)
        self.assertEqual(len(candidates['items']), 1); self.assertIsNone(candidates['accepted_count'])
        item = candidates['items'][0]
        self.assertEqual(candidates['query_terms'], view['agent_job']['query_terms'])
        self.assertEqual(item['query_text'], view['agent_job']['query_terms'][0])
        code, content = self.call(f'/v1/retrieval/tasks/{task_id}/candidates/{item["candidate_id"]}/preview?ref={item["preview_ref"]}', binary=True)
        self.assertEqual(code, 200, content); self.assertEqual(content, self.f.content)
        self.confirm_job(task_id, view['agent_job'])
        final = self.workspace(task_id)
        self.assertEqual(final['agent_job']['status'], 'completed')
        self.assertIsNone(final['progress']['counts']['accepted'])
        self.assertTrue(all(n['display_status']=='本次不执行' for n in final['progress']['nodes'][2:]))
        code, artifacts = self.call(f'/v1/retrieval/tasks/{task_id}/agent-jobs/{final["agent_job"]["job_ref"]}/artifacts')
        self.assertEqual(code, 200, artifacts); self.assertEqual(len(artifacts['items']), 1)
        self.assertEqual((self.f.encodings, self.f.searches, len(self.f.f.calls)), (1, 1, 1))
        self.assertEqual(self.legacy_calls, [])
        code, repeated = self.confirm_task(draft)
        self.assertEqual(code, 202, repeated); self.assertEqual(task_id, repeated['task_id'])
        self.assertEqual(self.task_count(), 2)

    def test_unsupported_or_excessive_scopes_do_not_create_formal_tasks(self):
        mutations = [lambda b: b['query_spec'].update(target_count=3, refill_policy={**b['query_spec']['refill_policy'], 'target_count':3}),
            lambda b: b['query_spec']['agent_requirements']['autonomy'].update(max_calls=3),
            lambda b: b['query_spec']['agent_requirements']['autonomy'].update(max_rounds=2),
            lambda b: b['query_spec']['agent_requirements']['autonomy'].update(actions=['paginate']),
            lambda b: b['query_spec'].update(source_constraints=[{'library':'not-supported'}]),
            lambda b: b['query_spec'].update(rules=[{'rule_id':'watermark','predicate_ref':'watermark.present@1','operator':'eq','value':False,'hardness':'hard','origin':'user_explicit'}]),
            lambda b: b['query_spec']['agent_requirements']['autonomy'].update(library_ids=['forbidden'])]
        for index, mutate in enumerate(mutations):
            body = self.body(); mutate(body)
            draft = self.draft(body, key='bad-'+str(index))
            code, error = self.confirm_task(draft, key='bad-confirm-'+str(index))
            self.assertEqual(code, 422, error)
            self.assertEqual(self.task_count(), 1)
            self.assertIsNone(self.service.get_draft(self.actor, 'demo', draft['draft_id'])['confirmed_task_id'])
        self.assertEqual((self.f.searches, len(self.f.f.calls)), (0, 0))

    def test_absent_runtime_closed_project_or_worker_keeps_admission_closed(self):
        draft = self.draft()
        probe = self.f.f.old.probe
        self.f.f.old.probe = lambda: replace(probe(), available=False)
        code, error = self.confirm_task(draft)
        self.assertEqual(code, 503, error); self.assertEqual(self.task_count(), 1)
        self.f.f.old.probe = probe
        self.window.enabled_projects = frozenset({'another-project'})
        code, error = self.confirm_task(draft)
        self.assertEqual(code, 422, error); self.assertEqual(self.task_count(), 1)
        self.window.enabled_projects = frozenset({'demo'})
        self.worker.close()
        code, error = self.confirm_task(draft)
        self.assertEqual(code, 503, error); self.assertEqual(self.task_count(), 1)

    def test_permission_change_inside_confirmation_transaction_rolls_back_task_and_batch(self):
        draft = self.draft(); original = self.window.sources_provider; calls = []
        def revoked(actor, project):
            calls.append(1)
            if len(calls) >= 2: raise ServiceError('ACCESS_DENIED', 'synthetic confirmation race', 403)
            return original(actor, project)
        self.window.sources_provider = revoked
        code, error = self.confirm_task(draft)
        self.assertEqual(code, 403, error); self.assertEqual(self.task_count(), 1)
        self.assertIsNone(self.service.get_draft(self.actor, 'demo', draft['draft_id'])['confirmed_task_id'])

    def test_old_execution_entry_points_cannot_bypass_agent_manual_checkpoints(self):
        _, task = self.create(); task_id = task['task_id']
        for suffix, body in [('/first-search/prepare',{}),('/first-search/confirm',{}),('/first-search/tick',{'max_requests':1}),
                             ('/cleaning/prepare',{}),('/refill',{}),('/conversation-turn',{'text':'开始检索'})]:
            code, error = self.call('/v1/retrieval/tasks/'+task_id+suffix, body)
            self.assertEqual(code, 409, (suffix,error)); self.assertEqual(error['code'], 'AGENT_LEGACY_EXECUTION_DENIED')
        self.assertEqual((self.f.searches, len(self.f.f.calls)), (0, 0))

    def test_feedback_cannot_upgrade_or_revise_agent_execution(self):
        _, task = self.create(); task_id = task['task_id']
        draft, _ = self.service.save_feedback(self.actor, task_id, 'window-feedback',
            {'text':'改为狗的图片', 'scope':'all', 'expected_revision':task['confirmed_revision'],
             'expected_task_version':task['task_version']})
        body = {'draft_id':draft['draft_id'], 'expected_draft_version':draft['draft_version'],
            'proposal_digest':draft['proposal_digest'], 'expected_task_version':draft['base_task_version'],
            'action':'confirm'}
        with self.assertRaises(ServiceError) as raised:
            self.service.confirm_feedback(self.actor, task_id, 'apply-window-feedback', body)
        self.assertEqual(raised.exception.code, 'AGENT_REVISION_UNAVAILABLE')
        current = self.repo.get(self.actor, task_id)
        self.assertEqual((current.confirmed_revision,current.run_generation,current.task_version),
            (task['confirmed_revision'],task['run_generation'],task['task_version']))
        self.service.confirm_feedback(self.actor, task_id, 'reject-window-feedback', {**body,'action':'reject'})
        self.assertEqual((self.f.searches,len(self.f.f.calls)), (0,0))

    def test_cursor_preview_scope_and_revocation_are_enforced_in_existing_candidate_window(self):
        _, task, _ = self.candidates_ready(); task_id = task['task_id']
        _, candidates = self.call(f'/v1/retrieval/tasks/{task_id}/candidates?limit=1')
        item = candidates['items'][0]; ref = item['preview_ref']
        path = f'/v1/retrieval/tasks/{task_id}/candidates/{item["candidate_id"]}/preview?ref='
        code, _ = self.call(path+ref, user='other'); self.assertEqual(code, 404)
        code, _ = self.call(path+'broken'); self.assertEqual(code, 422)
        code, value = self.call(f'/v1/retrieval/tasks/{task_id}/candidates/changes?limit=1&cursor='+candidates['changes_cursor'])
        self.assertEqual(code, 200, value); self.assertEqual(value['items'], [])
        self.f.denied = True
        code, _ = self.call(path+ref); self.assertEqual(code, 403)
        code, _ = self.call(f'/v1/retrieval/tasks/{task_id}/candidates'); self.assertEqual(code, 403)

    def test_cancelled_task_and_reopened_service_never_restart_model_or_search(self):
        draft, task = self.create(); task_id = task['task_id']; view = self.workspace(task_id)['agent_job']
        code, cancelled = self.call(f'/v1/retrieval/tasks/{task_id}/agent-jobs/{view["job_ref"]}/cancel',
            {'action':'cancel','expected_task_version':view['task_version']}, key='cancel')
        self.assertEqual(code, 200, cancelled)
        code, repeated = self.confirm_task(draft)
        self.assertEqual(code, 202, repeated); self.assertEqual(task_id, repeated['task_id'])
        self.assertEqual(self.workspace(task_id)['agent_job']['status'], 'cancelled')
        self.assertEqual((self.f.searches, len(self.f.f.calls)), (0, 0))

    def test_default_service_gate_and_legacy_request_behavior_remain(self):
        draft = self.draft(); self.service.agent_execution = None
        code, error = self.confirm_task(draft)
        self.assertEqual(code, 422, error); self.assertEqual(error['code'], 'AGENT_EXECUTION_UNAVAILABLE')
        self.service.agent_execution = self.window
        legacy = self.draft(deepcopy(BODY), key='legacy')
        code, task = self.confirm_task(legacy, key='legacy-confirm')
        self.assertEqual(code, 202, task); self.assertEqual(self.legacy_calls, ['auto_start'])
