"""Trusted CLIP hit-to-business-media mapping for integration simulation."""
from __future__ import annotations

import json
from pathlib import Path
import re

from .adapters.retrieval.clip_search import CLIPMatch
from .errors import ServiceError
from .integration_models import MediaRecord, MediaResolution
from .physical_index_registry import PhysicalIndexRegistry


def _invalid():
    raise ServiceError("CLIP_MEDIA_MAPPING_INVALID",
                       "Invalid trusted CLIP media mapping configuration", 422)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _invalid()
        value[key] = item
    return value


class CLIPMediaMappings:
    """Resolve only exact, versioned source evidence; never infer from a path."""

    def __init__(self, registry: PhysicalIndexRegistry, entries):
        if type(registry) is not PhysicalIndexRegistry or type(entries) is not tuple:
            _invalid()
        self.registry = registry
        self._entries = {}
        for key, media in entries:
            if (not isinstance(key, tuple) or len(key) != 6
                    or any(not isinstance(value, str) or not value for value in key)
                    or type(media) is not MediaRecord
                    or any(not isinstance(value, str) or not value for value in (
                        media.media_unit_id, media.asset_version, media.source_namespace,
                        media.source_asset_id, media.authorized_media_ref))
                    or not isinstance(media.content_digest, str)
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", media.content_digest) is None):
                _invalid()
            binding_id, index_version, *_ = key
            binding = next((item for item in registry.bindings
                            if item.binding_id == binding_id), None)
            if binding is None or binding.index_version != index_version or key in self._entries:
                _invalid()
            self._entries[key] = media

    @staticmethod
    def _key(match: CLIPMatch):
        binding = match.hit.binding
        return (binding.binding_id, binding.index_version, match.dataset_key,
                match.dataset_version, match.shard, match.full_path)

    def resolve(self, match: CLIPMatch) -> MediaResolution:
        if type(match) is not CLIPMatch:
            raise ServiceError("CLIP_MEDIA_MAPPING_INVALID", "CLIP match required", 422)
        self.registry.resolve(match.hit.binding)
        media = self._entries.get(self._key(match))
        return MediaResolution(match.hit, media, "resolved" if media else "mapping_missing")


def load_clip_media_mappings(path, registry: PhysicalIndexRegistry):
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 1_000_000:
            _invalid()
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if (type(value) is not dict or set(value) != {"schema", "entries"}
                or value["schema"] != "clip-media-mappings@1"
                or type(value["entries"]) is not list or len(value["entries"]) > 10000):
            _invalid()
        rows = []
        source_fields = {"binding_id", "index_version", "dataset_key", "dataset_version",
                         "shard", "full_path"}
        media_fields = set(MediaRecord.__dataclass_fields__)
        for entry in value["entries"]:
            if (type(entry) is not dict or set(entry) != {"source", "media"}
                    or type(entry["source"]) is not dict or set(entry["source"]) != source_fields
                    or any(not isinstance(item, str) or not item
                           for item in entry["source"].values())
                    or type(entry["media"]) is not dict or set(entry["media"]) != media_fields):
                _invalid()
            source = entry["source"]
            key = tuple(source[name] for name in (
                "binding_id", "index_version", "dataset_key", "dataset_version",
                "shard", "full_path"))
            rows.append((key, MediaRecord(**entry["media"])))
        return CLIPMediaMappings(registry, tuple(rows))
    except ServiceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        _invalid()
