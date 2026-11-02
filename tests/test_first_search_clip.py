import io
import json
from pathlib import Path
import tempfile
import time
import unittest

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.retrieval.clip_search import CLIPSearchEndpoint
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.first_search_clip import CLIPFirstSearch
from dsh_retrieval.integration_models import AccessContext, IndexBinding, Library
from dsh_retrieval.local_catalog import build_local_catalog
from dsh_retrieval.models import Actor
from dsh_retrieval.physical_index_registry import PhysicalIndex, PhysicalIndexRegistry
from dsh_retrieval.service import TaskService
from dsh_retrieval.text_catalog_config import CatalogConfigScope
from draft_helpers import BODY, confirmation


class Response(io.BytesIO):
    status = 200


class Transport:
    def __init__(self):
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        payload = {
            "success": True, "query_type": "text", "model": "chinese-clip-vit-huge-patch14",
            "datasets": [{"key": "dataset-key", "version": "dataset-v1"}],
            "current_page": 1, "group_size": 10, "has_next": False,
            "matches": [{"id": 1, "similarity_score": 0.42, "filename": "one.jpg",
                "full_path": "/mock/one.jpg", "web_path": "/local/one.jpg", "folder": "all",
                "app_name": "mock", "dataset_name": "mock-images",
                "dataset_key": "dataset-key", "shard": "shard_0.db"}],
        }
        return Response(json.dumps(payload).encode())


class CatalogRuntime:
    def suggest_catalog(self, text, llm_id, candidates):
        return [{"library_id": "clip-images", "description": "客厅图片",
                 "aliases": ["客厅", "图片"], "binding_ids": ["clip-v1"]}]


class FirstSearchCLIPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.actor = Actor("tenant-a", "user-a")
        projects = LocalProjectAccess([("tenant-a", "user-a", "demo", "default")])
        self.tasks = TaskService(SQLiteTaskRepository(root / "tasks.sqlite3"), projects)
        created, _ = self.tasks.create_task(self.actor, "task",
            confirmation(self.tasks, self.actor, body=BODY))
        self.task_id = created["task_id"]
        self.binding = IndexBinding("clip-v1", "clip-images", "index-v1", "clip@1", 4, "IP")
        source = {"schema": "local-catalog@1", "libraries": [{"library_id": "clip-images",
            "description": "找客厅图片", "media_type": "image", "bindings": [{
                "binding_id": "clip-v1", "library_id": "clip-images", "index_version": "index-v1",
                "encoder_ref": "clip@1", "vector_dimension": 4, "metric": "IP"}]}],
            "grants": [{"tenant_id": "tenant-a", "owner_id": "user-a", "project_id": "demo",
                        "library_ids": ["clip-images"], "can_configure": True}]}
        self.source = root / "catalog.json"
        self.source.write_text(json.dumps(source, ensure_ascii=False))
        capabilities = {"schema": "text-query-capabilities@1", "items": [{
            "binding": source["libraries"][0]["bindings"][0], "media_type": "image",
            "filter_template_ref": "all", "max_batch_size": 10}]}
        self.capabilities = root / "capabilities.json"
        self.capabilities.write_text(json.dumps(capabilities))
        mappings = {"schema": "clip-media-mappings@1", "entries": [{
            "source": {"binding_id": "clip-v1", "index_version": "index-v1",
                "dataset_key": "dataset-key", "dataset_version": "dataset-v1",
                "shard": "shard_0.db", "full_path": "/mock/one.jpg"},
            "media": {"media_unit_id": "unit-1", "asset_version": "asset-v1",
                "source_namespace": "mock-assets", "source_asset_id": "asset-1",
                "authorized_media_ref": "media:asset-1@asset-v1",
                "content_digest": "sha256:" + "1" * 64}}]}
        self.mappings = root / "mappings.json"
        self.mappings.write_text(json.dumps(mappings))
        self.configs = build_local_catalog(self.source, root / "catalog.sqlite3", CatalogRuntime())
        scope = CatalogConfigScope("tenant-a", "user-a", "demo")
        self.configs.save(scope, **self.configs.preview(scope, "配置客厅图片库", "text"))
        target = PhysicalIndex(self.binding, "clip-service", "none", "mock-images", "path", "vector",
            "chinese-clip-vit-huge-patch14", "unknown", "clip-text-77@1", "all",
            "offset", "live", 10, 100)
        registry = PhysicalIndexRegistry((target,))
        endpoint = CLIPSearchEndpoint(self.binding, "chinese-clip-vit-huge-patch14", "mock-project",
            "/mock/dataset", ("shard_0.db",), (("all", ()),), "all", 100, 0.1, 40,
            "http://clip.invalid:4444")
        self.transport = Transport()
        self.clip = CLIPFirstSearch(self.tasks, self.configs, source_path=self.source,
            capabilities_path=self.capabilities, registry=registry, endpoints=(endpoint,),
            jobs_path=root / "jobs.sqlite3", cursor_secret=b"c" * 32, opener=self.transport,
            media_mappings_path=self.mappings)
        self.addCleanup(self.clip.close)

    def wait_complete(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            progress = self.clip.progress(self.actor, self.task_id)
            if progress["pagination_stopped"]:
                return progress
            time.sleep(.01)
        self.fail("combined CLIP search did not finish")

    def test_refill_authority_only_allows_library_search(self):
        authority = self.clip._authority()
        context = AccessContext(self.actor, self.task_id, "refill", "current")
        authority.require_access(context, "library:clip-images", "search")
        with self.assertRaises(ServiceError) as error:
            authority.require_access(context, "library:clip-images", "discover")
        self.assertEqual(error.exception.code, "ACCESS_DENIED")

    def test_confirmed_terms_queue_background_job_and_expose_candidates(self):
        preview = self.clip.preview(self.actor, self.task_id)
        self.assertEqual(preview["mode"], "CLIP_COMBINED")
        self.assertEqual(preview["routes"][0]["options"][0]["binding_id"], "clip-v1")
        queued = self.clip.auto_start(self.actor, self.task_id, key="terms-confirmed")
        self.assertEqual(queued["status"], "queued")
        progress = self.wait_complete()
        self.assertEqual((progress["status"], progress["job_status"]), ("approved", "complete"))
        candidates = self.clip.candidates(self.actor, self.task_id)
        self.assertEqual(candidates["raw_hit_count"], 1)
        self.assertEqual(candidates["items"][0]["filename"], "one.jpg")
        self.assertEqual(candidates["items"][0]["query_text"], "找 10 张客厅图片")
        self.assertEqual(candidates["items"][0]["status"], "resolved")
        self.assertTrue(candidates["items"][0]["candidate_id"].startswith("candidate_"))
        self.assertEqual(candidates["items"][0]["content_digest"], "sha256:" + "1" * 64)
        self.assertEqual(candidates["resolved_count"], 1)
        self.assertEqual(candidates["verified_count"], 1)
        self.assertNotIn("authorized_media_ref", candidates["items"][0])
        self.assertNotIn("source_asset_id", candidates["items"][0])
        replay = self.clip.auto_start(self.actor, self.task_id, key="terms-confirmed")
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.transport.requests), 1)


if __name__ == "__main__":
    unittest.main()
