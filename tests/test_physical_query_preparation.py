from dataclasses import replace
import json
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import IndexBinding, Library, QueryVector
from dsh_retrieval.models import Actor
from dsh_retrieval.physical_query_preparation import PhysicalQueryPreparer, TextQueryCapability
from dsh_retrieval.query_branches import compile_query_branches
from dsh_retrieval.retrieval_contracts import RetrievalScope, validate_plan
from dsh_retrieval.service import InMemoryTaskRepository
from draft_helpers import make_service, confirmation


class PhysicalPreparationTest(unittest.TestCase):
    def setUp(self):
        self.actor = Actor('tenant', 'owner')
        self.repo = InMemoryTaskRepository()
        self.service = make_service(self.repo)
        task, _ = self.service.create_task(self.actor, 'create', confirmation(self.service, self.actor))
        self.task_id = task['task_id']
        self.task = self.repo.get(self.actor, self.task_id)
        self.binding = IndexBinding('binding', 'library', 'index-v1', 'text-image-v1', 2, 'COSINE')
        self.vector = QueryVector('text-image-v1', (0.1, 0.2))
        self.libraries = (Library('library', 'authorized fixture', 'image', (self.binding,)),)
        self.denied = False
        self.down = False
        self.after_encode = lambda: None
        self.calls = []
        self.capability = TextQueryCapability(self.binding, 'image', 'trusted-filter@1', 20)
        self.preparer = self.make_preparer((self.capability,))
        preview = compile_query_branches(json.loads(self.task.confirmed_proposal_json)['query_spec'])
        self.args = dict(expected_scope=RetrievalScope.from_task(self.task),
                         preview_digest=preview['preview_digest'], branch_id=preview['branches'][0]['branch_id'],
                         binding_id='binding', epoch=1, batch_size=10, authorization_version='auth-v1')

    def make_preparer(self, capabilities):
        return PhysicalQueryPreparer(tasks=self.repo, projects=self.service.projects,
                                     catalog=self, embeddings=self, authorization=self,
                                     capabilities=capabilities)

    def find_libraries(self, context, text, media_type):
        self.calls.append('catalog')
        return self.libraries

    def require_access(self, context, resource_ref, action):
        self.calls.append(action)
        if self.down:
            raise ServiceError('AUTH_UNAVAILABLE', 'Unavailable', 503)
        if self.denied:
            raise ServiceError('ACCESS_DENIED', 'Denied', 403)

    def encode_query(self, context, text, binding):
        self.calls.append('encode')
        self.after_encode()
        return self.vector

    def prepare(self, **changes):
        return self.preparer.prepare(self.actor, self.task_id, **(self.args | changes))

    def assert_error(self, code, **changes):
        with self.assertRaises(ServiceError) as caught:
            self.prepare(**changes)
        self.assertEqual(caught.exception.code, code)

    def test_prepares_existing_plan_without_approval_or_task_mutation(self):
        result = self.prepare()
        validate_plan(result.plan)
        self.assertEqual(result, self.prepare())
        self.assertEqual(self.repo.get(self.actor, self.task_id), self.task)
        self.assertEqual(result.plan.request.filter_template_ref, 'trusted-filter@1')
        self.assertIsNone(result.plan.request.cursor)
        self.assertLess(self.calls.index('search'), self.calls.index('encode'))
        self.assertNotEqual(result.plan.request.plan_id, self.prepare(batch_size=11).plan.request.plan_id)

    def test_preview_tamper_unknown_branch_and_stale_scope_fail_before_catalog(self):
        self.assert_error('PLAN_DIGEST_MISMATCH', preview_digest='changed')
        self.assert_error('INVALID_RETRIEVAL_PLAN', branch_id='other')
        self.assert_error('REVISION_CONFLICT', expected_scope=replace(self.args['expected_scope'], generation=2))
        self.assertEqual(self.calls, [])

    def test_absent_capability_changed_index_and_oversized_batch(self):
        self.assert_error('INPUT_LIMIT', batch_size=21)
        self.preparer = self.make_preparer(())
        self.assert_error('QUERY_UNSUPPORTED')
        self.preparer = self.make_preparer((self.capability,))
        self.libraries = (replace(self.libraries[0], bindings=(replace(self.binding, index_version='v2'),)),)
        self.assert_error('QUERY_UNSUPPORTED')
        self.assertNotIn('encode', self.calls)

    def test_missing_ambiguous_and_cross_library_binding(self):
        self.assert_error('INDEX_BINDING_MISMATCH', binding_id='hidden')
        self.libraries = self.libraries * 2
        self.assert_error('INDEX_BINDING_MISMATCH')
        self.libraries = (Library('other', 'fixture', 'image', (self.binding,)),)
        self.assert_error('INDEX_BINDING_MISMATCH')

    def test_bad_encoder_version_dimension_and_values(self):
        for vector in (None, QueryVector('wrong', (0.1, 0.2)),
                       QueryVector('text-image-v1', (0.1,)),
                       QueryVector('text-image-v1', (float('nan'), 0.2))):
            with self.subTest(vector=vector):
                self.vector = vector
                self.assert_error('ENCODER_MISMATCH')

    def test_permission_failure_and_revocation_during_encoding(self):
        self.denied = True
        self.assert_error('ACCESS_DENIED')
        self.assertNotIn('encode', self.calls)
        self.denied, self.down = False, True
        self.assert_error('AUTH_UNAVAILABLE')
        self.down = False
        self.after_encode = lambda: setattr(self, 'denied', True)
        self.assert_error('ACCESS_DENIED')

    def test_task_change_during_encoding(self):
        original_get = self.repo.get
        def changed_get(actor, task_id):
            return replace(original_get(actor, task_id), task_version=2)
        self.after_encode = lambda: setattr(self.repo, 'get', changed_get)
        self.assert_error('REVISION_CONFLICT')


if __name__ == '__main__':
    unittest.main()
