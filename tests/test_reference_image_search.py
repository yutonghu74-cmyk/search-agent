from copy import deepcopy
from io import BytesIO
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from PIL import Image

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.first_search_local import LocalFirstSearch
from dsh_retrieval.integration_models import QueryVector
from dsh_retrieval.local_catalog import build_local_catalog
from dsh_retrieval.model_runtime import ModelRuntime
from dsh_retrieval.models import Actor
from dsh_retrieval.query_images import QueryImageService
from dsh_retrieval.service import TaskService
from draft_helpers import BODY, confirmation


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"


class ReferenceImageSearchTest(unittest.TestCase):
    def test_real_plan_uses_image_vector_and_exact_exclusion(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "catalog.json"
            capabilities_path = root / "capabilities.json"
            fixture_path = root / "fixture.json"
            shutil.copyfile(CONFIG / "local-first-search-catalog.example.json", source)
            shutil.copyfile(CONFIG / "local-first-search-capabilities.example.json", capabilities_path)
            shutil.copyfile(CONFIG / "local-first-search-guided.example.json", fixture_path)

            for path in (source, capabilities_path):
                value = json.loads(path.read_text())
                bindings = ([binding for library in value["libraries"] for binding in library["bindings"]]
                            if "libraries" in value else [item["binding"] for item in value["items"]])
                for binding in bindings:
                    binding["encoder_ref"] = "local-clip:test-image"
                path.write_text(json.dumps(value))

            ledger = root / "ledger.sqlite3"
            projects = LocalProjectAccess([("tenant-a", "user-a", "demo", "default")])
            repository = SQLiteTaskRepository(ledger)
            images = QueryImageService(projects, ledger)
            tasks = TaskService(repository, projects, images)
            actor = Actor("tenant-a", "user-a")
            stream = BytesIO()
            Image.new("RGB", (8, 6), "red").save(stream, "PNG")
            reference, _ = images.create(actor, "demo", "reference", stream.getvalue(), "image/png")

            fixture = json.loads(fixture_path.read_text())
            fixture["query_vectors"] = []
            fixture.pop("query_rules", None)
            for record in fixture["records"]:
                for media in record["media"]:
                    if media["content_digest"] is None:
                        media["content_digest"] = "sha256:" + hashlib.sha256(
                            media["media_unit_id"].encode()).hexdigest()
            fixture["records"][0]["media"][0]["content_digest"] = reference["digest"]
            fixture_path.write_text(json.dumps(fixture))

            body = deepcopy(BODY)
            body["query_spec"]["raw_request"] = "找和参考图相似的动物图片"
            body["query_spec"]["query_input"] = {
                "type": "reference_image", "query_image_id": reference["query_image_id"],
                "digest": reference["digest"], "exclude_exact_content": True}
            task, _ = tasks.create_task(actor, "task", confirmation(tasks, actor, body=body))

            class Encoder:
                encoder_ref = "local-clip:test-image"
                dimension = 2
                image_calls = 0

                def __init__(self, model_path):
                    pass

                def encode_query(self, text, binding):
                    raise AssertionError("reference-image search must not call text encoding")

                def encode_image_bytes(self, content, binding):
                    self.image_calls += 1
                    self.assert_binding = binding
                    return QueryVector(self.encoder_ref, (1.0, 0.0))

            configs = build_local_catalog(source, root / "catalog.sqlite3", ModelRuntime())
            with patch("dsh_retrieval.local_clip.LocalCLIPEncoder", Encoder):
                local = LocalFirstSearch(tasks, configs, ledger_path=ledger, source_path=source,
                    capabilities_path=capabilities_path, fixture_path=fixture_path,
                    cursor_secret=b"reference-image-test-cursor-secret-123",
                    clip_model_path=root / "model")
            try:
                preview = local.preview(actor, task["task_id"])
                self.assertEqual(len(preview["routes"]), 1)
                self.assertTrue(preview["routes"][0]["options"])
                self.assertEqual(local.auto_start(actor, task["task_id"], key="start")["status"], "queued")
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if local.progress(actor, task["task_id"])["pagination_stopped"]:
                        break
                    time.sleep(.01)
                manifest = local.preview_manifest(actor, task["task_id"])
                self.assertGreater(manifest["candidate_count"], 0)
                self.assertNotIn(reference["digest"],
                                 {item["content_digest"] for item in manifest["candidates"]})
                self.assertGreater(local.clip_encoder.image_calls, 0)
            finally:
                local.close()


if __name__ == "__main__":
    unittest.main()
