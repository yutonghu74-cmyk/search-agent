from concurrent.futures import ThreadPoolExecutor
import unittest

from dsh_retrieval.candidate_intake import CandidateIntake
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from draft_helpers import confirmation, make_service
import test_cleaning


class IntakeTest(unittest.TestCase):
    def setUp(self):
        test_cleaning.CleaningTest.setUp(self)
        self.intake = CandidateIntake(self.repo, self.service.projects, self.service.catalog)
        self.view = self.intake.preview(self.actor, self.tid, "fixture")

    def confirm(self, key="select", **kwargs):
        args = {"proposal_digest": self.view["proposal_digest"],
                "expected_task_version": self.view["expected_task_version"], "action": "confirm"}
        args.update(kwargs)
        return self.intake.confirm(self.actor, self.tid, "fixture", key=key, **args)

    def test_explicit_approval_and_concurrent_replay(self):
        with self.assertRaises(ServiceError):
            self.confirm(action="auto")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda i: self.confirm(str(i)), range(12)))
        self.assertEqual(len({r[0]["manifest_ref"] for r in results}), 1)
        self.assertEqual(sum(not r[1] for r in results), 1)
        self.assertNotIn("source_path", str(results))
        self.assertFalse(self.executor.jobs)

    def test_changed_input_and_stale_version(self):
        with self.assertRaises(ServiceError):
            self.confirm(expected_task_version=self.view["expected_task_version"] + 1)
        self.path.write_bytes(b"changed")
        with self.assertRaises(ServiceError):
            self.confirm()

    def test_scoped_manifest_into_cleaning(self):
        result, _ = self.confirm()
        self.service.catalog = self.intake
        p = self.service.prepare(self.actor, self.tid, result["manifest_ref"])
        op, _ = self.service.start(self.actor, "clean", p["proposal_id"], p["proposal_digest"],
                                   p["expected_task_version"], action="confirm")
        self.service.poll(self.actor, op["operation_id"])
        replay, reused = self.confirm()
        self.assertTrue(reused)
        self.assertEqual(replay, result)

    def test_manifest_cannot_move_to_another_task(self):
        result, _ = self.confirm()
        tasks = make_service(self.repo)
        other, _ = tasks.create_task(self.actor, "other-task", confirmation(tasks, self.actor, "other-draft"))
        self.service.catalog = self.intake
        with self.assertRaises(ServiceError) as error:
            self.service.prepare(self.actor, other["task_id"], result["manifest_ref"])
        self.assertEqual(error.exception.code, "MANIFEST_SCOPE_MISMATCH")

    def test_old_generation_and_changed_content_not_resolved(self):
        result, _ = self.confirm()
        self.service.catalog = self.intake
        self.repo._tasks[self.tid].run_generation += 1
        with self.assertRaises(ServiceError):
            self.service.prepare(self.actor, self.tid, result["manifest_ref"])
        self.path.write_bytes(b"changed")
        with self.assertRaises(ServiceError):
            self.intake.resolve("tenant-a", "demo", result["manifest_ref"], "image-width@1")

    def test_identity_and_revocation(self):
        with self.assertRaises(ServiceError):
            self.intake.preview(Actor("tenant-a", "other"), self.tid, "fixture")
        self.confirm()
        self.service.projects.grants.clear()
        with self.assertRaises(ServiceError):
            self.confirm()

    def test_unknown_source_and_wrong_tenant(self):
        with self.assertRaises(ServiceError):
            self.intake.preview(self.actor, self.tid, "unknown")
        result, _ = self.confirm()
        with self.assertRaises(ServiceError):
            self.intake.resolve("other", "demo", result["manifest_ref"], "image-width@1")
