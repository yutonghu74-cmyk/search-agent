import io
import json
import unittest
from urllib.error import HTTPError

from dsh_retrieval.adapters.platform_access import PlatformClient, PlatformIdentity, PlatformProjectAccess
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.api import create_handler
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.service import TaskService, InMemoryTaskRepository


class Reply(io.BytesIO):
    status = 200


class PlatformAccessTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.member = True
        self.user = 7
        self.project = {'id': 101, 'status': 'normal'}
        def open_request(request, timeout):
            self.requests.append(request)
            if request.full_url.endswith('/user/info'):
                data = {'id': self.user}
            elif not self.member:
                raise HTTPError(request.full_url, 403, 'private upstream message', {}, None)
            else:
                data = self.project
            return Reply(json.dumps({'code': 200, 'data': data}).encode())
        self.client = PlatformClient('https://platform.invalid/api/v1', opener=open_request)
        self.identity = PlatformIdentity(self.client, 'instance-a', extract_user_id=lambda d: d['id'])
        self.access = PlatformProjectAccess(self.client, 'instance-a',
            credential_for_actor=lambda actor: 'personal-token', allowed_actions={'read', 'create'},
            extract_user_id=lambda d: d['id'])
        self.actor = Actor('instance-a', '7')

    def test_verified_identity_ignores_forged_headers(self):
        actor = self.identity.authenticate({'Authorization': 'Bearer personal-token',
            'X-User-Id': 'admin', 'X-Tenant-Id': 'other'})
        self.assertEqual(actor, self.actor)
        with self.assertRaises(ServiceError):
            self.identity.authenticate({'X-User-Id': 'admin'})

    def test_rechecks_membership_and_passes_project(self):
        self.access.require_access(self.actor, '101', 'default', 'read')
        self.assertEqual(self.requests[-1].get_header('X-app-id'), '101')
        self.member = False
        with self.assertRaises(ServiceError) as caught:
            self.access.require_access(self.actor, '101', 'default', 'read')
        self.assertEqual(caught.exception.status, 403)
        self.assertNotIn('private upstream', caught.exception.message)

    def test_wrong_user_never_uses_service_fallback(self):
        self.user = 8
        with self.assertRaises(ServiceError) as caught:
            self.access.require_access(self.actor, '101', 'default', 'read')
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(len(self.requests), 1)

    def test_missing_background_credential_denies(self):
        self.access.credential_for_actor = lambda actor: None
        with self.assertRaises(ServiceError):
            self.access.require_access(self.actor, '101', 'default', 'read')
        self.assertEqual(self.requests, [])

    def test_scope_and_actions_deny_before_call(self):
        for actor, project, dimension, action in [
            (Actor('other', '7'), '101', 'default', 'read'),
            (self.actor, '101', 'other', 'read'),
            (self.actor, '101', 'default', 'manage'),
            (self.actor, '0101', 'default', 'read'),
            (self.actor, '../102', 'default', 'read')]:
            with self.assertRaises(ServiceError):
                self.access.require_access(actor, project, dimension, action)
        self.assertEqual(self.requests, [])

    def test_mismatched_deleted_project_denies(self):
        for project in ({'id': 102, 'status': 'normal'}, {'id': 101, 'status': 'deleted'}):
            self.project = project
            with self.assertRaises(ServiceError):
                self.access.require_access(self.actor, '101', 'default', 'read')

    def test_bad_envelope_and_network_fail_closed(self):
        for raw in (b'broken', b'{}', b'{"code":200,"data":null}', b'x' * (1024*1024+1)):
            client = PlatformClient('https://platform.invalid', opener=lambda *a, **kw: Reply(raw))
            identity = PlatformIdentity(client, 'instance-a', extract_user_id=lambda d: d['id'])
            with self.assertRaises(ServiceError):
                identity.authenticate({'Authorization': 'Bearer personal-token'})
        def fail(*a, **kw):
            raise OSError('secret upstream address')
        self.client.opener = fail
        with self.assertRaises(ServiceError) as caught:
            self.identity.authenticate({'Authorization': 'Bearer personal-token'})
        self.assertEqual(caught.exception.status, 503)
        self.assertNotIn('secret', caught.exception.message)

    def test_platform_factory_requires_both_adapters(self):
        service = TaskService(InMemoryTaskRepository(), LocalProjectAccess())
        with self.assertRaises(ValueError):
            create_handler(service, identity_mode='platform', identity_provider=self.identity)
        service.projects = self.access
        handler = create_handler(service, identity_mode='platform', identity_provider=self.identity)
        instance = object.__new__(handler)
        instance.headers = {'Authorization': 'Bearer personal-token', 'X-User-Id': 'fake'}
        self.assertEqual(instance._actor(), self.actor)
        instance.headers = {'X-User-Id': 'fake', 'X-Tenant-Id': 'fake'}
        with self.assertRaises(ServiceError):
            instance._actor()

    def test_client_rejects_non_https_and_header_injection(self):
        for url in ('http://platform.invalid', 'https://user:pass@platform.invalid', 'https://platform.invalid?x=1'):
            with self.assertRaises(ValueError):
                PlatformClient(url)
        with self.assertRaises(ServiceError):
            self.client.get('/user/info', 'token\nsecret')

    def test_http_and_business_errors_do_not_leak_upstream(self):
        for code in (301, 401, 403, 500):
            def fail(request, timeout):
                raise HTTPError(request.full_url, code, 'credential-secret', {}, None)
            self.client.opener = fail
            with self.assertRaises(ServiceError) as caught:
                self.client.get('/user/info', 'personal-token')
            self.assertEqual(caught.exception.status, code if code in (401, 403) else 503)
            self.assertNotIn('credential-secret', str(caught.exception))
        for code in (401, 403, 500):
            self.client.opener = lambda *a, **kw: Reply(json.dumps({'code': code, 'msg': 'private'}).encode())
            with self.assertRaises(ServiceError) as caught:
                self.client.get('/user/info', 'personal-token')
            self.assertEqual(caught.exception.status, code if code in (401, 403) else 503)

    def test_request_scope_checks_header_and_resets_after_failure(self):
        for headers in ({'Authorization': 'Bearer personal-token'},
                        {'Authorization': 'Bearer personal-token', 'x-app-id': '0101'}):
            with self.assertRaises(ServiceError):
                with self.access.request_scope(headers):
                    self.fail('invalid header accepted')
        with self.assertRaises(RuntimeError):
            with self.access.request_scope({'Authorization': 'Bearer personal-token', 'x-app-id': '101'}):
                with self.assertRaises(ServiceError):
                    self.access.require_access(self.actor, '102', 'default', 'read')
                raise RuntimeError('end request')
        self.project = {'id': 102, 'status': 'normal'}
        # No stale request project remains on the thread.
        self.access.require_access(self.actor, '102', 'default', 'read')

    def test_duplicate_project_header_rejected(self):
        from email.message import Message
        headers = Message()
        headers['Authorization'] = 'Bearer personal-token'
        headers['x-app-id'] = '101'
        headers['x-app-id'] = '102'
        with self.assertRaises(ServiceError):
            with self.access.request_scope(headers):
                self.fail('duplicate accepted')

    def test_request_uses_own_credential_and_background_copy_keeps_project(self):
        from contextvars import copy_context
        self.access.credential_for_actor = lambda actor: None
        with self.access.request_scope({'Authorization': 'Bearer personal-token', 'x-app-id': '101'}):
            copied = copy_context()
        copied.run(self.access.require_access, self.actor, '101', 'default', 'read')
        with self.assertRaises(ServiceError):
            copied.run(self.access.require_access, self.actor, '102', 'default', 'read')
        with self.assertRaises(ServiceError):
            self.access.require_access(self.actor, '101', 'default', 'read')

    def test_parallel_request_contexts_are_independent(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        # Each project is authorized, but mismatched headers still deny locally.
        original = self.client.get
        def get(path, token, **kwargs):
            if path.startswith('/app/info/'):
                return {'id': int(path.rsplit('/', 1)[1]), 'status': 'normal'}
            return original(path, token, **kwargs)
        self.client.get = get
        def run(project, other):
            with self.access.request_scope({'Authorization': 'Bearer personal-token', 'x-app-id': project}):
                barrier.wait(timeout=3)
                self.access.require_access(self.actor, project, 'default', 'read')
                with self.assertRaises(ServiceError):
                    self.access.require_access(self.actor, other, 'default', 'read')
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, '101', '102'), pool.submit(run, '102', '101')]
            for future in futures:
                future.result(timeout=5)

    def test_http_header_route_boundary_all_methods(self):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from urllib.request import Request, urlopen
        service = TaskService(InMemoryTaskRepository(), self.access)
        handler = create_handler(service, identity_mode='platform', identity_provider=self.identity)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            for method in ('GET', 'POST', 'DELETE'):
                for project, expected in ((None, 422), ('101', 403)):
                    headers = {'Authorization': 'Bearer personal-token', 'X-User-Id': 'fake'}
                    if project:
                        headers['x-app-id'] = project
                    request = Request(base + '/v1/retrieval/projects/102/drafts',
                                      headers=headers, method=method)
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(request, timeout=3)
                    self.assertEqual(caught.exception.code, expected)
                    caught.exception.close()
            request = Request(base + '/v1/retrieval/projects/101/catalog-config',
                headers={'Authorization': 'Bearer personal-token', 'x-app-id': '101'})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=3)
            # Matching project reaches existing business handler (catalog not configured).
            self.assertEqual(caught.exception.code, 503)
            caught.exception.close()
            from types import SimpleNamespace
            service.repository.get = lambda actor, task_id: SimpleNamespace(project_id='102', dimension_id='default')
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(base + '/v1/retrieval/tasks/task-in-other-project',
                    headers={'Authorization': 'Bearer personal-token', 'x-app-id': '101'}), timeout=3)
            self.assertEqual(caught.exception.code, 403)
            caught.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_model_scopes_use_verified_actor(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        calls = []
        @contextmanager
        def settings_scope(tenant, user):
            calls.append(('settings', tenant, user))
            yield
        @contextmanager
        def runtime_scope(tenant, user, trace_id, path):
            calls.append(('runtime', tenant, user))
            yield
        runtime = SimpleNamespace(settings=SimpleNamespace(scope=settings_scope), request_scope=runtime_scope)
        service = TaskService(InMemoryTaskRepository(), self.access)
        handler = create_handler(service, identity_mode='platform', identity_provider=self.identity,
                                 model_runtime=runtime)
        instance = object.__new__(handler)
        instance._platform_actor = self.actor
        instance.headers = {'X-Tenant-Id': 'forged', 'X-User-Id': 'admin'}
        instance.path = '/v1/test'
        instance._json = lambda *args, **kwargs: None
        instance._run(lambda: (200, {}, False))
        self.assertEqual(calls, [('settings', 'instance-a', '7'), ('runtime', 'instance-a', '7')])

    def test_post_exchange_and_business_requests(self):
        self.client.post('/search', 'personal-token', {'limit': 10}, project_id='101')
        request = self.requests[-1]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data), {'limit': 10})
        self.assertEqual(request.get_header('X-app-id'), '101')
        self.client.post('/third-party/local/cli/exchange', None, {'refresh_key': 'private'}, credential_exchange=True)
        self.assertIsNone(self.requests[-1].get_header('Authorization'))
        with self.assertRaises(ServiceError):
            self.client.post('/search', None, {'limit': 10})
        with self.assertRaises(ServiceError):
            self.client.post('https://evil.invalid', 'personal-token', {})

    def test_background_invalid_personal_identity_clears_delegation(self):
        invalidated = []
        self.access.credential_invalidated = invalidated.append
        self.user = 8
        with self.assertRaises(ServiceError):
            self.access.require_access(self.actor, '101', 'default', 'read')
        self.assertEqual(invalidated, [self.actor])
