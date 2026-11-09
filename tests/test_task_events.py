import tempfile
import unittest
from contextlib import closing
from pathlib import Path
import sqlite3

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.task_events import TaskEventFeed
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository


ACTOR = Actor("tenant-a", "user-a")


def task(**changes):
    return {"task_id": "task-1", "confirmed_revision": 1, "run_generation": 1,
            "task_version": 2, "run_status": "RUNNING", "stage": "RETRIEVING"} | changes


def progress(status="running", gate="blocked", accepted=None):
    return {"nodes": [{"node": "retrieval", "label": "检索", "mode": "manual",
                        "node_status": status, "gate_status": gate,
                        "detail": "当前权威运行阶段"}],
            "counts": {"target": 10, "accepted": accepted}}


class TaskEventFeedTest(unittest.TestCase):
    def test_sse_reconnect_replays_without_rebuilding_snapshot(self):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from urllib.request import Request, urlopen
        from unittest.mock import patch
        from draft_helpers import make_service, confirmation
        from dsh_retrieval.api import create_handler
        with tempfile.TemporaryDirectory() as directory:
            service = make_service(SQLiteTaskRepository(Path(directory) / 'tasks.sqlite'))
            created = service.create_task(ACTOR, 'confirm', confirmation(service, ACTOR))[0]
            handler = create_handler(service)
            handler.event_stream_duration = 0.01
            handler.event_stream_poll_interval = 0.005
            with ThreadingHTTPServer(('127.0.0.1', 0), handler) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f'http://127.0.0.1:{server.server_port}/v1/retrieval/tasks/{created["task_id"]}'
                headers = {'X-Tenant-Id': ACTOR.tenant_id, 'X-User-Id': ACTOR.user_id}
                try:
                    with patch.object(handler, '_task_snapshot',
                                      side_effect=AssertionError('warm SSE rebuilt snapshot')):
                        with urlopen(Request(base + '/events/stream', headers=headers)) as response:
                            self.assertIn(b'event: task-event', response.read())
                        with urlopen(Request(base + '/events?compact=true', headers=headers)) as response:
                            import json
                            compact = json.loads(response.read())
                            self.assertNotIn('snapshot', compact)
                            self.assertTrue(compact['events'])
                finally:
                    server.shutdown()
                    thread.join()

    def test_rebuild_acknowledges_identical_projection_and_upgrades_old_event_state(self):
        import json
        from draft_helpers import make_service, confirmation
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tasks.sqlite'
            repo = SQLiteTaskRepository(path)
            service = make_service(repo)
            created = service.create_task(ACTOR, 'confirm', confirmation(service, ACTOR))[0]
            task_id = created['task_id']
            feed = TaskEventFeed(path)
            before = feed.read(ACTOR, task_id)['as_of_sequence']
            # A reimport touches projection rows without changing their meaning.
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('UPDATE task_read_summaries SET summary_json=summary_json WHERE task_id=?', (task_id,))
            self.assertFalse(feed.can_replay(ACTOR, created, feed.task_mutation_sequence(task_id)))
            repo.rebuild_task_read_summaries()
            self.assertTrue(feed.can_replay(ACTOR, created, feed.task_mutation_sequence(task_id)))
            self.assertEqual(feed.read(ACTOR, task_id)['as_of_sequence'], before)
            # Older deployments saved only the binding, not output versions.
            with closing(sqlite3.connect(path)) as db, db:
                saved = json.loads(db.execute('SELECT snapshot_json FROM task_event_state WHERE task_id=?', (task_id,)).fetchone()[0])
                db.execute('UPDATE task_event_state SET snapshot_json=? WHERE task_id=?',
                           (json.dumps({'binding': saved['binding']}), task_id))
            repo.rebuild_task_read_summaries()
            self.assertTrue(feed.can_replay(ACTOR, created, feed.task_mutation_sequence(task_id)))
            self.assertEqual(feed.read(ACTOR, task_id)['as_of_sequence'], before + 1)

    def test_task_watermarks_ignore_unrelated_writes_and_rollback(self):
        from draft_helpers import make_service, confirmation
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tasks.sqlite'
            service = make_service(SQLiteTaskRepository(path))
            first = service.create_task(ACTOR, 'confirm-a',
                confirmation(service, ACTOR, key='draft-a'))[0]
            second = service.create_task(ACTOR, 'confirm-b',
                confirmation(service, ACTOR, key='draft-b'))[0]
            feed = TaskEventFeed(path)
            a = feed.task_mutation_sequence(first['task_id'])
            b = feed.task_mutation_sequence(second['task_id'])
            feed.observe(ACTOR, first, progress(), mutation_sequence=a)
            self.assertTrue(feed.can_replay(ACTOR, first, a))
            self.assertFalse(feed.can_replay(Actor('tenant-a', 'other'), first, a))
            service.save_feedback(ACTOR, first['task_id'], 'feedback-a', {
                'text': '不要文字', 'scope': 'all', 'expected_revision': 1,
                'expected_task_version': first['task_version']})
            self.assertGreater(feed.task_mutation_sequence(first['task_id']), a)
            self.assertTrue(feed.can_replay(ACTOR, first,
                feed.task_mutation_sequence(first['task_id'])))
            with self.assertRaises(ServiceError) as stale:
                feed.observe(ACTOR, first, progress(), mutation_sequence=a)
            self.assertEqual(stale.exception.code, 'REVISION_CONFLICT')
            self.assertEqual(feed.task_mutation_sequence(second['task_id']), b)
            with closing(sqlite3.connect(path)) as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE task_read_summaries SET summary_json=summary_json '
                           'WHERE task_id=?', (second['task_id'],))
                db.rollback()
            self.assertEqual(feed.task_mutation_sequence(second['task_id']), b)
            restarted = TaskEventFeed(path)
            self.assertEqual(restarted.task_mutation_sequence(first['task_id']),
                             feed.task_mutation_sequence(first['task_id']))

    def test_business_and_write_event_failure_roll_back_together(self):
        from unittest.mock import patch
        from draft_helpers import make_service, confirmation
        with tempfile.TemporaryDirectory() as directory:
            service = make_service(SQLiteTaskRepository(Path(directory) / 'tasks.sqlite'))
            created = service.create_task(ACTOR, 'confirm', confirmation(service, ACTOR))[0]
            feed = TaskEventFeed(service.repository.path)
            before = feed.read(ACTOR, created['task_id'])
            with patch('dsh_retrieval.task_events.publish_task_change', side_effect=RuntimeError('event failed')):
                with self.assertRaisesRegex(RuntimeError, 'event failed'):
                    service.save_feedback(ACTOR, created['task_id'], 'failed-feedback', {
                        'text':'不要文字','scope':'all','expected_revision':1,
                        'expected_task_version':created['task_version']})
            self.assertEqual(feed.read(ACTOR, created['task_id']), before)
            self.assertIsNone(service.repository.get_pending_feedback(ACTOR,created['task_id']))

    def test_legacy_writer_can_fall_back_then_rebuild_write_events(self):
        from draft_helpers import make_service, confirmation
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tasks.sqlite'
            repository = SQLiteTaskRepository(path)
            service = make_service(repository)
            created = service.create_task(ACTOR, 'confirm', confirmation(service, ACTOR))[0]
            feed = TaskEventFeed(path)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('UPDATE local_ledger SET payload=payload WHERE id=1')
            self.assertIsNone(feed.task_mutation_sequence(created['task_id']))
            feed.observe(ACTOR,created,progress())
            repository.rebuild_task_read_summaries()
            self.assertTrue(feed.can_replay(ACTOR,created,feed.task_mutation_sequence(created['task_id'])))

    def test_snapshot_and_changes_have_monotonic_replay_sequence(self):
        feed = TaskEventFeed()
        first = feed.observe(ACTOR, task(), progress())
        self.assertEqual(first, 1)
        page = feed.read(ACTOR, "task-1")
        self.assertEqual([event["kind"] for event in page["events"]], ["task.snapshot"])

        result = {"result_set_id": "result-1", "result_set_digest": "sha256:" + "a" * 64,
                  "kind": "preview", "accepted_count": 2, "failed_count": 1,
                  "pending_review_count": 1, "user_excluded_count": 0, "gap": 8}
        second = feed.observe(ACTOR, task(task_version=3, stage="REFILL"),
                              progress("succeeded", "approved", 2), result,
                              [{"run_id": "run-1", "status": "proposal_received"}],
                              [{"operation_id": "operation-1",
                                "step_execution_id": "step-1",
                                "step_id": "semantic",
                                "output_version": "sha256:" + "b" * 64,
                                "counts": {"input": 2, "retained": 1}}])
        self.assertGreater(second, first)
        changed = feed.read(ACTOR, "task-1", after_sequence=first)
        self.assertEqual(changed["as_of_sequence"], second)
        self.assertEqual([event["sequence"] for event in changed["events"]],
                         list(range(first + 1, second + 1)))
        self.assertIn("node.progress", [event["kind"] for event in changed["events"]])
        self.assertIn("gate.changed", [event["kind"] for event in changed["events"]])
        self.assertIn("output.available", [event["kind"] for event in changed["events"]])
        self.assertTrue(any(event["kind"] == "output.available"
                            and event["payload"].get("node") == "cleaning"
                            for event in changed["events"]))
        self.assertIn("execution.updated", [event["kind"] for event in changed["events"]])
        self.assertEqual(feed.observe(ACTOR, task(task_version=3, stage="REFILL"),
                     progress("succeeded", "approved", 2), result,
                     [{"run_id": "run-1", "status": "proposal_received"}],
                     [{"operation_id": "operation-1",
                       "step_execution_id": "step-1", "step_id": "semantic",
                       "output_version": "sha256:" + "b" * 64,
                       "counts": {"input": 2, "retained": 1}}]), second)

    def test_sqlite_restart_keeps_watermark_and_owner_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite3"
            SQLiteTaskRepository(path)
            feed = TaskEventFeed(path)
            mutation = feed.mutation_sequence()
            with closing(sqlite3.connect(path)) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE local_ledger SET payload=payload WHERE id=1")
                db.rollback()
            self.assertEqual(feed.mutation_sequence(), mutation,
                             "rolled back writes must not wake event streams")
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE local_ledger SET payload=payload WHERE id=1")
            self.assertGreater(feed.mutation_sequence(), mutation)
            sequence = feed.observe(ACTOR, task(), progress())
            restarted = TaskEventFeed(path)
            self.assertEqual(restarted.read(ACTOR, "task-1")["as_of_sequence"], sequence)
            self.assertEqual(restarted.mutation_sequence(), feed.mutation_sequence())
            SQLiteTaskRepository(path)
            with self.assertRaises(ServiceError) as caught:
                restarted.read(Actor("tenant-a", "other"), "task-1")
            self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_expired_and_future_cursors_fail_closed(self):
        feed = TaskEventFeed(retention=8)
        feed.observe(ACTOR, task(), progress())
        for version in range(3, 14):
            feed.observe(ACTOR, task(task_version=version),
                         progress(accepted=version))
        with self.assertRaises(ServiceError) as expired:
            feed.read(ACTOR, "task-1", after_sequence=1)
        self.assertEqual(expired.exception.code, "EVENT_HISTORY_EXPIRED")
        with self.assertRaises(ServiceError) as future:
            feed.read(ACTOR, "task-1", after_sequence=999)
        self.assertEqual(future.exception.code, "INVALID_EVENT_CURSOR")


if __name__ == "__main__":
    unittest.main()
