from datetime import UTC, datetime, timedelta
import tempfile
import unittest

from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.models import Actor
from dsh_retrieval.service import InMemoryTaskRepository
from draft_helpers import BODY, confirmation, make_service


class ChatHistoryTest(unittest.TestCase):
    def setUp(self):
        self.actor = Actor("tenant-a", "user-a")
        self.repo = InMemoryTaskRepository()
        self.service = make_service(self.repo)

    def create(self, suffix):
        body = {**BODY, "query_spec": {**BODY["query_spec"], "raw_request": f"需求 {suffix}"}}
        confirmed = confirmation(self.service, self.actor, key="draft-" + suffix, body=body)
        return self.service.create_task(self.actor, "task-" + suffix, confirmed)[0]

    def test_lists_owned_project_tasks_newest_first_with_pending(self):
        first = self.create("一")
        second = self.create("二")
        self.repo._tasks[first["task_id"]].created_at = datetime.now(UTC) - timedelta(days=1)
        task = self.service.get_task(self.actor, second["task_id"])
        feedback, _ = self.service.save_feedback(self.actor, second["task_id"], "feedback", {
            "text": "追加条件", "scope": "all", "expected_revision": task["confirmed_revision"],
            "expected_task_version": task["task_version"]})
        items = self.service.list_tasks(self.actor, "demo")
        self.assertEqual([item["task_id"] for item in items], [second["task_id"], first["task_id"]])
        self.assertEqual(items[0]["raw_request"], "需求 二")
        self.assertEqual(items[0]["pending_draft"]["draft_id"], feedback["draft_id"])
        self.assertIn("T", items[0]["created_at"])

    def test_cross_identity_project_and_revoked_access_do_not_leak(self):
        task = self.create("隔离")
        self.repo._tasks[task["task_id"]].project_id = "other-project"
        self.assertEqual(self.repo.list_tasks(self.actor, "demo"), ())
        self.assertEqual(self.repo.list_tasks(Actor("tenant-a", "other"), "other-project"), ())
        self.service.projects.grants.clear()
        self.assertEqual(self.service.list_tasks(self.actor, "other-project"), ())

    def test_list_is_bounded_and_does_not_mutate_storage(self):
        for index in range(105):
            self.create(str(index))
        before = tuple((key, value.task_version) for key, value in self.repo._tasks.items())
        items = self.service.list_tasks(self.actor, "demo")
        after = tuple((key, value.task_version) for key, value in self.repo._tasks.items())
        self.assertEqual(len(items), 100)
        self.assertEqual(after, before)

    def test_sqlite_restart_recovers_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/ledger.sqlite3"
            service = make_service(SQLiteTaskRepository(path))
            task = service.create_task(self.actor, "task", confirmation(service, self.actor))[0]
            reopened = make_service(SQLiteTaskRepository(path))
            items = reopened.list_tasks(self.actor, "demo")
            self.assertEqual((len(items), items[0]["task_id"]), (1, task["task_id"]))


if __name__ == "__main__":
    unittest.main()
