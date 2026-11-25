from dataclasses import replace
import unittest

from retrieval import test_materialization as image_fixture
from dsh_retrieval.adapters.retrieval.materialization import LocalCandidateMaterializer
from dsh_retrieval.integration_models import MediaResolution
from dsh_retrieval.whole_video import canonical_whole_video_record


class WholeVideoMaterializationTest(unittest.TestCase):
    def setUp(self):
        self.f = f = image_fixture.MaterializationTest()
        f.setUp(); self.addCleanup(f.doCleanups)
        f.record = canonical_whole_video_record(f.record, source_unit_type='whole_video')
        f.subject = LocalCandidateMaterializer(ledger=f.f.repo, media=f, gateway=f,
            authorization=f.f.auth, workspace_root=f.root, media_type='video')

    def test_multiple_whole_video_vector_hits_materialize_once_with_all_evidence(self):
        f = self.f; f.two_hits()
        with f.preview() as page:
            self.assertEqual((len(page.items), len(page.items[0].hits), f.opens), (1, 2, 1))
            self.assertEqual(page.items[0].media, f.record)
        f.clean()

    def test_frame_or_noncanonical_mapping_rejected_before_media_read(self):
        f = self.f; f.record = replace(f.record, media_unit_id='frame-42'); f.commit()
        f.expect_error('MEDIA_MAPPING_CONFLICT')
        self.assertEqual(f.opens, 0)

    def test_conflicting_original_versions_rejected_before_read(self):
        f = self.f
        f.transform = lambda rows: (rows[0], MediaResolution(rows[1].hit,
            canonical_whole_video_record(replace(f.record, asset_version='v2'), source_unit_type='whole_video'),
            'resolved'))
        f.two_hits(); f.expect_error('MEDIA_MAPPING_CONFLICT')
        self.assertEqual(f.opens, 0)

    def test_snapshot_or_digest_change_never_publishes_video_candidate(self):
        f = self.f; f.body = b'changed'; f.commit()
        f.expect_error('INPUT_SNAPSHOT_CHANGED')
