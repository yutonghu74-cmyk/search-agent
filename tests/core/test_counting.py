from copy import deepcopy
import unittest

from dsh_retrieval.core.counting import CountingPreviewService
from dsh_retrieval.errors import ServiceError
import test_cleaning
from draft_helpers import BODY, confirmation, make_service


class CountingTest(unittest.TestCase):
    setUp = test_cleaning.CleaningTest.setUp
    start = test_cleaning.CleaningTest.start
    operation = test_cleaning.CleaningTest.operation

    def prepare_results(self, verdicts=("pass", "pass", "fail", "unknown")):
        # Two equal eligible files, plus failed/unknown copies of the same bytes.
        self.service.catalog.manifests[("tenant-a", "demo", "fixture")] = tuple(
            (f"c{i}", "v1", "image.png") for i in range(len(verdicts)))
        self.proposal = self.service.prepare(self.actor, self.tid, "fixture")
        op = self.operation()
        response = self.executor.poll(op)
        for row, verdict in zip(response["results"], verdicts):
            row["verdict"] = verdict
        self.repo.publish_cleaning(self.actor, op.operation_id, response)
        return CountingPreviewService(self.repo, self.service.projects, self.service.catalog)

    def test_duplicates_fail_unknown_and_no_state_write(self):
        counter = self.prepare_results()
        before = self.repo.get(self.actor, self.tid)
        result = counter.preview(self.actor, self.tid)
        self.assertEqual(result["cleaning_counts"], {"pass": 2, "fail": 1, "unknown": 1})
        self.assertEqual(result["unique_pass_count"], 1)
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(result["preview_gap"], 9)
        self.assertEqual(result["representatives"], [{"candidate_id": "c0", "asset_version": "v1"}])
        self.assertIsNone(result["accepted_count"])
        self.assertEqual(counter.preview(self.actor, self.tid), result)
        self.assertEqual(self.repo.get(self.actor, self.tid), before)

    def test_failed_copy_cannot_be_representative(self):
        counter = self.prepare_results(("fail", "pass"))
        result = counter.preview(self.actor, self.tid)
        self.assertEqual(result["representatives"][0]["candidate_id"], "c1")
        self.assertEqual(result["duplicate_count"], 0)

    def test_no_cleaning_no_preview(self):
        counter = CountingPreviewService(self.repo, self.service.projects, self.service.catalog)
        with self.assertRaises(ServiceError):
            counter.preview(self.actor, self.tid)

    def test_changed_bytes_rejected(self):
        counter = self.prepare_results()
        self.path.write_bytes(b"changed")
        with self.assertRaises(ServiceError) as error:
            counter.preview(self.actor, self.tid)
        self.assertEqual(error.exception.code, "INPUT_SNAPSHOT_CHANGED")

    def test_revoked_access_rejected(self):
        counter = self.prepare_results()
        self.service.projects.grants.clear()
        with self.assertRaises(ServiceError):
            counter.preview(self.actor, self.tid)

    def test_old_generation_rejected(self):
        counter = self.prepare_results()
        self.repo._tasks[self.tid].run_generation += 1
        with self.assertRaises(ServiceError):
            counter.preview(self.actor, self.tid)

    def test_version_race_rejected(self):
        counter = self.prepare_results()
        resolve = counter.catalog.resolve
        def racing(*args):
            self.repo._tasks[self.tid].task_version += 1
            return resolve(*args)
        counter.catalog.resolve = racing
        with self.assertRaises(ServiceError) as error:
            counter.preview(self.actor, self.tid)
        self.assertEqual(error.exception.code, "REVISION_CONFLICT")

    def test_target_caps_representatives(self):
        tasks = make_service(self.repo)
        body = deepcopy(BODY)
        body["query_spec"]["target_count"] = 1
        body["query_spec"]["refill_policy"]["target_count"] = 1
        task, _ = tasks.create_task(self.actor, "target-one", confirmation(tasks, self.actor, "draft-one", body))
        self.tid = task["task_id"]
        (self.path.parent / "different.png").write_bytes(b"different fixture")
        self.service.catalog.manifests[("tenant-a", "demo", "fixture")] = (
            ("c1", "v1", "image.png"), ("c0", "v1", "different.png"))
        self.proposal = self.service.prepare(self.actor, self.tid, "fixture")
        op = self.operation()
        self.repo.publish_cleaning(self.actor, op.operation_id, self.executor.poll(op))
        counter = CountingPreviewService(self.repo, self.service.projects, self.service.catalog)
        result = counter.preview(self.actor, self.tid)
        self.assertEqual(result["unique_pass_count"], 2)
        self.assertEqual(len(result["representatives"]), 1)
        self.assertEqual(result["representatives"][0]["candidate_id"], "c0")
        self.assertEqual(result["additional_unique_count"], 1)
        self.assertEqual(result["preview_gap"], 0)
