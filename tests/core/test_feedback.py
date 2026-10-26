from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest

from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import preview_edited_groups
from dsh_retrieval.models import Actor
from dsh_retrieval.service import InMemoryTaskRepository
from draft_helpers import confirmation, make_service


class FeedbackTest(unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryTaskRepository()
        self.service = make_service(self.repo)
        self.actor = Actor("tenant-a", "user-a")
        body = confirmation(self.service, self.actor)
        self.task = self.service.create_task(self.actor, "confirm", body)[0]
        self.task_id = self.task["task_id"]

    def feedback_body(self, **changes):
        body = {"text": "再去掉所有包含文字的图片", "scope": "all",
                "expected_revision": self.task["confirmed_revision"],
                "expected_task_version": self.task["task_version"]}
        body.update(changes)
        return body

    def save(self, key="feedback", **changes):
        return self.service.save_feedback(self.actor, self.task_id, key, self.feedback_body(**changes))

    @staticmethod
    def confirmation_body(draft, action="confirm", **changes):
        body = {"draft_id": draft["draft_id"], "expected_draft_version": draft["draft_version"],
                "proposal_digest": draft["proposal_digest"],
                "expected_task_version": draft["base_task_version"], "action": action}
        body.update(changes)
        return body

    def assert_code(self, code, action):
        with self.assertRaises(ServiceError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def test_save_is_a_draft_and_task_view_exposes_pending(self):
        before = self.repo.get(self.actor, self.task_id)
        draft, replayed = self.save()
        after = self.repo.get(self.actor, self.task_id)
        self.assertFalse(replayed)
        self.assertEqual((after.confirmed_revision, after.task_version, after.run_generation, after.run_status),
                         (before.confirmed_revision, before.task_version, before.run_generation, before.run_status))
        self.assertIn("追加反馈：再去掉所有包含文字的图片",
                      draft["proposal"]["query_spec"]["raw_request"])
        self.assertEqual(self.service.get_task(self.actor, self.task_id)["pending_draft"]["draft_id"],
                         draft["draft_id"])
        self.assertIn("尚未编译", draft["execution_note"])

    def test_feedback_save_replay_conflict_and_one_pending(self):
        draft, _ = self.save()
        replay, replayed = self.save()
        self.assertTrue(replayed)
        self.assertEqual(replay["draft_id"], draft["draft_id"])
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: self.save(text="不同反馈"))
        self.assert_code("PENDING_DRAFT_EXISTS", lambda: self.save("another"))

    def test_confirm_publishes_revision_and_fences_old_generation(self):
        draft, _ = self.save()
        before = self.repo.get(self.actor, self.task_id)
        result, replayed = self.service.confirm_feedback(
            self.actor, self.task_id, "apply", self.confirmation_body(draft))
        after = self.repo.get(self.actor, self.task_id)
        self.assertFalse(replayed)
        self.assertEqual((after.confirmed_revision, after.task_version, after.run_generation),
                         (before.confirmed_revision + 1, before.task_version + 1, before.run_generation + 1))
        self.assertEqual(after.run_status.value, "PLANNING")
        self.assertIsNone(self.service.get_task(self.actor, self.task_id)["pending_draft"])
        self.assertEqual(result["draft"]["confirmed_revision"], 2)
        self.assertEqual([item["revision"] for item in self.service.list_revisions(self.actor, self.task_id)], [1, 2])
        replay, replayed = self.service.confirm_feedback(
            self.actor, self.task_id, "apply", self.confirmation_body(draft))
        self.assertTrue(replayed)
        self.assertEqual(replay, result)

    def test_expansion_feedback_updates_terms_and_target_only_after_confirmation(self):
        plan = preview_edited_groups(None, [{'primary_label': '狗', 'auxiliary_terms': [
            '狗在草地奔跑', '狗接飞盘', '狗在公园散步', '狗与主人玩耍', '狗在户外训练']}])['plan']
        draft, _ = self.save(text='只需要5张图，要五个召回词，主体是狗，不是狗的情绪',
                             grouped_expansion=plan, target_count=5)
        before = self.service.list_revisions(self.actor, self.task_id)[-1]['proposal']['query_spec']
        self.assertNotEqual(before['target_count'], 5)
        self.assertEqual(draft['proposal']['query_spec']['target_count'], 5)
        self.assertEqual(len(draft['proposal']['query_spec']['grouped_expansion']['groups'][0]['auxiliary_terms']), 5)
        result, _ = self.service.confirm_feedback(self.actor, self.task_id, 'apply-expansion',
                                                  self.confirmation_body(draft))
        self.assertEqual(result['task']['task_id'], self.task_id)
        confirmed = self.service.list_revisions(self.actor, self.task_id)[-1]['proposal']['query_spec']
        self.assertEqual(confirmed['target_count'], 5)
        self.assertEqual(confirmed['refill_policy']['target_count'], 5)
        self.assertEqual(confirmed['grouped_expansion']['groups'][0]['primary_label'], '狗')

    def test_expansion_feedback_requires_plan_and_target_together(self):
        self.assert_code('INVALID_SPEC', lambda: self.save(target_count=5))

    def test_reject_does_not_change_task(self):
        draft, _ = self.save()
        before = self.repo.get(self.actor, self.task_id)
        result, _ = self.service.confirm_feedback(
            self.actor, self.task_id, "reject", self.confirmation_body(draft, "reject"))
        after = self.repo.get(self.actor, self.task_id)
        self.assertEqual(after, before)
        self.assertEqual(result["draft"]["status"], "rejected")
        self.assertIsNone(self.service.get_task(self.actor, self.task_id)["pending_draft"])

    def test_stale_digest_scope_and_cross_user_are_rejected(self):
        self.assert_code("FEATURE_NOT_SUPPORTED", lambda: self.save(scope="candidate"))
        draft, _ = self.save()
        self.assert_code("DRAFT_VERSION_CONFLICT", lambda: self.service.confirm_feedback(
            self.actor, self.task_id, "bad", self.confirmation_body(draft, proposal_digest="sha256:" + "0" * 64)))
        self.assert_code("NOT_FOUND", lambda: self.service.get_feedback(
            Actor("tenant-a", "other"), self.task_id, draft["draft_id"]))
        self.assert_code("REVISION_CONFLICT", lambda: self.service.confirm_feedback(
            self.actor, self.task_id, "stale", self.confirmation_body(
                draft, expected_task_version=draft["base_task_version"] + 1)))

    def test_revoked_project_blocks_read_write_and_confirm(self):
        draft, _ = self.save()
        self.service.projects.grants.clear()
        self.assert_code("PROJECT_ACCESS_DENIED", lambda: self.service.get_task(self.actor, self.task_id))
        self.assert_code("PROJECT_ACCESS_DENIED", lambda: self.service.confirm_feedback(
            self.actor, self.task_id, "apply", self.confirmation_body(draft)))

    def test_concurrent_confirm_has_one_effect(self):
        draft, _ = self.save()
        body = self.confirmation_body(draft)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.service.confirm_feedback(
                self.actor, self.task_id, "same", body), range(16)))
        self.assertEqual(sum(not replayed for _, replayed in results), 1)
        self.assertEqual(self.repo.get(self.actor, self.task_id).confirmed_revision, 2)


