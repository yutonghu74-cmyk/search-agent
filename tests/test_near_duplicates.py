from io import BytesIO
import unittest

from PIL import Image

from dsh_retrieval.near_duplicates import (
    ALGORITHM, NearDuplicatePair, PerceptualFingerprint,
    assess_near_duplicates, build_near_duplicate_clusters,
    compute_perceptual_fingerprint, hamming_distance,
)


class NearDuplicateTests(unittest.TestCase):
    def test_image_fingerprint_is_stable_and_invalid_bytes_are_unavailable(self):
        image = Image.new("RGB", (48, 32))
        image.putdata(tuple((x * 5, y * 7, (x + y) * 3)
                            for y in range(32) for x in range(48)))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        first = compute_perceptual_fingerprint(buffer.getvalue(), "asset-a")
        second = compute_perceptual_fingerprint(buffer.getvalue(), "asset-a")
        self.assertEqual(first, second)
        self.assertEqual((first.algorithm, first.bit_length), (ALGORITHM, 64))
        self.assertEqual(len(first.value), 16)
        self.assertIsNone(compute_perceptual_fingerprint(b"not-an-image", "asset-b"))

    def test_assessment_is_stable_and_only_returns_pairs_within_threshold(self):
        fingerprints = (
            PerceptualFingerprint("asset-c", ALGORITHM, 64, "ffffffffffffffff"),
            PerceptualFingerprint("asset-a", ALGORITHM, 64, "0000000000000000"),
            PerceptualFingerprint("asset-b", ALGORITHM, 64, "0000000000000003"),
        )
        self.assertEqual(hamming_distance(fingerprints[1], fingerprints[2]), 2)
        self.assertEqual(assess_near_duplicates(fingerprints), (
            NearDuplicatePair("asset-a", "asset-b", 2, 6),))

    def test_invalid_assessment_input_is_rejected(self):
        fingerprint = PerceptualFingerprint(
            "asset-a", ALGORITHM, 64, "0000000000000000")
        with self.assertRaises(ValueError):
            assess_near_duplicates((fingerprint, fingerprint))
        with self.assertRaises(ValueError):
            assess_near_duplicates((fingerprint,), review_threshold=17)

    def test_pairs_form_transitive_clusters_and_recommend_best_quality(self):
        fingerprints = (
            PerceptualFingerprint("asset-a", ALGORITHM, 64, "0" * 16,
                                  640, 480, 5.0),
            PerceptualFingerprint("asset-b", ALGORITHM, 64, "0" * 15 + "1",
                                  1920, 1080, 3.0),
            PerceptualFingerprint("asset-c", ALGORITHM, 64, "0" * 15 + "3",
                                  1920, 1080, 8.0),
        )
        pairs = (NearDuplicatePair("asset-a", "asset-b", 1, 6),
                 NearDuplicatePair("asset-b", "asset-c", 1, 6))
        clusters = build_near_duplicate_clusters(pairs, fingerprints)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].candidate_ids,
                         ("asset-a", "asset-b", "asset-c"))
        self.assertEqual(clusters[0].recommended_candidate_id, "asset-c")


if __name__ == "__main__":
    unittest.main()
