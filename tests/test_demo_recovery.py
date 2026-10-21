import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dsh_retrieval.demo import run_session, resume_session
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from test_demo import TrackingExecutor


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.root = Path(__file__).resolve().parents[1]

    def initial(self, executor=None, **kwargs):
        return run_session(self.root, self.workspace, executor or TrackingExecutor(), "simulated",
                           lambda _: True, lambda *args: None, interval=0, **kwargs)

    def resume(self, executor=None, mode="simulated"):
        return resume_session(self.workspace, executor or TrackingExecutor(), mode,
                              lambda _: True, lambda *args: None, interval=0)

    def test_completed_session_new_process_does_not_change_operation(self):
        before = self.initial()
        child = subprocess.run([sys.executable, "-m", "dsh_retrieval.demo", "--executor", "simulated",
                                "--resume", str(self.workspace)], input="y\n", text=True,
                               capture_output=True, check=True)
        after = json.loads((self.workspace / "demo-report.json").read_text())
        self.assertTrue(after["resumed"])
        self.assertEqual(after["operation"], before["operation"])
        self.assertEqual(after["preview"], before["preview"])
        self.assertIn("恢复结果", child.stdout)

    def test_crash_after_reservation_reuses_operation(self):
        executor = TrackingExecutor()
        def crash(operation):
            raise SystemExit(99)  # simulates process loss, not an explicit cancel
        executor.submit = crash
        with self.assertRaises(SystemExit):
            self.initial(executor)
        checkpoint = json.loads((self.workspace / "session.json").read_text())
        repo = SQLiteTaskRepository(self.workspace / "ledger.sqlite3")
        oid = repo.get(Actor("tenant-a", "user-a"), checkpoint["task_id"]).active_cleaning_operation_id
        after = self.resume()
        self.assertEqual(after["operation"]["operation_id"], oid)
        self.assertEqual(after["status"], "preview_ready")

    def test_changed_source_rejected_without_reexecution(self):
        self.initial()
        (self.workspace / "media/c1.png").write_bytes(b"changed")
        executor = TrackingExecutor()
        with self.assertRaises(ServiceError) as error:
            self.resume(executor)
        self.assertEqual(error.exception.code, "INPUT_SNAPSHOT_CHANGED")
        self.assertEqual(executor.submits, 0)

    def test_wrong_mode_and_preapproval_sessions_not_resumed(self):
        with self.assertRaises(OSError):
            self.resume()
        self.initial()
        with self.assertRaises(ServiceError) as error:
            self.resume(mode="real")
        self.assertEqual(error.exception.code, "RECOVERY_NOT_AVAILABLE")

    def test_explicitly_cancelled_task_cannot_resume(self):
        result = self.initial(timeout=0)
        self.assertEqual(result["task_status"], "PAUSED")
        executor = TrackingExecutor()
        with self.assertRaises(ServiceError):
            self.resume(executor)
        self.assertEqual(executor.submits, 0)
