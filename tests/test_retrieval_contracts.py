from copy import deepcopy
from dataclasses import replace
import unittest

from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import IndexBinding, QueryVector, SearchHit, SearchPage, SearchRequest
from dsh_retrieval.models import Actor, RunStatus, TaskLifecycle
from dsh_retrieval.retrieval_contracts import (
    RetrievalPlan, RetrievalScope, validate_confirmation, validate_execution, validate_page,
)
from draft_helpers import confirmation, make_service


class RetrievalContractTest(unittest.TestCase):
    def setUp(self):
        self.actor = Actor("tenant", "owner")
        self.repo = InMemoryTaskRepository()
        service = make_service(self.repo)
        result, _ = service.create_task(self.actor, "create", confirmation(service, self.actor))
        self.task = self.repo.get(self.actor, result["task_id"])
        self.binding = IndexBinding("binding", "library", "index-v1", "encoder-v1", 2, "COSINE")
        self.request = SearchRequest("plan", 1, self.binding,
                                     QueryVector("encoder-v1", (0.5, 0.5)), "filter-v1", 2)
        self.plan = RetrievalPlan(RetrievalScope.from_task(self.task), 1, self.request)
        self.hit = SearchHit(self.binding, 1, 0.8)
        self.first = SearchPage((self.hit,), "cursor-2", "more_available", "snapshot", "snap-v1")

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as error:
            call()
        self.assertEqual(error.exception.code, code)

    def confirm(self, **changes):
        args = dict(action="confirm", proposal_digest=self.plan.proposal_digest,
                    expected_task_version=self.task.task_version)
        args.update(changes)
        validate_confirmation(self.actor, self.task, self.plan, **args)

    def execute(self, *, actor=None, task=None, submitted=None, current=None, request=None):
        validate_execution(actor or self.actor, task or self.task, submitted or self.plan,
                           current or self.plan, request or self.request)

    def test_confirmation_has_no_side_effect_and_digest_is_stable(self):
        before = deepcopy(self.task)
        self.confirm()
        self.confirm()
        self.assertEqual(self.task, before)
        self.assertEqual(self.plan.proposal_digest, deepcopy(self.plan).proposal_digest)
        self.assertEqual(self.repo.get(self.actor, self.task.task_id), before)

    def test_explicit_confirmation_version_and_digest(self):
        self.assert_code("CONFIRMATION_REQUIRED", lambda: self.confirm(action="auto"))
        self.assert_code("REVISION_CONFLICT", lambda: self.confirm(expected_task_version=True))
        self.assert_code("REVISION_CONFLICT", lambda: self.confirm(expected_task_version=99))
        self.assert_code("PLAN_DIGEST_MISMATCH", lambda: self.confirm(proposal_digest="changed"))

    def test_wrong_owner_or_tenant_hidden(self):
        for actor in (Actor("tenant", "other"), Actor("other", "owner")):
            self.assert_code("NOT_FOUND", lambda: self.execute(actor=actor))

    def test_task_revision_generation_and_requirement_changes_rejected(self):
        for changes in ({"task_id": "other"}, {"project_id": "other"},
                        {"confirmed_revision": 2}, {"run_generation": 2},
                        {"task_version": self.task.task_version + 1},
                        {"confirmed_proposal_json": "changed"}):
            with self.subTest(changes=changes):
                self.assert_code("REVISION_CONFLICT", lambda: self.execute(task=replace(self.task, **changes)))

    def test_pause_cancel_terminal_archive_rejected(self):
        for status in (RunStatus.PAUSED, RunStatus.CANCELLED, RunStatus.COMPLETED,
                       RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.AWAITING_INPUT):
            self.assert_code("INVALID_STATE_TRANSITION", lambda: self.execute(task=replace(self.task, run_status=status)))
        self.assert_code("INVALID_STATE_TRANSITION", lambda: self.execute(
            task=replace(self.task, lifecycle=TaskLifecycle.ARCHIVED)))

    def test_unapproved_or_superseded_epoch_cannot_execute(self):
        self.assert_code("PLAN_NOT_CURRENT", lambda: validate_execution(
            self.actor, self.task, self.plan, None, self.request))
        self.assert_code("PLAN_NOT_CURRENT", lambda: self.execute(current=replace(self.plan, epoch=2)))
        self.assert_code("PLAN_NOT_CURRENT", lambda: self.execute(current=replace(
            self.plan, request=replace(self.request, plan_version=2))))

    def test_finalizing_closes_retrieval_publication(self):
        self.assert_code("INVALID_STATE_TRANSITION", lambda: self.execute(
            task=replace(self.task, run_status=RunStatus.RUNNING, stage="FINALIZING")))

    def test_bad_hit_shape_is_an_explicit_connector_error(self):
        self.assert_code("INVALID_SEARCH_PAGE", lambda: validate_page(
            self.request, replace(self.first, hits=({"vector_id": 1},))))

    def test_frozen_request_cannot_be_changed(self):
        for changes in ({"batch_size": 1}, {"filter_template_ref": "sql"}, {"plan_id": "other"},
                        {"binding": replace(self.binding, index_version="v2")},
                        {"vector": QueryVector("encoder-v1", (0.9, 0.1))}):
            self.assert_code("PLAN_REQUEST_MISMATCH", lambda: self.execute(request=replace(self.request, **changes)))
        self.execute(request=replace(self.request, cursor="cursor-2"))
        self.assert_code("CURSOR_INVALID", lambda: self.execute(request=replace(self.request, cursor="")))

    def test_plan_with_cursor_or_bad_limits_rejected(self):
        for changes in ({"cursor": "cursor-2"}, {"batch_size": 0}, {"batch_size": 101},
                        {"plan_version": True}, {"filter_template_ref": ""}):
            plan = replace(self.plan, request=replace(self.request, **changes))
            self.assert_code("INVALID_RETRIEVAL_PLAN", lambda: self.execute(submitted=plan, current=plan))

    def test_encoder_dimension_nonfinite_mismatch(self):
        for vector in (QueryVector("wrong", (0.5, 0.5)), QueryVector("encoder-v1", (0.5,)),
                       QueryVector("encoder-v1", (float("nan"), 0.5))):
            plan = replace(self.plan, request=replace(self.request, vector=vector))
            self.assert_code("ENCODER_MISMATCH", lambda: self.execute(submitted=plan, current=plan))

    def test_empty_page_is_valid_without_cleaning_or_count_side_effects(self):
        before = deepcopy(self.task)
        validate_page(self.request, SearchPage((), None, "exhausted", "snapshot", "snap-v1"))
        self.assertEqual(self.task, before)
        self.assertEqual(self.repo.get(self.actor, self.task.task_id), before)

    def test_valid_continuation_preserves_snapshot(self):
        validate_page(self.request, self.first)
        request = replace(self.request, cursor="cursor-2")
        validate_page(request, SearchPage((), None, "exhausted", "snapshot", "snap-v1"), previous=self.first)
        validate_page(request, SearchPage((), None, "depth_limit", "snapshot", "snap-v1"), previous=self.first)

    def test_cursor_loop_unknown_predecessor_and_snapshot_switch(self):
        request = replace(self.request, cursor="cursor-2")
        self.assert_code("INVALID_SEARCH_PAGE", lambda: validate_page(request, self.first, previous=self.first))
        last = SearchPage((), None, "exhausted", "snapshot", "snap-v1")
        self.assert_code("INVALID_SEARCH_PAGE", lambda: validate_page(request, last))
        self.assert_code("INVALID_SEARCH_PAGE", lambda: validate_page(
            request, replace(last, snapshot_ref="snap-v2"), previous=self.first))
        self.assert_code("INVALID_SEARCH_PAGE", lambda: validate_page(
            request, replace(self.first, next_cursor="old"), previous=self.first, seen_cursors=frozenset({"old"})))

    def test_stop_reason_and_cursor_must_agree(self):
        for page in (replace(self.first, next_cursor=None), replace(self.first, stop_reason="exhausted"),
                     replace(self.first, stop_reason="depth_limit"), replace(self.first, snapshot_ref=None),
                     replace(self.first, next_cursor="")):
            self.assert_code("INVALID_SEARCH_PAGE", lambda: validate_page(self.request, page))

    def test_live_not_claimed_exhausted_or_snapshot(self):
        page = SearchPage((), None, "depth_limit", "live", None)
        validate_page(self.request, page)
        self.assert_code("INVALID_SEARCH_PAGE", lambda: validate_page(self.request, replace(page, stop_reason="exhausted")))
        self.assert_code("INVALID_SEARCH_PAGE", lambda: validate_page(self.request, replace(page, snapshot_ref="fake")))

    def test_foreign_binding_nonfinite_or_oversized_hits_rejected(self):
        for hits in ((replace(self.hit, binding=replace(self.binding, binding_id="other")),),
                     (replace(self.hit, score=float("inf")),), (replace(self.hit, vector_id=True),),
                     (self.hit, self.hit, self.hit)):
            self.assert_code("INVALID_SEARCH_PAGE", lambda: validate_page(self.request, replace(self.first, hits=hits)))


if __name__ == "__main__":
    unittest.main()
