from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.api import create_handler
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.query_images import QueryImageService
from dsh_retrieval.service import InMemoryTaskRepository, TaskService
from draft_helpers import BODY
from copy import deepcopy


def image_bytes(kind="PNG", color="red"):
    stream = BytesIO()
    Image.new("RGB", (12, 8), color).save(stream, kind)
    return stream.getvalue()


class QueryImageServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.projects = LocalProjectAccess([
            ("tenant-a", "user-a", "demo", "default"),
            ("tenant-a", "user-b", "demo", "default"),
        ])
        # The query-image table is a recognized sibling of the task ledger.
        SQLiteTaskRepository(self.path)
        self.service = QueryImageService(self.projects, self.path)
        self.actor = Actor("tenant-a", "user-a")

    def tearDown(self):
        self.temp.cleanup()

    def test_immutable_upload_is_idempotent_and_survives_restart(self):
        content = image_bytes()
        created, replayed = self.service.create(
            self.actor, "demo", "upload-1", content, "image/png")
        self.assertFalse(replayed)
        self.assertEqual((created["status"], created["width"], created["height"], created["format"]),
                         ("READY", 12, 8, "PNG"))
        again, replayed = self.service.create(
            self.actor, "demo", "upload-1", content, "image/png")
        self.assertTrue(replayed)
        self.assertEqual(again["query_image_id"], created["query_image_id"])

        reopened = QueryImageService(self.projects, self.path)
        self.assertEqual(reopened.get(self.actor, "demo", created["query_image_id"])["digest"],
                         created["digest"])
        self.assertEqual(reopened.preview(self.actor, "demo", created["query_image_id"]),
                         ("image/png", content))
        # A task repository can still reopen the shared local ledger.
        SQLiteTaskRepository(self.path)

    def test_rejects_spoofed_invalid_and_cross_user_reads(self):
        with self.assertRaises(ServiceError) as mismatch:
            self.service.create(self.actor, "demo", "bad-type", image_bytes(), "image/jpeg")
        self.assertEqual(mismatch.exception.code, "QUERY_IMAGE_TYPE_MISMATCH")
        with self.assertRaises(ServiceError) as invalid:
            self.service.create(self.actor, "demo", "bad-content", b"not an image", "image/png")
        self.assertEqual(invalid.exception.code, "QUERY_IMAGE_INVALID")

        created, _ = self.service.create(
            self.actor, "demo", "private", image_bytes(), "image/png")
        with self.assertRaises(ServiceError) as hidden:
            self.service.get(Actor("tenant-a", "user-b"), "demo", created["query_image_id"])
        self.assertEqual(hidden.exception.code, "NOT_FOUND")
        with self.assertRaises(ServiceError) as conflict:
            self.service.create(self.actor, "demo", "private", image_bytes(color="blue"), "image/png")
        self.assertEqual(conflict.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_draft_freezes_owned_image_id_digest_and_exclusion_choice(self):
        content = image_bytes()
        created, _ = self.service.create(
            self.actor, "demo", "draft-reference", content, "image/png")
        tasks = TaskService(InMemoryTaskRepository(), self.projects, self.service)
        body = deepcopy(BODY)
        body["query_spec"]["query_input"] = {
            "type": "reference_image", "query_image_id": created["query_image_id"],
            "digest": created["digest"], "exclude_exact_content": True}
        draft, _ = tasks.save_draft(self.actor, "demo", "draft", body)
        self.assertEqual(draft["proposal"]["query_spec"]["query_input"],
                         body["query_spec"]["query_input"])
        changed = deepcopy(body)
        changed["query_spec"]["query_input"]["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(ServiceError) as stale:
            tasks.save_draft(self.actor, "demo", "changed", changed)
        self.assertEqual(stale.exception.code, "QUERY_IMAGE_CHANGED")


class QueryImageApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        projects = LocalProjectAccess([("tenant-a", "user-a", "demo", "default")])
        service = TaskService(InMemoryTaskRepository(), projects)
        cls.images = QueryImageService(projects)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(
            service, query_images=cls.images))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, *, data=None, content_type=None, key=None):
        headers = {"X-Tenant-Id": "tenant-a", "X-User-Id": "user-a"}
        if content_type:
            headers["Content-Type"] = content_type
        if key:
            headers["Idempotency-Key"] = key
        request = Request(self.base + path, data=data, headers=headers,
                          method="POST" if data is not None else "GET")
        try:
            response = urlopen(request)
        except HTTPError as error:
            return error.code, json.loads(error.read()), error.headers
        body = response.read()
        if response.headers.get_content_type() == "application/json":
            body = json.loads(body)
        return response.status, body, response.headers

    def test_upload_status_and_controlled_preview(self):
        content = image_bytes()
        status, body, headers = self.request(
            "/v1/retrieval/projects/demo/query-images", data=content,
            content_type="image/png", key="http-upload")
        self.assertEqual(status, 201)
        self.assertEqual(body["status"], "READY")
        self.assertNotIn("content", body)
        identifier = body["query_image_id"]

        status, metadata, _ = self.request(
            f"/v1/retrieval/projects/demo/query-images/{identifier}")
        self.assertEqual((status, metadata["digest"]), (200, body["digest"]))
        status, preview, preview_headers = self.request(body["preview_url"])
        self.assertEqual((status, preview), (200, content))
        self.assertEqual(preview_headers["Cache-Control"], "private, no-store")
        self.assertEqual(preview_headers["X-Content-Type-Options"], "nosniff")

        status, replay, replay_headers = self.request(
            "/v1/retrieval/projects/demo/query-images", data=content,
            content_type="image/png", key="http-upload")
        self.assertEqual((status, replay["query_image_id"]), (200, identifier))
        self.assertEqual(replay_headers["Idempotent-Replayed"], "true")


if __name__ == "__main__":
    unittest.main()
