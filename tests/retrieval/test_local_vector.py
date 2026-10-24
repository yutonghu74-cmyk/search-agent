from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from dsh_retrieval.adapters.retrieval.local_vector import LocalVectorRecord, LocalVectorRetrieval
from dsh_retrieval.adapters.retrieval.simulated import ApprovedFixture
from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.task_storage.retrieval import SQLiteRetrievalPlanRepository
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import (
    AccessContext, IndexBinding, Library, MediaRecord, QueryVector, SearchRequest,
)
from dsh_retrieval.models import Actor
from dsh_retrieval.proposals import NODES
from dsh_retrieval.retrieval_contracts import RetrievalPlan, RetrievalScope, validate_page
from dsh_retrieval.service import TaskService


class Authority:
    def __init__(self):
        self.denied = set()
        self.down = False

    def require_access(self, context, resource_ref, action):
        if self.down:
            raise ServiceError("AUTH_UNAVAILABLE", "Authority unavailable", 503)
        if resource_ref in self.denied:
            raise ServiceError("ACCESS_DENIED", "Access denied", 403)


class LocalVectorTest(unittest.TestCase):
    def setUp(self):
        self.authority = Authority()
        self.context = AccessContext(Actor("tenant", "owner"), "task", "search", "auth-v1")
        self.dog = IndexBinding("dogs", "animals", "v1", "dog-encoder-v1", 2, "COSINE")
        self.cat = IndexBinding("cats", "cats", "v2", "cat-encoder-v2", 2, "L2")
        self.dog_query = QueryVector(self.dog.encoder_ref, (1.0, 0.0))
        self.cat_query = QueryVector(self.cat.encoder_ref, (0.0, 1.0))
        self.dog_request = SearchRequest("dog-plan", 1, self.dog, self.dog_query, "allowed", 2)
        self.cat_request = SearchRequest("cat-plan", 1, self.cat, self.cat_query, "allowed", 2)
        self.libraries = (Library("animals", "dog images", "image", (self.dog,)),
                          Library("cats", "cat images", "image", (self.cat,)))
        self.media_dog = MediaRecord("dog-unit", "1", "source", "dog", "media:dog", "digest")
        self.media_cat = MediaRecord("cat-unit", "1", "source", "cat", "media:cat", "digest")
        self.records = (
            LocalVectorRecord(self.dog, 1, (1.0, 0.0), "public", (self.media_dog,)),
            LocalVectorRecord(self.dog, 2, (0.8, 0.6), "public"),
            LocalVectorRecord(self.dog, 3, (0.6, 0.8), "public"),
            LocalVectorRecord(self.dog, 4, (0.9, 0.1), "private"),
            LocalVectorRecord(self.cat, 1, (0.0, 1.0), "public", (self.media_cat,)),
            LocalVectorRecord(self.cat, 2, (1.0, 0.0), "public"),
        )

    def make_adapter(self, *, records=None, depth=10, secret=b"s" * 32):
        return LocalVectorRetrieval(
            self.authority, libraries=self.libraries,
            query_vectors={(self.dog, "dog"): self.dog_query,
                           (self.cat, "cat"): self.cat_query},
            approved=(ApprovedFixture(self.context, self.dog_request, (), depth),
                      ApprovedFixture(self.context, self.cat_request, (), depth)),
            records=self.records if records is None else records,
            filter_templates={"allowed": frozenset({"public"})}, cursor_secret=secret,
        )

    def assert_code(self, code, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_two_index_query_rank_page_and_mapping(self):
        adapter = self.make_adapter()
        self.assertEqual(adapter.mode, "LOCAL_EXACT")
        self.assertEqual(adapter.encode_query(self.context, "dog", self.dog), self.dog_query)
        self.assertEqual(adapter.encode_query(self.context, "cat", self.cat), self.cat_query)
        first = adapter.search(self.context, self.dog_request)
        validate_page(self.dog_request, first)
        self.assertEqual([hit.vector_id for hit in first.hits], [1, 2])
        self.assertEqual(first.stop_reason, "more_available")
        self.assertEqual(adapter.search(self.context, self.dog_request), first)
        second_request = replace(self.dog_request, cursor=first.next_cursor)
        second = adapter.search(self.context, second_request)
        validate_page(second_request, second, previous=first)
        self.assertEqual([hit.vector_id for hit in second.hits], [3])
        self.assertEqual(second.stop_reason, "exhausted")
        self.assertEqual(first.snapshot_ref, second.snapshot_ref)
        self.assertEqual(adapter.resolve_hits(self.context, first.hits)[0].media, self.media_dog)
        self.assertEqual(adapter.resolve_hits(self.context, second.hits)[0].status, "mapping_missing")
        cat = adapter.search(self.context, self.cat_request)
        self.assertEqual([hit.vector_id for hit in cat.hits], [1, 2])
        self.assertEqual(adapter.resolve_hits(self.context, cat.hits)[0].media, self.media_cat)

    def test_cursor_survives_same_corpus_restart_but_not_changed_corpus_or_plan(self):
        first = self.make_adapter().search(self.context, self.dog_request)
        continuation = replace(self.dog_request, cursor=first.next_cursor)
        self.assertEqual(self.make_adapter().search(self.context, continuation).hits[0].vector_id, 3)
        changed = self.records + (LocalVectorRecord(self.dog, 5, (0.4, 0.9), "public"),)
        self.assert_code("INDEX_SNAPSHOT_EXPIRED", lambda: self.make_adapter(records=changed).search(
            self.context, continuation))
        self.assert_code("CURSOR_INVALID", lambda: self.make_adapter(secret=b"x" * 32).search(
            self.context, continuation))
        self.assert_code("PLAN_MISMATCH", lambda: self.make_adapter().search(
            self.context, replace(continuation, plan_id="new")))
        self.assert_code("CURSOR_INVALID", lambda: self.make_adapter().search(
            self.context, replace(self.dog_request, cursor="bad-token")))

    def test_depth_limit_empty_and_auth_failure(self):
        first = self.make_adapter(depth=2).search(self.context, self.dog_request)
        self.assertEqual(first.stop_reason, "depth_limit")
        self.assertIsNone(first.next_cursor)
        empty = self.make_adapter(records=()).search(self.context, self.dog_request)
        self.assertEqual(empty.hits, ())
        self.assertEqual(empty.stop_reason, "exhausted")
        self.authority.denied.add("library:animals")
        self.assert_code("ACCESS_DENIED", lambda: self.make_adapter().search(self.context, self.dog_request))
        self.authority.denied.clear()
        self.authority.denied.add("media:dog")
        hit = self.make_adapter().search(self.context, self.dog_request).hits[0]
        resolution = self.make_adapter().resolve_hits(self.context, (hit,))[0]
        self.assertEqual(resolution.status, "unavailable")
        self.assertIsNone(resolution.media)
        self.authority.down = True
        self.assert_code("AUTH_UNAVAILABLE", lambda: self.make_adapter().search(self.context, self.dog_request))

    def test_mismatched_encoder_and_bad_fixture_rejected(self):
        self.assert_code("ENCODER_MISMATCH", lambda: self.make_adapter().search(
            self.context, replace(self.dog_request, vector=self.cat_query)))
        with self.assertRaises(ValueError):
            self.make_adapter(records=(LocalVectorRecord(self.dog, 99, (0.0, 0.0), "public"),))
        self.assert_code("PLAN_MISMATCH", lambda: self.make_adapter().search(
            self.context, replace(self.dog_request, filter_template_ref="arbitrary")))
        with self.assertRaises(ValueError):
            self.make_adapter(records=self.records + (self.records[0],))

    def test_confirmed_plan_pages_commit_and_resume_from_sqlite(self):
        body = {
            "dimension_id": "default",
            "query_spec": {
                "schema_version": "query-spec@2", "raw_request": "找狗图片",
                "media_type": "image", "target_count": 3, "business_domain": "animals",
                "intra_batch_dedup": {"policy_ref": "exact-content", "config_version": "1"},
                "cross_batch_dedup": {"scope": "none", "references": [],
                                      "comparison_methods": [], "reference_digest": None},
                "refill_policy": {"mode": "input_only", "target_count": 3,
                                  "approved_library_ids": [], "progression": "manual"},
            },
            "node_modes": dict.fromkeys(NODES, "manual"),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            task_repo = SQLiteTaskRepository(path)
            projects = LocalProjectAccess([("tenant", "owner", "demo", "default")])
            tasks = TaskService(task_repo, projects)
            draft, _ = tasks.save_draft(self.context.actor, "demo", "draft", body)
            task, _ = tasks.create_task(self.context.actor, "task", {
                "draft_id": draft["draft_id"], "expected_draft_version": draft["draft_version"],
                "proposal_digest": draft["proposal_digest"], "action": "confirm",
            })
            task_id = task["task_id"]
            context = replace(self.context, task_id=task_id)
            ledger = SQLiteRetrievalPlanRepository(path, projects=projects, authorization=self.authority)
            proposal = RetrievalPlan(RetrievalScope.from_task(task_repo.get(self.context.actor, task_id)),
                                     1, self.dog_request)
            ledger.put_proposal(self.context.actor, proposal)
            plan, replay = ledger.confirm(self.context.actor, task_id, proposal.request.plan_id,
                                          key="approve", proposal_digest=proposal.proposal_digest,
                                          expected_task_version=proposal.scope.task_version,
                                          action="confirm")
            self.assertFalse(replay)
            gateway = LocalVectorRetrieval(
                self.authority, libraries=self.libraries,
                query_vectors={(self.dog, "dog"): self.dog_query},
                approved=(ApprovedFixture(context, plan.request, (), 10),),
                records=self.records, filter_templates={"allowed": frozenset({"public"})},
                cursor_secret=b"s" * 32)
            request = plan.request
            operation, _ = ledger.reserve_page(self.context.actor, plan, request, "page-1")
            first = gateway.search(context, request)
            self.assertFalse(ledger.commit_page(self.context.actor, operation, plan, request, first))
            self.assertEqual(len(first.hits), 2)
            # New ledger and connector instances represent a process restart.
            resumed = SQLiteRetrievalPlanRepository(path, projects=projects, authorization=self.authority)
            gateway = LocalVectorRetrieval(
                self.authority, libraries=self.libraries,
                query_vectors={(self.dog, "dog"): self.dog_query},
                approved=(ApprovedFixture(context, plan.request, (), 10),),
                records=self.records, filter_templates={"allowed": frozenset({"public"})},
                cursor_secret=b"s" * 32)
            request = replace(plan.request, cursor=first.next_cursor)
            operation, _ = resumed.reserve_page(self.context.actor, plan, request, "page-2")
            last = gateway.search(context, request)
            self.assertFalse(resumed.commit_page(self.context.actor, operation, plan, request, last))
            self.assertEqual([hit.vector_id for hit in last.hits], [3])
            self.assertEqual(last.stop_reason, "exhausted")
            self.assertEqual(len(resumed.list_pending(self.context.actor, task_id)), 0)
            self.assertIsNone(task_repo.cleaning_results(self.context.actor, task_id))


if __name__ == "__main__":
    unittest.main()