class SQLiteFeedbackTest(unittest.TestCase):
    def test_restart_recovers_pending_and_confirmed_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/ledger.sqlite3"
            actor = Actor("tenant-a", "user-a")
            service = make_service(SQLiteTaskRepository(path))
            task = service.create_task(actor, "confirm", confirmation(service, actor))[0]
            draft, _ = service.save_feedback(actor, task["task_id"], "feedback", {
                "text": "不要文字", "scope": "all", "expected_revision": 1,
                "expected_task_version": task["task_version"]})
            reopened = make_service(SQLiteTaskRepository(path))
            self.assertEqual(reopened.get_task(actor, task["task_id"])["pending_draft"]["draft_id"], draft["draft_id"])
            result, _ = reopened.confirm_feedback(actor, task["task_id"], "apply", {
                "draft_id": draft["draft_id"], "expected_draft_version": draft["draft_version"],
                "proposal_digest": draft["proposal_digest"],
                "expected_task_version": draft["base_task_version"], "action": "confirm"})
            final = make_service(SQLiteTaskRepository(path))
            self.assertEqual(final.get_task(actor, task["task_id"])["confirmed_revision"], 2)
            self.assertEqual(len(final.list_revisions(actor, task["task_id"])), 2)
            self.assertEqual(result["task"]["run_generation"], 2)


if __name__ == "__main__":
    unittest.main()
