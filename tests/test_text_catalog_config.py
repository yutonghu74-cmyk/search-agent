from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import IndexBinding, Library
from dsh_retrieval.intent_model import ModelConfig
from dsh_retrieval.model_runtime import ModelProfile, ModelRuntime
from dsh_retrieval.query_spec import digest
from dsh_retrieval.text_catalog_config import CatalogConfigScope, TextCatalogConfigService


class Transport:
    def __init__(self, output):
        self.output, self.calls, self.hook = output, [], lambda: None

    def open(self, request, timeout):
        self.calls.append(json.loads(request.data))
        self.hook()
        return BytesIO(json.dumps({'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(self.output)}}]}).encode())


class TextCatalogConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'catalog.sqlite'
        self.scope = CatalogConfigScope('tenant', 'owner', 'project')
        self.rows = (Library('animals', '动物图片库', 'image', (
            IndexBinding('animal-image-v1', 'animals', 'v1', 'cross-modal@1', 512, 'COSINE'),)),
            Library('private', 'hidden-secret-description', 'image', (
            IndexBinding('hidden-binding', 'private', 'v1', 'private-encoder', 768, 'IP'),)))
        self.entries = [{'library_id': 'animals', 'description': '猫狗图片',
                         'aliases': ['宠物', '猫', '狗'], 'binding_ids': ['animal-image-v1']}]
        self.transport = Transport({'entries': deepcopy(self.entries)})
        self.runtime = ModelRuntime([ModelProfile('text', 'llm', ModelConfig(
            'fixture', 'model-v1', 'https://fixture.invalid/v1', 'test-secret'))], opener=self.transport)
        self.denied, self.project_allowed = {'private'}, True
        self.service = self.make_service()

    def authorize(self, scope, action, library):
        return self.project_allowed and library not in self.denied

    def make_service(self):
        return TextCatalogConfigService(self.path, self.runtime, lambda scope: self.rows, self.authorize)

    def preview(self):
        return self.service.preview(self.scope, '动物库用来查猫狗，别名宠物', 'text')

    def save(self, preview):
        return self.service.save(self.scope, **preview)

    def test_text_to_confirmed_config_restart_and_libraries(self):
        draft = self.preview()
        self.assertIsNone(self.service.read(self.scope))
        self.assertEqual(self.save(draft), 1)
        restarted = self.make_service()
        self.assertEqual(restarted.read(self.scope)['entries'], self.entries)
        libraries = restarted.configured_libraries(self.scope)
        self.assertEqual(libraries[0].bindings, self.rows[0].bindings)
        self.assertIn('宠物', libraries[0].description)
        self.assertEqual(len(libraries), 1)
        model_input = json.dumps(self.transport.calls)
        self.assertNotIn('hidden', model_input)
        self.assertNotIn('cross-modal', model_input)
        self.assertNotIn('512', model_input)

    def test_idempotent_and_stale_version(self):
        first = self.preview()
        stale = deepcopy(first)
        stale['proposal']['entries'][0]['description'] = '另一份描述'
        stale['proposal_digest'] = digest(stale['proposal'])
        self.assertEqual(self.save(first), 1)
        self.assertEqual(self.save(first), 1)
        with self.assertRaises(ServiceError):
            self.save(stale)
        second = self.preview()
        self.assertEqual(self.save(second), 2)
        with self.assertRaises(ServiceError):
            self.save(first)

    def test_concurrent_same_confirmation_once(self):
        draft = self.preview()
        with ThreadPoolExecutor(max_workers=4) as executor:
            versions = list(executor.map(lambda _: self.make_service().save(self.scope, **draft), range(4)))
        self.assertEqual(versions, [1] * 4)

    def test_scopes_isolated_and_payload_cannot_cross_owner(self):
        draft = self.preview()
        self.save(draft)
        for other in (replace(self.scope, tenant_id='other'), replace(self.scope, owner_id='other'),
                      replace(self.scope, project_id='other')):
            self.assertIsNone(self.service.read(other))
            with self.assertRaises(ServiceError):
                self.service.save(other, **draft)

    def test_authorization_before_model_save_read(self):
        draft = self.preview()
        self.project_allowed = False
        for call in (self.preview, lambda: self.save(draft), lambda: self.service.read(self.scope)):
            with self.assertRaises(ServiceError):
                call()
        self.assertEqual(len(self.transport.calls), 1)

    def test_revocation_during_model_fails_no_save(self):
        self.transport.hook = lambda: self.denied.add('animals')
        with self.assertRaises(ServiceError):
            self.preview()
        self.denied.clear()
        self.assertIsNone(self.service.read(self.scope))

    def test_directory_changes_during_generation_or_after_confirmation(self):
        def change():
            self.rows = (replace(self.rows[0], bindings=(replace(self.rows[0].bindings[0], index_version='v2'),)),)
        self.transport.hook = change
        with self.assertRaises(ServiceError):
            self.preview()
        self.transport.hook = lambda: None
        draft = self.preview()
        self.save(draft)
        self.rows = (replace(self.rows[0], description='变更介绍'),)
        for call in (lambda: self.save(draft), lambda: self.service.read(self.scope),
                     lambda: self.service.configured_libraries(self.scope)):
            with self.assertRaises(ServiceError):
                call()

    def test_unknown_binding_and_extra_model_fields_rejected(self):
        invalid = [[], [dict(self.entries[0], binding_ids=['invented'])],
                   [dict(self.entries[0], library_id='private')],
                   [dict(self.entries[0], vector_dimension=123)],
                   [dict(self.entries[0], aliases=['狗', ' 狗 '])],
                   [self.entries[0], self.entries[0]],
                   [dict(self.entries[0], binding_ids=[{}])],
                   [dict(self.entries[0], description=' ')]]
        for entries in invalid:
            with self.subTest(entries=entries):
                self.transport.output = {'entries': entries}
                with self.assertRaises(ServiceError):
                    self.preview()
        self.transport.output = {'entries': self.entries, 'url': 'not-allowed'}
        with self.assertRaises(ServiceError):
            self.preview()
        self.assertIsNone(self.service.read(self.scope))

    def test_changed_or_forged_confirmation_rejected(self):
        draft = self.preview()
        draft['proposal']['entries'][0]['description'] = 'changed'
        with self.assertRaises(ServiceError):
            self.save(draft)
        draft['proposal']['entries'][0]['binding_ids'] = ['invented']
        draft['proposal_digest'] = digest(draft['proposal'])
        with self.assertRaises(ServiceError):
            self.save(draft)

    def test_model_change_during_generation(self):
        def change():
            self.runtime.profiles['text'] = replace(self.runtime.profiles['text'],
                config=ModelConfig('fixture', 'model-v2', 'https://fixture.invalid/v1', 'test-secret'))
        self.transport.hook = change
        with self.assertRaises(ServiceError):
            self.preview()

    def test_inventory_order_not_a_version_change(self):
        draft = self.preview()
        self.rows = tuple(reversed(self.rows))
        self.assertEqual(self.save(draft), 1)

    def test_authorization_outage_propagates(self):
        def outage(*args):
            raise RuntimeError('authorization unavailable')
        self.service.authorize = outage
        with self.assertRaises(RuntimeError):
            self.preview()
        self.assertEqual(self.transport.calls, [])

    def test_recheck_inside_save_transaction(self):
        draft = self.preview()
        calls = []
        def inventory(scope):
            calls.append(scope)
            if len(calls) == 2:
                self.denied.add('animals')
            return self.rows
        self.service.inventory = inventory
        with self.assertRaises(ServiceError):
            self.save(draft)
        self.denied.clear()
        self.assertIsNone(self.service.read(self.scope))

    def test_concurrent_different_confirmations_conflict(self):
        first = self.preview()
        second = deepcopy(first)
        second['proposal']['entries'][0]['description'] = '猫狗素材'
        second['proposal_digest'] = digest(second['proposal'])
        def save(draft):
            try:
                return self.make_service().save(self.scope, **draft)
            except ServiceError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, (first, second)))
        self.assertCountEqual(results, [1, 'conflict'])

    def test_invalid_inventory_and_empty_inputs(self):
        self.rows = (replace(self.rows[0], bindings=(replace(self.rows[0].bindings[0], vector_dimension=True),)),)
        with self.assertRaises(ServiceError):
            self.preview()
        for text in (' ', 'x' * 4001, None):
            with self.assertRaises(ServiceError):
                self.service.preview(self.scope, text, 'text')
        self.assertEqual(self.transport.calls, [])


if __name__ == '__main__':
    unittest.main()
