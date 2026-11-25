from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import unittest
from unittest.mock import patch

from core import test_multi_rule_approval as approval_fixture
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.multi_rule_contracts import (
    CleaningCandidateVerdict, CleaningJobResult, CleaningRuleVerdict,
)


class CleaningResultStorageTest(unittest.TestCase):
    def setUp(self):
        self.f = approval_fixture.MultiRuleApprovalTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.proposal = self.f.prepare()
        self.approval, _ = self.f.confirm()
        self.repo = self.f.reopen()

    def make_result(self, first="pass"):
        rules = []
        for index, rule in enumerate(self.approval.job.recipe.rules):
            verdict = first if index == 0 else "pass"
            rules.append(CleaningRuleVerdict(
                rule.rule_id, verdict,
                "MATCH" if verdict == "pass" else "MISMATCH" if verdict == "fail" else "FACT_UNAVAILABLE",
                None if verdict == "unknown" else f"evidence.{index}"))
        overall = "fail" if any(rule.verdict == "fail" for rule in rules) else (
            "unknown" if any(rule.verdict == "unknown" for rule in rules) else "pass")
        candidate = self.approval.job.candidates[0]
        return CleaningJobResult(self.approval.job.binding, (
            CleaningCandidateVerdict(candidate.candidate_id, candidate.asset_version,
                                     overall, tuple(rules)),))

    def assert_code(self, code, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_publish_is_atomic_restartable_and_does_not_count(self):
        result = self.make_result()
        task_before = self.f.f.tasks.get(self.f.f.actor, self.f.f.tid)
        published, replayed = self.repo.publish_result(
            self.f.f.actor, self.f.f.tid, self.approval.operation_id, result)
        self.assertEqual((published, replayed), (result, False))
        self.assertEqual(self.f.f.tasks.get(self.f.f.actor, self.f.f.tid), task_before)
        reopened = self.f.reopen()
        self.assertEqual(reopened.get_result(
            self.f.f.actor, self.f.f.tid, self.approval.operation_id), result)
        self.assertEqual(reopened.publish_result(
            self.f.f.actor, self.f.f.tid, self.approval.operation_id, result), (result, True))
        state = self.f.state()
        self.assertEqual(state["results"], {self.approval.operation_id: result})
        self.assertEqual(len(state["events"]), 2)
        self.assertNotIn("accepted_count", str(state))

    def test_concurrent_publish_has_one_effect(self):
        result = self.make_result()
        repos = [self.f.reopen() for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            outputs = list(pool.map(lambda i: repos[i % 6].publish_result(
                self.f.f.actor, self.f.f.tid, self.approval.operation_id, result), range(12)))
        self.assertEqual(sum(not replayed for _, replayed in outputs), 1)
        self.assertEqual(len(self.f.state()["events"]), 2)

    def test_changed_or_incomplete_result_is_rejected(self):
        result = self.make_result()
        changed = self.make_result("fail")
        self.repo.publish_result(self.f.f.actor, self.f.f.tid, self.approval.operation_id, result)
        self.assert_code("RESULT_CONFLICT", lambda: self.repo.publish_result(
            self.f.f.actor, self.f.f.tid, self.approval.operation_id, changed))
        incomplete = replace(result, candidates=(replace(result.candidates[0],
                                                          candidate_id="another.candidate"),))
        self.assert_code("RESULT_INVALID", lambda: self.f.reopen().publish_result(
            self.f.f.actor, self.f.f.tid, self.approval.operation_id, incomplete))

    def test_stale_generation_wrong_owner_and_revocation_fail_closed(self):
        result = self.make_result()
        self.assert_code("NOT_FOUND", lambda: self.repo.publish_result(
            Actor("tenant", "other"), self.f.f.tid, self.approval.operation_id, result))
        self.f.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", lambda: self.repo.publish_result(
            self.f.f.actor, self.f.f.tid, self.approval.operation_id, result))
        self.f.f.auth.denied = False
        self.f.f.change_task(run_generation=self.approval.proposal.generation + 1)
        self.assert_code("REVISION_CONFLICT", lambda: self.repo.publish_result(
            self.f.f.actor, self.f.f.tid, self.approval.operation_id, result))
        self.assertEqual(self.f.state()["results"], {})

    def test_published_result_remains_fenced_by_version_and_access(self):
        result = self.make_result()
        self.repo.publish_result(self.f.f.actor, self.f.f.tid, self.approval.operation_id, result)
        self.f.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", lambda: self.f.reopen().get_result(
            self.f.f.actor, self.f.f.tid, self.approval.operation_id))
        self.f.f.auth.denied = False
        self.f.f.change_task(task_version=self.approval.task_version + 1)
        self.assert_code("STALE_GENERATION", lambda: self.f.reopen().get_result(
            self.f.f.actor, self.f.f.tid, self.approval.operation_id))
        self.assert_code("STALE_GENERATION", lambda: self.f.reopen().publish_result(
            self.f.f.actor, self.f.f.tid, self.approval.operation_id, result))
        self.assertEqual(self.f.state()["results"], {self.approval.operation_id: result})

    def test_serialization_failure_rolls_back_result_and_event(self):
        result = self.make_result()
        with patch("dsh_retrieval.adapters.task_storage.ledger_rows.prepare_state",
                   side_effect=ServiceError("STORAGE_LIMIT", "full", 507)):
            self.assert_code("STORAGE_LIMIT", lambda: self.repo.publish_result(
                self.f.f.actor, self.f.f.tid, self.approval.operation_id, result))
        state = self.f.state()
        self.assertEqual(state["results"], {})
        self.assertEqual(len(state["events"]), 1)


if __name__ == "__main__":
    unittest.main()
