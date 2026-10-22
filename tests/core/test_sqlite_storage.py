from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository, decode, encode
from dsh_retrieval.adapters.cleaning_catalog import LocalCleaningCatalog
from dsh_retrieval.cleaning_service import CleaningService
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from draft_helpers import make_service, confirmation, BODY
from test_cleaning import FakeExecutor


class SQLiteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "ledger.sqlite3"
        self.repo = SQLiteTaskRepository(self.db)
        self.actor = Actor("tenant-a", "user-a")
        self.tasks = make_service(self.repo)
        self.body = confirmation(self.tasks, self.actor)

    def create(self):
        return self.tasks.create_task(self.actor, "confirm", self.body)[0]["task_id"]

    def cleaning(self):
        tid = self.create()
        (self.root / "image.png").write_bytes(b"fixture")
        catalog = LocalCleaningCatalog(self.root, {("tenant-a", "demo", "fixture"): [("c1", "v1", "image.png")]})
        service = CleaningService(self.repo, self.tasks.projects, catalog, FakeExecutor())
        p = service.prepare(self.actor, tid, "fixture")
        op, _ = service.start(self.actor, "start", p["proposal_id"], p["proposal_digest"],
                              p["expected_task_version"], action="confirm")
        return tid, service, op["operation_id"]

    def test_restart_replays_draft_and_confirm(self):
        tid = self.create()
        new = make_service(SQLiteTaskRepository(self.db))
        draft, replay = new.save_draft(self.actor, "demo", "draft", BODY)
        self.assertTrue(replay)
        self.assertEqual(draft["draft_id"], self.body["draft_id"])
        task, replay = new.create_task(self.actor, "confirm", self.body)
        self.assertTrue(replay)
        self.assertEqual(task["task_id"], tid)

    def test_multiple_connections_confirm_once(self):
        def confirm(i):
            return make_service(SQLiteTaskRepository(self.db)).create_task(self.actor, str(i), self.body)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(confirm, range(18)))
        self.assertEqual(len({r[0]["task_id"] for r in results}), 1)
        self.assertEqual(sum(not r[1] for r in results), 1)

    def test_new_process_recovers_result_and_binding(self):
        tid, service, oid = self.cleaning()
        service.poll(self.actor, oid)
        code = """import json,sys
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.models import Actor
r=SQLiteTaskRepository(sys.argv[1]); a=Actor('tenant-a','user-a')
print(json.dumps({'task':r.get(a,sys.argv[2]).to_summary(),'op':r.cleaning_results(a,sys.argv[2]).view()}))
"""
        child = subprocess.run([sys.executable, "-c", code, str(self.db), tid], text=True, capture_output=True, check=True)
        result = json.loads(child.stdout)
        self.assertEqual(result["task"]["run_status"], "RUNNING")
        self.assertEqual(result["op"]["operation_id"], oid)
        self.assertEqual(result["op"]["results"]["results"][0]["verdict"], "pass")

    def test_cancellation_survives_restart_and_fences_late_result(self):
        tid, service, oid = self.cleaning()
        old = self.repo.get_cleaning_operation(self.actor, oid)
        version = self.repo.get(self.actor, tid).task_version
        service.cancel(self.actor, oid, "cancel", version)
        new = SQLiteTaskRepository(self.db)
        with self.assertRaises(ServiceError) as error:
            new.publish_cleaning(self.actor, oid, service.executor.poll(old))
        self.assertEqual(error.exception.code, "STALE_GENERATION")
        new.cancel_cleaning(self.actor, oid, "cancel", version)
        self.assertEqual(new.get(self.actor, tid).run_generation, old.proposal.generation + 1)

    def test_failed_serialization_rolls_back_confirmation(self):
        with patch("dsh_retrieval.adapters.task_storage.sqlite.dump", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                self.create()
        new = SQLiteTaskRepository(self.db)
        self.assertIsNone(new.get_draft(self.actor, self.body["draft_id"]).confirmed_task_id)
        _, replay = make_service(new).create_task(self.actor, "confirm", self.body)
        self.assertFalse(replay)

    def test_unknown_format_and_corruption_fail_closed(self):
        for statement, code in (("UPDATE local_ledger SET format=999", "STORAGE_FORMAT_UNSUPPORTED"),
                                ("UPDATE local_ledger SET format=1,payload='broken'", "STORAGE_CORRUPT")):
            with closing(sqlite3.connect(self.db)) as db, db:
                db.execute(statement)
            with self.assertRaises(ServiceError) as error:
                SQLiteTaskRepository(self.db)
            self.assertEqual(error.exception.code, code)

    def test_identity_isolation_after_restart(self):
        tid = self.create()
        new = SQLiteTaskRepository(self.db)
        with self.assertRaises(ServiceError):
            new.get(Actor("tenant-a", "other"), tid)

    def test_pre_feedback_snapshot_adds_empty_feedback_state(self):
        tid = self.create()
        with closing(sqlite3.connect(self.db)) as db, db:
            payload = db.execute("SELECT payload FROM local_ledger WHERE id=1").fetchone()[0]
            state = decode(json.loads(payload))
            for key in ("_feedback_drafts", "_feedback_requests", "_revisions"):
                state.pop(key)
            db.execute("UPDATE local_ledger SET payload=? WHERE id=1",
                       (json.dumps(encode(state), ensure_ascii=False, separators=(",", ":")),))
        reopened = SQLiteTaskRepository(self.db)
        self.assertEqual(reopened.get(self.actor, tid).task_id, tid)
        self.assertEqual(tuple(item["revision"] for item in reopened.list_revisions(self.actor, tid)), (1,))

    def test_incomplete_cleaning_reservation_recovers_same_operation(self):
        tid, service, oid = self.cleaning()
        original = self.repo.get_cleaning_operation(self.actor, oid)
        p = original.proposal
        new = SQLiteTaskRepository(self.db)
        replay, reused = new.reserve_cleaning(self.actor, "start", p.proposal_id, p.plan_digest, p.task_version)
        self.assertTrue(reused)
        self.assertEqual(replay, original)
