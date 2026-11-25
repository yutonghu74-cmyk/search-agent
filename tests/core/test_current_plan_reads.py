from contextlib import closing
import unittest
from unittest.mock import patch

from dsh_retrieval.adapters.task_storage import sqlite
from dsh_retrieval.errors import ServiceError
from tests.core import test_plan_set_progress as fixtures


class CurrentPlanReadTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PlanSetProgressTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.repo, self.actor, self.task_id = self.f.repo, self.f.actor, self.f.fixture.task_id
        operation = self.f.reserve(self.f.first, 'old-page'); self.f.commit(operation, self.f.first)
        self.old = self.f.approved
        proposal = self.f.fixture.make_proposal(2); self.repo.put_proposal(self.actor, proposal)
        self.current, _ = self.f.fixture.confirm(proposal, key='current-plan')
        request = self.current.members[0].plan.request
        self.operation, _ = self.repo.reserve_page(self.actor, self.current,
            plan_id=request.plan_id, request=request, key='pending-current-page')
        self.expected = self.repo.get_progress(self.actor, self.current)

    def assert_corrupt(self):
        with self.assertRaises(ServiceError) as caught:
            self.repo.get_progress_snapshot(self.actor, self.task_id)
        self.assertEqual(caught.exception.code, 'STORAGE_CORRUPT')

    def test_old_partition_is_never_selected_or_decoded(self):
        with closing(self.repo.tasks._connect()) as db, db:
            db.execute("UPDATE plan_page_sources SET payload='broken unused history' WHERE plan_digest=?",
                       (self.old.proposal_digest,))
        queries, epochs = [], []
        connect, decode = self.repo.tasks._connect, sqlite.decode
        def traced():
            db = connect(); db.set_trace_callback(queries.append); return db
        def record(value):
            if isinstance(value, dict) and value.get('type') == 'RetrievalPlanSet':
                epochs.append(value['value']['epoch'])
            return decode(value)
        with patch.object(self.repo.tasks, '_connect', side_effect=traced), patch.object(sqlite, 'decode', side_effect=record):
            self.assertEqual(self.repo.get_current(self.actor, self.task_id), self.current)
            self.assertEqual(self.repo.get_progress_snapshot(self.actor, self.task_id), (self.current, self.expected))
        self.assertTrue(epochs)
        self.assertEqual(set(epochs), {2})
        self.assertFalse(any('from plan_task_read_views' in q.lower() or 'from plan_set_ledger' in q.lower() for q in queries))
        self.assertFalse(any(self.old.proposal_digest in q for q in queries))

    def test_current_partition_corruption_cannot_hide_behind_legacy_view(self):
        with closing(self.repo.tasks._connect()) as db, db:
            db.execute("UPDATE plan_page_sources SET payload='broken current' WHERE plan_digest=?",
                       (self.current.proposal_digest,))
        self.assert_corrupt()

    def test_header_corruption_cannot_hide_behind_legacy_view(self):
        with closing(self.repo.tasks._connect()) as db, db:
            db.execute("UPDATE plan_task_sources SET payload='broken header' WHERE task_id=?", (self.task_id,))
        self.assert_corrupt()

    def test_missing_header_falls_back_without_rebuilding_on_read(self):
        with closing(self.repo.tasks._connect()) as db, db:
            db.execute('DELETE FROM plan_task_sources WHERE task_id=?', (self.task_id,))
        self.assertEqual(self.repo.get_progress_snapshot(self.actor, self.task_id), (self.current, self.expected))
        with closing(self.repo.tasks._connect()) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM plan_task_sources WHERE task_id=?', (self.task_id,)).fetchone())

    def test_valid_checksum_with_wrong_plan_records_is_rejected(self):
        with closing(self.repo.tasks._connect()) as db, db:
            old = db.execute('SELECT payload,checksum FROM plan_page_sources WHERE plan_digest=?', (self.old.proposal_digest,)).fetchone()
            db.execute('UPDATE plan_page_sources SET payload=?,checksum=? WHERE plan_digest=?', (*old, self.current.proposal_digest))
        self.assert_corrupt()
