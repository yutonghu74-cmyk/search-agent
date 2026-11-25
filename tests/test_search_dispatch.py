from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.expansion_models import preview_edited_groups
from dsh_retrieval.models import Actor
from dsh_retrieval.search_dispatch import SearchDispatchCoordinator
from draft_helpers import BODY, confirmation, make_service


class FakeFirstSearch:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = []

    def auto_start(self, actor, task_id, *, key):
        self.calls.append((actor, task_id, key))
        if self.failures:
            self.failures -= 1
            raise ServiceError("SEARCH_UNAVAILABLE", "temporary failure", 503, retryable=True)
        return {"status": "started", "task_id": task_id, "key": key}


class SearchDispatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tasks.sqlite3"
        self.repo = SQLiteTaskRepository(self.path)
        self.service = make_service(self.repo)
        self.actor = Actor("tenant-a", "user-a")

    def create(self):
        body = confirmation(self.service, self.actor)
        return self.service.create_task(self.actor, "confirm", body)[0]["task_id"]

    def test_confirmation_and_dispatch_event_commit_together_and_recover(self):
        task_id = self.create()
        pending = self.repo.pending_search_dispatches()
        self.assertEqual([row["task_id"] for row in pending], [task_id])

        reopened = SQLiteTaskRepository(self.path)
        search = FakeFirstSearch()
        coordinator = SearchDispatchCoordinator(reopened, search, start=False)
        result = coordinator.dispatch_task(task_id)
        self.assertEqual(result["status"], "started")
        self.assertEqual(len(search.calls), 1)

        self.assertEqual(reopened.pending_search_dispatches(), ())

        replay = coordinator.dispatch_task(task_id)
        self.assertEqual(replay, result)
        self.assertEqual(len(search.calls), 1)

    def test_shared_mode_persists_manual_gate_without_automatic_approval(self):
        task_id=self.create();search=FakeFirstSearch()
        dispatcher=SearchDispatchCoordinator(self.repo,search,start=False,respect_manual_retrieval=True)
        self.assertEqual(dispatcher.dispatch_task(task_id)['status'],'manual')
        self.assertEqual(search.calls,[])
        restarted=SearchDispatchCoordinator(SQLiteTaskRepository(self.path),search,start=False,respect_manual_retrieval=True)
        self.assertEqual(restarted.dispatch_task(task_id)['status'],'manual')
        self.assertEqual(search.calls,[])

    def test_transient_failure_stays_pending_and_retries(self):
        task_id = self.create()
        search = FakeFirstSearch(failures=1)
        coordinator = SearchDispatchCoordinator(self.repo, search, start=False)
        self.assertEqual(coordinator.dispatch_task(task_id)["status"], "queued")
        self.assertEqual(len(self.repo.pending_search_dispatches()), 1)
        self.assertEqual(coordinator.dispatch_task(task_id)["status"], "started")
        self.assertEqual(len(search.calls), 2)

    def test_new_term_revision_supersedes_undelivered_old_revision(self):
        task_id = self.create()
        task = self.repo.get(self.actor, task_id)
        groups = preview_edited_groups(
            "动物", [{"primary_label": "狗", "auxiliary_terms": ["金毛"]}])["plan"]
        draft, _ = self.service.save_feedback(self.actor, task_id, "feedback", {
            "text": "狗组改成金毛", "scope": "all",
            "expected_revision": task.confirmed_revision,
            "expected_task_version": task.task_version,
            "grouped_expansion": groups, "target_count": 20,
        })
        self.service.confirm_feedback(self.actor, task_id, "feedback-confirm", {
            "draft_id": draft["draft_id"],
            "expected_draft_version": draft["draft_version"],
            "proposal_digest": draft["proposal_digest"],
            "expected_task_version": task.task_version,
            "action": "confirm",
        })
        self.assertEqual(len(self.repo.pending_search_dispatches()), 2)

        search = FakeFirstSearch()
        coordinator = SearchDispatchCoordinator(self.repo, search, start=False)
        self.assertEqual(coordinator.dispatch_task(task_id)["status"], "started")
        self.assertEqual(len(search.calls), 1)
        self.assertEqual(search.calls[0][2], "terms:" + draft["proposal_digest"])
        self.assertEqual(self.repo.pending_search_dispatches(), ())


if __name__ == "__main__":
    unittest.main()

    def test_one_users_full_queue_does_not_block_other_users_dispatch(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        events = [dict(event_id='e'+user, tenant_id='t', owner_user_id=user, task_id=user,
                       revision=1, generation=1, task_version=1, dispatch_key='k'+user)
                  for user in ('a', 'b')]
        repository = Mock()
        repository.pending_search_dispatches.return_value = events
        repository.get.return_value = SimpleNamespace(confirmed_revision=1, run_generation=1,
            task_version=1, media_type='image')
        search = Mock()
        def start(actor, task_id, **_):
            if task_id == 'a':
                raise ServiceError('SEARCH_USER_QUEUE_FULL', 'full', 503, True)
            return {'status': 'queued', 'task_id': task_id}
        search.auto_start.side_effect = start
        coordinator = SearchDispatchCoordinator(repository, search, start=False)
        coordinator._drain()
        repository.mark_search_dispatched.assert_called_once_with('eb', {'status': 'queued', 'task_id': 'b'})
        self.assertEqual(search.auto_start.call_count, 2)
