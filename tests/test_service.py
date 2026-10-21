import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor, RunStatus, Task
from dsh_retrieval.service import InMemoryTaskRepository, TaskService
from dsh_retrieval.state_machine import accept_worker_commit, transition
from draft_helpers import confirmation, make_service


class TaskServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = make_service(InMemoryTaskRepository())
        self.actor = Actor("tenant-a", "user-a")
        self.body = confirmation(self.service, self.actor)

    def test_same_idempotency_key_and_body_replays_task(self):
        first, first_replayed = self.service.create_task(self.actor, "key-1", self.body)
        second, second_replayed = self.service.create_task(self.actor, "key-1", self.body)
        self.assertFalse(first_replayed)
        self.assertTrue(second_replayed)
        self.assertEqual(first["task_id"], second["task_id"])

    def test_same_idempotency_key_with_new_body_conflicts(self):
        self.service.create_task(self.actor, "key-1", self.body)
        changed = {**self.body, "proposal_digest": "sha256:" + "0" * 64}
        with self.assertRaises(ServiceError) as raised:
            self.service.create_task(self.actor, "key-1", changed)
        self.assertEqual(raised.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_cross_user_task_is_hidden(self):
        created, _ = self.service.create_task(self.actor, "key-1", self.body)
        with self.assertRaises(ServiceError) as raised:
            self.service.get_task(Actor("tenant-a", "user-b"), created["task_id"])
        self.assertEqual(raised.exception.status, 404)


class StateMachineTest(unittest.TestCase):
    def task(self):
        return Task("rt_1", "tenant", "user", "request", "image", 1, "domain", True, "interactive", "digest")

    def test_pause_increments_generation_and_rejects_late_worker(self):
        task = self.task()
        transition(task, RunStatus.PARSING, expected_task_version=1)
        transition(task, RunStatus.PLANNING, expected_task_version=2)
        transition(task, RunStatus.RUNNING, expected_task_version=3)
        old_generation = task.run_generation
        transition(task, RunStatus.PAUSED, expected_task_version=4)
        self.assertGreater(task.run_generation, old_generation)
        with self.assertRaises(ServiceError) as raised:
            accept_worker_commit(task, old_generation)
        self.assertEqual(raised.exception.code, "STALE_GENERATION")

    def test_stale_task_version_is_rejected(self):
        task = self.task()
        with self.assertRaises(ServiceError) as raised:
            transition(task, RunStatus.PARSING, expected_task_version=99)
        self.assertEqual(raised.exception.code, "REVISION_CONFLICT")


if __name__ == "__main__":
    unittest.main()
