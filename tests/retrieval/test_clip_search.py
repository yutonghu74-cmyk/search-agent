from dataclasses import asdict, replace
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import urllib.error

from dsh_retrieval.adapters.retrieval.clip_search import (
    CLIPSearchEndpoint, CLIPTextSearchClient, CLIPTextSearchRequest,
    load_clip_search_endpoints,
)
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import AccessContext, IndexBinding
from dsh_retrieval.models import Actor
from dsh_retrieval.physical_index_registry import PhysicalIndex, PhysicalIndexRegistry


class FakeResponse(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload, ensure_ascii=False).encode())
        self.status = status


class FakeTransport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, tuple):
            return FakeResponse(reply[1], reply[0])
        return FakeResponse(reply)


class Authority:
    def __init__(self, context):
        self.context = context
        self.calls = 0
        self.revoke_after = None

    def require_access(self, context, resource_ref, action):
        self.calls += 1
        if (context != self.context or resource_ref != "library:clip-images"
                or action != "search" or self.revoke_after == self.calls):
            raise ServiceError("ACCESS_DENIED", "Denied", 403)


def response(*, page=1, has_next=False, dataset_version="v1", match_id=1):
    dataset_key = "mock-dataset-key"
    return {
        "success": True,
        "query_type": "text",
        "dataset_mode": "custom",
        "datasets": [{"key": dataset_key, "version": dataset_version}],
        "folder_filter": ["春季上新"],
        "total_matches_found": 2,
        "max_results": 100,
        "min_similarity": 0.1,
        "group_size": 2,
        "total_groups": 1,
        "current_page": page,
        "has_next": has_next,
        "has_previous": page > 1,
        "matches": [{
            "id": match_id,
            "similarity_score": 0.37,
            "filename": f"IMG_{match_id}.jpg",
            "full_path": f"/mock/images/IMG_{match_id}.jpg",
            "web_path": f"/local_images/mock/IMG_{match_id}.jpg",
            "folder": "春季上新",
            "app_name": "mock",
            "dataset_name": "mock-images",
            "dataset_key": dataset_key,
            "shard": "shard_0.db",
        }],
        "model": "chinese-clip-vit-huge-patch14",
        "served_from_cache": page > 1,
        "search_scope": "指定数据集",
        "message": "搜索成功",
        "project_name": "mock-project",
    }


