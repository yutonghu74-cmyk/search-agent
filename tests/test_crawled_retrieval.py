"""Crawled, multi-type corpus through the local first-search flow.

``dsh_retrieval.crawled_corpus`` is a deterministic offline "crawler": it
synthesizes several different image data types (动物·狗 / 动物·猫 / 车辆·汽车 /
风景·山水), each with its own library, index binding, encoder, vector dimension,
metric and records, and writes them as the trusted local JSON trio. These tests
drive that corpus through ``LocalFirstSearch`` (LOCAL_EXACT, no network) and
verify that a broad query spans heterogeneous indexes while a type-specific
query narrows to one.
"""
import json
from pathlib import Path
import tempfile
import time
import unittest

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.crawled_corpus import DATA_TYPES, build_crawled_corpus
from dsh_retrieval.first_search_local import LocalFirstSearch
from dsh_retrieval.local_catalog import build_local_catalog
from dsh_retrieval.model_runtime import ModelRuntime
from dsh_retrieval.models import Actor
from dsh_retrieval.service import TaskService
from draft_helpers import BODY, confirmation


BINDING_BY_TYPE = {spec["library_id"]: spec["binding_id"] for spec in DATA_TYPES}
ALL_BINDINGS = {spec["binding_id"] for spec in DATA_TYPES}


class CrawledRetrievalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        build_crawled_corpus(self.root, seed=0)
        self.source = self.root / "catalog.json"
        self.fixture_path = self.root / "fixture.json"
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
            capabilities_path=self.root / "capabilities.json",
            fixture_path=self.fixture_path,
            cursor_secret=b"test-only-crawled-cursor-secret-12345")
        self.addCleanup(local.close)
        return local

    def create_task(self, raw_request, key):
        body = json.loads(json.dumps(BODY))
        body["query_spec"]["raw_request"] = raw_request
        created, _ = self.tasks.create_task(self.actor, key,
            confirmation(self.tasks, self.actor, key=key, body=body))
        return created["task_id"]

    def wait_stopped(self, local, task_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if local.progress(self.actor, task_id).get("pagination_stopped"):
                return
            time.sleep(.01)
        self.fail("background search did not finish")

    def run_auto(self, local, task_id, key):
        queued = local.auto_start(self.actor, task_id, key=key)
        self.assertEqual(queued["status"], "queued")
        self.wait_stopped(local, task_id)
        return queued

    def test_corpus_has_distinct_heterogeneous_image_types(self):
        catalog = json.loads(self.source.read_text())
        self.assertEqual(catalog["schema"], "local-catalog@1")
        self.assertEqual({lib["library_id"] for lib in catalog["libraries"]},
                         {"dogs", "cats", "cars", "landscapes"})
        self.assertTrue(all(lib["media_type"] == "image" for lib in catalog["libraries"]))
        metcalf = [
            (lib["bindings"][0]["metric"], lib["bindings"][0]["vector_dimension"])
            for lib in catalog["libraries"]
        ]
        # Distinct crawl types must differ in metric or vector shape, proving the
        # corpus genuinely spans several kinds of data rather than one repeated.
        self.assertEqual(len(metcalf), len(set(metcalf)))
        fixture = json.loads(self.fixture_path.read_text())
        self.assertEqual(len(fixture["records"]),
                         len(DATA_TYPES) * 3)
        self.assertTrue(all(media["content_digest"].startswith("sha256:")
                            for record in fixture["records"] for media in record["media"]))

    def test_broad_query_routes_across_all_crawled_indexes(self):
        task_id = self.create_task("找图片", "crawled-broad")
        preview = self.local.preview(self.actor, task_id)
        self.assertEqual(preview["mode"], "LOCAL_EXACT")
        self.assertEqual(len(preview["routes"]), 1)
        route = preview["routes"][0]
        self.assertEqual({option["binding_id"] for option in route["options"]}, ALL_BINDINGS)
        queued = self.run_auto(self.local, task_id, "terms:crawled-broad:1")
        self.assertEqual(queued["source_count"], len(DATA_TYPES))
        candidates = self.local.candidates(self.actor, task_id)
        # 3 records per crawled type, all reachable in one pass.
        self.assertEqual(candidates["raw_hit_count"], len(DATA_TYPES) * 3)
        self.assertEqual(candidates["resolved_count"], len(DATA_TYPES) * 3)
        self.assertEqual(candidates["verified_count"], len(DATA_TYPES) * 3)
        self.assertEqual({row["binding_id"] for row in candidates["sources"]}, ALL_BINDINGS)
        self.assertEqual({row["binding_id"] for row in candidates["items"]}, ALL_BINDINGS)
        self.assertTrue(all(row["status"] == "resolved"
                            and row["content_digest"].startswith("sha256:")
                            and row["candidate_id"].startswith("candidate_")
                            for row in candidates["items"]))

    def test_type_specific_query_narrows_to_one_crawled_type(self):
        for spec in DATA_TYPES:
            binding_id = spec["binding_id"]
            task_id = self.create_task(spec["specific_text"], f"crawled-{spec['library_id']}")
            preview = self.local.preview(self.actor, task_id)
            self.assertEqual(len(preview["routes"]), 1, spec["library_id"])
            self.assertEqual([option["binding_id"] for option in preview["routes"][0]["options"]],
                             [binding_id], spec["library_id"])
            queued = self.run_auto(self.local, task_id, f"terms:crawled-{spec['library_id']}:1")
            self.assertEqual(queued["source_count"], 1, spec["library_id"])
            candidates = self.local.candidates(self.actor, task_id)
            self.assertEqual(candidates["raw_hit_count"], 3, spec["library_id"])
            self.assertTrue(all(row["binding_id"] == binding_id
                                for row in candidates["items"]), spec["library_id"])
            self.assertEqual({row["library_id"] for row in candidates["items"]},
                             {spec["library_id"]}, spec["library_id"])

    def test_guided_rule_resolves_across_types_without_exact_query_vector(self):
        # "宠物" is a shared word rule for dogs and cats but not cars or scenery,
        # so only the two animal type indexes can encode it.
        task_id = self.create_task("宠物", "crawled-pet-rule")
        preview = self.local.preview(self.actor, task_id)
        self.assertEqual({option["binding_id"] for option in preview["routes"][0]["options"]},
                         {BINDING_BY_TYPE["dogs"], BINDING_BY_TYPE["cats"]})
        queued = self.run_auto(self.local, task_id, "terms:crawled-pet:1")
        self.assertEqual(queued["source_count"], 2)
        candidates = self.local.candidates(self.actor, task_id)
        self.assertEqual(candidates["raw_hit_count"], 6)
        self.assertEqual({row["binding_id"] for row in candidates["items"]},
                         {BINDING_BY_TYPE["dogs"], BINDING_BY_TYPE["cats"]})


if __name__ == "__main__":
    unittest.main()