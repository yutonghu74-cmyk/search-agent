from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import test_query_routing as routing_fixture
from dsh_retrieval.configured_catalog import build_configured_routing, matches_topic
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import AccessContext
from dsh_retrieval.models import Actor
from dsh_retrieval.text_catalog_config import TextCatalogConfigService, CatalogConfigScope


class ConfiguredCatalogTest(unittest.TestCase):
    discover = routing_fixture.QueryRoutingTest.discover
    prepare = routing_fixture.QueryRoutingTest.prepare
    choices = routing_fixture.QueryRoutingTest.choices
    assert_code = routing_fixture.QueryRoutingTest.assert_code
    require_access = routing_fixture.QueryRoutingTest.require_access
    encode_query = routing_fixture.QueryRoutingTest.encode_query

    def setUp(self):
        routing_fixture.QueryRoutingTest.setUp(self)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.entries = [{'library_id': 'dogs', 'description': '犬类图片', 'aliases': ['狗', 'dog'],
                         'binding_ids': [self.a.binding_id]},
                        {'library_id': 'cats', 'description': '猫科图片', 'aliases': ['猫', 'cat'],
                         'binding_ids': [self.b.binding_id]}]
        self.config_scope = CatalogConfigScope(self.actor.tenant_id, self.actor.user_id, self.task.project_id)
        self.configs = TextCatalogConfigService(Path(self.temp.name) / 'config.sqlite', self,
            lambda scope: self.libraries, lambda scope, action, library: True)
        self.router = build_configured_routing(tasks=self.repo, projects=self.service.projects,
            configs=self.configs, authorization=self, embeddings=self,
            capabilities=tuple(self.preparer.capabilities.values()))
        self.context = AccessContext(self.actor, self.task_id, 'search', 'auth-v1')
        self.save_config()

    def suggest_catalog(self, text, llm_id, candidates):
        return self.entries

    def save_config(self):
        preview = self.configs.preview(self.config_scope, '配置猫狗库', 'text')
        return self.configs.save(self.config_scope, **preview)

    def test_saved_aliases_route_to_distinct_encoders(self):
        preview = self.discover()
        self.assertEqual([[o.binding.library_id for o in r.options] for r in preview.routes],
                         [['cats', 'dogs'], ['dogs'], ['cats']])
        self.assertTrue(preview.catalog_config_digest.startswith('sha256:'))
        plans = self.prepare(preview)
        self.assertEqual([len(p.plan.request.vector.values) for p in plans.members], [3, 2, 3])
        self.assertEqual(self.repo.get(self.actor, self.task_id), self.task)

    def test_configuration_revision_changes_even_if_options_identical(self):
        before = self.discover()
        self.save_config()
        after = self.discover()
        self.assertEqual(before.routes, after.routes)
        self.assertNotEqual(before.routing_digest, after.routing_digest)
        self.assert_code('ROUTING_CHANGED', lambda: self.prepare(before))
        self.assertEqual(self.encodes, [])

    def test_encoding_during_configuration_change_returns_no_plan(self):
        before = self.discover()
        def change_once():
            self.after_encode = lambda: None
            self.save_config()
        self.after_encode = change_once
        self.assert_code('ROUTING_CHANGED', lambda: self.prepare(before))
        self.assertEqual(self.repo.get(self.actor, self.task_id), self.task)

    def test_unauthorized_library_never_reaches_matcher(self):
        self.denied.add('library:cats')
        with patch('dsh_retrieval.configured_catalog.matches_topic', wraps=matches_topic) as matcher:
            preview = self.discover()
            self.assertTrue(all(call.args[1]['library_id'] == 'dogs' for call in matcher.call_args_list))
        self.assertTrue(all(o.binding.library_id != 'cats' for r in preview.routes for o in r.options))
        self.down = True
        self.assert_code('AUTH_UNAVAILABLE', self.discover)

    def test_no_configuration_is_not_fallback_to_all_indexes(self):
        self.configs.path = str(Path(self.temp.name) / 'empty.sqlite')
        self.router.preparer.catalog.configs = TextCatalogConfigService(self.configs.path, self,
            lambda scope: self.libraries, lambda *args: True)
        self.assert_code('CATALOG_SELECTION_REQUIRED', self.discover)

    def test_no_match_and_wrong_media_return_empty(self):
        catalog = self.router.preparer.catalog
        self.assertEqual(catalog.find_libraries(self.context, '火箭', 'image'), ())
        self.assertEqual(catalog.find_libraries(self.context, '猫', 'video'), ())
        self.assertEqual(self.encodes, [])

    def test_configuration_change_during_matching_is_rejected(self):
        original = matches_topic
        changed = []
        def matcher(*args):
            if not changed:
                changed.append(True)
                self.save_config()
            return original(*args)
        with patch('dsh_retrieval.configured_catalog.matches_topic', side_effect=matcher):
            self.assert_code('CATALOG_CHANGED', self.discover)

    def test_only_selected_indexes_and_registered_capabilities(self):
        self.entries = self.entries[:1]
        self.save_config()
        preview = self.discover()
        self.assertEqual(preview.routes[-1].options, ())
        self.router.preparer.capabilities.clear()
        self.assertTrue(all(not route.options for route in self.discover().routes))

    def test_wrong_identity_and_revocation(self):
        context = AccessContext(Actor('tenant', 'intruder'), self.task_id, 'search', 'auth-v1')
        with self.assertRaises(ServiceError):
            self.router.preparer.catalog.find_libraries(context, '猫', 'image')
        self.configs.authorize = lambda *args: False
        self.assert_code('ACCESS_DENIED', self.discover)

    def test_literal_matching_does_not_match_latin_subwords(self):
        entry = self.entries[1]
        self.assertFalse(matches_topic('education', entry))
        self.assertTrue(matches_topic('CAT pictures', entry))
        self.assertTrue(matches_topic('找猫的照片', entry))


if __name__ == '__main__':
    unittest.main()
