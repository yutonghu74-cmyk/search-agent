"""Business-side integration contracts; no database or SDK objects cross ports."""

from dataclasses import dataclass
from typing import Literal

from .models import Actor


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Created by the backend; adapters must recheck current authorization."""

    actor: Actor
    task_id: str
    purpose: str
    authorization_version: str


@dataclass(frozen=True, slots=True)
class IndexBinding:
    binding_id: str
    library_id: str
    index_version: str
    encoder_ref: str
    vector_dimension: int
    metric: str


@dataclass(frozen=True, slots=True)
class Library:
    library_id: str
    description: str
    media_type: str
    bindings: tuple[IndexBinding, ...]


@dataclass(frozen=True, slots=True)
class QueryVector:
    encoder_ref: str
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class SearchRequest:
    plan_id: str
    plan_version: int
    binding: IndexBinding
    vector: QueryVector
    filter_template_ref: str
    batch_size: int
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class SearchHit:
    binding: IndexBinding
    vector_id: str | int
    score: float


@dataclass(frozen=True, slots=True)
class SearchPage:
    hits: tuple[SearchHit, ...]
    next_cursor: str | None
    # Absence of a cursor alone is not proof that the source is exhausted.
    stop_reason: Literal["more_available", "exhausted", "depth_limit"]
    consistency: Literal["snapshot", "live"]
    snapshot_ref: str | None


@dataclass(frozen=True, slots=True)
class MediaRecord:
    media_unit_id: str
    asset_version: str
    source_namespace: str
    source_asset_id: str
    authorized_media_ref: str
    content_digest: str | None


@dataclass(frozen=True, slots=True)
class MediaResolution:
    hit: SearchHit
    media: MediaRecord | None
    status: Literal["resolved", "unavailable", "mapping_missing", "mapping_conflict"]
