from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_agent_jobs as fixtures
from dsh_retrieval.adapters.platform_access import PlatformClient, PlatformIdentity, PlatformProjectAccess
from dsh_retrieval.adapters.task_storage.shared import SQLiteExternalBindings
from dsh_retrieval.agent_jobs import AgentJobDispatcher
from dsh_retrieval.api import create_handler
from dsh_retrieval.shared_access import SharedAccess, SharedPrincipal
from dsh_retrieval.shared_sdk import AgentClient, AgentJobClient, OwnerClient, SharedTransport, SharedApiError


class AgentJobsHttpTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AgentJobsTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.actor, self.task_id = self.f.actor, self.f.task.task_id
        self.member = True
        class Reply(BytesIO): status = 200
        def upstream(request, timeout):
            token = request.get_header('Authorization', '').removeprefix('Bearer ')
            if token not in ('fixture-owner', 'fixture-agent', 'fixture-other'):
                raise HTTPError(request.full_url, 401, 'unauthorized', {}, None)
            user = 'other' if token == 'fixture-other' else 'owner'
            if request.full_url.endswith('/user/info'): data = {'id': user}
            else:
                if not self.member: raise HTTPError(request.full_url, 403, 'revoked', {}, None)
                data = {'id': int(request.get_header('X-app-id')), 'status': 'normal'}
            return Reply(json.dumps({'code': 200, 'data': data}).encode())
        client = PlatformClient('https://fixture.invalid/api', opener=upstream)
        self.identity = PlatformIdentity(client, 'tenant', extract_user_id=lambda value: value['id'])
        self.projects = PlatformProjectAccess(client, 'tenant', credential_for_actor=lambda actor: 'fixture-owner',
            allowed_actions={'read', 'create', 'approve', 'write'}, extract_user_id=lambda value: value['id'])
        self.f.domain.mutate_task(lambda task: setattr(task, 'project_id', '101'))
        self.f.f.routes['101'] = dict(self.f.f.routes['demo'])
        self.f.domain.service.projects = self.projects
        self.f.f.app.projects = self.projects
        self.f.f.app.journal.projects = self.projects
        self.f.retrieval.projects = self.projects
        self.f.manifests.retrieval.projects = self.projects
        self.bindings = SQLiteExternalBindings(self.f.domain.repo, self.projects)
        self.grants = frozenset({'agent.retrieval.read', 'agent.retrieval.prepare', 'agent.retrieval.start', 'task.cancel'})
        def principal(actor, headers):
            if headers['Authorization'] == 'Bearer fixture-owner': return SharedPrincipal(actor, '101', 'human')
            return SharedPrincipal(actor, '101', 'integration', 'synthetic-agent', self.grants, frozenset({self.task_id}))
        self.shared = SharedAccess(self.bindings, resolve_principal=principal)
        handler = create_handler(self.f.domain.service, agent_jobs=self.f.jobs,
            identity_provider=self.identity, identity_mode='platform', shared_access=self.shared)
        self.addCleanup(handler.task_commands.store.connection.close)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(timeout=2)))
        self.base = f'http://127.0.0.1:{server.server_port}'
        def transport(token):
            return SharedTransport(self.base, project_id='101', credential=lambda: token, allow_local_http=True)
        self.agent_transport = transport('fixture-agent')
        self.agent = AgentJobClient(self.agent_transport, external_task_ref='synthetic-training-root')
        self.modules = AgentClient(self.agent_transport, external_task_ref='synthetic-training-root')
        self.owner = OwnerClient(transport('fixture-owner'))
        self.modules.bind(self.task_id, revision=1, generation=1, task_version=1, key='bind')
        self.worker = AgentJobDispatcher(self.f.jobs, start=True, poll_seconds=.05)
        self.addCleanup(self.worker.close)

    def wait_phase(self, ref, phase):
        for _ in range(200):
            value = self.agent.get(self.task_id, ref)
            if value['phase'] == phase: return value
            if value['status'] == 'failed': self.fail(str(value))
            time.sleep(.01)
        self.fail('HTTP job did not reach '+phase)

    def test_external_sdk_calls_the_whole_job_with_explicit_owner_checkpoints(self):
        view = self.agent.prepare(self.task_id, self.f.body['planning'], key='prepare')
        self.assertEqual(view['phase'], 'planning')
        with self.assertRaises(SharedApiError) as denied:
            OwnerClient(self.agent_transport).confirm_job(self.task_id, view['job_ref'], view['confirmation'], key='agent-cannot-confirm')
        self.assertEqual(denied.exception.code, 'SHARED_CAPABILITY_DENIED')
        self.owner.confirm_job(self.task_id, view['job_ref'], view['confirmation'], key='planning')
        self.agent.start(self.task_id, view['job_ref'], view['planning_review'], key='start')
        plan = self.wait_phase(view['job_ref'], 'retrieval')
        self.assertEqual(self.f.searches, 0)
        self.owner.confirm_job(self.task_id, view['job_ref'], plan['confirmation'], key='retrieval')
        candidates = self.wait_phase(view['job_ref'], 'candidates')
        self.owner.confirm_job(self.task_id, view['job_ref'], candidates['confirmation'], key='candidates')
        finished = self.agent.get(self.task_id, view['job_ref'])
        self.assertEqual(finished['status'], 'completed')
        artifacts = self.agent.artifacts(self.task_id, view['job_ref'])
        self.assertEqual(len(artifacts['items']), 1)
        self.assertIsNone(artifacts['items'][0]['accepted_count'])
        self.assertEqual(self.f.searches, 1)
        self.assertEqual(len(self.f.f.calls), 1)
        repeated = self.agent.prepare(self.task_id, self.f.body['planning'], key='prepare')
        self.assertEqual(repeated['job_ref'], view['job_ref'])
        self.assertEqual(len(self.agent.list(self.task_id)['items']), 1)
        self.assertEqual(self.f.encodings, 1)
        self.assertNotIn('trusted:synthetic', json.dumps(artifacts))

    def test_delegation_scopes_root_binding_and_cancellation_replay(self):
        wrong = AgentJobClient(self.agent_transport, external_task_ref='copied-root')
        with self.assertRaises(SharedApiError): wrong.prepare(self.task_id, self.f.body['planning'], key='wrong')
        self.grants = frozenset({'agent.retrieval.read'})
        with self.assertRaises(SharedApiError): self.agent.prepare(self.task_id, self.f.body['planning'], key='missing-grant')
        self.grants |= {'agent.retrieval.prepare', 'task.cancel'}
        view = self.agent.prepare(self.task_id, self.f.body['planning'], key='prepare')
        cancelled = self.agent.cancel(self.task_id, view['job_ref'], task_version=view['task_version'], key='cancel')
        repeated = self.agent.cancel(self.task_id, view['job_ref'], task_version=view['task_version'], key='cancel')
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertEqual(cancelled['generation'], repeated['generation'])
        self.assertEqual(self.f.searches, 0)

    def test_existing_frontend_window_routes_stay_unchanged(self):
        request = Request(self.base+'/chat')
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn('text/html', response.headers.get('Content-Type', ''))
        self.assertEqual((self.f.searches, self.f.encodings), (0, 0))
