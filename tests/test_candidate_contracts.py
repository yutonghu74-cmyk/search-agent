from dataclasses import asdict, replace
from hashlib import sha256
import unittest
from core import test_retrieval_storage as fixture
from dsh_retrieval.candidate_contracts import CandidateManifestProposal, ManifestItem, validate_manifest
from dsh_retrieval.cleaning_models import Candidate
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import MediaRecord
from dsh_retrieval.materialization_models import MaterializedCandidate, MaterializedPage


class CandidateContractTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RetrievalStorageTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        plan, _ = self.f.confirm()
        oid, _ = self.f.repo.reserve_page(self.f.actor, plan, plan.request, "page")
        self.f.repo.commit_page(self.f.actor, oid, plan, plan.request, self.f.first)
        self.operation = self.f.repo.get_operation(self.f.actor, self.f.tid, oid)
        self.media = MediaRecord("unit", "v1", "source", "asset", "trusted:ref",
                                 "sha256:" + sha256(b"fixture").hexdigest())
        item = ManifestItem(self.media, self.f.first.hits)
        candidate = Candidate(item.candidate_id, "v1", "/private/temporary/file", self.media.content_digest)
        self.page = MaterializedPage(self.operation, (MaterializedCandidate(candidate, self.media, item.hits),))
        self.proposal = CandidateManifestProposal.from_materialized(self.page)

    def test_no_path_or_query_vector_persisted_and_public_refs_hidden(self):
        stored = str(asdict(self.proposal))
        self.assertNotIn("source_path", stored)
        self.assertNotIn("temporary", stored)
        self.assertNotIn("values", stored)
        self.assertNotIn("trusted:", str(self.proposal.view()))
        self.assertIsNone(self.proposal.view()["accepted_count"])

    def test_temporary_path_independent_digest(self):
        item = self.page.items[0]
        changed = replace(self.page, items=(replace(item, candidate=replace(item.candidate, source_path="/new")),))
        self.assertEqual(CandidateManifestProposal.from_materialized(changed), self.proposal)

    def test_incomplete_duplicate_and_wrong_operation_rejected(self):
        for proposal in (replace(self.proposal, items=()),
                         replace(self.proposal, items=self.proposal.items * 2),
                         replace(self.proposal, operation_id="wrong"),
                         replace(self.proposal, plan_digest="wrong")):
            with self.assertRaises(ServiceError):
                validate_manifest(proposal, self.operation)

    def test_materialized_identity_tampering_rejected(self):
        item = self.page.items[0]
        with self.assertRaises(ServiceError):
            CandidateManifestProposal.from_materialized(replace(self.page, items=(
                replace(item, candidate=replace(item.candidate, candidate_id="wrong")),)))

    def test_empty_committed_page_valid_but_pending_not(self):
        empty = replace(self.operation, page=self.f.empty)
        proposal = replace(self.proposal, items=())
        validate_manifest(proposal, empty)
        with self.assertRaises(ServiceError):
            validate_manifest(proposal, replace(empty, page=None))

    def test_source_version_and_authorized_ref_covered_by_digest(self):
        for change in ({"asset_version": "v2"}, {"source_namespace": "other"},
                       {"authorized_media_ref": "other"}):
            item = replace(self.proposal.items[0], media=replace(self.media, **change))
            self.assertNotEqual(replace(self.proposal, items=(item,)).proposal_digest, self.proposal.proposal_digest)
