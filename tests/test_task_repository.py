"""Contract tests reusable by replacing make_repository in a persistent suite."""

from concurrent.futures import ThreadPoolExecutor
import unittest

from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.api import create_handler
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.service import TaskService
from test_service import BODY


class TaskRepositoryContractTest(unittest.TestCase):
    def make_repository(self):
        return InMemoryTaskRepository()

    def test_concurrent_replay_creates_one_task(self):
        service = TaskService(self.make_repository())
        actor = Actor("tenant", "owner")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _: service.create_task(actor, "same-key", BODY), range(24)
            ))
        self.assertEqual(len({body["task_id"] for body, _ in results}), 1)
        self.assertEqual(sum(not replayed for _, replayed in results), 1)

    def test_returned_tasks_are_detached_and_wrong_identity_cannot_create(self):
        repository = self.make_repository()
        actor = Actor("tenant", "owner")
        summary, _ = TaskService(repository).create_task(actor, "key", BODY)
        task = repository.get(actor, summary["task_id"])
        task.target_count = 999
        self.assertEqual(repository.get(actor, task.task_id).target_count, BODY["target_count"])
        with self.assertRaises(ServiceError) as raised:
            repository.create(Actor("another-tenant", "owner"), "key", task.request_digest, task)
        self.assertEqual(raised.exception.code, "ACCESS_DENIED")

    def test_idempotency_is_scoped_to_tenant_and_user(self):
        service = TaskService(self.make_repository())
        first, _ = service.create_task(Actor("a", "user"), "key", BODY)
        second, replayed = service.create_task(Actor("b", "user"), "key", BODY)
        self.assertFalse(replayed)
        self.assertNotEqual(first["task_id"], second["task_id"])
        with self.assertRaises(ServiceError) as raised:
            service.get_task(Actor("b", "user"), first["task_id"])
        self.assertEqual(raised.exception.status, 404)

    def test_http_handlers_use_injected_repository_without_shared_global(self):
        first = create_handler(TaskService(self.make_repository()))
        second = create_handler(TaskService(self.make_repository()))
        actor = Actor("tenant", "owner")
        created, _ = first.service.create_task(actor, "key", BODY)
        self.assertEqual(first.service.get_task(actor, created["task_id"])["task_id"], created["task_id"])
        with self.assertRaises(ServiceError) as raised:
            second.service.get_task(actor, created["task_id"])
        self.assertEqual(raised.exception.status, 404)
