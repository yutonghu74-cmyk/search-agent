"""Deterministic, in-process full-chain regression.

One synthetic JPEG moves through the entire business chain start to finish:

  1. 诉求澄清  -- natural-language task draft + explicit owner confirmation
                 (``TaskService`` + ``SQLiteTaskRepository``).
  2. 扩检索词/检索 -- retrieval plan proposal + confirm, then page reserve/commit
                 through a simulated gateway (``SQLiteRetrievalPlanRepository``
                 + ``SimulatedRetrieval`` + ``LocalCandidateMaterializer``).
  3. 候选清单  -- candidate manifest proposal + confirm
                 (``SQLiteCandidateManifestRepository`` / ``CandidatePublicationService``).
  4. 多规则清洗 -- owner-approved cleaning job executed by a synthetic released
                 detector (``SQLiteMultiRuleApprovalRepository`` +
                 ``run_approved_multi_rule``).
  5. 补数     -- exact result-set preview transitions the task to ``refill``
                 (``build_exact_result_set`` -> ``ExactResultSetPreview``).
  6. 打包     -- delivery to an immutable ZIP, explicit publication
                 (``ImageDeliveryService.prepare``/``publish``/``download``).

No live model, no network, no real business data: temp SQLite ledgers, a
simulated retrieval gateway, a released synthetic detector, and a ``BytesIO``
media gateway recover the whole chain in-process.
"""

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import sqlite3
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import draft_helpers
import test_multi_rule_pipeline as pipeline_fixture
from core import test_retrieval_storage
from core import test_multi_rule_approval
from dsh_retrieval.delivery import ImageDeliveryService
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor, RunStatus, TaskLifecycle


# A valid JPEG magic sequence so every image-only gate stays open end to end.
IMAGE = b"\xff\xd8\xffsynthetic-jpeg-test"
IMAGE_DIGEST = "sha256:" + sha256(IMAGE).hexdigest()


class ImageGateway:
    """The single media gateway for the whole chain; always yields ``IMAGE``."""

    def __init__(self, authority):
        self.authority = authority
        self.reads = 0

    @contextmanager
    def open_read(self, context, ref, *, max_bytes):
        self.authority.require_access(context, ref, "read")
        self.reads += 1
        if ref != "trusted:ref":
            raise AssertionError("unapproved media reference")
        with BytesIO(IMAGE) as stream:
            yield stream


