"""Deterministic, in-process fixtures for the existing retrieval ports.

No database, network, learned embedding or similarity search is performed.
Trusted test setup supplies approved requests and ordered hits per query. Never
expose fixture construction as an approval API or use it for production auth.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import hmac
import math
import secrets

from ...errors import ServiceError
from ...integration_models import (
    AccessContext, IndexBinding, Library, MediaRecord, MediaResolution,
    QueryVector, SearchHit, SearchPage, SearchRequest,
)
from ...ports import AuthorizationService


@dataclass(frozen=True)
class ApprovedFixture:
    context: AccessContext
    request: SearchRequest
    hits: tuple[SearchHit, ...]
    depth_limit: int = 1000


class SimulatedRetrieval:
    """CatalogRepository, EmbeddingProvider, VectorSearchGateway, MediaRepository.

Authorization resource refs are library:<id> (discover/search) and trusted media
refs (read). All authority is delegated to the injected service on every call.
Snapshot/cursor state is local to this instance; process recovery is unsupported.
"""

    mode = "SIMULATED"

    def __init__(self, authorization: AuthorizationService, *,
                 libraries: tuple[Library, ...],
                 vectors: dict[tuple[IndexBinding, str], QueryVector],
                 approved: tuple[ApprovedFixture, ...],
                 mappings: dict[tuple[IndexBinding, str | int], tuple[MediaRecord, ...]]):
        self.authorization = authorization
        self._libraries = tuple(libraries)
        self._bindings = {b for lib in libraries for b in lib.bindings}
        if len({lib.library_id for lib in libraries}) != len(libraries):
            raise ValueError("duplicate library")
        if any(b.library_id != lib.library_id for lib in libraries for b in lib.bindings):
            raise ValueError("binding/library mismatch")
        if len({b.binding_id for b in self._bindings}) != len(self._bindings):
            raise ValueError("ambiguous binding")
        self._vectors = dict(vectors)
        self._mappings = {key: tuple(value) for key, value in mappings.items()}
        self._approved = {}
        self._expired: set[IndexBinding] = set()
        self._cursors: dict[str, tuple[AccessContext, SearchRequest, int]] = {}
        self._secret = secrets.token_bytes(32)
        for fixture in approved:
            request = fixture.request
            self._validate_request(request)
            if request.cursor is not None or type(fixture.depth_limit) is not int or fixture.depth_limit < 1:
                raise ValueError("invalid fixture limits/cursor")
            if any(hit.binding != request.binding or not math.isfinite(hit.score)
                   or type(hit.vector_id) not in (str, int) for hit in fixture.hits):
                raise ValueError("invalid fixture hit")
            key = (fixture.context, request.plan_id, request.plan_version, request.binding.binding_id)
            if key in self._approved:
                raise ValueError("duplicate approved fixture")
            self._approved[key] = replace(fixture, hits=tuple(fixture.hits))

    def _binding(self, binding: IndexBinding) -> None:
        if binding not in self._bindings:
            raise ServiceError("INDEX_BINDING_MISMATCH", "Unknown physical binding/version", 409)
        if binding in self._expired:
            raise ServiceError("INDEX_SNAPSHOT_EXPIRED", "Simulated snapshot expired", 409)

    @staticmethod
    def _vector(binding: IndexBinding, vector: QueryVector) -> None:
        if (binding.vector_dimension < 1 or vector.encoder_ref != binding.encoder_ref
                or len(vector.values) != binding.vector_dimension
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector.values)):
            raise ServiceError("ENCODER_MISMATCH", "Incompatible query encoder/vector", 422)

    def _validate_request(self, request: SearchRequest) -> None:
        self._binding(request.binding)
        self._vector(request.binding, request.vector)
        if (type(request.batch_size) is not int or not 1 <= request.batch_size <= 100
                or type(request.plan_version) is not int or request.plan_version < 1
                or not request.plan_id or not request.filter_template_ref):
            raise ServiceError("INVALID_SEARCH_REQUEST", "Invalid search bounds or plan", 422)

    def _access(self, context: AccessContext, library_id: str, action: str) -> None:
        self.authorization.require_access(context, "library:" + library_id, action)

    def find_libraries(self, context: AccessContext, topic: str, media_type: str) -> tuple[Library, ...]:
        found = []
        for library in self._libraries:
            # Authorization precedes even matching a description. Dependency errors
            # propagate; only an explicit denial hides an individual library.
            try:
                self._access(context, library.library_id, "discover")
            except ServiceError as error:
                if error.code == "ACCESS_DENIED":
                    continue
                raise
            if library.media_type == media_type and topic.casefold() in library.description.casefold():
                found.append(library)
        return tuple(sorted(found, key=lambda library: library.library_id))

    def encode_query(self, context: AccessContext, text: str, binding: IndexBinding) -> QueryVector:
        self._access(context, binding.library_id, "search")
        self._binding(binding)
        vector = self._vectors.get((binding, text))
        if vector is None:
            raise ServiceError("QUERY_UNSUPPORTED", "No simulated query fixture", 422)
        self._vector(binding, vector)
        return vector

    def expire_snapshot(self, binding: IndexBinding) -> None:
        """Trusted test control: invalidate this snapshot, never reset a cursor."""
        self._expired.add(binding)

    def search(self, context: AccessContext, request: SearchRequest) -> SearchPage:
        self._access(context, request.binding.library_id, "search")
        self._validate_request(request)
        frozen_request = replace(request, cursor=None)
        key = (context, request.plan_id, request.plan_version, request.binding.binding_id)
        fixture = self._approved.get(key)
        if fixture is None or fixture.request != frozen_request:
            raise ServiceError("PLAN_MISMATCH", "Request is not an approved fixture", 409)
        offset = 0
        if request.cursor is not None:
            saved = self._cursors.get(request.cursor)
            if saved is None or saved[:2] != (context, frozen_request):
                raise ServiceError("CURSOR_INVALID", "Cursor is unknown or out of scope", 409)
            offset = saved[2]
        end = min(offset + request.batch_size, len(fixture.hits), fixture.depth_limit)
        hits = fixture.hits[offset:end]
        cursor = None
        if end < min(len(fixture.hits), fixture.depth_limit):
            reason = "more_available"
            state = (context, frozen_request, end)
            cursor = hmac.new(self._secret, repr(state).encode(), hashlib.sha256).hexdigest()
            self._cursors[cursor] = state
        elif end < len(fixture.hits):
            reason = "depth_limit"
        else:
            reason = "exhausted"
        snapshot = "simulated:" + hashlib.sha256(repr(request.binding).encode()).hexdigest()
        return SearchPage(hits, cursor, reason, "snapshot", snapshot)

    def resolve_hits(self, context: AccessContext, hits: tuple[SearchHit, ...]) -> tuple[MediaResolution, ...]:
        result = []
        for hit in hits:
            self._access(context, hit.binding.library_id, "search")
            self._binding(hit.binding)
            records = self._mappings.get((hit.binding, hit.vector_id), ())
            if not records:
                result.append(MediaResolution(hit, None, "mapping_missing"))
                continue
            # Do not disclose even conflicting media records before authorization.
            try:
                for record in records:
                    self.authorization.require_access(context, record.authorized_media_ref, "read")
            except ServiceError as error:
                if error.code != "ACCESS_DENIED":
                    raise
                result.append(MediaResolution(hit, None, "unavailable"))
                continue
            if len(records) != 1:
                result.append(MediaResolution(hit, None, "mapping_conflict"))
            else:
                result.append(MediaResolution(hit, records[0], "resolved"))
        return tuple(result)
