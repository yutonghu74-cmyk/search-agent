from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
import json
import sqlite3
import unittest
from unittest.mock import patch

from core import test_retrieval_storage as fixture
from dsh_retrieval.adapters.task_storage.candidates import SQLiteCandidateManifestRepository
from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository, load_state
from dsh_retrieval.candidate_contracts import CandidateManifestProposal, ManifestItem
from dsh_retrieval.cleaning_models import Candidate
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import MediaRecord
from dsh_retrieval.materialization_models import MaterializedCandidate, MaterializedPage
from dsh_retrieval.models import Actor
from dsh_retrieval.multi_rule_contracts import CleaningRecipe, CleaningRule, DetectionStep
from dsh_retrieval.adapters.task_storage.sqlite import decode, encode
from dsh_retrieval.query_spec import digest


class MultiRuleApprovalTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RetrievalStorageTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        task = self.f.tasks.get(self.f.actor, self.f.tid)
        confirmed = json.loads(task.confirmed_proposal_json)
        confirmed["query_spec"]["rules"] = [
            {"rule_id": ident, "predicate_ref": predicate, "operator": "eq", "value": False,
             "hardness": "hard", "origin": "user_explicit"}
            for ident, predicate in (("text", "text.present@1"), ("mark", "watermark.present@1"))]
        self.f.change_task(confirmed_proposal_json=json.dumps(confirmed, sort_keys=True),
                           request_digest=digest({"project_id": task.project_id, "proposal": confirmed}))
        self.f.proposal = self.f.propose(plan_id="rules-plan")
        plan, _ = self.f.confirm(self.f.proposal)
        oid, _ = self.f.repo.reserve_page(self.f.actor, plan, plan.request, "page")
        candidate_count = getattr(self, "fixture_candidate_count", 1)
        hits = tuple(
            self.f.hit if index == 0 else replace(
                self.f.hit, vector_id=index + 1,
                score=self.f.hit.score - index / 100)
            for index in range(candidate_count))
        source_page = replace(self.f.first, hits=hits)
        self.f.repo.commit_page(self.f.actor, oid, plan, plan.request, source_page)
        operation = self.f.repo.get_operation(self.f.actor, self.f.tid, oid)
        materialized = []
        for index, hit in enumerate(hits):
            suffix = "" if index == 0 else f"-duplicate-{index}"
            media = MediaRecord("unit" + suffix, "v1", "source", "asset" + suffix,
                                "trusted:ref",
                                "sha256:" + sha256(b"fixture").hexdigest())
            item = ManifestItem(media, (hit,))
            candidate = Candidate(item.candidate_id, "v1", "", media.content_digest)
            materialized.append(MaterializedCandidate(candidate, media, item.hits))
        page = MaterializedPage(operation, tuple(materialized))
        self.manifest_proposal = CandidateManifestProposal.from_materialized(page)
        manifests = SQLiteCandidateManifestRepository(self.f.path, projects=self.f.projects,
                                                       authorization=self.f.auth)
        manifests.put_proposal(self.f.actor, self.manifest_proposal)
        self.manifest, _ = manifests.confirm(self.f.actor, self.manifest_proposal, key="manifest",
            expected_task_version=self.manifest_proposal.scope.task_version, action="confirm")
        step = DetectionStep("features", "fixture.detector", "fixture.validator", "fixture.eval")
        self.recipe = CleaningRecipe("image", digest({"review": "fixture"}),
            digest({"registry": "fixture"}), (step,), (
                CleaningRule("text", "features", "text.present@1", "eq", False),
                CleaningRule("mark", "features", "watermark.present@1", "eq", False)))
        self.release_digest = digest({"detector_release": "fixture-v1"})
        self.repo = self.reopen()

    def reopen(self):
        return SQLiteMultiRuleApprovalRepository(self.f.path, projects=self.f.projects,
                                                 authorization=self.f.auth)

    def prepare(self, repo=None, recipe=None, release_digest=None):
        return (repo or self.repo).prepare(self.f.actor, self.f.tid, self.manifest.manifest_ref,
                                           recipe or self.recipe,
                                           detector_release_digest=release_digest or self.release_digest)

    def confirm(self, proposal=None, repo=None, **changes):
        proposal = proposal or self.proposal
        args = dict(key="approve", expected_task_version=proposal.expected_task_version,
                    action="confirm")
        args.update(changes)
        return (repo or self.repo).confirm(self.f.actor, self.f.tid,
                                          proposal.proposal_digest, **args)

    def state(self):
        with closing(sqlite3.connect(self.f.path)) as db:
            return load_state(db.execute("SELECT format,payload FROM multi_rule_cleaning_ledger WHERE id=1").fetchone())

    def assert_code(self, code, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_explicit_approval_is_atomic_and_restartable(self):
        before = self.f.tasks.get(self.f.actor, self.f.tid)
        self.proposal = self.prepare()
        self.assertEqual(self.f.tasks.get(self.f.actor, self.f.tid), before)
        approval, replayed = self.confirm()
        self.assertFalse(replayed)
        self.assertEqual(approval.proposal.detector_release_digest, self.release_digest)
        self.assertEqual(approval.job.binding.detector_release_digest, self.release_digest)
        self.assertEqual(approval.job.binding.task_version, before.task_version + 1)
        self.assertEqual(self.f.tasks.get(self.f.actor, self.f.tid).task_version, before.task_version + 1)
        self.assertEqual(self.reopen().get_current(self.f.actor, self.f.tid, approval.operation_id), approval)
        self.assertEqual(self.confirm(repo=self.reopen()), (approval, True))
        self.assertEqual(self.confirm(repo=self.reopen(), key="another"), (approval, True))
        self.assertEqual(len(self.state()["events"]), 1)
        self.assertNotIn("accepted_count", str(self.state()))
        self.assertNotIn("source_path", str(self.state()))

    def test_concurrent_confirmation_one_effect(self):
        self.proposal = self.prepare()
        repos = [self.reopen() for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda i: self.confirm(repo=repos[i % 6], key=f"key-{i}"), range(12)))
        self.assertEqual(sum(not replay for _, replay in results), 1)
        self.assertEqual(len({a.operation_id for a, _ in results}), 1)
        self.assertEqual(len(self.state()["events"]), 1)

    def test_action_version_and_idempotency_conflict(self):
        self.proposal = self.prepare()
        self.assert_code("CONFIRMATION_REQUIRED", lambda: self.confirm(action="auto"))
        self.assert_code("REVISION_CONFLICT", lambda: self.confirm(expected_task_version=999))
        self.assertEqual(self.state()["approvals"], {})
        self.confirm()
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: self.confirm(action="reject"))
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: self.repo.confirm(
            self.f.actor, self.f.tid, "sha256:" + "0" * 64,
            key="approve", expected_task_version=self.proposal.expected_task_version,
            action="confirm"))

    def test_release_digest_is_required_and_changes_proposal_identity(self):
        with self.assertRaises(TypeError):
            self.repo.prepare(self.f.actor, self.f.tid, self.manifest.manifest_ref, self.recipe)
        self.assert_code("DETECTOR_RELEASE_INVALID", lambda: self.repo.prepare(
            self.f.actor, self.f.tid, self.manifest.manifest_ref, self.recipe,
            detector_release_digest="release-v1"))
        first = self.prepare()
        second = self.prepare(release_digest=digest({"detector_release": "fixture-v2"}))
        self.assertNotEqual(first.proposal_digest, second.proposal_digest)
        self.proposal = first
        self.confirm()
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: self.repo.confirm(
            self.f.actor, self.f.tid, second.proposal_digest, key="approve",
            expected_task_version=second.expected_task_version, action="confirm"))

    def test_decoder_only_defaults_missing_release_field_for_legacy_types(self):
        proposal = self.prepare()
        encoded = encode(proposal)
        encoded["value"].pop("detector_release_digest")
        legacy = decode(encoded)
        self.assertIsNone(legacy.detector_release_digest)

        binding = encode(self.confirm(proposal=proposal)[0].job.binding)
        binding["value"].pop("detector_release_digest")
        self.assertIsNone(decode(binding).detector_release_digest)

        missing_other = encode(proposal)
        missing_other["value"].pop("manifest_ref")
        with self.assertRaises(ValueError):
            decode(missing_other)
        extra = encode(proposal)
        extra["value"]["unknown"] = None
        with self.assertRaises(ValueError):
            decode(extra)

    def test_wrong_rules_owner_and_revoked_media_rejected(self):
        changed = replace(self.recipe, rules=self.recipe.rules[:1])
        self.assert_code("RECIPE_RULE_MISMATCH", lambda: self.prepare(recipe=changed))
        self.assert_code("NOT_FOUND", lambda: self.repo.prepare(
            Actor("tenant", "other"), self.f.tid, self.manifest.manifest_ref, self.recipe,
            detector_release_digest=self.release_digest))
        self.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", self.prepare)
        self.f.auth.denied = False
        self.proposal = self.prepare()
        self.f.auth.down = True
        self.assert_code("AUTH_UNAVAILABLE", self.confirm)

    def test_stale_task_and_generation_cannot_approve_or_read(self):
        self.proposal = self.prepare()
        self.f.change_task(task_version=self.proposal.expected_task_version + 1)
        self.assert_code("REVISION_CONFLICT", self.confirm)
        self.f.change_task(task_version=self.proposal.expected_task_version)
        approval, _ = self.confirm()
        self.f.change_task(run_generation=self.proposal.generation + 1)
        self.assert_code("REVISION_CONFLICT", lambda: self.reopen().get_current(
            self.f.actor, self.f.tid, approval.operation_id))

    def test_serialization_failure_rolls_back_task_and_event(self):
        self.proposal = self.prepare()
        before = self.f.tasks.get(self.f.actor, self.f.tid)
        with patch("dsh_retrieval.adapters.task_storage.ledger_rows.prepare_state",
                   side_effect=ServiceError("STORAGE_LIMIT", "full", 507)):
            self.assert_code("STORAGE_LIMIT", self.confirm)
        self.assertEqual(self.f.tasks.get(self.f.actor, self.f.tid), before)
        self.assertEqual(self.state()["events"], [])


if __name__ == "__main__":
    unittest.main()
