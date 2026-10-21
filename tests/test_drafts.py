from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import unittest

from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.service import TaskService
from draft_helpers import BODY, make_service, confirmation


class DraftTest(unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryTaskRepository()
        self.service = make_service(self.repo)
        self.actor = Actor("tenant-a", "user-a")

    def test_draft_has_no_formal_records_and_replays(self):
        first, _ = self.service.save_draft(self.actor, "demo", "d", BODY)
        second, replay = self.service.save_draft(self.actor, "demo", "d", deepcopy(BODY))
        self.assertTrue(replay)
        self.assertEqual(first, second)
        self.assertEqual(self.repo._tasks, {})
        self.assertEqual(self.repo._batches, {})

    def test_default_manual_modes_are_displayed_and_hashed(self):
        body = deepcopy(BODY)
        del body["node_modes"]
        result, _ = self.service.save_draft(self.actor, "demo", "d", body)
        replay, reused = self.service.save_draft(self.actor, "demo", "d", BODY)
        self.assertEqual(result["proposal"]["node_modes"], BODY["node_modes"])
        self.assertTrue(reused)
        self.assertEqual(result["proposal_digest"], replay["proposal_digest"])

    def test_different_confirmation_keys_still_create_one(self):
        body = confirmation(self.service, self.actor)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: self.service.create_task(self.actor, str(i), body), range(24)))
        self.assertEqual(len(self.repo._tasks), 1)
        self.assertEqual(len(self.repo._batches), 1)
        self.assertEqual(sum(not replay for _, replay in results), 1)
        result = results[0][0]
        self.assertEqual(result["run_status"], "PLANNING")
        self.assertEqual(result["confirmed_revision"], 1)
        self.assertIsNone(result["pending_draft"])
        self.assertNotIn("pending_revision", result)

    def test_stale_confirmation_after_edit_rejected(self):
        old = confirmation(self.service, self.actor)
        changed = deepcopy(BODY)
        changed["query_spec"]["raw_request"] = "改为卧室"
        changed["expected_draft_version"] = 1
        updated, _ = self.service.save_draft(self.actor, "demo", "edit", changed, old["draft_id"])
        self.assertEqual(updated["draft_version"], 2)
        with self.assertRaises(ServiceError) as error:
            self.service.create_task(self.actor, "confirm", old)
        self.assertEqual(error.exception.code, "DRAFT_VERSION_CONFLICT")
        self.assertEqual(self.repo._tasks, {})

    def test_other_owner_cannot_confirm_even_with_project_grant(self):
        body = confirmation(self.service, self.actor)
        self.service.projects.grants.add(("tenant-a", "other", "demo", "default"))
        with self.assertRaises(ServiceError) as error:
            self.service.create_task(Actor("tenant-a", "other"), "confirm", body)
        self.assertEqual(error.exception.status, 404)

    def test_revocation_blocks_confirm_replay_and_read(self):
        body = confirmation(self.service, self.actor)
        task, _ = self.service.create_task(self.actor, "confirm", body)
        self.service.projects.grants.clear()
        for call in (lambda: self.service.create_task(self.actor, "confirm", body),
                     lambda: self.service.get_task(self.actor, task["task_id"])):
            with self.assertRaises(ServiceError) as error:
                call()
            self.assertEqual(error.exception.code, "PROJECT_ACCESS_DENIED")

    def test_confirmation_snapshot_detached_and_draft_locked(self):
        body = confirmation(self.service, self.actor)
        task, _ = self.service.create_task(self.actor, "confirm", body)
        view = self.service.get_draft(self.actor, "demo", body["draft_id"])
        view["proposal"]["query_spec"]["target_count"] = 999
        frozen = self.repo.get(self.actor, task["task_id"])
        self.assertEqual(json.loads(frozen.confirmed_proposal_json)["query_spec"]["target_count"], 10)
        with self.assertRaises(ServiceError):
            self.service.save_draft(self.actor, "demo", "edit", {**BODY, "expected_draft_version": 1}, body["draft_id"])

    def test_legacy_direct_creation_and_mixed_schema_rejected(self):
        with self.assertRaises(ServiceError) as error:
            self.service.create_task(self.actor, "key", {"request": "直接创建"})
        self.assertEqual(error.exception.code, "CONFIRMATION_REQUIRED")
        for field in ("auto_refill", "allow_auto_refill", "dedup_policy"):
            body = deepcopy(BODY)
            body["query_spec"][field] = True
            with self.assertRaises(ServiceError) as error:
                self.service.save_draft(self.actor, "demo", "key", body)
            self.assertEqual(error.exception.code, "SCHEMA_CONFLICT")

    def test_boolean_version_rejected_and_permissions_default_deny(self):
        body = confirmation(self.service, self.actor)
        with self.assertRaises(ServiceError):
            self.service.create_task(self.actor, "key", {**body, "expected_draft_version": True})
        with self.assertRaises(ServiceError):
            TaskService(self.repo).save_draft(self.actor, "demo", "key", BODY)

    def test_cross_project_draft_route_rejected(self):
        body = confirmation(self.service, self.actor)
        with self.assertRaises(ServiceError):
            self.service.get_draft(self.actor, "other-project", body["draft_id"])

    def test_unsupported_modes_and_invalid_types_never_create_records(self):
        cases = []
        for value in (True, 0, -1, "10"):
            body = deepcopy(BODY)
            body["query_spec"]["target_count"] = value
            cases.append(body)
        for mode in ("maintain_target", "auto"):
            body = deepcopy(BODY)
            body["query_spec"]["refill_policy"]["mode"] = mode
            cases.append(body)
        body = deepcopy(BODY)
        body["query_spec"]["source_constraints"] = ["arbitrary"]
        cases.append(body)
        body = deepcopy(BODY)
        body["node_modes"]["cleaning"] = "auto"
        cases.append(body)
        for index, body in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ServiceError):
                self.service.save_draft(self.actor, "demo", str(index), body)
        self.assertEqual(self.repo._drafts, {})
        self.assertEqual(self.repo._tasks, {})

    def test_same_draft_key_changed_body_conflicts(self):
        confirmation(self.service, self.actor)
        body = deepcopy(BODY)
        body["query_spec"]["raw_request"] = "另一份方案"
        with self.assertRaises(ServiceError) as error:
            self.service.save_draft(self.actor, "demo", "draft", body)
        self.assertEqual(error.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_concurrent_draft_edit_is_compare_and_swap(self):
        old = confirmation(self.service, self.actor)
        def revise(i):
            body = deepcopy(BODY)
            body["expected_draft_version"] = 1
            body["query_spec"]["raw_request"] = str(i)
            try:
                self.service.save_draft(self.actor, "demo", f"edit-{i}", body, old["draft_id"])
                return "updated"
            except ServiceError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(revise, range(12)))
        self.assertEqual(results.count("updated"), 1)
        self.assertEqual(results.count("DRAFT_VERSION_CONFLICT"), 11)
