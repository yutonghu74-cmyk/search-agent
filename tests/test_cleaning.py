from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from dsh_retrieval.adapters.cleaning_catalog import LocalCleaningCatalog
from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.cleaning_service import CleaningService
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from draft_helpers import make_service, confirmation


class FakeExecutor:
    def __init__(self):
        self.jobs = {}
        self.fail_submit = False
        self.fail_cancel = False

    def submit(self, operation):
        self.jobs.setdefault(operation.operation_id, operation)
        if self.fail_submit:
            self.fail_submit = False
            raise ServiceError("TRANSPORT", "retry", 503)
        return {"dj_run_id": operation.operation_id}

    def poll(self, operation):
        return {"binding": operation.binding(), "status": "succeeded", "results": [
            {"candidate_id": c.candidate_id, "asset_version": c.asset_version,
             "verdict": "pass", "evidence_ref": "fixture:audit"} for c in operation.proposal.candidates]}

    def cancel(self, operation):
        if self.fail_cancel:
            self.fail_cancel = False
            raise ServiceError("TRANSPORT", "retry", 503)
        return {"status": "cancelled"}


class CleaningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "image.png"
        self.path.write_bytes(b"fixture bytes; fake executor only")
        self.repo = InMemoryTaskRepository()
        tasks = make_service(self.repo)
        self.actor = Actor("tenant-a", "user-a")
        task, _ = tasks.create_task(self.actor, "confirm", confirmation(tasks, self.actor))
        self.tid = task["task_id"]
        self.executor = FakeExecutor()
        catalog = LocalCleaningCatalog(self.tmp.name, {("tenant-a", "demo", "fixture"): [("c1", "v1", "image.png")]})
        self.service = CleaningService(self.repo, tasks.projects, catalog, self.executor)
        self.proposal = self.service.prepare(self.actor, self.tid, "fixture")

    def start(self, key="start", **changes):
        p = self.proposal
        args = dict(proposal_id=p["proposal_id"], proposal_digest=p["proposal_digest"],
                    expected_task_version=p["expected_task_version"], action="confirm")
        args.update(changes)
        return self.service.start(self.actor, key, **args)

    def operation(self):
        view, _ = self.start()
        return self.repo.get_cleaning_operation(self.actor, view["operation_id"])

    def test_explicit_confirmation_and_digest(self):
        self.assertFalse(self.executor.jobs)
        for changes in ({"action": "auto"}, {"proposal_digest": "wrong"}):
            with self.assertRaises(ServiceError):
                self.start(**changes)
        self.assertFalse(self.executor.jobs)

    def test_same_and_different_keys_execute_once(self):
        first, _ = self.start()
        for key in ("start", "another"):
            view, replay = self.start(key)
            self.assertTrue(replay)
            self.assertEqual(view, first)
        self.assertEqual(len(self.executor.jobs), 1)

    def test_changed_input_rejected(self):
        self.path.write_bytes(b"changed")
        with self.assertRaises(ServiceError) as error:
            self.start()
        self.assertEqual(error.exception.code, "INPUT_SNAPSHOT_CHANGED")
        self.assertFalse(self.executor.jobs)

    def test_transport_retry_reuses_operation(self):
        self.executor.fail_submit = True
        with self.assertRaises(ServiceError):
            self.start()
        view, replay = self.start()
        self.assertTrue(replay)
        self.assertEqual(view["status"], "running")
        self.assertEqual(len(self.executor.jobs), 1)

    def test_results_replay_not_business_completion(self):
        op = self.operation()
        response = self.executor.poll(op)
        self.service.poll(self.actor, op.operation_id)
        self.repo.publish_cleaning(self.actor, op.operation_id, response)
        result = self.service.results(self.actor, self.tid)
        self.assertEqual(result["counts"], {"pass": 1, "fail": 0, "unknown": 0})
        self.assertIsNone(result["accepted_count"])
        self.assertEqual(self.repo.get(self.actor, self.tid).run_status.value, "RUNNING")
        self.assertEqual(len(self.repo._cleaning_events), 1)

    def test_invalid_results_never_publish(self):
        op = self.operation()
        base = self.executor.poll(op)
        cases = []
        for field, value in (("candidate_id", []), ("candidate_id", "foreign"),
                             ("asset_version", "v2"), ("verdict", []), ("evidence_ref", None)):
            response = deepcopy(base)
            response["results"][0][field] = value
            cases.append(response)
        cases += [{**base, "results": []}, {**base, "status": "failed"},
                  {**base, "binding": {**base["binding"], "task_id": "foreign"}}]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(ServiceError):
                self.repo.publish_cleaning(self.actor, op.operation_id, response)
        self.assertEqual(self.service.results(self.actor, self.tid)["items"], [])

    def test_failed_execution_only_unknown(self):
        op = self.operation()
        response = self.executor.poll(op)
        response["status"] = "failed"
        response["results"][0].update(verdict="unknown", evidence_ref=None)
        self.repo.publish_cleaning(self.actor, op.operation_id, response)
        self.assertEqual(self.service.results(self.actor, self.tid)["counts"]["unknown"], 1)

    def test_cancel_fences_before_transport_and_retries_once(self):
        op = self.operation()
        before = self.repo.get(self.actor, self.tid)
        self.executor.fail_cancel = True
        with self.assertRaises(ServiceError):
            self.service.cancel(self.actor, op.operation_id, "cancel", before.task_version)
        with self.assertRaises(ServiceError) as error:
            self.repo.publish_cleaning(self.actor, op.operation_id, self.executor.poll(op))
        self.assertEqual(error.exception.code, "STALE_GENERATION")
        self.service.cancel(self.actor, op.operation_id, "cancel", before.task_version)
        self.assertEqual(self.repo.get(self.actor, self.tid).run_generation, before.run_generation + 1)
        self.assertEqual(len(self.repo._cleaning_events), 1)

    def test_same_generation_old_plan_rejected(self):
        op = self.operation()
        self.repo._tasks[self.tid].active_plan_digest = "new-plan"
        with self.assertRaises(ServiceError) as error:
            self.repo.publish_cleaning(self.actor, op.operation_id, self.executor.poll(op))
        self.assertEqual(error.exception.code, "STALE_GENERATION")

    def test_owner_and_current_grants_required(self):
        with self.assertRaises(ServiceError):
            self.service.prepare(Actor("tenant-a", "other"), self.tid, "fixture")
        op = self.operation()
        self.service.projects.grants.clear()
        for call in (self.start, lambda: self.service.poll(self.actor, op.operation_id),
                     lambda: self.service.results(self.actor, self.tid)):
            with self.assertRaises(ServiceError):
                call()
