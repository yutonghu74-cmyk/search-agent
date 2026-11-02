import unittest

from dsh_retrieval.result_views import ResultCursorError, build_result_page


TASK = {"task_id": "task-1", "confirmed_revision": 2, "task_version": 7,
        "run_generation": 3}
PROPOSAL = {"query_spec": {"target_count": 3}}


def published():
    accepted = tuple({"candidate_id": f"asset-{index}", "asset_version": "v1",
                      "content_digest": f"sha256:{index:064x}"} for index in range(3))
    reserve = ({"candidate_id": "asset-4", "asset_version": "v2",
                "content_digest": "sha256:" + "4" * 64},)
    return {"result_set_id": "resultset-1", "kind": "preview", "status": "published",
            "as_of_task_version": 7, "operation_id": "operation-1", "generation": 3,
            "result_set_digest": "sha256:" + "a" * 64, "target_count": 3,
            "eligible_unique_count": 4, "accepted_count": 3, "reserve_count": 1,
            "duplicate_count": 2, "failed_count": 1, "pending_review_count": 1,
            "counts_status": "fresh", "gap": 0, "stop_reason": "TARGET_MET",
            "accepted": accepted, "reserve": reserve}


class ResultViewTest(unittest.TestCase):
    def test_missing_publication_is_explicit_unknown_not_fake_empty_result(self):
        page = build_result_page(TASK, PROPOSAL, None)
        self.assertFalse(page["available"])
        self.assertEqual(page["items"], [])
        self.assertTrue(all(value is None for value in page["counts"].values()))
        self.assertIn("不展示模拟素材", page["note"])

    def test_published_view_has_authoritative_counts_and_stable_pages(self):
        first = build_result_page(TASK, PROPOSAL, published(), limit=2)
        self.assertTrue(first["available"])
        self.assertEqual(first["counts"], {"eligible_unique": 4, "accepted": 3,
            "reserve": 1, "duplicate": 2, "failed": 1, "pending_review": 1,
            "near_duplicate_review": 0, "gap": 0})
        self.assertEqual([item["candidate_id"] for item in first["items"]], ["asset-0", "asset-1"])
        self.assertTrue(all(item["preview_status"] == "unavailable" for item in first["items"]))
        second = build_result_page(TASK, PROPOSAL, published(), limit=2,
                                   cursor=first["next_cursor"])
        self.assertEqual([item["candidate_id"] for item in second["items"]], ["asset-2"])
        self.assertIsNone(second["next_cursor"])

    def test_reserve_is_a_separate_view(self):
        page = build_result_page(TASK, PROPOSAL, published(), view_type="reserve")
        self.assertEqual([item["candidate_id"] for item in page["items"]], ["asset-4"])

    def test_fused_source_evidence_is_visible_without_inflating_counts(self):
        result = published()
        first = {**result["accepted"][0],
                 "candidate_ids": ("asset-0", "asset-copy"),
                 "sources": (
                     {"candidate_id": "asset-0", "binding_id": "binding-a",
                      "library_id": "library-a", "index_version": "v1",
                      "vector_id": "vec-a", "score": 0.9},
                     {"candidate_id": "asset-copy", "binding_id": "binding-b",
                      "library_id": "library-b", "index_version": "v2",
                      "vector_id": 2, "score": 0.8},)}
        result["accepted"] = (first,) + result["accepted"][1:]
        page = build_result_page(TASK, PROPOSAL, result)
        item = page["items"][0]
        self.assertEqual((item["duplicate_candidate_count"], item["source_count"]), (1, 2))
        self.assertEqual([source["library_id"] for source in item["sources"]],
                         ["library-a", "library-b"])
        self.assertEqual(page["counts"]["accepted"], 3)

    def test_near_duplicate_review_is_symmetric_and_does_not_change_counts(self):
        result = published()
        result["near_duplicate_pairs"] = ({
            "candidate_id": "asset-0", "other_candidate_id": "asset-1",
            "hamming_distance": 3, "review_threshold": 6,
            "policy_ref": "near-duplicate-review@1"},)
        result["near_duplicate_review_count"] = 1
        page = build_result_page(TASK, PROPOSAL, result)
        self.assertEqual(page["counts"]["near_duplicate_review"], 1)
        self.assertEqual(page["counts"]["accepted"], 3)
        self.assertEqual(page["items"][0]["near_duplicate_matches"][0]["candidate_id"],
                         "asset-1")
        self.assertEqual(page["items"][1]["near_duplicate_matches"][0]["candidate_id"],
                         "asset-0")

    def test_cursor_is_bound_to_digest_and_view(self):
        cursor = build_result_page(TASK, PROPOSAL, published(), limit=1)["next_cursor"]
        with self.assertRaises(ResultCursorError):
            build_result_page(TASK, PROPOSAL, published(), view_type="reserve", cursor=cursor)
        changed = dict(published(), result_set_digest="sha256:" + "b" * 64)
        with self.assertRaises(ResultCursorError):
            build_result_page(TASK, PROPOSAL, changed, cursor=cursor)

    def test_stale_binding_and_inconsistent_counts_fail_closed(self):
        with self.assertRaises(ValueError):
            build_result_page(TASK, PROPOSAL, dict(published(), generation=2))
        with self.assertRaises(ValueError):
            build_result_page(TASK, PROPOSAL, dict(published(), accepted_count=99))

    def test_limit_and_unpublished_cursor_are_rejected(self):
        with self.assertRaises(ValueError):
            build_result_page(TASK, PROPOSAL, published(), limit=101)
        with self.assertRaises(ResultCursorError):
            build_result_page(TASK, PROPOSAL, None, cursor="not-valid")


if __name__ == "__main__":
    unittest.main()
