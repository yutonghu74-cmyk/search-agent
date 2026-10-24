"""Real HTTP reads over the temporary SQLite approved plan-set ledger."""
from dataclasses import replace
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from dsh_retrieval.api import create_handler, main
from dsh_retrieval.integration_models import SearchPage
from dsh_retrieval.retrieval_progress import build_local_demo_progress_repository
from tests.core import test_plan_set_storage as fixtures


class RetrievalProgressApiTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PlanSetStorageTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo = self.fixture.repo
        self.actor = self.fixture.actor
        self.task_id = self.fixture.task_id
        self.handler = create_handler(self.fixture.service, plan_set_repository=self.repo)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = (f'http://127.0.0.1:{self.server.server_port}'
                    f'/v1/retrieval/tasks/{self.task_id}/retrieval-progress')

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def get(self, *, user='owner', suffix=''):
        headers = {'X-Tenant-Id': 'tenant'}
        if user is not None:
            headers['X-User-Id'] = user
        try:
            response = urlopen(Request(self.url + suffix, headers=headers))
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def approve(self):
        self.repo.put_proposal(self.actor, self.fixture.proposal)
        approved, _ = self.fixture.confirm()
        return approved

    def test_disabled_unapproved_and_unknown_parameters(self):
        status, view = self.get()
        self.assertEqual((status, view['status'], view['members']), (200, 'unapproved', []))
        self.assertEqual(self.get(suffix='?cursor=arbitrary')[0], 422)
        self.handler.plan_set_repository = None
        self.assertEqual(self.get()[1]['code'], 'RETRIEVAL_PROGRESS_NOT_CONFIGURED')

    def test_authorized_progress_only_contains_safe_metadata(self):
        approved = self.approve()
        before = self.repo._transaction(False, lambda memory, state: state.copy())
        status, view = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(view['set_digest'], approved.proposal_digest)
        self.assertEqual([m['status'] for m in view['members']], ['not_started', 'not_started'])
        self.assertEqual(self.repo._transaction(False, lambda memory, state: state.copy()), before)
        first = approved.members[0].plan.request
        operation, _ = self.repo.reserve_page(self.actor, approved,
            plan_id=first.plan_id, request=first, key='first')
        status, view = self.get()
        self.assertEqual((status, view['members'][0]['status'], view['members'][0]['committed_pages']),
                         (200, 'pending', 0))
        page = SearchPage((), 'opaque-token', 'more_available', 'snapshot', 'snapshot')
        self.repo.commit_page(self.actor, approved, operation_id=operation,
                              plan_id=first.plan_id, request=first, page=page)
        status, view = self.get()
        self.assertEqual((status, view['members'][0]['status'], view['members'][0]['committed_pages']),
                         (200, 'more_available', 1))
        self.assertFalse(view['pagination_stopped'])
        serialized = json.dumps(view)
        for private in ('opaque-token', operation, 'vector_id', 'hits', 'filter_template_ref', 'QueryVector'):
            self.assertNotIn(private, serialized)
        after = self.repo._transaction(False, lambda memory, state: state.copy())
        self.assertEqual(len(after['events']), len(before['events']) + 1)
        self.assertEqual(after['operations'][operation][3], page)

    def test_wrong_identity_revocation_and_old_generation_fail_closed(self):
        approved = self.approve()
        self.assertEqual(self.get(user=None)[0], 401)
        self.assertEqual(self.get(user='other')[0], 404)
        self.fixture.denied.add('library:lib1')
        self.assertEqual(self.get()[1]['code'], 'ACCESS_DENIED')
        self.fixture.denied.clear()
        def advance(memory, state):
            task = memory._tasks[self.task_id]
            memory._tasks[task.task_id] = replace(task, run_generation=task.run_generation + 1)
        self.repo._transaction(True, advance)
        self.assertEqual(self.get()[1]['code'], 'REVISION_CONFLICT')
        self.assertIsNotNone(approved)

    def test_fresh_repository_recovers_pending_page_via_http(self):
        approved = self.approve()
        request = approved.members[0].plan.request
        self.repo.reserve_page(self.actor, approved, plan_id=request.plan_id,
                               request=request, key='pending')
        self.handler.plan_set_repository = self.fixture.reopen()
        status, view = self.get()
        self.assertEqual((status, view['members'][0]['status'], view['members'][0]['committed_pages']),
                         (200, 'pending', 0))

    def test_all_members_stopped_means_pagination_only(self):
        approved = self.approve()
        for index, member in enumerate(approved.members):
            request = member.plan.request
            operation, _ = self.repo.reserve_page(self.actor, approved,
                plan_id=request.plan_id, request=request, key=f'page-{index}')
            self.repo.commit_page(self.actor, approved, operation_id=operation,
                plan_id=request.plan_id, request=request,
                page=SearchPage((), None, 'exhausted', 'snapshot', 'snapshot'))
        status, view = self.get()
        self.assertEqual(status, 200)
        self.assertTrue(view['pagination_stopped'])
        self.assertEqual([member['status'] for member in view['members']], ['stopped', 'stopped'])
        self.assertEqual([member['committed_pages'] for member in view['members']], [1, 1])
        self.assertNotIn('accepted_count', view)

    def test_opt_in_local_catalog_grant_is_checked_on_every_read(self):
        self.approve()
        source = Path(self.fixture.directory.name) / 'source.json'
        libraries = []
        for member in self.fixture.proposal.members:
            binding = member.plan.request.binding
            libraries.append({'library_id': binding.library_id, 'description': binding.library_id,
                              'media_type': 'image', 'bindings': [{
                                  'binding_id': binding.binding_id,
                                  'library_id': binding.library_id,
                                  'index_version': binding.index_version,
                                  'encoder_ref': binding.encoder_ref,
                                  'vector_dimension': binding.vector_dimension,
                                  'metric': binding.metric}]})
        grant = {'tenant_id': 'tenant', 'owner_id': 'owner', 'project_id': 'demo',
                 'library_ids': ['lib0', 'lib1'], 'can_configure': False}
        data = {'schema': 'local-catalog@1', 'libraries': libraries, 'grants': [grant]}
        source.write_text(json.dumps(data), encoding='utf-8')
        self.handler.plan_set_repository = build_local_demo_progress_repository(
            self.fixture.path, projects=self.fixture.service.projects, source_path=source)
        self.assertEqual(self.get()[0], 200)
        cli = ['dsh_retrieval.api', '--demo-project', '--ledger', str(self.fixture.path),
               '--catalog-source', str(source), '--catalog-ledger',
               str(Path(self.fixture.directory.name) / 'catalog.sqlite3'), '--plan-set-progress']
        with patch('sys.argv', cli), patch('dsh_retrieval.api.ThreadingHTTPServer') as server:
            server.return_value.server_port = 8083
            main()
            self.assertIsNotNone(server.call_args.args[1].plan_set_repository)
        grant['library_ids'] = ['lib0']
        source.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(self.get()[1]['code'], 'ACCESS_DENIED')
        source.unlink()
        self.assertEqual(self.get()[1]['code'], 'CATALOG_CONFIG_UNAVAILABLE')

    def test_cli_requires_explicit_demo_dependencies(self):
        with patch('sys.argv', ['dsh_retrieval.api', '--plan-set-progress']):
            with self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
