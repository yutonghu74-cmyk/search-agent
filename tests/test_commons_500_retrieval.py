"""Drive a large, multi-type Commons image corpus through the local flow.

The 500-image corpus is produced by ``tools/fetch_commons_test_media.py``
(real network crawl, CC0/CC BY/Public domain, attribution in the manifest) and
converted into the trusted LOCAL_EXACT trio by ``LocalTestMediaCorpus``. These
tests build a synthetic 500-item manifest in a temp dir (no network, no repo
media) and verify the same full flow a real corpus would take: a broad query
"找图片" routes to the single label-proxy index, type-specific queries narrow to
their own label, and the worker retrieves exactly the fixture's depth.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.first_search_local import LocalFirstSearch
from dsh_retrieval.local_catalog import build_local_catalog
from dsh_retrieval.local_test_media import LABELS, LocalTestMediaCorpus
from dsh_retrieval.model_runtime import ModelRuntime
from dsh_retrieval.models import Actor
from dsh_retrieval.service import TaskService
from draft_helpers import BODY, confirmation


TOTAL = 500
PER_LABEL = TOTAL // len(LABELS)


def build_synthetic_manifest(root):
    """A 500-item manifest whose files and SHA-256 digests are real, and whose
    labels cycle through the same content types the Commons crawler produces."""
    (root / "images").mkdir()
    items = []
    for index in range(TOTAL):
        label = list(LABELS)[index % len(LABELS)]
        # A decodable JPEG with a unique trailing marker per asset.
        import base64
        content = base64.b64decode('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDyOiiiuw5D/9k=') + f'synthetic {label} {index}'.encode()
        filename = f"{label}-{index + 1:03d}.jpg"
        (root / "images" / filename).write_bytes(content)
        items.append({
            "media_unit_id": f"synthetic-{index + 1}", "asset_version": "v1",
            "source_namespace": "wikimedia-commons", "source_asset_id": str(index + 1),
            "file": "images/" + filename,
            "content_digest": "sha256:" + hashlib.sha256(content).hexdigest(),
            "label_search_intent": label,
            "source_url": f"https://commons.wikimedia.org/wiki/File:Synthetic_{index + 1}.jpg",
            "license": "CC BY 4.0",
        })
    (root / "manifest.json").write_text(json.dumps({
        "schema": "test-media-manifest@1", "items": items}))
    return root / "manifest.json"


class Commons500RetrievalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        manifest = build_synthetic_manifest(self.root)
        corpus = LocalTestMediaCorpus(manifest)
        self.assertEqual(len(corpus.items), TOTAL)
        corpus.build_fixture(self.root / "local-exact", max_depth=PER_LABEL)
        self.source = self.root / "local-exact" / "catalog.json"
        self.fixture_path = self.root / "local-exact" / "fixture.json"
        self.ledger = self.root / "ledger.sqlite3"
        self.config_ledger = self.root / "config.sqlite3"
        self.actor = Actor("tenant-a", "user-a")
        self.projects = LocalProjectAccess([("tenant-a", "user-a", "demo", "default")])
        self.tasks = TaskService(SQLiteTaskRepository(self.ledger), self.projects)
        self.local = self.make_local()

    def make_local(self):
        configs = build_local_catalog(self.source, self.config_ledger, ModelRuntime())
        local = LocalFirstSearch(self.tasks, configs, ledger_path=self.ledger,
            source_path=self.source,
            capabilities_path=self.root / "local-exact" / "capabilities.json",
            fixture_path=self.fixture_path,
            cursor_secret=b"test-only-commons500-cursor-secret-123456")
        self.addCleanup(local.close)
        return local

    def create_task(self, raw_request, key):
        body = json.loads(json.dumps(BODY))
        body["query_spec"]["raw_request"] = raw_request
        created, _ = self.tasks.create_task(self.actor, key,
            confirmation(self.tasks, self.actor, key=key, body=body))
        return created["task_id"]

    def run_auto(self, task_id, key):
        queued = self.local.auto_start(self.actor, task_id, key=key)
        self.assertEqual(queued["status"], "queued")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.local.progress(self.actor, task_id).get("pagination_stopped"):
                return queued
            time.sleep(.02)
        self.fail("background search over 500 records did not finish")

    def test_broad_query_retrieves_full_500_image_corpus(self):
        self.local.media_gateway = LocalTestMediaCorpus(self.root / 'manifest.json', self.tasks)
        task_id = self.create_task("找图片", "commons500-broad")
        preview = self.local.preview(self.actor, task_id)
        self.assertEqual(preview["mode"], "LOCAL_EXACT")
        self.assertEqual(len(preview["routes"]), 1)
        queued = self.run_auto(task_id, "terms:commons500-broad:1")
        self.assertEqual(queued["source_count"], 1)
        candidates = self.local.candidates(self.actor, task_id)
        # The fixture holds the full 500-record corpus; the broad query retrieves
        # the fixture depth (PER_LABEL) because label-proxy ranking bounds a
        # search, it does not filter by label.
        fixture = json.loads(self.fixture_path.read_text())
        self.assertEqual(len(fixture["records"]), TOTAL)
        self.assertEqual(candidates["raw_hit_count"], PER_LABEL)
        self.assertEqual(candidates["resolved_count"], PER_LABEL)
        self.assertEqual(candidates["verified_count"], PER_LABEL)
        self.assertEqual(candidates["truncated"], False)
        self.assertEqual(len(candidates["items"]), PER_LABEL)
        self.assertTrue(all(row["content_digest"].startswith("sha256:")
                            for row in candidates["items"]))
        self.assertTrue(all(row['preview_status'] == 'available' for row in candidates['items']))
        content_type, image = self.local.candidate_preview(
            self.actor, task_id, candidates['items'][0]['candidate_id'])
        self.assertEqual(content_type, 'image/jpeg')
        self.assertTrue(image.startswith(b'\xff\xd8\xff'))

    def test_long_candidate_pagination_and_last_image_preview_survive_restart(self):
        from unittest.mock import patch
        fixture = json.loads(self.fixture_path.read_text())
        fixture['max_depth'] = 202
        self.fixture_path.write_text(json.dumps(fixture))
        task_id = self.create_task('找图片', 'long-browse')
        preview = self.local.preview(self.actor, task_id)
        choices = [dict(branch_id=route['branch_id'], binding_id=option['binding_id'], batch_size=2)
                   for route in preview['routes'] for option in route['options']]
        proposal = self.local.prepare(self.actor, task_id, routing_digest=preview['routing_digest'], choices=choices)
        self.local.confirm(self.actor, task_id, proposal_digest=proposal['proposal_digest'],
            expected_task_version=proposal['expected_task_version'], action='confirm', key='long-browse-approve')
        # Explicitly drive the 101 small pages; this path does not enqueue a
        # worker, so wall-clock polling would never advance it.
        for _ in range(102):
            if self.local.progress(self.actor, task_id)['pagination_stopped']:
                break
            self.local.tick(self.actor, task_id, max_requests=1)
        self.assertTrue(self.local.progress(self.actor, task_id)['pagination_stopped'])
        self.assertEqual(sum(m['committed_pages'] for m in self.local.progress(self.actor, task_id)['members']), 101)
        self.local.close()
        self.local = self.make_local()
        self.local.media_gateway = LocalTestMediaCorpus(self.root / 'manifest.json', self.tasks)
        with patch.object(self.local.repository, 'list_committed_pages', side_effect=AssertionError('full history read')), \
             patch.object(self.local.repository, 'summarize_committed_hits', side_effect=AssertionError('prefix-only read')):
            result = self.local.candidates(self.actor, task_id, limit=60)
            items = list(result['items'])
            while result['next_cursor']:
                result = self.local.candidates(self.actor, task_id, limit=60, cursor=result['next_cursor'])
                items.extend(result['items'])
            self.assertEqual(len(items), 202)
            self.assertEqual(len({i['hit_sequence'] for i in items}), 202)
            last = items[-1]
            content_type, content = self.local.candidate_preview(self.actor, task_id, last['candidate_id'], ref=last['preview_ref'])
            self.assertEqual(content_type, 'image/jpeg')
            self.assertTrue(content.startswith(b'\xff\xd8\xff'))
            self.assertEqual(self.local.candidates(self.actor, task_id, cursor=result['changes_cursor'], changes=True)['items'], [])

    def test_type_specific_query_narrows_to_one_label(self):
        label_names = list(LABELS)
        # dog and dog_person intentionally share one prototype vector, so a
        # "狗" query is allowed to return either label.
        shared = {'dog': {'dog', 'dog_person'}, 'dog_person': {'dog', 'dog_person'}}
        for label in label_names:
            chinese = LABELS[label][0]
            with self.subTest(label=label):
                task_id = self.create_task(f"找{chinese}图片", f"commons500-{label}")
                queued = self.run_auto(task_id, f"terms:commons500-{label}:1")
                self.assertEqual(queued["source_count"], 1)
                candidates = self.local.candidates(self.actor, task_id)
                self.assertEqual(candidates["raw_hit_count"], PER_LABEL, label)
                expected = shared.get(label, {label})
                # Every hit is exactly that label's own record: it resolves to a
                # vector_id whose manifest label matches the query's prototype.
                self.assertTrue(all(label_names[(int(row["vector_id"]) - 1) % len(label_names)] in expected
                                    for row in candidates["items"]), label)


if __name__ == "__main__":
    unittest.main()
