from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from dsh_retrieval.adapters.retrieval.clip_search import CLIPMatch
from dsh_retrieval.clip_media_mapping import load_clip_media_mappings
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import IndexBinding, SearchHit
from dsh_retrieval.physical_index_registry import PhysicalIndex, PhysicalIndexRegistry


class CLIPMediaMappingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "mapping.json"
        self.binding = IndexBinding("clip-v1", "images", "index-v1", "clip@1", 4, "IP")
        target = PhysicalIndex(self.binding, "service", "none", "collection", "path", "vector",
            "clip", "v1", "prep-v1", "all", "offset", "live", 10, 100)
        self.registry = PhysicalIndexRegistry((target,))
        self.entry = {"source": {"binding_id": "clip-v1", "index_version": "index-v1",
            "dataset_key": "dataset-key", "dataset_version": "dataset-v1", "shard": "s0.db",
            "full_path": "/trusted/source/7.jpg"},
            "media": {"media_unit_id": "unit-7", "asset_version": "asset-v1",
                "source_namespace": "assets", "source_asset_id": "asset-7",
                "authorized_media_ref": "media:asset-7@asset-v1",
                "content_digest": "sha256:" + "7" * 64}}

    def write(self, entries=None):
        self.path.write_text(json.dumps({"schema": "clip-media-mappings@1",
                                         "entries": entries or [self.entry]}))
        return load_clip_media_mappings(self.path, self.registry)

    def match(self, **changes):
        values = dict(hit=SearchHit(self.binding, 7, .4), filename="7.jpg",
            full_path="/trusted/source/7.jpg", web_path="/not-authorized/7.jpg", folder="all",
            app_name="mock", dataset_name="images", dataset_key="dataset-key",
            dataset_version="dataset-v1", shard="s0.db")
        values.update(changes)
        return CLIPMatch(**values)

    def test_exact_source_evidence_resolves_versioned_media(self):
        resolution = self.write().resolve(self.match())
        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.media.media_unit_id, "unit-7")
        self.assertEqual(resolution.media.content_digest, "sha256:" + "7" * 64)

    def test_path_dataset_or_index_change_does_not_guess(self):
        mappings = self.write()
        for match in (self.match(full_path="/trusted/source/other.jpg"),
                      self.match(dataset_version="dataset-v2")):
            self.assertEqual(mappings.resolve(match).status, "mapping_missing")
        with self.assertRaises(ServiceError) as caught:
            mappings.resolve(self.match(hit=SearchHit(
                replace(self.binding, index_version="index-v2"), 7, .4)))
        self.assertEqual(caught.exception.code, "INDEX_BINDING_MISMATCH")

    def test_duplicate_or_unverified_mapping_is_rejected(self):
        for entries in ([self.entry, self.entry],
                        [{**self.entry, "media": {**self.entry["media"],
                                                  "content_digest": None}}]):
            self.path.write_text(json.dumps({"schema": "clip-media-mappings@1",
                                             "entries": entries}))
            with self.assertRaises(ServiceError) as caught:
                load_clip_media_mappings(self.path, self.registry)
            self.assertEqual(caught.exception.code, "CLIP_MEDIA_MAPPING_INVALID")


if __name__ == "__main__":
    unittest.main()
