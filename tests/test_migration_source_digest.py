from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
from tools.migrate_task_asset_bindings import migrate, source_digest


class MigrationSourceDigestTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'source.sqlite3'
        self.repo = SQLiteMultiRuleApprovalRepository(self.path, projects=None, authorization=None).tasks

    def test_usage_observations_participate_in_source_fingerprint_and_copy(self):
        from dsh_retrieval.cleaning_usage import CleaningUsageRecorder
        from cleaning.test_generic_runner import make_job
        job = make_job(contents=(b'one',))
        before = source_digest(self.path)
        recorder = CleaningUsageRecorder(self.path, job.binding, lambda: None)
        with recorder.step(job.candidates[0], job.recipe.steps[0], supported=False):
            pass
        after = source_digest(self.path)
        self.assertNotEqual(before, after)
        target = Path(self.folder.name) / 'usage-copy.sqlite3'
        migrate(self.path, target)
        self.assertEqual(source_digest(target), after)

    def test_normalized_ledger_view_changes_are_in_source_fingerprint(self):
        before = source_digest(self.path)
        with closing(self.repo._connect()) as db, db:
            self.assertEqual(db.execute("SELECT type FROM sqlite_master WHERE name='local_ledger'").fetchone(), ('view',))
            db.execute('UPDATE local_ledger SET format=format+1')
        self.assertNotEqual(source_digest(self.path), before)

    def test_resume_rejects_source_view_change_before_reopening_copy(self):
        target = Path(self.folder.name) / 'copy.sqlite3'
        with patch('tools.verify_task_read_projections.verify', side_effect=RuntimeError('interrupt')):
            with self.assertRaisesRegex(RuntimeError, 'interrupt'):
                migrate(self.path, target)
        with closing(self.repo._connect()) as db, db:
            db.execute('UPDATE local_ledger SET format=format+1')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            migrate(self.path, target, resume=True)
        self.assertFalse(target.exists())

    def test_successful_copy_preserves_normalized_business_source(self):
        before = source_digest(self.path)
        target = Path(self.folder.name) / 'copy.sqlite3'
        self.assertEqual(migrate(self.path, target), 0)
        self.assertEqual(source_digest(self.path), before)
        self.assertEqual(source_digest(target), before)
