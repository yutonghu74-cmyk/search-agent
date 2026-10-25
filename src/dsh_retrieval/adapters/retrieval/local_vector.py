"""Small, exact-search development connector for the existing retrieval ports.

This is not Milvus or a learned embedding service. Trusted setup supplies exact
query vectors or explicit word rules and immutable test records; it ranks vectors and
pages the resulting authorized scope. Never expose setup as an approval API.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import base64
import binascii
import hashlib
import hmac
import json
import math
import unicodedata

from ...errors import ServiceError
from ...integration_models import (
    AccessContext, IndexBinding, Library, MediaRecord, QueryVector,
    SearchHit, SearchPage, SearchRequest,
)
from ...ports import AuthorizationService
from .simulated import ApprovedFixture, SimulatedRetrieval


@dataclass(frozen=True, slots=True)
class LocalVectorRecord:
    binding: IndexBinding
    vector_id: str | int
    values: tuple[float, ...]
    scope: str
    media: tuple[MediaRecord, ...] = ()


class LocalVectorRetrieval(SimulatedRetrieval):
    """Exact local ranking with signed, restartable cursors for a fixed corpus.

    The caller still needs the authoritative approved-plan ledger. A configured
    filter template is a trusted scope allowlist, not a user expression. A cursor
    only survives restart with the same secret, corpus, templates and request.
    """

    mode = "LOCAL_EXACT"

    def __init__(self, authorization: AuthorizationService, *,
                 libraries: tuple[Library, ...],
                 query_vectors: dict[tuple[IndexBinding, str], QueryVector],
                 approved: tuple[ApprovedFixture, ...],
                 records: tuple[LocalVectorRecord, ...],
                 filter_templates: dict[str, frozenset[str]],
                 cursor_secret: bytes,
                 query_rules: tuple[tuple[IndexBinding, tuple[str, ...], QueryVector], ...] = ()):
        if not isinstance(cursor_secret, bytes) or len(cursor_secret) < 32:
            raise ValueError("stable cursor_secret must contain at least 32 bytes")
        if not filter_templates or any(not isinstance(key, str) or not key
                                       or not isinstance(scopes, frozenset)
                                       or not scopes or any(not isinstance(scope, str) or not scope
                                                            for scope in scopes)
                                       for key, scopes in filter_templates.items()):
            raise ValueError("invalid server-owned filter templates")
        bindings = {binding for library in libraries for binding in library.bindings}
        seen = set()
        mappings = {}
        for record in records:
            key = (record.binding, record.vector_id)
            if (record.binding not in bindings or type(record.vector_id) not in (str, int)
                    or not record.vector_id and type(record.vector_id) is str
                    or key in seen or not isinstance(record.scope, str) or not record.scope
                    or record.binding.metric not in ("COSINE", "IP", "L2")
                    or len(record.values) != record.binding.vector_dimension
                    or any(type(value) not in (int, float) or not math.isfinite(value)
                           for value in record.values)
                    or (record.binding.metric == "COSINE"
                        and not any(value != 0 for value in record.values))):
                raise ValueError("invalid or duplicate local vector record")
            seen.add(key)
            mappings[key] = record.media
        if any(fixture.request.filter_template_ref not in filter_templates for fixture in approved):
            raise ValueError("approved request uses unknown filter template")
        super().__init__(authorization, libraries=libraries, vectors=query_vectors,
                         approved=tuple(replace(fixture, hits=()) for fixture in approved),
                         mappings=mappings)
        for binding, tokens, vector in query_rules:
            if (binding not in bindings or not tokens or any(not isinstance(token, str) or not token
                    for token in tokens)):
                raise ValueError("invalid local query rule")
            try:
                self._vector(binding, vector)
            except ServiceError as error:
                raise ValueError("invalid local query rule vector") from error
            if binding.metric == "COSINE" and not any(value != 0 for value in vector.values):
                raise ValueError("zero-norm local query rule")
        self._query_rules = query_rules
        self._records = tuple(records)
        self._templates = dict(filter_templates)
        self._cursor_secret = cursor_secret
        snapshot_data = (
            tuple(sorted((key, tuple(sorted(scopes))) for key, scopes in filter_templates.items())),
            tuple(sorted((repr(record.binding), type(record.vector_id).__name__,
                          str(record.vector_id), record.values, record.scope)
                         for record in records)),
        )
        self._snapshot = "local:" + hashlib.sha256(repr(snapshot_data).encode()).hexdigest()

    def encode_query(self, context: AccessContext, text: str, binding: IndexBinding) -> QueryVector:
        self._access(context, binding.library_id, "search")
        self._binding(binding)
        exact = self._vectors.get((binding, text))
        if exact is not None:
            self._vector(binding, exact)
            return exact
        normalized = unicodedata.normalize("NFKC", text).casefold()
        matches = {vector for rule_binding, tokens, vector in self._query_rules
                   if rule_binding == binding and any(token in normalized for token in tokens)}
        if len(matches) != 1:
            raise ServiceError("QUERY_UNSUPPORTED", "No unambiguous local query rule", 422)
        return matches.pop()

    def can_encode_query(self, context: AccessContext, text: str, binding: IndexBinding) -> bool:
        try:
            self.encode_query(context, text, binding)
            return True
        except ServiceError as error:
            if error.code == "QUERY_UNSUPPORTED":
                return False
            raise

    @staticmethod
    def _score(metric: str, query: tuple[float, ...], item: tuple[float, ...]) -> float:
        if metric == "L2":
            return math.sqrt(sum((a - b) ** 2 for a, b in zip(query, item)))
        dot = sum(a * b for a, b in zip(query, item))
        if metric == "IP":
            return dot
        if metric == "COSINE":
            norms = math.sqrt(sum(a * a for a in query) * sum(b * b for b in item))
            if norms == 0:
                raise ServiceError("ENCODER_MISMATCH", "Zero-norm cosine vector", 422)
            return dot / norms
        raise ServiceError("INDEX_BINDING_MISMATCH", "Unsupported local metric", 409)

    def _cursor(self, fingerprint: str, offset: int) -> str:
        payload = json.dumps({"fingerprint": fingerprint, "offset": offset,
                              "snapshot": self._snapshot}, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")

    def _offset(self, cursor: str | None, fingerprint: str) -> int:
        if cursor is None:
            return 0
        try:
            if not isinstance(cursor, str) or len(cursor) > 4096:
                raise ValueError("cursor length")
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(signature, hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()):
                raise ValueError("cursor signature")
            data = json.loads(payload)
            if (not isinstance(data, dict) or data.get("fingerprint") != fingerprint
                    or type(data.get("offset")) is not int or data["offset"] < 1):
                raise ValueError("cursor scope")
            if data.get("snapshot") != self._snapshot:
                raise ServiceError("INDEX_SNAPSHOT_EXPIRED", "Local corpus changed", 409)
            return data["offset"]
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, binascii.Error) as error:
            raise ServiceError("CURSOR_INVALID", "Cursor is invalid or stale", 409) from error

    def search(self, context: AccessContext, request: SearchRequest) -> SearchPage:
        self._access(context, request.binding.library_id, "search")
        self._validate_request(request)
        frozen = replace(request, cursor=None)
        fixture = self._approved.get((context, request.plan_id, request.plan_version,
                                      request.binding.binding_id))
        if fixture is None or fixture.request != frozen:
            raise ServiceError("PLAN_MISMATCH", "Request is not an approved fixture", 409)
        if (request.binding.metric not in ("COSINE", "IP", "L2")
                or request.binding.metric == "COSINE"
                and not any(value != 0 for value in request.vector.values)):
            raise ServiceError("ENCODER_MISMATCH", "Unsupported metric or zero-norm query", 422)
        scopes = self._templates.get(request.filter_template_ref)
        if scopes is None:
            raise ServiceError("FILTER_UNAVAILABLE", "Unknown server filter template", 409)
        fingerprint = hashlib.sha256(repr((context, frozen)).encode()).hexdigest()
        offset = self._offset(request.cursor, fingerprint)
        ranked = []
        for record in self._records:
            if record.binding == request.binding and record.scope in scopes:
                score = self._score(request.binding.metric, request.vector.values, record.values)
                if not math.isfinite(score):
                    raise ServiceError("ENCODER_MISMATCH", "Non-finite local score", 422)
                ranked.append((score, record))
        reverse = request.binding.metric != "L2"
        ranked.sort(key=lambda pair: (pair[0] if not reverse else -pair[0],
                                      type(pair[1].vector_id).__name__, str(pair[1].vector_id)))
        limit = min(len(ranked), fixture.depth_limit)
        if offset > 0 and offset >= limit:
            raise ServiceError("CURSOR_INVALID", "Cursor exceeds searchable depth", 409)
        end = min(offset + request.batch_size, limit)
        hits = tuple(SearchHit(request.binding, record.vector_id, score)
                     for score, record in ranked[offset:end])
        if end < limit:
            reason, cursor = "more_available", self._cursor(fingerprint, end)
        elif end < len(ranked):
            reason, cursor = "depth_limit", None
        else:
            reason, cursor = "exhausted", None
        return SearchPage(hits, cursor, reason, "snapshot", self._snapshot)
