"""Contract adapter for the internal combined CLIP text-search service.

The upstream service owns both text embedding and Milvus search, so this client
does not implement the separate EmbeddingProvider/VectorSearchGateway ports.
It validates the HTTP boundary without inventing a query vector. Runtime wiring
must use a combined-search coordinator and persist returned materialization data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from http.client import HTTPException
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
from types import MappingProxyType
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from ...errors import ServiceError
from ...integration_models import AccessContext, IndexBinding, SearchHit, SearchPage
from ...intent_model import NoRedirect
from ...physical_index_registry import PhysicalIndexRegistry


def _invalid():
    raise ServiceError("CLIP_SEARCH_CONFIG_INVALID", "Invalid trusted CLIP search configuration", 422)


@dataclass(frozen=True, slots=True)
class CLIPSearchEndpoint:
    binding: IndexBinding
    model_id: str
    project_name: str
    dataset_path: str
    shard_paths: tuple[str, ...]
    filter_templates: tuple[tuple[str, tuple[str, ...]], ...]
    default_filter_template_ref: str
    max_results: int
    min_similarity: float
    wait_seconds: int
    base_url: str = field(repr=False)

    def validate(self, registry: PhysicalIndexRegistry):
        if type(self.binding) is not IndexBinding:
            _invalid()
        target = registry.resolve(self.binding)
        parsed = urlsplit(self.base_url)
        template_names = [name for name, _ in self.filter_templates]
        if (target.model_id != self.model_id
                or not isinstance(self.model_id, str) or not self.model_id
                or not isinstance(self.project_name, str) or not self.project_name
                or not isinstance(self.dataset_path, str) or not self.dataset_path.startswith("/")
                or not isinstance(self.shard_paths, tuple) or not 1 <= len(self.shard_paths) <= 4
                or any(not isinstance(path, str) or not path.endswith(".db") or "/" in path
                       for path in self.shard_paths)
                or not isinstance(self.filter_templates, tuple) or not self.filter_templates
                or len(template_names) != len(set(template_names))
                or self.default_filter_template_ref not in template_names
                or any(not isinstance(name, str) or not name
                       or not isinstance(folders, tuple)
                       or any(not isinstance(folder, str) or not folder for folder in folders)
                       for name, folders in self.filter_templates)
                or type(self.max_results) is not int or not 1 <= self.max_results <= 1000
                or type(self.min_similarity) not in (int, float)
                or not math.isfinite(self.min_similarity)
                or not -1 <= self.min_similarity <= 1
                or type(self.wait_seconds) is not int or not 0 <= self.wait_seconds <= 90
                or parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            _invalid()


@dataclass(frozen=True, slots=True)
class CLIPTextSearchRequest:
    plan_id: str
    plan_version: int
    binding: IndexBinding
    query_text: str
    filter_template_ref: str
    batch_size: int
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class CLIPMatch:
    hit: SearchHit
    filename: str
    full_path: str
    web_path: str
    folder: str
    app_name: str
    dataset_name: str
    dataset_key: str
    dataset_version: str
    shard: str


@dataclass(frozen=True, slots=True)
class CLIPSearchBatch:
    page: SearchPage
    matches: tuple[CLIPMatch, ...]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def load_clip_search_endpoints(path, registry: PhysicalIndexRegistry, *, environ=None):
    """Load bounded private declarations; endpoint values come from env vars."""
    env = os.environ if environ is None else environ
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(1_000_001)
        if len(raw) > 1_000_000:
            _invalid()
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if (type(value) is not dict or set(value) != {"schema", "entries"}
                or value["schema"] != "clip-search-endpoints@1"
                or type(value["entries"]) is not list or not 1 <= len(value["entries"]) <= 256):
            _invalid()
        endpoints = []
        required = {"binding", "model_id", "project_name", "dataset_path", "shard_paths",
                    "filter_templates", "default_filter_template_ref", "max_results", "min_similarity", "wait_seconds",
                    "base_url_env"}
        for item in value["entries"]:
            if (type(item) is not dict or set(item) != required
                    or type(item["binding"]) is not dict
                    or set(item["binding"]) != set(IndexBinding.__dataclass_fields__)
                    or not isinstance(item["base_url_env"], str)
                    or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item["base_url_env"]) is None
                    or type(item["shard_paths"]) is not list
                    or type(item["filter_templates"]) is not dict
                    or any(type(folders) is not list
                           for folders in item["filter_templates"].values())):
                _invalid()
            endpoint = CLIPSearchEndpoint(
                binding=IndexBinding(**item["binding"]), model_id=item["model_id"],
                project_name=item["project_name"], dataset_path=item["dataset_path"],
                shard_paths=tuple(item["shard_paths"]),
                filter_templates=tuple((name, tuple(folders))
                                       for name, folders in item["filter_templates"].items()),
                default_filter_template_ref=item["default_filter_template_ref"],
                max_results=item["max_results"], min_similarity=item["min_similarity"],
                wait_seconds=item["wait_seconds"], base_url=env.get(item["base_url_env"], ""))
            endpoint.validate(registry)
            endpoints.append(endpoint)
        if len({endpoint.binding.binding_id for endpoint in endpoints}) != len(endpoints):
            _invalid()
        return tuple(endpoints)
    except ServiceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        _invalid()


class CLIPTextSearchClient:
    """Strict client for POST /search-by-text using opaque signed page cursors."""

    def __init__(self, registry: PhysicalIndexRegistry, authorization, endpoints,
                 *, cursor_secret: bytes, opener=None, timeout=95):
        if (type(registry) is not PhysicalIndexRegistry or type(endpoints) is not tuple
                or not endpoints or not isinstance(cursor_secret, bytes) or len(cursor_secret) < 32
                or type(timeout) not in (int, float) or not 0 < timeout <= 120):
            _invalid()
        selected = {}
        for endpoint in endpoints:
            if type(endpoint) is not CLIPSearchEndpoint:
                _invalid()
            endpoint.validate(registry)
            if endpoint.binding.binding_id in selected:
                _invalid()
            selected[endpoint.binding.binding_id] = endpoint
        self.registry = registry
        self.authorization = authorization
        self.endpoints = MappingProxyType(selected)
        self.cursor_secret = cursor_secret
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.timeout = timeout

    @staticmethod
    def _validate_request(request: CLIPTextSearchRequest):
        if (type(request) is not CLIPTextSearchRequest
                or not isinstance(request.plan_id, str) or not request.plan_id
                or type(request.plan_version) is not int or request.plan_version < 1
                or type(request.binding) is not IndexBinding
                or not isinstance(request.query_text, str)
                or not 1 <= len(request.query_text.strip()) <= 4000
                or not isinstance(request.filter_template_ref, str) or not request.filter_template_ref
                or type(request.batch_size) is not int or not 1 <= request.batch_size <= 100
                or request.cursor is not None and (not isinstance(request.cursor, str) or not request.cursor)):
            raise ServiceError("INVALID_SEARCH_REQUEST", "Invalid combined CLIP search request", 422)

    @staticmethod
    def _fingerprint(context: AccessContext, request: CLIPTextSearchRequest) -> str:
        data = (context.actor.tenant_id, context.actor.user_id, context.task_id,
                context.authorization_version, request.plan_id, request.plan_version,
                request.binding, request.query_text, request.filter_template_ref, request.batch_size)
        return hashlib.sha256(repr(data).encode()).hexdigest()

    def _encode_cursor(self, fingerprint: str, page: int, source_digest: str) -> str:
        payload = json.dumps({"fingerprint": fingerprint, "page": page,
                              "source_digest": source_digest},
                             sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self.cursor_secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")

    def _decode_cursor(self, cursor: str | None, fingerprint: str):
        if cursor is None:
            return 1, None
        try:
            if len(cursor) > 4096:
                raise ValueError("cursor length")
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(signature, hmac.new(self.cursor_secret, payload,
                                                            hashlib.sha256).digest()):
                raise ValueError("cursor signature")
            value = json.loads(payload)
            if (type(value) is not dict or value.get("fingerprint") != fingerprint
                    or type(value.get("page")) is not int or value["page"] < 2
                    or not isinstance(value.get("source_digest"), str)
                    or not value["source_digest"]):
                raise ValueError("cursor scope")
            return value["page"], value["source_digest"]
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, binascii.Error):
            raise ServiceError("CURSOR_INVALID", "Cursor is invalid or stale", 409) from None

    @staticmethod
    def _multipart(fields, boundary):
        chunks = []
        for name, value in fields:
            chunks.extend((f"--{boundary}\r\n".encode(),
                           f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                           str(value).encode("utf-8"), b"\r\n"))
        chunks.append(f"--{boundary}--\r\n".encode())
        return b"".join(chunks)

    @staticmethod
    def _source_digest(datasets):
        try:
            identity = tuple(sorted((item["key"], item["version"]) for item in datasets))
        except (KeyError, TypeError):
            raise ValueError("dataset identity") from None
        if not identity or any(not all(isinstance(value, str) and value for value in pair)
                               for pair in identity):
            raise ValueError("dataset identity")
        return hashlib.sha256(repr(identity).encode()).hexdigest(), dict(identity)

    def search_text(self, context: AccessContext, request: CLIPTextSearchRequest) -> CLIPSearchBatch:
        self._validate_request(request)
        self.registry.resolve(request.binding)
        endpoint = self.endpoints.get(request.binding.binding_id)
        if endpoint is None or endpoint.binding != request.binding:
            raise ServiceError("QUERY_UNSUPPORTED", "No trusted CLIP endpoint for this index", 422)
        filters = dict(endpoint.filter_templates)
        if request.filter_template_ref not in filters:
            raise ServiceError("FILTER_UNAVAILABLE", "Unknown server filter template", 409)
        self.authorization.require_access(context, "library:" + request.binding.library_id, "search")
        fingerprint = self._fingerprint(context, request)
        page_number, expected_source = self._decode_cursor(request.cursor, fingerprint)
        boundary = "dsh-" + fingerprint[:32]
        fields = [("query", request.query_text), ("project_name", endpoint.project_name),
                  ("dataset_path", endpoint.dataset_path),
                  ("shard_paths", ",".join(endpoint.shard_paths)),
                  ("max_results", endpoint.max_results),
                  ("min_similarity", endpoint.min_similarity),
                  ("group_size", request.batch_size), ("page", page_number),
                  ("wait_seconds", endpoint.wait_seconds)]
        if filters[request.filter_template_ref]:
            fields.append(("folders", ",".join(filters[request.filter_template_ref])))
        http_request = urllib.request.Request(endpoint.base_url.rstrip("/") + "/search-by-text",
            data=self._multipart(fields, boundary),
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary}, method="POST")
        try:
            with self.opener.open(http_request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise ValueError("oversized response")
            payload = json.loads(raw)
            if status == 202:
                raise ServiceError("SEARCH_SOURCE_LOADING", "CLIP dataset is loading", 503)
            if status != 200:
                raise ServiceError("SEARCH_REQUEST_FAILED", "CLIP search request failed", 502)
            batch = self._parse_response(endpoint, request, payload, page_number,
                                         expected_source, fingerprint)
        except ServiceError:
            raise
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError,
                OSError, HTTPException):
            raise ServiceError("SEARCH_REQUEST_FAILED", "CLIP search request failed", 502) from None
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeDecodeError):
            raise ServiceError("SEARCH_RESPONSE_INVALID", "CLIP search response is incompatible", 502) from None
        self.authorization.require_access(context, "library:" + request.binding.library_id, "search")
        return batch

    def _parse_response(self, endpoint, request, payload, page_number,
                        expected_source, fingerprint):
        if (type(payload) is not dict or payload.get("success") is not True
                or payload.get("query_type") != "text" or payload.get("model") != endpoint.model_id
                or payload.get("current_page") != page_number
                or payload.get("group_size") != request.batch_size
                or type(payload.get("has_next")) is not bool
                or type(payload.get("matches")) is not list
                or len(payload["matches"]) > request.batch_size
                or type(payload.get("datasets")) is not list):
            raise ValueError("response shape")
        source_digest, versions = self._source_digest(payload["datasets"])
        if expected_source is not None and expected_source != source_digest:
            raise ServiceError("INDEX_SNAPSHOT_EXPIRED", "CLIP dataset changed during pagination", 409)
        matches, seen = [], set()
        required = {"id", "similarity_score", "filename", "full_path", "web_path", "folder",
                    "app_name", "dataset_name", "dataset_key", "shard"}
        for item in payload["matches"]:
            if (type(item) is not dict or not required.issubset(item)
                    or type(item["id"]) is not int
                    or type(item["similarity_score"]) not in (int, float)
                    or not math.isfinite(item["similarity_score"])
                    or any(not isinstance(item[name], str) or not item[name]
                           for name in required - {"id", "similarity_score"})
                    or item["dataset_key"] not in versions):
                raise ValueError("match shape")
            identity = hashlib.sha256(repr((request.binding.binding_id,
                request.binding.index_version, item["dataset_key"], item["shard"],
                item["full_path"])).encode()).hexdigest()
            if identity in seen:
                raise ValueError("duplicate match")
            seen.add(identity)
            hit = SearchHit(request.binding, identity, float(item["similarity_score"]))
            matches.append(CLIPMatch(hit, item["filename"], item["full_path"], item["web_path"],
                item["folder"], item["app_name"], item["dataset_name"], item["dataset_key"],
                versions[item["dataset_key"]], item["shard"]))
        next_cursor = (self._encode_cursor(fingerprint, page_number + 1, source_digest)
                       if payload["has_next"] else None)
        # The upstream cache has a finite TTL and exposes no immutable snapshot.
        # A final page therefore means no more data in this bounded call, not
        # authoritative source exhaustion.
        stop_reason = "more_available" if next_cursor else "depth_limit"
        page = SearchPage(tuple(match.hit for match in matches), next_cursor,
                          stop_reason, "live", None)
        return CLIPSearchBatch(page, tuple(matches))
