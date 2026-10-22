from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
import unittest

from core import test_retrieval_storage as fixture
from dsh_retrieval.adapters.retrieval.materialization import LocalCandidateMaterializer
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import MediaRecord, MediaResolution


class MaterializationTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RetrievalStorageTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.plan, _ = self.f.confirm()
        self.oid, _ = self.f.repo.reserve_page(self.f.actor, self.plan, self.plan.request, "page")
        self.root = Path(self.f.temp.name) / "media"
        self.body = b"synthetic bytes"
        self.record = MediaRecord("unit", "v1", "source", "asset", "trusted:asset",
                                  "sha256:" + sha256(self.body).hexdigest())
        self.opens = self.resolves = 0
        self.transform = lambda results: results
        self.after_read = lambda: None
        self.stream = lambda: BytesIO(self.body)
        self.subject = LocalCandidateMaterializer(ledger=self.f.repo, media=self, gateway=self,
                                                 authorization=self.f.auth, workspace_root=self.root)

    def resolve_hits(self, context, hits):
        self.resolves += 1
        return self.transform(tuple(MediaResolution(h, self.record, "resolved") for h in hits))

    @contextmanager
    def open_read(self, context, ref, max_bytes):
        self.opens += 1
        with self.stream() as stream:
            yield stream
        self.after_read()

    def commit(self, page=None):
        self.f.repo.commit_page(self.f.actor, self.oid, self.plan, self.plan.request, page or self.f.first)

    def preview(self):
        return self.subject.materialize(self.f.actor, self.f.tid, self.oid)

    def clean(self):
        self.assertFalse(self.root.exists() and list(self.root.iterdir()))

    def expect_error(self, code):
        with self.assertRaises(ServiceError) as error:
            with self.preview():
                self.fail_if_yielded()
        self.assertEqual(error.exception.code, code)
        self.clean()

    def fail_if_yielded(self):
        raise AssertionError("Must not yield candidate preview")

    def test_success_cleanup_stable_digest_and_no_writes(self):
        self.commit()
        before = self.f.state()
        task = self.f.tasks.get(self.f.actor, self.f.tid)
        with self.preview() as page:
            path = Path(page.items[0].candidate.source_path)
            self.assertEqual(path.read_bytes(), self.body)
            digest = page.preview_digest
            self.assertNotIn("trusted:", str(page.view()))
            self.assertNotIn(str(path), str(page.view()))
        self.assertFalse(path.exists())
        with self.preview() as page:
            self.assertEqual(page.preview_digest, digest)
        self.assertEqual(self.f.state(), before)
        self.assertEqual(self.f.tasks.get(self.f.actor, self.f.tid), task)
        self.clean()

    def two_hits(self):
        self.commit(replace(self.f.first, hits=(self.f.hit, replace(self.f.hit, vector_id=2))))

    def test_same_identity_aggregates_hits(self):
        self.two_hits()
        with self.preview() as page:
            self.assertEqual((len(page.items), len(page.items[0].hits), self.opens), (1, 2, 1))

    def test_distinct_sources_not_content_deduplicated(self):
        self.two_hits()
        self.transform = lambda r: (r[0], replace(r[1], media=replace(self.record, source_namespace="other")))
        with self.preview() as page:
            self.assertEqual(len(page.items), 2)
            self.assertNotEqual(page.items[0].candidate.candidate_id, page.items[1].candidate.candidate_id)

    def test_conflicting_same_identity(self):
        self.two_hits()
        self.transform = lambda r: (r[0], replace(r[1], media=replace(self.record, authorized_media_ref="other")))
        self.expect_error("MEDIA_MAPPING_CONFLICT")
        self.assertEqual(self.opens, 0)

    def test_incomplete_or_out_of_order_mapping(self):
        self.two_hits()
        for transform in (lambda r: (), lambda r: r[::-1],
                          lambda r: (replace(r[0], status="mapping_missing", media=None), r[1])):
            self.transform = transform
            self.expect_error("MEDIA_MAPPING_INCOMPLETE")
        self.assertEqual(self.opens, 0)

    def test_unverified_digest(self):
        self.commit()
        for value in (None, "bad", "sha256:abc"):
            self.record = replace(self.record, content_digest=value)
            self.expect_error("MEDIA_VERSION_UNVERIFIED")
        self.assertEqual(self.opens, 0)

    def test_changed_content(self):
        self.commit()
        self.body = b"changed"
        self.expect_error("INPUT_SNAPSHOT_CHANGED")

    def test_per_item_limit_and_exact_limit(self):
        self.commit()
        self.subject.item_limit = len(self.body) - 1
        self.expect_error("MEDIA_LIMIT_EXCEEDED")
        self.subject.item_limit = len(self.body)
        with self.preview():
            pass
        self.clean()

    def test_page_limit(self):
        self.two_hits()
        self.transform = lambda r: (r[0], replace(r[1], media=replace(self.record, source_asset_id="second")))
        self.subject.total_limit = len(self.body)
        self.expect_error("MEDIA_LIMIT_EXCEEDED")

    def test_invalid_stream(self):
        self.commit()
        self.stream = lambda: StringIO("text not bytes")
        self.expect_error("MEDIA_READ_INVALID")

    def test_authority_revoked_during_read(self):
        self.commit()
        self.after_read = lambda: setattr(self.f.auth, "denied", True)
        self.expect_error("ACCESS_DENIED")

    def test_scope_changes_during_read(self):
        self.commit()
        self.after_read = lambda: self.f.change_task(run_generation=self.plan.scope.generation + 1)
        self.expect_error("REVISION_CONFLICT")

    def test_mapping_changes_at_recheck(self):
        self.commit()
        self.after_read = lambda: setattr(self, "record", replace(self.record, asset_version="v2"))
        self.expect_error("INPUT_SNAPSHOT_CHANGED")

    def test_pending_rejected(self):
        self.expect_error("RETRIEVAL_NOT_COMMITTED")
        self.assertEqual(self.opens, 0)

    def test_empty_page_needs_no_media(self):
        self.commit(self.f.empty)
        with self.preview() as page:
            self.assertEqual(page.items, ())
        self.assertEqual((self.resolves, self.opens), (0, 0))
        self.assertFalse(self.root.exists())

    def test_caller_exception_cleans_files(self):
        self.commit()
        with self.assertRaisesRegex(RuntimeError, "caller"):
            with self.preview():
                raise RuntimeError("caller")
        self.clean()

    def test_gateway_failure_cleans_files(self):
        self.commit()
        def broken():
            raise OSError("fake failure")
        self.stream = broken
        with self.assertRaises(OSError):
            with self.preview():
                pass
        self.clean()

    def test_invalid_limits(self):
        for limit in (True, 0, -1, 10_000_001):
            with self.assertRaises(ValueError):
                LocalCandidateMaterializer(ledger=self.f.repo, media=self, gateway=self,
                                           authorization=self.f.auth, workspace_root=self.root,
                                           max_item_bytes=limit)
