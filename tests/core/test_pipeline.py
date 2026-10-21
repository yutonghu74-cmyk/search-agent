"""Service-boundary integration tests. Fixture input and executor are SIMULATED.

No repository-private publication calls: exercise the same confirmation/poll
boundaries that an eventual UI or orchestration adapter must use.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from dsh_retrieval.adapters.cleaning_catalog import LocalCleaningCatalog
from dsh_retrieval.adapters.memory import InMemoryTaskRepository
from dsh_retrieval.cleaning_service import CleaningService
from dsh_retrieval.core.counting import CountingPreviewService
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from draft_helpers import BODY, make_service


class ScriptedExecutor:
    """Only controls worker outcomes, not business acceptance or counts."""
    def __init__(self, failed=False):
        self.jobs = {}
        self.failed = failed
        self.cancelled = set()

    def submit(self, operation):
        self.jobs.setdefault(operation.operation_id, operation)
        return {"dj_run_id": "SIMULATED:" + operation.operation_id}

    def poll(self, operation):
        verdicts = ("pass", "pass", "pass", "fail", "unknown")
        return {"binding": operation.binding(), "status": "failed" if self.failed else "succeeded",
                "error": "SIMULATED_FAILURE" if self.failed else None,
                "results": [{"candidate_id": c.candidate_id, "asset_version": c.asset_version,
                             "verdict": "unknown" if self.failed else verdicts[i],
                             "evidence_ref": None if self.failed else "SIMULATED:observation"}
                            for i, c in enumerate(operation.proposal.candidates)]}

    def cancel(self, operation):
        self.cancelled.add(operation.operation_id)
        return {"status": "cancelled"}


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        media = Path(self.temp.name)
        rows = []
        # First two candidates are byte-identical; third is unique.
        for i, data in enumerate((b"same", b"same", b"unique", b"rejected", b"unknown")):
            (media / f"{i}.png").write_bytes(data)
            rows.append((f"c{i}", "v1", f"{i}.png"))
        self.repo = InMemoryTaskRepository()
        self.tasks = make_service(self.repo)
        self.actor = Actor("tenant-a", "user-a")
        self.executor = ScriptedExecutor()
        self.catalog = LocalCleaningCatalog(media, {("tenant-a", "demo", "fixed-input"): rows})
        self.cleaning = CleaningService(self.repo, self.tasks.projects, self.catalog, self.executor)
        self.counter = CountingPreviewService(self.repo, self.tasks.projects, self.catalog)

    def confirm_task(self):
        body = deepcopy(BODY)
        body["query_spec"]["target_count"] = 3
        body["query_spec"]["refill_policy"]["target_count"] = 3
        draft, _ = self.tasks.save_draft(self.actor, "demo", "draft", body)
        self.assertFalse(self.executor.jobs)
        confirmation = {"draft_id": draft["draft_id"], "expected_draft_version": draft["draft_version"],
                        "proposal_digest": draft["proposal_digest"], "action": "confirm"}
        task, _ = self.tasks.create_task(self.actor, "confirm", confirmation)
        replay, reused = self.tasks.create_task(self.actor, "confirm-again", confirmation)
        self.assertTrue(reused)
        self.assertEqual(replay, task)
        self.assertFalse(self.executor.jobs)
        return task["task_id"]

    def start_cleaning(self, task_id):
        proposal = self.cleaning.prepare(self.actor, task_id, "fixed-input")
        self.assertFalse(self.executor.jobs)
        args = (self.actor, "clean", proposal["proposal_id"], proposal["proposal_digest"],
                proposal["expected_task_version"])
        with self.assertRaises(ServiceError):
            self.cleaning.start(*args, action="auto")
        operation, _ = self.cleaning.start(*args, action="confirm")
        repeat, replayed = self.cleaning.start(*args, action="confirm")
        self.assertTrue(replayed)
        self.assertEqual(repeat, operation)
        self.assertEqual(len(self.executor.jobs), 1)
        return operation["operation_id"]

    def test_confirm_clean_count_chain_with_no_automatic_refill(self):
        task_id = self.confirm_task()
        operation_id = self.start_cleaning(task_id)
        with self.assertRaises(ServiceError):
            self.counter.preview(self.actor, task_id)
        self.cleaning.poll(self.actor, operation_id)
        preview = self.counter.preview(self.actor, task_id)
        self.assertEqual(preview["cleaning_counts"], {"pass": 3, "fail": 1, "unknown": 1})
        self.assertEqual(preview["unique_pass_count"], 2)
        self.assertEqual(preview["duplicate_count"], 1)
        self.assertEqual(preview["preview_gap"], 1)
        self.assertFalse(preview["refill_dispatched"])
        self.assertIsNone(preview["accepted_count"])
        self.assertEqual(self.tasks.get_task(self.actor, task_id)["run_status"], "RUNNING")
        self.cleaning.poll(self.actor, operation_id)
        self.assertEqual(self.counter.preview(self.actor, task_id), preview)

    def test_worker_failure_yields_zero_unique_pass(self):
        self.executor.failed = True
        task_id = self.confirm_task()
        self.cleaning.poll(self.actor, self.start_cleaning(task_id))
        preview = self.counter.preview(self.actor, task_id)
        self.assertEqual(preview["unique_pass_count"], 0)
        self.assertEqual(preview["cleaning_counts"]["unknown"], 5)
        self.assertEqual(preview["preview_gap"], 3)

    def test_cancelled_pipeline_cannot_count(self):
        task_id = self.confirm_task()
        operation_id = self.start_cleaning(task_id)
        task = self.tasks.get_task(self.actor, task_id)
        self.cleaning.cancel(self.actor, operation_id, "cancel", task["task_version"])
        self.assertEqual(self.cleaning.poll(self.actor, operation_id)["status"], "cancelled")
        with self.assertRaises(ServiceError):
            self.counter.preview(self.actor, task_id)
        self.assertEqual(self.tasks.get_task(self.actor, task_id)["run_status"], "PAUSED")

    def test_completed_cleaning_does_not_bypass_revoked_access(self):
        task_id = self.confirm_task()
        self.cleaning.poll(self.actor, self.start_cleaning(task_id))
        self.tasks.projects.grants.clear()
        with self.assertRaises(ServiceError):
            self.counter.preview(self.actor, task_id)
