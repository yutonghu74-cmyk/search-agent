from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import IndexBinding, Library, QueryVector, SearchHit, SearchPage, MediaRecord, MediaResolution
from dsh_retrieval.models import Actor
from dsh_retrieval.physical_query_preparation import TextQueryCapability
from dsh_retrieval.platform_resources import DatasetVersionRef
from dsh_retrieval.platform_server import PlatformApplication
from draft_helpers import BODY


class PlatformApplicationTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = {'api_base_url': 'https://platform.invalid/api/v1', 'tenant_id': 'platform',
            'task_ledger': str(root/'tasks.sqlite'), 'dialogue_ledger': str(root/'dialogue.sqlite'),
            'credential_store': str(root/'credentials.sqlite'), 'credential_key': str(root/'key'),
            'catalog_ledger': str(root/'catalog.sqlite'), 'allowed_actions': ['read', 'create', 'approve']}
        self.member = True
        self.user_id = 'a'
        self.token = 'token-a'
        self.calls = []
        self.binding = IndexBinding('binding-a', 'dogs', 'index-v1', 'encoder-v1', 2, 'COSINE')
        self.ref = DatasetVersionRef('dogs', 'dataset-v1')
        self.content = b'\x89PNG\r\n\x1a\nsynthetic-test-data'
        self.media = MediaRecord('media-a', 'asset-v1', 'dataset', 'file-a', 'media:opaque-a',
                                 'sha256:'+hashlib.sha256(self.content).hexdigest())
        self.hit = SearchHit(self.binding, 'vector-a', .9)
        self.page_cursor = None
        self.backend_cursor = None
        class Client:
            def get(_, path, token, **kwargs):
                self.calls.append((path, token))
                if path == '/user/info':
                    return {'id': self.user_id}
                if not self.member:
                    raise ServiceError('PROJECT_ACCESS_DENIED', 'removed', 403)
                return {'id': int(path.rsplit('/',1)[1]), 'status': 'normal'}
        self.patch_client = patch('dsh_retrieval.platform_server.PlatformClient', return_value=Client())
        self.patch_client.start()
        self.addCleanup(self.patch_client.stop)
        @contextmanager
        def open_media(*args):
            yield BytesIO(self.content)
        def search(app, context, request, project, versions):
            self.backend_cursor = request.cursor
            self.assertEqual(project, '101')
            self.assertEqual(versions, frozenset({'dataset-v1'}))
            return SearchPage((self.hit,), self.page_cursor, 'more_available' if self.page_cursor else 'exhausted', 'snapshot', 'snapshot-a')
        self.hooks = {'extract_user_id': lambda data: data['id'],
            'exchange': lambda client, refresh: (self.token, 3600),
            'list_projects': lambda client, actor, token: [{'id': 101, 'name': 'Alpha'}],
            'project_datasets': lambda app, actor, project: (self.ref,),
            'user_datasets': lambda app, actor: (self.ref,),
            'libraries': lambda app, actor, project: (Library('dogs', '图片素材', 'image', (self.binding,)),),
            'resolve_hits': lambda app, context, hits, project: tuple(MediaResolution(hit,self.media,'resolved') for hit in hits),
            'search': search, 'open_media': open_media, 'media_version': lambda *args: self.ref,
            'capabilities': lambda app: (TextQueryCapability(self.binding, 'image', 'trusted-filter', 20),),
            'encode_query': lambda app, context, text, binding: QueryVector('encoder-v1', (1.,0.))}
        self.app = self.open()
        self.addCleanup(lambda: self.app.close())
        self.actor = self.app.credentials.register('token-a', 'refresh-a')

    def open(self):
        return PlatformApplication(self.config, self.hooks, start_workers=False)

    def create_task(self):
        draft, _ = self.app.service.save_draft(self.actor, '101', 'draft-a', deepcopy(BODY))
        task, _ = self.app.service.create_task(self.actor, 'confirm-a', {
            'draft_id': draft['draft_id'], 'expected_draft_version': draft['draft_version'],
            'proposal_digest': draft['proposal_digest'], 'action': 'confirm'})
        return task['task_id']

    def test_real_business_approval_search_manifest_and_restart(self):
        task_id = self.create_task()
        first = self.app.handler.first_search
        preview = first.preview(self.actor, task_id)
        self.assertTrue(preview['routes'])
        choices = [{'branch_id': route['branch_id'], 'binding_id': route['options'][0]['binding_id'], 'batch_size': 2}
                   for route in preview['routes']]
        plan = first.prepare(self.actor, task_id, routing_digest=preview['routing_digest'], choices=choices)
        first.confirm(self.actor, task_id, proposal_digest=plan['proposal_digest'],
            expected_task_version=plan['expected_task_version'], action='confirm', key='plan-confirm')
        first.tick(self.actor, task_id, max_requests=20)
        candidates = first.candidates(self.actor, task_id)
        self.assertTrue(candidates['items'])
        item = candidates['items'][0]
        mime, content = first.candidate_preview(self.actor, task_id, item['candidate_id'], ref=item['preview_ref'])
        self.assertEqual((mime, content), ('image/png', self.content))
        manifest = first.preview_manifest(self.actor, task_id)
        self.assertTrue(manifest)
        self.app.close()
        self.app = self.open()
        self.assertEqual(self.app.service.get_task(self.actor, task_id)['project_id'], '101')
        self.assertTrue(self.app.handler.first_search.candidates(self.actor, task_id)['items'])
        self.member = False
        with self.assertRaises(ServiceError):
            self.app.handler.first_search.candidates(self.actor, task_id)

    def test_cursor_bound_to_task_and_survives_restart(self):
        from dsh_retrieval.integration_models import AccessContext, SearchRequest
        task_id = self.create_task()
        context = AccessContext(self.actor, task_id, 'retrieval', 'current')
        request = SearchRequest('plan-a', 1, self.binding, QueryVector('encoder-v1',(1.,0.)), 'filter', 2)
        self.page_cursor = 'upstream-private-cursor'
        page = self.app.resources.search(context, request)
        self.assertNotEqual(page.next_cursor, self.page_cursor)
        self.app.close()
        self.app = self.open()
        self.app.resources.search(context, replace(request,cursor=page.next_cursor))
        self.assertEqual(self.backend_cursor, 'upstream-private-cursor')
        with self.assertRaises(ServiceError):
            self.app.resources.search(context, replace(request,plan_id='other-plan',cursor=page.next_cursor))

    def test_missing_protocol_and_overlapping_storage_rejected(self):
        with self.assertRaises(ValueError):
            PlatformApplication(self.config, {}, start_workers=False)
        config = {**self.config, 'credential_store': self.config['task_ledger']}
        with self.assertRaises(ValueError):
            PlatformApplication(config, self.hooks, start_workers=False)

    def test_refill_position_preserves_platform_mode_and_cursor(self):
        import json
        task_id = self.create_task()
        first = self.app.handler.first_search
        preview = first.preview(self.actor, task_id)
        choices = [{'branch_id': route['branch_id'], 'binding_id': route['options'][0]['binding_id'], 'batch_size': 2}
                   for route in preview['routes']]
        plan = first.prepare(self.actor, task_id, routing_digest=preview['routing_digest'], choices=choices)
        first.confirm(self.actor, task_id, proposal_digest=plan['proposal_digest'],
            expected_task_version=plan['expected_task_version'], action='confirm', key='refill-plan')
        self.page_cursor = 'next-upstream-page'
        first.tick(self.actor, task_id, max_requests=1)
        task = self.app.repository.get(self.actor, task_id)
        positions = self.app.handler.refill_service.runtime.adapter.list_refill_positions(
            self.actor, task_id, revision=task.confirmed_revision, generation=task.run_generation)
        self.assertTrue(positions)
        self.assertEqual(json.loads(positions[0].binding_json)['parameters']['mode'], 'PLATFORM')
