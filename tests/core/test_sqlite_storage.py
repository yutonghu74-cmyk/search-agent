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

    def test_task_list_reads_pending_feedback_in_one_ledger_snapshot(self):
        tid = self.create()
        with patch.object(self.repo, '_call', wraps=self.repo._call) as calls:
            rows = self.tasks.list_tasks(self.actor, 'demo')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['task_id'], tid)
        self.assertEqual(calls.call_count, 1)
        with patch.object(self.repo, '_call', wraps=self.repo._call) as calls:
            page = self.tasks.list_tasks_page(self.actor, 'demo', limit=20)
        self.assertEqual(page['items'][0]['task_id'], tid)
        self.assertEqual(calls.call_count, 0)
        with patch.object(self.repo, '_load', side_effect=AssertionError(
                'task page must not deserialize the local ledger')):
            self.assertEqual(self.tasks.list_tasks_page(
                self.actor, 'demo', limit=20), page)
            self.assertEqual(self.tasks.current_read_binding(
                self.actor, tid)[0]['task_id'], tid)

    def test_task_read_summary_migration_and_cascade(self):
        tid = self.create()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('DELETE FROM task_read_summaries')
            db.execute('DELETE FROM task_read_summaries_meta')
        # An unmigrated ledger retains its compatible read path.
        self.assertEqual(self.tasks.list_tasks_page(
            self.actor, 'demo')['items'][0]['task_id'], tid)
        self.assertEqual(self.repo.rebuild_task_read_summaries(), 1)
        reopened = make_service(SQLiteTaskRepository(self.db))
        with patch.object(reopened.repository, '_load', side_effect=AssertionError(
                'migrated task page must use persisted rows')):
            self.assertEqual(reopened.list_tasks_page(
                self.actor, 'demo')['items'][0]['task_id'], tid)
        conversation_id = reopened.get_task(self.actor, tid)['conversation_id']
        from dataclasses import replace
        from cleaning.test_generic_runner import make_job
        from dsh_retrieval.cleaning_usage import CleaningUsageRecorder, observed_http
        from io import BytesIO
        from types import SimpleNamespace
        job = make_job(contents=(b'test',))
        binding = replace(job.binding, task_id=tid)
        recorder = CleaningUsageRecorder(self.db, binding, lambda: None)
        with recorder.step(job.candidates[0], job.recipe.steps[0], supported=False):
            opener = SimpleNamespace(open=lambda *args, **kwargs: BytesIO(b'fixture'))
            with observed_http(opener, None, timeout=1, model_kind='vlm') as response:
                response.read()
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cleaning_usage_http').fetchone(), (1,))
        reopened.hard_delete_conversation(self.actor, conversation_id)
        self.assertEqual(reopened.list_tasks_page(self.actor, 'demo')['items'], [])
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM task_read_summaries').fetchone(), (0,))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cleaning_usage_steps').fetchone(), (0,))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cleaning_usage_http').fetchone(), (0,))

    def test_hard_delete_task_also_purges_usage(self):
        from dataclasses import replace
        from cleaning.test_generic_runner import make_job
        from dsh_retrieval.cleaning_usage import CleaningUsageRecorder
        from dsh_retrieval.models import Task
        legacy = Task('rt_legacy_usage', self.actor.tenant_id, self.actor.user_id,
                      'legacy request', 'image', 1, 'domain', True, 'interactive',
                      'sha256:' + '1' * 64, project_id='demo', dimension_id='default')
        self.repo.create(self.actor, 'legacy-usage', legacy.request_digest, legacy)
        tid = legacy.task_id
        job = make_job(contents=(b'test',))
        recorder = CleaningUsageRecorder(self.db, replace(job.binding, task_id=tid), lambda: None)
        with recorder.step(job.candidates[0], job.recipe.steps[0], supported=False):
            pass
        self.repo.hard_delete_task(self.actor, tid)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cleaning_usage_steps').fetchone()[0], 0)

    def test_task_read_summary_missing_row_fails_closed(self):
        tid = self.create()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('DELETE FROM task_read_summaries WHERE task_id=?', (tid,))
        with self.assertRaises(ServiceError) as failure:
            self.tasks.list_tasks_page(self.actor, 'demo')
        self.assertEqual(failure.exception.code, 'STORAGE_CORRUPT')

    def test_progress_summary_failure_rolls_back_confirmation(self):
        with patch.object(SQLiteTaskRepository, '_task_progress_summary',
                          side_effect=ValueError('invalid progress')):
            with self.assertRaises(ServiceError) as failure:
                self.create()
        self.assertEqual(failure.exception.code, 'STORAGE_CORRUPT')
        self.assertEqual(self.tasks.list_tasks_page(self.actor, 'demo')['items'], [])
        self.assertEqual(self.repo.pending_search_dispatches(), ())

    def test_unprojected_ledger_write_invalidates_task_binding(self):
        from dsh_retrieval.adapters.task_storage.sqlite import dump
        tid = self.create()
        with closing(self.repo._connect()) as db, db:
            memory = self.repo._load(db.execute(
                'SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            memory._tasks[tid].task_version += 1
            expected = memory._tasks[tid].task_version
            db.execute('UPDATE local_ledger SET payload=? WHERE id=1', (dump(memory),))
            self.assertEqual(db.execute('SELECT dirty FROM task_read_summaries_meta '
                'WHERE id=1').fetchone(), (1,))
        self.assertEqual(self.repo.get_task_read_binding(self.actor, tid).task_version, expected)
        self.repo.rebuild_task_read_summaries()
        with patch.object(self.repo, '_load', side_effect=AssertionError('full ledger read')):
            self.assertEqual(self.repo.get_task_read_binding(self.actor, tid).task_version, expected)

    def test_task_read_summary_pages_are_scoped_and_stable(self):
        first_id = self.create()
        for index in range(2):
            body = confirmation(self.tasks, self.actor, key=f'page-draft-{index}')
            self.tasks.create_task(self.actor, f'page-confirm-{index}', body)
        found = []
        cursor = None
        while True:
            page = self.tasks.list_tasks_page(
                self.actor, 'demo', limit=1, cursor=cursor)
            found.extend(item['task_id'] for item in page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        self.assertEqual(len(found), 3)
        self.assertIn(first_id, found)
        self.assertEqual(len(set(found)), 3)
        self.assertEqual(self.tasks.list_tasks_page(
            Actor('tenant-a', 'another-user'), 'demo')['items'], [])

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
        with patch("dsh_retrieval.adapters.task_storage.ledger_rows.prepare_state", side_effect=RuntimeError("injected")):
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
