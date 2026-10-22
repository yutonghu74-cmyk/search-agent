from dataclasses import replace
import unittest

from dsh_retrieval.cleaning_models import Candidate
from dsh_retrieval.integration_models import IndexBinding, QueryVector, SearchRequest, SearchHit, SearchPage, MediaRecord
from dsh_retrieval.materialization_models import MaterializedCandidate, MaterializedPage
from dsh_retrieval.retrieval_contracts import RetrievalOperation, RetrievalPlan, RetrievalScope


class MaterializationModelTest(unittest.TestCase):
    def setUp(self):
        binding = IndexBinding("b", "l", "v1", "e", 1, "COSINE")
        request = SearchRequest("p", 1, binding, QueryVector("e", (1.0,)), "f", 1)
        plan = RetrievalPlan(RetrievalScope("t", "u", "p", "task", 1, 1, 1, "requirement"), 1, request)
        hit = SearchHit(binding, 1, 0.9)
        operation = RetrievalOperation("op", plan, request, SearchPage((hit,), None, "exhausted", "snapshot", "s"))
        item = MaterializedCandidate(Candidate("c", "v1", "/private/temp/media.bin", "sha256:abc"),
                                     MediaRecord("unit", "v1", "ns", "asset", "private:media", "sha256:abc"), (hit,))
        self.preview = MaterializedPage(operation, (item,))

    def test_public_view_excludes_paths_and_media_refs(self):
        view = self.preview.view()
        self.assertNotIn("private", str(view))
        self.assertNotIn("source_path", str(view))
        self.assertIsNone(view["accepted_count"])
        self.assertEqual(view["kind"], "ephemeral_candidate_preview")

    def test_digest_does_not_depend_on_temporary_path(self):
        item = self.preview.items[0]
        moved = replace(item, candidate=replace(item.candidate, source_path="/another/root/file"))
        self.assertEqual(self.preview.preview_digest, replace(self.preview, items=(moved,)).preview_digest)
        changed = replace(item, candidate=replace(item.candidate, content_digest="sha256:changed"))
        self.assertNotEqual(self.preview.preview_digest, replace(self.preview, items=(changed,)).preview_digest)
