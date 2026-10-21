import json
from pathlib import Path
import tempfile
import unittest

from dsh_retrieval.demo import run_session, SimulatedExecutor
from dsh_retrieval.errors import ServiceError


class TrackingExecutor(SimulatedExecutor):
    def __init__(self):
        self.submits = 0
        self.cancels = 0

    def submit(self, operation):
        self.submits += 1
        return super().submit(operation)

    def cancel(self, operation):
        self.cancels += 1
        return super().cancel(operation)


class DemoTest(unittest.TestCase):
    def run_demo(self, executor, approve=lambda _: True, **kwargs):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            report = run_session(Path(__file__).resolve().parents[1], workspace, executor,
                                 "simulated", approve, lambda *args: None, interval=0, **kwargs)
            self.assertEqual(json.loads((workspace / "demo-report.json").read_text()), report)
            return report

    def test_all_three_approval_stops_never_dispatch(self):
        for index, stage in enumerate(("draft", "candidates", "cleaning")):
            with self.subTest(stage=stage):
                answers = iter([True] * index + [False])
                executor = TrackingExecutor()
                result = self.run_demo(executor, lambda _: next(answers))
                self.assertEqual(result["status"], "declined")
                self.assertEqual(result["stopped_at"], stage)
                self.assertEqual(executor.submits, 0)
                if index == 0:
                    self.assertNotIn("task_status", result)
                else:
                    self.assertEqual(result["task_status"], "PLANNING")

    def test_approved_simulation_is_explicit_and_not_completed(self):
        executor = TrackingExecutor()
        result = self.run_demo(executor)
        self.assertEqual(executor.submits, 1)
        self.assertEqual(result["status"], "preview_ready")
        self.assertEqual(result["execution_mode"], "simulated")
        self.assertEqual(result["preview"]["unique_pass_count"], 2)
        self.assertEqual(result["preview"]["duplicate_count"], 1)
        self.assertEqual(result["preview"]["preview_gap"], 1)
        self.assertIsNone(result["accepted_count"])
        self.assertEqual(result["task_status"], "RUNNING")

    def test_timeout_cancels_and_fences(self):
        executor = TrackingExecutor()
        result = self.run_demo(executor, timeout=0)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(executor.cancels, 1)
        self.assertEqual(result["task_status"], "PAUSED")

    def test_interrupt_during_poll_cancels(self):
        executor = TrackingExecutor()
        def interrupt(operation):
            raise KeyboardInterrupt()
        executor.poll = interrupt
        result = self.run_demo(executor)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["task_status"], "PAUSED")
        self.assertEqual(executor.cancels, 1)

    def test_submit_transport_error_fences_reserved_operation(self):
        executor = TrackingExecutor()
        def unavailable(operation):
            raise ServiceError("TRANSPORT", "unavailable", 503)
        executor.submit = unavailable
        result = self.run_demo(executor)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "TRANSPORT")
        self.assertEqual(result["task_status"], "PAUSED")
        self.assertEqual(executor.cancels, 1)

    def test_failed_job_remains_unknown_not_success(self):
        executor = TrackingExecutor()
        def failed(operation):
            result = SimulatedExecutor.poll(executor, operation)
            result["status"] = "failed"
            result["error"] = "SIMULATED_FAILURE"
            for row in result["results"]:
                row.update(verdict="unknown", evidence_ref=None)
            return result
        executor.poll = failed
        result = self.run_demo(executor)
        self.assertEqual(result["status"], "cleaning_failed")
        self.assertEqual(result["preview"]["unique_pass_count"], 0)
        self.assertEqual(result["preview"]["cleaning_counts"]["unknown"], 4)