class CLIPTextSearchClientTest(unittest.TestCase):
    def setUp(self):
        self.binding = IndexBinding("clip-v1", "clip-images", "index-v1", "clip-model@1", 4, "IP")
        self.target = PhysicalIndex(self.binding, "clip-service", "none", "mock-images", "path",
            "vector", "chinese-clip-vit-huge-patch14", "unknown", "clip-text-77@1",
            "fashion-only", "offset", "live", 100, 1000)
        self.registry = PhysicalIndexRegistry((self.target,))
        self.endpoint = CLIPSearchEndpoint(self.binding, "chinese-clip-vit-huge-patch14",
            "mock-project", "/mock/clip/dataset", ("shard_0.db", "shard_1.db"),
            (("fashion-only", ("春季上新",)),), "fashion-only", 100, 0.1, 40,
            "http://clip-search.invalid:4444")
        self.context = AccessContext(Actor("tenant", "owner"), "task", "search", "auth-v1")
        self.authority = Authority(self.context)
        self.request = CLIPTextSearchRequest("plan", 1, self.binding, "红色连衣裙",
                                             "fashion-only", 2)

    def client(self, replies, authority=None):
        transport = FakeTransport(replies)
        client = CLIPTextSearchClient(self.registry, authority or self.authority, (self.endpoint,),
            cursor_secret=b"x" * 32, opener=transport)
        return client, transport

    def assert_code(self, code, callback):
        with self.assertRaises(ServiceError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_declared_group_rejects_hits_from_other_shards(self):
        self.endpoint = replace(self.endpoint, declared_shards=('shard_0.db', 'shard_1.db'))
        valid, _ = self.client([response()])
        self.assertEqual(len(valid.search_text(self.context, self.request).matches), 1)
        payload = response()
        payload['matches'][0]['shard'] = 'another_shard.db'
        invalid, _ = self.client([payload])
        self.assert_code('SEARCH_RESPONSE_INVALID', lambda: invalid.search_text(self.context, self.request))

    def test_builds_bounded_form_and_projects_source_fields(self):
        client, transport = self.client([response()])
        batch = client.search_text(self.context, self.request)
        self.assertEqual(batch.page.consistency, "live")
        self.assertEqual(batch.page.stop_reason, "depth_limit")
        self.assertIsNone(batch.page.snapshot_ref)
        self.assertEqual(batch.matches[0].dataset_version, "v1")
        self.assertEqual(batch.matches[0].hit, batch.page.hits[0])
        http_request, timeout = transport.requests[0]
        self.assertEqual(http_request.full_url, "http://clip-search.invalid:4444/search-by-text")
        self.assertEqual(timeout, 95)
        for expected in ("红色连衣裙", "mock-project", "/mock/clip/dataset",
                         "shard_0.db,shard_1.db", "春季上新"):
            self.assertIn(expected.encode(), http_request.data)
        self.assertNotIn(b"vector", http_request.data)

    def test_signed_pagination_binds_query_and_rejects_dataset_change(self):
        client, _ = self.client([response(has_next=True), response(page=2, match_id=2)])
        first = client.search_text(self.context, self.request)
        self.assertEqual(first.page.stop_reason, "more_available")
        second_request = replace(self.request, cursor=first.page.next_cursor)
        second = client.search_text(self.context, second_request)
        self.assertEqual(second.matches[0].filename, "IMG_2.jpg")
        self.assertEqual(second.page.stop_reason, "depth_limit")

        changed, _ = self.client([response(has_next=True),
                                  response(page=2, dataset_version="v2", match_id=2)])
        first = changed.search_text(self.context, self.request)
        self.assert_code("INDEX_SNAPSHOT_EXPIRED", lambda: changed.search_text(
            self.context, replace(self.request, cursor=first.page.next_cursor)))

        client, transport = self.client([])
        self.assert_code("CURSOR_INVALID", lambda: client.search_text(
            self.context, replace(self.request, cursor="fabricated")))
        self.assertFalse(transport.requests)

    def test_unknown_binding_filter_and_authorization_fail_before_network(self):
        client, transport = self.client([])
        self.assert_code("FILTER_UNAVAILABLE", lambda: client.search_text(
            self.context, replace(self.request, filter_template_ref="arbitrary")))
        self.assert_code("INDEX_BINDING_MISMATCH", lambda: client.search_text(
            self.context, replace(self.request, binding=replace(self.binding, index_version="v2"))))
        denied = Authority(replace(self.context, task_id="other"))
        client, denied_transport = self.client([], denied)
        self.assert_code("ACCESS_DENIED", lambda: client.search_text(self.context, self.request))
        self.assertFalse(transport.requests)
        self.assertFalse(denied_transport.requests)

    def test_loading_transport_and_bad_response_are_sanitized(self):
        for reply, code in (((202, {"success": False, "status": "loading"}),
                             "SEARCH_SOURCE_LOADING"),
                            (urllib.error.URLError("private internal failure"),
                             "SEARCH_REQUEST_FAILED"),
                            ({**response(), "model": "wrong"}, "SEARCH_RESPONSE_INVALID")):
            with self.subTest(code=code):
                client, _ = self.client([reply])
                self.assert_code(code, lambda: client.search_text(self.context, self.request))

    def test_reauthorization_after_response_discards_revoked_result(self):
        authority = Authority(self.context)
        authority.revoke_after = 2
        client, transport = self.client([response()], authority)
        self.assert_code("ACCESS_DENIED", lambda: client.search_text(self.context, self.request))
        self.assertEqual(len(transport.requests), 1)

    def test_private_loader_uses_environment_and_rejects_guesses(self):
        entry = {
            "binding": asdict(self.binding),
            "model_id": "chinese-clip-vit-huge-patch14",
            "project_name": "mock-project",
            "dataset_path": "/mock/clip/dataset",
            "shard_paths": ["shard_0.db", "shard_1.db"],
            "filter_templates": {"fashion-only": ["春季上新"]},
            "default_filter_template_ref": "fashion-only",
            "max_results": 100,
            "min_similarity": 0.1,
            "wait_seconds": 40,
            "base_url_env": "CLIP_BASE_URL",
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "clip.json"
            path.write_text(json.dumps({"schema": "clip-search-endpoints@1",
                                        "entries": [entry]}))
            loaded = load_clip_search_endpoints(path, self.registry,
                environ={"CLIP_BASE_URL": "http://clip-search.invalid:4444"})
            self.assertEqual(loaded, (self.endpoint,))
            catalog = dict(schema='image-shard-catalog@1', datasets=[dict(
                library_id=self.binding.library_id, index_version=self.binding.index_version,
                shards=[dict(shard_id=str(i), file_name=f'shard_{i}.db', status='ready')
                        for i in range(3)])])
            declared = dict(schema='clip-search-endpoints@1', entries=[entry], shard_catalog=catalog)
            path.write_text(json.dumps(declared))
            self.assert_code('SHARD_COVERAGE_INCOMPLETE', lambda: load_clip_search_endpoints(
                path, self.registry, environ={'CLIP_BASE_URL': 'http://clip-search.invalid:4444'}))
            catalog['datasets'][0]['shards'].pop()
            path.write_text(json.dumps(declared))
            self.assertEqual(load_clip_search_endpoints(path, self.registry,
                environ={'CLIP_BASE_URL': 'http://clip-search.invalid:4444'}),
                (replace(loaded[0], declared_shards=('shard_0.db', 'shard_1.db')),))

            self.assertNotIn("clip-search.invalid", repr(loaded))
            for broken in ({**entry, "model_id": "guessed-model"},
                           {**entry, "dataset_path": "relative/path"},
                           {**entry, "filter_templates": {"bad": "folder"}}):
                path.write_text(json.dumps({"schema": "clip-search-endpoints@1",
                                            "entries": [broken]}))
                self.assert_code("CLIP_SEARCH_CONFIG_INVALID", lambda: load_clip_search_endpoints(
                    path, self.registry, environ={"CLIP_BASE_URL": "http://clip.invalid"}))
            path.write_text(json.dumps({"schema": "clip-search-endpoints@1", "entries": [entry]}))
            self.assert_code("CLIP_SEARCH_CONFIG_INVALID", lambda: load_clip_search_endpoints(
                path, self.registry, environ={}))


if __name__ == "__main__":
    unittest.main()
