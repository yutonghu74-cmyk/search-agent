from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import preview_edited_groups
from dsh_retrieval.integration_models import IndexBinding, Library, QueryVector
from dsh_retrieval.models import Actor
from dsh_retrieval.multi_branch_contracts import validate_plan_set
from dsh_retrieval.physical_query_preparation import PhysicalQueryPreparer, TextQueryCapability
from dsh_retrieval.query_routing import QueryRouteChoice, QueryRoutingService
from dsh_retrieval.retrieval_contracts import RetrievalScope
from dsh_retrieval.service import InMemoryTaskRepository
from draft_helpers import BODY, confirmation, make_service


class QueryRoutingTest(unittest.TestCase):
    def setUp(self):
        self.actor = Actor('tenant', 'owner')
        self.repo = InMemoryTaskRepository()
        self.service = make_service(self.repo)
        body = deepcopy(BODY)
        body['query_spec']['raw_request'] = '狗和猫的图片'
        body['query_spec']['grouped_expansion'] = preview_edited_groups(None, [
            {'primary_label': '狗', 'auxiliary_terms': []},
            {'primary_label': '猫', 'auxiliary_terms': []}])['plan']
        summary, _ = self.service.create_task(self.actor, 'task', confirmation(self.service, self.actor, body=body))
        self.task_id = summary['task_id']
        self.task = self.repo.get(self.actor, self.task_id)
        self.scope = RetrievalScope.from_task(self.task)
        self.a = IndexBinding('dogs-index', 'dogs', 'v1', 'encoder-a', 2, 'COSINE')
        self.b = IndexBinding('cats-index', 'cats', 'v3', 'encoder-b', 3, 'IP')
        self.libraries = (Library('dogs', 'dog fixture', 'image', (self.a,)),
                          Library('cats', 'cat fixture', 'image', (self.b,)))
        self.denied, self.down, self.catalog_calls = set(), False, []
        self.encodes = []
        self.after_encode = lambda: None
        self.preparer = PhysicalQueryPreparer(tasks=self.repo, projects=self.service.projects,
            catalog=self, embeddings=self, authorization=self, capabilities=(
                TextQueryCapability(self.a, 'image', 'filter-a', 20),
                TextQueryCapability(self.b, 'image', 'filter-b', 10)))
        self.router = QueryRoutingService(self.preparer)

    def find_libraries(self, context, topic, media_type):
        self.catalog_calls.append(topic)
        return tuple(lib for lib in self.libraries
                     if topic == '狗和猫的图片' or (topic == '狗' and lib.library_id == 'dogs')
                     or (topic == '猫' and lib.library_id == 'cats'))

    def require_access(self, context, ref, action):
        if self.down:
            raise ServiceError('AUTH_UNAVAILABLE', 'unavailable', 503)
        if ref in self.denied:
            raise ServiceError('ACCESS_DENIED', 'denied', 403)

    def encode_query(self, context, text, binding):
        self.encodes.append((text, binding))
        self.after_encode()
        return QueryVector(binding.encoder_ref, (0.25,) * binding.vector_dimension)

    def discover(self, **kwargs):
        return self.router.discover(self.actor, self.task_id,
            expected_scope=self.scope, authorization_version='auth-v1', **kwargs)

    def choices(self, preview):
        return tuple(QueryRouteChoice(route.branch_id, route.options[0].binding.binding_id, 5)
                     for route in preview.routes if route.options)

    def prepare(self, preview, choices=None, **kwargs):
        return self.router.prepare(self.actor, self.task_id, preview=preview,
            choices=self.choices(preview) if choices is None else choices, epoch=kwargs.get('epoch', 1))

    def assert_code(self, code, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_two_libraries_route_and_encode_in_their_own_spaces(self):
        preview = self.discover()
        self.assertEqual([[option.binding.library_id for option in route.options]
                          for route in preview.routes], [['cats', 'dogs'], ['dogs'], ['cats']])
        self.assertEqual(self.encodes, [])
        plan_set = self.prepare(preview)
        validate_plan_set(self.actor, self.task, plan_set)
        by_text = {text: binding for text, binding in self.encodes}
        self.assertEqual((by_text['狗'].encoder_ref, by_text['猫'].encoder_ref), ('encoder-a', 'encoder-b'))
        self.assertEqual([len(member.plan.request.vector.values) for member in plan_set.members], [3, 2, 3])
        self.assertEqual(self.repo.get(self.actor, self.task_id), self.task)
        self.assertEqual(preview.routing_digest, self.discover().routing_digest)

    def test_same_branch_can_select_two_libraries_but_not_duplicate_pair(self):
        preview = self.discover()
        original = preview.routes[0]
        choices = tuple(QueryRouteChoice(original.branch_id, option.binding.binding_id, 5)
                        for option in original.options)
        self.assertEqual(len(self.prepare(preview, choices).members), 2)
        self.encodes.clear()
        self.assert_code('INVALID_ROUTE_SELECTION', lambda: self.prepare(preview, choices + choices[:1]))
        self.assertEqual(self.encodes, [])

    def test_unauthorized_and_unregistered_indexes_are_not_exposed(self):
        self.denied.add('library:cats')
        preview = self.discover()
        self.assertNotIn('cats-index', json.dumps(preview.to_view()))
        self.assertEqual(preview.to_view()['routes'][-1]['availability'], 'no_available_index')
        self.preparer.capabilities.pop(self.a)
        self.assertTrue(all(not route.options for route in self.discover().routes))
        self.down = True
        self.assert_code('AUTH_UNAVAILABLE', self.discover)

    def test_wrong_owner_and_stale_scope_never_query_catalog(self):
        self.assert_code('NOT_FOUND', lambda: self.router.discover(Actor('tenant', 'intruder'),
            self.task_id, expected_scope=self.scope, authorization_version='auth-v1'))
        self.assert_code('REVISION_CONFLICT', lambda: self.router.discover(self.actor,
            self.task_id, expected_scope=replace(self.scope, generation=999), authorization_version='auth-v1'))
        self.assertEqual(self.catalog_calls, [])

    def test_invalid_selection_and_missing_original_never_encode(self):
        preview = self.discover()
        choices = self.choices(preview)
        for bad, code in ((choices[1:], 'ORIGINAL_QUERY_REQUIRED'),
                          ((replace(choices[0], binding_id='unknown'),), 'INVALID_ROUTE_SELECTION'),
                          ((replace(choices[0], batch_size=100),), 'INPUT_LIMIT'),
                          ((replace(choices[0], batch_size=True),), 'INVALID_ROUTE_SELECTION'),
                          (choices * 22, 'INVALID_ROUTE_SELECTION')):
            self.assert_code(code, lambda: self.prepare(preview, bad))
        self.assertEqual(self.encodes, [])

    def test_capability_change_invalidates_displayed_preview(self):
        preview = self.discover()
        self.preparer.capabilities[self.a] = replace(self.preparer.capabilities[self.a], filter_template_ref='filter-v2')
        self.assert_code('ROUTING_CHANGED', lambda: self.prepare(preview))
        self.assertEqual(self.encodes, [])

    def test_index_version_change_during_encoding_cannot_return_plan(self):
        preview = self.discover()
        def switch():
            updated = replace(self.b, index_version='v4')
            self.libraries = (self.libraries[0], replace(self.libraries[1], bindings=(updated,)))
            self.preparer.capabilities[updated] = replace(self.preparer.capabilities[self.b], binding=updated)
        self.after_encode = switch
        self.assert_code('ROUTING_CHANGED', lambda: self.prepare(preview))
        self.assertEqual(self.repo.get(self.actor, self.task_id), self.task)

    def test_previous_library_revoked_during_later_encoding_rejects_whole_set(self):
        preview = self.discover()
        choices = (QueryRouteChoice(preview.routes[0].branch_id, self.a.binding_id, 5),
                   QueryRouteChoice(preview.routes[2].branch_id, self.b.binding_id, 5))
        def revoke():
            if len(self.encodes) == 2:
                self.denied.add('library:dogs')
        self.after_encode = revoke
        self.assert_code('ROUTING_CHANGED', lambda: self.prepare(preview, choices))

    def test_task_change_during_preparation_rejects_whole_set(self):
        preview = self.discover()
        original_get = self.repo.get
        def get(actor, task_id):
            task = original_get(actor, task_id)
            return replace(task, task_version=task.task_version + 1) if self.encodes else task
        with patch.object(self.repo, 'get', side_effect=get):
            self.assert_code('REVISION_CONFLICT', lambda: self.prepare(preview))

    def test_duplicate_catalog_binding_fails_without_guessing(self):
        self.libraries = (replace(self.libraries[0], bindings=(self.a, self.a)), self.libraries[1])
        self.assert_code('INDEX_BINDING_MISMATCH', self.discover)
        self.assertEqual(self.encodes, [])

    def test_malformed_index_metadata_and_branch_subset(self):
        preview = self.discover()
        subset = (preview.routes[0].branch_id, preview.routes[2].branch_id)
        self.assertEqual(len(self.discover(branch_ids=subset).routes), 2)
        self.assert_code('ORIGINAL_QUERY_REQUIRED', lambda: self.discover(branch_ids=subset[1:]))
        original = self.libraries
        for malformed in (replace(self.a, vector_dimension=[]), replace(self.a, vector_dimension=True),
                          replace(self.a, encoder_ref='')):
            self.libraries = (replace(original[0], bindings=(malformed,)), original[1])
            self.assert_code('INDEX_BINDING_MISMATCH', self.discover)


if __name__ == '__main__':
    unittest.main()