class FullChainTest(unittest.TestCase):
    def setUp(self):
        # The delivery slice needs a full result (``gap == 0``): shrink the
        # raw-request target to the single candidate the fixtures synthesize.
        body = deepcopy(draft_helpers.BODY)
        body["query_spec"]["target_count"] = 1
        body["query_spec"]["refill_policy"]["target_count"] = 1

        def confirmation(service, actor):
            return draft_helpers.confirmation(service, actor, body=body)

        # Re-point the fixture's synthetic media to a valid JPEG byte string, so
        # the same content_digest flows from retrieval through delivery.
        self.pipeline = pipeline_fixture.MultiRulePipelineTests()
        with patch("core.test_retrieval_storage.confirmation", confirmation), patch(
                "core.test_multi_rule_approval.sha256",
                lambda data: sha256(IMAGE if data == b"fixture" else data)):
            self.pipeline.setUp()
        self.addCleanup(self.pipeline.doCleanups)

        self.repo = self.pipeline.repo
        self.actor, self.task_id = self.pipeline.actor, self.pipeline.task_id

        # Swap the cleaning fixture's ``b"fixture"`` gateway for the JPEG one,
        # then run the approved cleaning job through to the refill preview.
        self.gateway = ImageGateway(self.pipeline.f.f.auth)
        self.pipeline.gateway = self.gateway
        self.preview, _ = self.pipeline.run_pipeline()
        self.service = ImageDeliveryService(self.repo, self.gateway)

    def prepare(self):
        return self.service.prepare(self.actor, self.task_id,
            expected_task_version=self.preview["as_of_task_version"],
            result_set_digest=self.preview["result_set_digest"])

    def publish(self, package):
        return self.service.publish(self.actor, self.task_id,
            package_id=package["package_id"], manifest_digest=package["manifest_digest"],
            expected_task_version=package["current_version"], action="confirm")

    def assert_code(self, code, operation):
        with self.assertRaises(ServiceError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def test_full_chain_end_to_end(self):
        task = self.repo.tasks.get(self.actor, self.task_id)

        # 1. 诉求澄清 -- a confirmed, active task bound to the cleaned-up request.
        self.assertEqual(task.lifecycle, TaskLifecycle.ACTIVE)
        self.assertEqual(task.target_count, 1)
        self.assertGreaterEqual(task.confirmed_revision, 1)

        # 2. 检索 -- the confirmed retrieval plan holds one committed page, whose
        # operation id is exactly what the candidate manifest references. Reading
        # the raw ledger (not ``get_current``) also works after the task has moved
        # past the ""retrieving"" stage, which a live scope check would fence.
        retrieval_state = self.pipeline.f.f.state()
        plan = retrieval_state["current"][self.task_id]
        self.assertEqual(plan.request.plan_id, "rules-plan")
        operations = retrieval_state["operations"]
        self.assertEqual(len(operations), 1)
        operation_id, committed = next(iter(operations.items()))
        self.assertIsNotNone(committed[2])  # page reserved *and* committed
        self.assertEqual(self.pipeline.f.manifest.proposal.operation_id, operation_id)

        # 3. 候选清单 -- the confirmed manifest carries the same image digest the
        # gateway will later re-materialize and the detector will read.
        manifest = self.pipeline.f.manifest
        self.assertEqual(manifest.proposal.items[0].media.content_digest, IMAGE_DIGEST)

        # 4. 多规则清洗 -- exactly one candidate passed the synthetic detector.
        self.assertEqual((self.gateway.reads, self.pipeline.detector.calls), (1, 1))
        result = self.pipeline.f.state()["results"][self.pipeline.operation_id]
        self.assertEqual([item.verdict for item in result.candidates], ["pass"])

        # 5. 补数 -- a gap-free qualified preview moved the task to ``refill``.
        self.assertEqual(self.preview["kind"], "preview")
        self.assertEqual((self.preview["accepted_count"], self.preview["gap"],
                          self.preview["stop_reason"]), (1, 0, "TARGET_MET"))
        self.assertEqual(self.preview["accepted"][0].content_digest, IMAGE_DIGEST)
        self.assertEqual(self.preview["accepted"][0].candidate_id,
                         self.pipeline.f.manifest.proposal.items[0].candidate_id)
        self.assertEqual(task.task_version, self.preview["as_of_task_version"])
        self.assertEqual(task.stage, "refill")
        self.assertEqual(task.run_status, RunStatus.RUNNING)

        # 6. 打包 -- stage the package, wait for the owner's explicit publish, then
        # download an immutable, re-validated ZIP.
        package, replayed = self.prepare()
        self.assertFalse(replayed)
        self.assertEqual(package["status"], "staged")
        self.assertEqual(package["manifest"]["dedup_policy"], "exact-content@1")
        self.assertEqual((package["manifest"]["eligible_unique_count"],
                          package["manifest"]["exact_duplicate_count"],
                          package["manifest"]["packaged_file_count"]), (1, 0, 1))
        self.assertEqual(self.repo.tasks.get(self.actor, self.task_id).run_status,
                         RunStatus.AWAITING_INPUT)
        self.assert_code("PACKAGE_APPROVAL_REQUIRED", lambda: self.service.download(
            self.actor, self.task_id, package["package_id"]))

        published, replayed = self.publish(package)
        self.assertFalse(replayed)
        self.assertEqual(published["status"], "published")
        self.assertEqual(self.repo.tasks.get(self.actor, self.task_id).run_status,
                         RunStatus.COMPLETED)
        self.assertEqual(self.repo.get_current_result_set(self.actor, self.task_id)["kind"],
                         "delivery")

        content = self.service.download(self.actor, self.task_id, package["package_id"])
        self.assertEqual("sha256:" + sha256(content).hexdigest(), package["zip_digest"])
        with ZipFile(BytesIO(content)) as archive:
            self.assertEqual(archive.namelist(), ["images/0001.jpg", "manifest.json"])
            self.assertEqual(archive.read("images/0001.jpg"), IMAGE)
            self.assertEqual(json.loads(archive.read("manifest.json")), package["manifest"])
            self.assertEqual(package["manifest"]["packaged_file_count"],
                             len([name for name in archive.namelist()
                                  if name.startswith("images/")]))
        with sqlite3.connect(self.pipeline.f.f.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM image_delivery_events").fetchone()[0], 2)

    def test_full_chain_replay_is_idempotent(self):
        # Re-running the already-published chain re-uses saved results instead of
        # re-reading media or re-running the detector.
        view, replayed = self.pipeline.run_pipeline()
        self.assertTrue(replayed)
        self.assertEqual(view["result_set_digest"], self.preview["result_set_digest"])
        self.assertEqual((self.gateway.reads, self.pipeline.detector.calls), (1, 1))

        # Staging the same result is a replay of the staged package, and the
        # already-published package replays its publication. Delivery re-reads
        # media to validate immutability, but it must never re-run the detector.
        package, replayed = self.prepare()
        self.assertFalse(replayed)
        self.assertEqual(self.prepare(), (package, True))
        published, replayed = self.publish(package)
        if not replayed:
            # First publication in this test; a second publish is then a replay.
            self.assertEqual(published["status"], "published")
            published, replayed = self.publish(package)
        self.assertTrue(replayed)
        self.assertEqual(self.repo.tasks.get(self.actor, self.task_id).run_status,
                         RunStatus.COMPLETED)
        self.assertEqual(self.pipeline.detector.calls, 1)

    def test_full_chain_revocation_fails_closed(self):
        # Late authority revocation must not be able to stage a package.
        self.pipeline.f.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", self.prepare)
        self.pipeline.f.f.auth.denied = False
        package, _ = self.prepare()
        self.pipeline.f.f.auth.denied = True
        self.assert_code("ACCESS_DENIED", lambda: self.publish(package))

    def test_two_identical_candidates_clean_then_deliver_one_representative(self):
        body = deepcopy(draft_helpers.BODY)
        body["query_spec"]["target_count"] = 1
        body["query_spec"]["refill_policy"]["target_count"] = 1

        def confirmation(service, actor):
            return draft_helpers.confirmation(
                service, actor, key="duplicate-full-chain", body=body)

        duplicate = pipeline_fixture.MultiRulePipelineTests()
        with patch.object(test_multi_rule_approval.MultiRuleApprovalTest,
                          "fixture_candidate_count", 2, create=True), patch(
                "core.test_retrieval_storage.confirmation", confirmation), patch(
                "core.test_multi_rule_approval.sha256",
                lambda data: sha256(IMAGE if data == b"fixture" else data)):
            duplicate.setUp()
        self.addCleanup(duplicate.doCleanups)
        gateway = ImageGateway(duplicate.f.f.auth)
        duplicate.gateway = gateway

        preview, replayed = duplicate.run_pipeline()
        self.assertFalse(replayed)
        self.assertEqual((gateway.reads, duplicate.detector.calls), (2, 2))
        self.assertEqual((preview["eligible_unique_count"],
                          preview["accepted_count"], preview["duplicate_count"],
                          preview["gap"]), (1, 1, 1, 0))
        representative = preview["accepted"][0]
        self.assertEqual(len(representative.candidate_ids), 2)
        self.assertEqual(len(set(representative.candidate_ids)), 2)
        self.assertEqual(representative.content_digest, IMAGE_DIGEST)

        delivery = ImageDeliveryService(duplicate.repo, gateway)
        package, replayed = delivery.prepare(
            duplicate.actor, duplicate.task_id,
            expected_task_version=preview["as_of_task_version"],
            result_set_digest=preview["result_set_digest"])
        self.assertFalse(replayed)
        self.assertEqual((package["manifest"]["eligible_unique_count"],
                          package["manifest"]["exact_duplicate_count"],
                          package["manifest"]["packaged_file_count"]), (1, 1, 1))
        self.assertEqual(len(package["manifest"]["members"]), 1)
        self.assertEqual(len(package["manifest"]["members"][0]["candidate_ids"]), 2)

        published, replayed = delivery.publish(
            duplicate.actor, duplicate.task_id,
            package_id=package["package_id"],
            manifest_digest=package["manifest_digest"],
            expected_task_version=package["current_version"], action="confirm")
        self.assertFalse(replayed)
        self.assertEqual(published["status"], "published")
        content = delivery.download(
            duplicate.actor, duplicate.task_id, package["package_id"])
        with ZipFile(BytesIO(content)) as archive:
            image_members = [name for name in archive.namelist()
                             if name.startswith("images/")]
            self.assertEqual(image_members, ["images/0001.jpg"])
            self.assertEqual(archive.read(image_members[0]), IMAGE)


if __name__ == "__main__":
    unittest.main()
