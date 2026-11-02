from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch

from core import test_cleaning_result_storage as result_fixture
from dsh_retrieval.adapters.task_storage.multi_rule import dump_state
from dsh_retrieval.errors import ServiceError


class ExactResultPublicationTest(unittest.TestCase):
    def setUp(self):
        self.f = result_fixture.CleaningResultStorageTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.result = self.f.make_result()
        self.f.repo.publish_result(self.f.f.f.actor, self.f.f.f.tid,
                                   self.f.approval.operation_id, self.result)
        self.repo = self.f.f.reopen()
        self.actor = self.f.f.f.actor
        self.task_id = self.f.f.f.tid
        self.operation_id = self.f.approval.operation_id

    def assert_code(self, code, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_publish_advances_version_and_restart_reads_current_preview(self):
        before = self.f.f.f.tasks.get(self.actor, self.task_id)
        view, replayed = self.repo.publish_preview(self.actor, self.task_id, self.operation_id)
        self.assertFalse(replayed)
        self.assertEqual((view["accepted_count"], view["eligible_unique_count"],
                          view["reserve_count"], view["gap"]), (1, 1, 0, 9))
        self.assertEqual(view["stop_reason"], "INPUT_SCOPE_COMPLETE")
        self.assertFalse(view["refill_dispatched"])
        after = self.f.f.f.tasks.get(self.actor, self.task_id)
        self.assertEqual(after.task_version, before.task_version + 1)
        self.assertEqual(after.stage, "refill")
        self.assertIsNone(after.active_cleaning_operation_id)
        self.assertEqual(self.f.f.reopen().get_current_result_set(self.actor, self.task_id), view)

    def test_concurrent_publish_has_one_result_set_and_event(self):
        repos = [self.f.f.reopen() for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda i: repos[i % 6].publish_preview(
                self.actor, self.task_id, self.operation_id), range(12)))
        self.assertEqual(sum(not replayed for _, replayed in results), 1)
        self.assertEqual(len({view["result_set_id"] for view, _ in results}), 1)
        state = self.f.f.state()
        self.assertEqual(len(state["result_sets"]), 1)
        self.assertEqual(sum(event["kind"] == "preview_result_set_published"
                             for event in state["events"]), 1)

    def test_policy_mismatch_and_stale_generation_do_not_publish(self):
        task = self.f.f.f.tasks.get(self.actor, self.task_id)
        confirmed = json.loads(task.confirmed_proposal_json)
        confirmed["query_spec"]["intra_batch_dedup"] = {"policy_ref": "near", "config_version": "1"}
        self.f.f.f.change_task(confirmed_proposal_json=json.dumps(confirmed, sort_keys=True))
        # A changed confirmed policy no longer matches the approved request
        # digest, so the snapshot fence rejects it before policy dispatch.
        self.assert_code("REVISION_CONFLICT", lambda: self.repo.publish_preview(
            self.actor, self.task_id, self.operation_id))
        self.f.f.f.change_task(confirmed_proposal_json=task.confirmed_proposal_json,
                               run_generation=task.run_generation + 1)
        self.assert_code("REVISION_CONFLICT", lambda: self.repo.publish_preview(
            self.actor, self.task_id, self.operation_id))
        self.assertEqual(self.f.f.state()["result_sets"], {})

    def test_revocation_and_version_change_block_current_read(self):
        view, _ = self.repo.publish_preview(self.actor, self.task_id, self.operation_id)
        self.f.f.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", lambda: self.f.f.reopen().get_current_result_set(
            self.actor, self.task_id))
        self.f.f.f.auth.denied = False
        task = self.f.f.f.tasks.get(self.actor, self.task_id)
        self.f.f.f.change_task(task_version=task.task_version + 1)
        self.assert_code("REVISION_CONFLICT", lambda: self.f.f.reopen().get_current_result_set(
            self.actor, self.task_id))
        self.assertEqual(self.f.f.state()["current_result_sets"][self.task_id], view["result_set_id"])

    def test_serialization_failure_rolls_back_task_pointer_and_event(self):
        before = self.f.f.f.tasks.get(self.actor, self.task_id)
        with patch("dsh_retrieval.adapters.task_storage.multi_rule.dump_state",
                   side_effect=ServiceError("STORAGE_LIMIT", "full", 507)):
            self.assert_code("STORAGE_LIMIT", lambda: self.repo.publish_preview(
                self.actor, self.task_id, self.operation_id))
        self.assertEqual(self.f.f.f.tasks.get(self.actor, self.task_id), before)
        state = self.f.f.state()
        self.assertEqual(state["result_sets"], {})
        self.assertEqual(len(state["events"]), 2)

    def test_tampered_source_result_fails_closed_after_restart(self):
        self.repo.publish_preview(self.actor, self.task_id, self.operation_id)
        state = self.f.f.state()
        state["results"][self.operation_id] = self.f.make_result("fail")
        with closing(sqlite3.connect(self.f.f.f.path)) as db, db:
            db.execute("UPDATE multi_rule_cleaning_ledger SET payload=? WHERE id=1",
                       (dump_state(state),))
        self.assert_code("STORAGE_CORRUPT", lambda: self.f.f.reopen().get_current_result_set(
            self.actor, self.task_id))


if __name__ == "__main__":
    unittest.main()
