from dataclasses import replace
import unittest

from dsh_retrieval.adapters.retrieval.simulated import ApprovedFixture, SimulatedRetrieval
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import (
    AccessContext, IndexBinding, Library, MediaRecord, QueryVector, SearchHit, SearchRequest,
)
from dsh_retrieval.models import Actor
from dsh_retrieval.retrieval_contracts import validate_page


class Authority:
    def __init__(self, context):
        self.context = context
        self.denied = set()
        self.calls = []
        self.down = False

    def require_access(self, context, resource_ref, action):
        self.calls.append((resource_ref, action))
        if self.down:
            raise ServiceError("AUTH_UNAVAILABLE", "Authority unavailable", 503)
        if context != self.context or resource_ref in self.denied:
            raise ServiceError("ACCESS_DENIED", "Denied", 403)


class RetrievalTest(unittest.TestCase):
    def setUp(self):
        self.context = AccessContext(Actor("tenant", "owner"), "task", "search", "auth-v1")
        self.auth = Authority(self.context)
        self.binding = IndexBinding("binding", "images", "index-v1", "encoder-v1", 2, "COSINE")
        self.other = IndexBinding("other", "other-images", "index-v2", "encoder-v2", 2, "L2")
        self.vector = QueryVector("encoder-v1", (0.5, 0.5))
        self.request = SearchRequest("plan", 1, self.binding, self.vector, "approved-filter", 2)
        self.hits = tuple(SearchHit(self.binding, i, 1.0 - i / 10) for i in range(5))
        self.media = MediaRecord("unit", "v1", "source", "asset", "media:one", "digest")
        self.libraries = (Library("images", "living room", "image", (self.binding,)),
                          Library("other-images", "living room other", "image", (self.other,)))
        self.adapter = self.make_adapter()

    def make_adapter(self, *, hits=None, depth=1000, approved=None, mappings=None, vectors=None):
        return SimulatedRetrieval(
            self.auth, libraries=self.libraries,
            vectors=vectors if vectors is not None else {(self.binding, "room"): self.vector},
            approved=approved if approved is not None else (
                ApprovedFixture(self.context, self.request, self.hits if hits is None else hits, depth),),
            mappings=mappings if mappings is not None else {(self.binding, 0): (self.media,)})

    def assert_code(self, code, callback):
        with self.assertRaises(ServiceError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_catalog_filters_authority_before_descriptions(self):
        self.auth.denied.add("library:other-images")
        self.assertEqual(self.adapter.find_libraries(self.context, "ROOM", "image"), self.libraries[:1])
        self.assertEqual(self.adapter.find_libraries(self.context, "room", "video"), ())
        self.assertIn(("library:other-images", "discover"), self.auth.calls)

    def test_matching_encoder_is_explicit_and_unknown_query_rejected(self):
        self.assertEqual(self.adapter.encode_query(self.context, "room", self.binding), self.vector)
        self.assert_code("QUERY_UNSUPPORTED", lambda: self.adapter.encode_query(self.context, "new", self.binding))
        self.assert_code("QUERY_UNSUPPORTED", lambda: self.adapter.encode_query(self.context, "room", self.other))
        bad = self.make_adapter(vectors={(self.binding, "room"): QueryVector("encoder-v2", (0.5, 0.5))})
        self.assert_code("ENCODER_MISMATCH", lambda: bad.encode_query(self.context, "room", self.binding))

    def test_vector_validation(self):
        for vector in (QueryVector("wrong", (0.5, 0.5)), QueryVector("encoder-v1", (0.5,)),
                       QueryVector("encoder-v1", (float("nan"), 0.5)),
                       QueryVector("encoder-v1", (float("inf"), 0.5))):
            with self.subTest(vector=vector):
                self.assert_code("ENCODER_MISMATCH", lambda: self.adapter.search(
                    self.context, replace(self.request, vector=vector)))

    def test_repeatable_pagination_no_duplicate_first_page(self):
        page = self.adapter.search(self.context, self.request)
        self.assertEqual(page, self.adapter.search(self.context, self.request))
        self.assertEqual(page.stop_reason, "more_available")
        found = list(page.hits)
        snapshot = page.snapshot_ref
        while page.next_cursor:
            request = replace(self.request, cursor=page.next_cursor)
            page = self.adapter.search(self.context, request)
            self.assertEqual(page, self.adapter.search(self.context, request))
            self.assertEqual(page.snapshot_ref, snapshot)
            found.extend(page.hits)
        self.assertEqual(tuple(found), self.hits)
        self.assertEqual(page.stop_reason, "exhausted")
        self.assertEqual(page.consistency, "snapshot")
        self.assertTrue(snapshot.startswith("simulated:"))

    def test_empty_results_are_valid_exhausted_page(self):
        page = self.make_adapter(hits=()).search(self.context, self.request)
        self.assertEqual(page.hits, ())
        self.assertIsNone(page.next_cursor)
        self.assertEqual(page.stop_reason, "exhausted")

    def test_depth_limit_is_not_exhaustion(self):
        adapter = self.make_adapter(depth=3)
        first = adapter.search(self.context, self.request)
        last = adapter.search(self.context, replace(self.request, cursor=first.next_cursor))
        self.assertEqual(last.hits, self.hits[2:3])
        self.assertEqual(last.stop_reason, "depth_limit")
        self.assertIsNone(last.next_cursor)

    def test_changed_approved_plan_rejected(self):
        for change in ({"plan_id": "other"}, {"plan_version": 2}, {"batch_size": 3},
                       {"filter_template_ref": "arbitrary SQL"},
                       {"vector": QueryVector("encoder-v1", (0.1, 0.9))}):
            with self.subTest(change=change):
                self.assert_code("PLAN_MISMATCH", lambda: self.adapter.search(
                    self.context, replace(self.request, **change)))

    def test_invalid_bounds_and_unknown_cursor(self):
        for size in (0, 101, True):
            self.assert_code("INVALID_SEARCH_REQUEST", lambda: self.adapter.search(
                self.context, replace(self.request, batch_size=size)))
        self.assert_code("CURSOR_INVALID", lambda: self.adapter.search(
            self.context, replace(self.request, cursor="fabricated")))

    def test_cursor_cannot_cross_approved_plans(self):
        second = replace(self.request, plan_id="second")
        adapter = self.make_adapter(approved=(ApprovedFixture(self.context, self.request, self.hits),
                                             ApprovedFixture(self.context, second, self.hits)))
        cursor = adapter.search(self.context, self.request).next_cursor
        self.assert_code("CURSOR_INVALID", lambda: adapter.search(self.context, replace(second, cursor=cursor)))

    def test_cross_task_and_tenant_denied(self):
        for context in (replace(self.context, task_id="other"),
                        replace(self.context, actor=Actor("other", "owner")),
                        replace(self.context, actor=Actor("tenant", "other"))):
            self.assert_code("ACCESS_DENIED", lambda: self.adapter.search(context, self.request))
        # Even if the authority permits a second task, the plan is still task-bound.
        other = replace(self.context, task_id="other")
        self.auth.context = other
        self.assert_code("PLAN_MISMATCH", lambda: self.adapter.search(other, self.request))

    def test_revocation_blocks_next_page_and_encoding(self):
        first = self.adapter.search(self.context, self.request)
        self.auth.denied.add("library:images")
        self.assert_code("ACCESS_DENIED", lambda: self.adapter.search(
            self.context, replace(self.request, cursor=first.next_cursor)))
        self.assert_code("ACCESS_DENIED", lambda: self.adapter.encode_query(self.context, "room", self.binding))

    def test_snapshot_expired_and_version_changed_never_restart(self):
        cursor = self.adapter.search(self.context, self.request).next_cursor
        changed = replace(self.binding, index_version="index-v2")
        self.assert_code("INDEX_BINDING_MISMATCH", lambda: self.adapter.search(
            self.context, replace(self.request, binding=changed, cursor=cursor)))
        self.adapter.expire_snapshot(self.binding)
        self.assert_code("INDEX_SNAPSHOT_EXPIRED", lambda: self.adapter.search(
            self.context, replace(self.request, cursor=cursor)))

    def test_resolution_preserves_hits_and_isolates_missing_conflict(self):
        adapter = self.make_adapter(mappings={(self.binding, 0): (self.media,),
            (self.binding, 2): (self.media, replace(self.media, media_unit_id="different"))})
        result = adapter.resolve_hits(self.context, self.hits[:3])
        self.assertEqual(tuple(r.hit for r in result), self.hits[:3])
        self.assertEqual([r.status for r in result], ["resolved", "mapping_missing", "mapping_conflict"])
        self.assertIsNone(result[2].media)

    def test_same_vector_id_different_binding_does_not_alias(self):
        other_media = replace(self.media, media_unit_id="unit-two", authorized_media_ref="media:two")
        adapter = self.make_adapter(mappings={(self.binding, 0): (self.media,), (self.other, 0): (other_media,)})
        hits = (self.hits[0], SearchHit(self.other, 0, 0.9))
        result = adapter.resolve_hits(self.context, hits)
        self.assertEqual([r.media.media_unit_id for r in result], ["unit", "unit-two"])
        stale = replace(self.hits[0], binding=replace(self.binding, index_version="old"))
        self.assert_code("INDEX_BINDING_MISMATCH", lambda: adapter.resolve_hits(self.context, (stale,)))

    def test_media_revoked_exposes_no_metadata(self):
        self.auth.denied.add("media:one")
        result = self.adapter.resolve_hits(self.context, self.hits[:1])[0]
        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.media)

    def test_auth_outage_not_empty_results(self):
        self.auth.down = True
        for call in (lambda: self.adapter.find_libraries(self.context, "room", "image"),
                     lambda: self.adapter.search(self.context, self.request),
                     lambda: self.adapter.resolve_hits(self.context, self.hits[:1])):
            self.assert_code("AUTH_UNAVAILABLE", call)

    def test_cursor_not_portable_to_restarted_adapter(self):
        cursor = self.adapter.search(self.context, self.request).next_cursor
        restarted = self.make_adapter()
        self.assert_code("CURSOR_INVALID", lambda: restarted.search(
            self.context, replace(self.request, cursor=cursor)))

    def test_query_to_page_to_resolution_simulated_chain(self):
        libraries = self.adapter.find_libraries(self.context, "living", "image")
        binding = libraries[0].bindings[0]
        vector = self.adapter.encode_query(self.context, "room", binding)
        page = self.adapter.search(self.context, replace(self.request, vector=vector))
        results = self.adapter.resolve_hits(self.context, page.hits)
        self.assertEqual(self.adapter.mode, "SIMULATED")
        self.assertEqual(results[0].media, self.media)
        self.assertEqual(results[1].status, "mapping_missing")

    def test_pages_satisfy_shared_contract_after_baseline_sync(self):
        request = self.request
        previous = None
        seen = set()
        while True:
            page = self.adapter.search(self.context, request)
            validate_page(request, page, previous=previous, seen_cursors=frozenset(seen))
            if page.next_cursor is None:
                self.assertEqual(page.stop_reason, "exhausted")
                break
            seen.add(page.next_cursor)
            request = replace(self.request, cursor=page.next_cursor)
            previous = page

    def test_empty_and_depth_pages_satisfy_shared_contract(self):
        for hits, depth, expected in (((), 1000, "exhausted"), (self.hits, 1, "depth_limit")):
            with self.subTest(expected=expected):
                page = self.make_adapter(hits=hits, depth=depth).search(self.context, self.request)
                validate_page(self.request, page)
                self.assertEqual(page.stop_reason, expected)


if __name__ == "__main__":
    unittest.main()
