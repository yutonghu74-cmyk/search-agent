import unittest
from unittest.mock import patch

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

    def test_current_read_binding_uses_confirmed_query_without_revision_scan(self):
        created, _ = self.service.create_task(self.actor, 'read-binding', self.body)
        with patch.object(self.service, 'list_revisions',
                          side_effect=AssertionError('history scan')):
            task, proposal = self.service.current_read_binding(
                self.actor, created['task_id'])
        self.assertEqual(task['confirmed_revision'], created['confirmed_revision'])
        self.assertEqual(proposal['query_spec']['target_count'],
                         self.service.repository.get(self.actor, created['task_id']).target_count)
        with self.assertRaises(ServiceError) as hidden:
            self.service.current_read_binding(
                Actor('tenant-a', 'another-user'), created['task_id'])
        self.assertEqual(hidden.exception.code, 'NOT_FOUND')

    def test_task_list_page_cursor_is_stable_and_scoped(self):
        first, _ = self.service.create_task(self.actor, 'first-task', self.body)
        second_body = confirmation(self.service, self.actor, key='second-page-draft')
        second, _ = self.service.create_task(self.actor, 'second-task', second_body)
        page = self.service.list_tasks_page(self.actor, 'demo', limit=1)
        self.assertEqual(len(page['items']), 1)
        self.assertIsNotNone(page['next_cursor'])
        following = self.service.list_tasks_page(
            self.actor, 'demo', limit=1, cursor=page['next_cursor'])
        self.assertEqual({page['items'][0]['task_id'], following['items'][0]['task_id']},
                         {first['task_id'], second['task_id']})
        with self.assertRaises(ServiceError) as tampered:
            self.service.list_tasks_page(self.actor, 'demo', limit=1,
                cursor=page['next_cursor'][:-2] + 'xx')
        self.assertEqual(tampered.exception.code, 'INVALID_CURSOR')
        with self.assertRaises(ServiceError) as scoped:
            self.service.list_tasks_page(Actor('tenant-a', 'another-user'),
                'demo', limit=1, cursor=page['next_cursor'])
        self.assertEqual(scoped.exception.code, 'INVALID_CURSOR')

    def test_unconfirmed_conversation_can_be_deleted_but_confirmed_one_cannot(self):
        draft = self.service.get_draft(self.actor, 'demo', self.body['draft_id'])
        conversation_id = draft['conversation_id']
        self.assertTrue(self.service.delete_conversation(self.actor, conversation_id)['deleted'])
        with self.assertRaises(ServiceError) as missing:
            self.service.get_conversation(self.actor, conversation_id)
        self.assertEqual(missing.exception.status, 404)

        body = confirmation(self.service, self.actor, key='second-draft')
        confirmed_draft = self.service.get_draft(self.actor, 'demo', body['draft_id'])
        created, _ = self.service.create_task(self.actor, 'second-task', body)
        task_id = created['task_id']
        with self.assertRaises(ServiceError) as protected:
            self.service.delete_conversation(self.actor, confirmed_draft['conversation_id'])
        self.assertEqual(protected.exception.code, 'CONVERSATION_DELETE_FORBIDDEN')

        # The default guard still blocks the confirmed Chat; the explicit force
        # path cascades the linked task tree and its cleaning records together.
        cascade = self.service.hard_delete_conversation(self.actor, confirmed_draft['conversation_id'])
        self.assertTrue(cascade['deleted'])
        self.assertTrue(cascade['force'])
        self.assertIn(task_id, cascade['tasks'])
        with self.assertRaises(ServiceError) as gone:
            self.service.get_task(self.actor, task_id)
        self.assertEqual(gone.exception.status, 404)
        with self.assertRaises(ServiceError) as conv_gone:
            self.service.get_conversation(self.actor, confirmed_draft['conversation_id'])
        self.assertEqual(conv_gone.exception.status, 404)

    def test_hard_delete_task_cascades_legacy_task_and_refuses_conversation_linked(self):
        body = confirmation(self.service, self.actor, key='legacy-draft')
        created, _ = self.service.create_task(self.actor, 'legacy-task', body)
        task_id = created['task_id']
        # A task created through the conversation flow carries a conversation_id,
        # so the task-scoped force path must refuse it (no dangling confirmation).
        with self.assertRaises(ServiceError) as linked:
            self.service.hard_delete_task(self.actor, task_id)
        self.assertEqual(linked.exception.code, 'TASK_HAS_CONVERSATION')
        # The task is still present — the refusal happened before any mutation.
        self.assertEqual(self.service.get_task(self.actor, task_id)['task_id'], task_id)

        # A legacy task with no conversation_id is the intended target.
        from dsh_retrieval.models import Task, TaskLifecycle, RunStatus
        legacy = Task("rt_legacy", self.actor.tenant_id, self.actor.user_id, "legacy request",
                      "image", 1, "domain", True, "interactive", "sha256:" + "1" * 64,
                      project_id="demo", dimension_id="default")
        self.service.repository.create(self.actor, "legacy-key", legacy.request_digest, legacy)
        cascade = self.service.hard_delete_task(self.actor, "rt_legacy")
        self.assertTrue(cascade['deleted'])
        self.assertTrue(cascade['force'])
        self.assertIn("rt_legacy", cascade['tasks'])
        with self.assertRaises(ServiceError) as gone:
            self.service.get_task(self.actor, "rt_legacy")
        self.assertEqual(gone.exception.status, 404)
        # Cross-user hard delete is hidden, mirroring the conversation path.
        with self.assertRaises(ServiceError) as foreign:
            self.service.hard_delete_task(Actor("tenant-a", "user-b"), task_id)
        self.assertEqual(foreign.exception.status, 404)


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
