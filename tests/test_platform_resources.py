from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.integration_models import (AccessContext, IndexBinding, Library, MediaRecord,
    MediaResolution, SearchHit, SearchPage, SearchRequest, QueryVector)
from dsh_retrieval.platform_resources import PlatformResources, DatasetVersionRef


class ResourcesTest(unittest.TestCase):
    def setUp(self):
        self.actor = Actor('platform', 'a')
        self.context = AccessContext(self.actor, 'task-a', 'candidate_preview', 'current')
        self.binding = IndexBinding('binding-a', 'lib-a', 'index-v1', 'encoder-v1', 2, 'COSINE')
        self.hit = SearchHit(self.binding, 'hit-a', .9)
        self.ref_a, self.ref_b = DatasetVersionRef('lib-a', 'v1'), DatasetVersionRef('lib-b', 'v2')
        self.project_set, self.user_set = {self.ref_a}, {self.ref_a, self.ref_b}
        self.media_ref = self.ref_a
        self.data = b'picture-bytes'
        self.member = True
        self.on_read = lambda: None
        class Projects:
            def require_access(_, actor, project, dimension, action):
                if not self.member:
                    raise ServiceError('PROJECT_ACCESS_DENIED', 'removed', 403)
        class Tasks:
            def get(_, actor, task):
                if actor != self.actor or task != 'task-a':
                    raise ServiceError('NOT_FOUND', 'not found', 404)
                return SimpleNamespace(project_id='101', dimension_id='default')
        self.media = MediaRecord('media-a', 'asset-v1', 'namespace-a', 'asset-a', 'opaque-media-a', 'sha256:'+'a'*64)
        @contextmanager
        def open_media(*args):
            self.on_read()
            yield BytesIO(self.data)
        def search(context, request, project, versions):
            self.search_args = project, versions
            return SearchPage((self.hit,), None, 'exhausted', 'snapshot', 'snapshot-a')
        self.resources = PlatformResources(tasks=Tasks(), projects=Projects(),
            project_datasets=lambda actor, project: self.project_set,
            user_datasets=lambda actor: self.user_set,
            libraries=lambda actor, project: (Library('lib-a', 'A', 'image', (self.binding,)),
                                               Library('lib-b', 'B', 'image', ())),
            resolve_hits=lambda context, hits, project: tuple(MediaResolution(hit, self.media, 'resolved') for hit in hits),
            search=search, open_media=open_media,
            media_version=lambda actor, project, ref: self.media_ref)

    def test_directory_is_project_personal_intersection(self):
        self.assertEqual([lib.library_id for lib in self.resources.find_libraries(self.context, '', 'image')], ['lib-a'])
        with self.assertRaises(ServiceError):
            self.resources.require_access(self.context, 'library:lib-b', 'search')
        self.user_set = {self.ref_b}
        self.assertEqual(self.resources.find_libraries(self.context, '', 'image'), ())

    def test_search_filters_before_recall_and_checks_hits_after(self):
        request = SearchRequest('plan-a', 1, self.binding, QueryVector('encoder-v1', (1., 0.)), 'filter', 10)
        self.resources.search(self.context, request)
        self.assertEqual(self.search_args, ('101', frozenset({'v1'})))
        self.media_ref = self.ref_b
        with self.assertRaises(ServiceError):
            self.resources.search(self.context, request)

    def test_mapping_rejects_missing_and_wrong_hits(self):
        self.resources.resolver = lambda *args: ()
        with self.assertRaises(ServiceError):
            self.resources.resolve_hits(self.context, (self.hit,))
        self.resources.resolver = lambda *args: (MediaResolution(SearchHit(self.binding, 'other', .5), self.media, 'resolved'),)
        with self.assertRaises(ServiceError):
            self.resources.resolve_hits(self.context, (self.hit,))

    def test_bounded_media_and_revocation_before_yield(self):
        with self.resources.open_read(self.context, 'opaque-media-a', 100) as stream:
            self.assertEqual(stream.read(), self.data)
        with self.assertRaises(ServiceError):
            with self.resources.open_read(self.context, 'opaque-media-a', 3):
                self.fail('oversized media yielded')
        self.on_read = lambda: setattr(self, 'member', False)
        with self.assertRaises(ServiceError):
            with self.resources.open_read(self.context, 'opaque-media-a', 100):
                self.fail('revoked media yielded')

    def test_media_version_changed_during_read_denied(self):
        self.on_read = lambda: setattr(self, 'media_ref', self.ref_b)
        with self.assertRaises(ServiceError):
            with self.resources.open_read(self.context, 'opaque-media-a', 100):
                self.fail('wrong version yielded')

    def test_unknown_purpose_and_cross_owner_denied(self):
        context = AccessContext(Actor('platform', 'b'), 'task-a', 'candidate_preview', 'current')
        with self.assertRaises(ServiceError):
            self.resources.resolve_hits(context, (self.hit,))
        with self.assertRaises(ServiceError):
            self.resources.require_access(self.context, 'opaque-media-a', 'unknown-purpose')

    def test_revocation_after_consumer_read_denied(self):
        with self.assertRaises(ServiceError):
            with self.resources.open_read(self.context, 'opaque-media-a', 100) as stream:
                stream.read()
                self.member = False
