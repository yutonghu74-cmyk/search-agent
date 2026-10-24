from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository, dump
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.first_search_local import LocalFirstSearch
from dsh_retrieval.local_catalog import build_local_catalog
from dsh_retrieval.model_runtime import ModelRuntime
from dsh_retrieval.models import Actor
from dsh_retrieval.service import TaskService
from draft_helpers import BODY, confirmation


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config'


class FirstSearchLocalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'catalog.json'
        shutil.copyfile(CONFIG / 'local-first-search-catalog.example.json', self.source)
        self.fixture_path = self.root / 'fixture.json'
        shutil.copyfile(CONFIG / 'local-first-search.example.json', self.fixture_path)
        self.ledger = self.root / 'ledger.sqlite3'
        self.config_ledger = self.root / 'config.sqlite3'
        self.actor = Actor('tenant-a', 'user-a')
        self.projects = LocalProjectAccess([('tenant-a', 'user-a', 'demo', 'default')])
        self.tasks = TaskService(SQLiteTaskRepository(self.ledger), self.projects)
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = '找动物图片'
        created, _ = self.tasks.create_task(self.actor, 'task',
            confirmation(self.tasks, self.actor, body=body))
        self.task_id = created['task_id']
        self.local = self.make_local()

    def make_local(self):
        configs = build_local_catalog(self.source, self.config_ledger, ModelRuntime())
        return LocalFirstSearch(self.tasks, configs, ledger_path=self.ledger,
            source_path=self.source,
            capabilities_path=CONFIG / 'local-first-search-capabilities.example.json',
            fixture_path=self.fixture_path,
            cursor_secret=b'test-only-local-cursor-secret-12345')

    def select_and_approve(self):
        preview = self.local.preview(self.actor, self.task_id)
        self.assertEqual(preview['mode'], 'LOCAL_EXACT')
        self.assertEqual(len(preview['routes']), 1)
        route = preview['routes'][0]
        self.assertEqual({option['binding_id'] for option in route['options']}, {'dog-v1', 'cat-v1'})
        choices = [{'branch_id': route['branch_id'], 'binding_id': option['binding_id'], 'batch_size': 2}
                   for option in route['options']]
        proposed = self.local.prepare(self.actor, self.task_id,
            routing_digest=preview['routing_digest'], choices=choices)
        confirmed, replay = self.local.confirm(self.actor, self.task_id,
            proposal_digest=proposed['proposal_digest'],
            expected_task_version=proposed['expected_task_version'],
            action='confirm', key='approve')
        self.assertFalse(replay)
        self.assertEqual(confirmed['status'], 'approved')
        return proposed

    def test_two_index_first_batch_restart_and_candidate_mapping(self):
        proposed = self.select_and_approve()
        self.assertEqual(len(proposed['members']), 2)
        first = self.local.tick(self.actor, self.task_id, max_requests=2)
        self.assertEqual(first['network_attempts'], 2)
        self.assertEqual({row['status'] for row in first['outcomes']}, {'committed'})
        candidates = self.local.candidates(self.actor, self.task_id)
        self.assertEqual(candidates['raw_hit_count'], 4)
        self.assertIsNone(candidates['accepted_count'])
        self.assertEqual({(item['binding_id'], item['vector_id']) for item in candidates['items']},
                         {('dog-v1', 1), ('dog-v1', 2), ('cat-v1', 1), ('cat-v1', 2)})
        self.assertTrue(all(item['query_text'] == '找动物图片' and item['query_role'] == 'original'
                            and item['source_term'] == '找动物图片' and item['branch_id'].startswith('branch_')
                            and item['plan_id'] for item in candidates['items']))
        self.assertNotEqual(next(item for item in candidates['items'] if item['binding_id'] == 'dog-v1')['media_unit_id'],
                            next(item for item in candidates['items'] if item['binding_id'] == 'cat-v1')['media_unit_id'])
        self.assertTrue(all('authorized_media_ref' not in item for item in candidates['items']))
        restarted = self.make_local()
        last = restarted.tick(self.actor, self.task_id, max_requests=2)
        self.assertEqual(last['network_attempts'], 1)
        self.assertTrue(last['pagination_stopped'])
        all_candidates = restarted.candidates(self.actor, self.task_id)
        self.assertEqual(all_candidates['raw_hit_count'], 5)
        self.assertEqual([item['status'] for item in all_candidates['items']].count('mapping_missing'), 1)
        self.assertEqual(restarted.tick(self.actor, self.task_id, max_requests=2)['network_attempts'], 0)

    def test_approval_required_and_changed_preview_rejected(self):
        preview = self.local.preview(self.actor, self.task_id)
        with self.assertRaises(ServiceError) as error:
            self.local.tick(self.actor, self.task_id, max_requests=1)
        self.assertEqual(error.exception.code, 'PLAN_NOT_APPROVED')
        with self.assertRaises(ServiceError) as error:
            self.local.prepare(self.actor, self.task_id, routing_digest='changed', choices=[])
        self.assertEqual(error.exception.code, 'ROUTING_CHANGED')
        self.assertFalse(preview['status'] == 'approved')

    def test_revocation_blocks_candidate_read(self):
        self.select_and_approve()
        self.local.tick(self.actor, self.task_id, max_requests=1)
        data = json.loads(self.source.read_text())
        data['grants'][0]['library_ids'] = []
        self.source.write_text(json.dumps(data))
        with self.assertRaises(ServiceError) as error:
            self.local.candidates(self.actor, self.task_id)
        self.assertEqual(error.exception.code, 'ACCESS_DENIED')

    def test_changed_fixture_does_not_rebind_approved_first_page(self):
        self.select_and_approve()
        data = json.loads(self.fixture_path.read_text())
        data['records'][0]['values'] = [0.8, 0.2]
        self.fixture_path.write_text(json.dumps(data))
        with self.assertRaises(ServiceError) as error:
            self.make_local().tick(self.actor, self.task_id, max_requests=1)
        self.assertEqual(error.exception.code, 'INDEX_SNAPSHOT_EXPIRED')

    def test_empty_local_corpus_is_exhausted_but_not_effective_count(self):
        data = json.loads(self.fixture_path.read_text())
        data['records'] = []
        self.fixture_path.write_text(json.dumps(data))
        self.local = self.make_local()
        self.select_and_approve()
        result = self.local.tick(self.actor, self.task_id, max_requests=2)
        self.assertTrue(result['pagination_stopped'])
        candidates = self.local.candidates(self.actor, self.task_id)
        self.assertEqual(candidates['raw_hit_count'], 0)
        self.assertIsNone(candidates['accepted_count'])

    def test_old_generation_cannot_execute_or_read_candidates(self):
        self.select_and_approve()
        with closing(sqlite3.connect(self.ledger)) as db, db:
            memory = self.tasks.repository._load(
                db.execute('SELECT format,payload FROM local_ledger WHERE id=1').fetchone())
            task = memory._tasks[self.task_id]
            memory._tasks[self.task_id] = replace(task, run_generation=task.run_generation + 1)
            db.execute('UPDATE local_ledger SET payload=? WHERE id=1', (dump(memory),))
        for action in (lambda: self.local.tick(self.actor, self.task_id, max_requests=1),
                       lambda: self.local.candidates(self.actor, self.task_id)):
            with self.assertRaises(ServiceError) as error:
                action()
            self.assertEqual(error.exception.code, 'REVISION_CONFLICT')


if __name__ == '__main__':
    unittest.main()
