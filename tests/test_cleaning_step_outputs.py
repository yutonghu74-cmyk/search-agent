import unittest

from dsh_retrieval.cleaning_step_outputs import (
    StepOutputCursorError, build_step_output_page,
)


def complete_view():
    items = tuple({"candidate_id": f"candidate_{index}", "asset_version": "v1",
                   "status": ("retained" if index < 2 else
                              "rejected" if index == 2 else "review"),
                   "preview_status": "unavailable",
                   "reasons": ({"rule_id": "rule-1",
                                "verdict": ("pass" if index < 2 else
                                            "fail" if index == 2 else "unknown"),
                                "reason_code": ("MATCH" if index < 2 else
                                                "MISMATCH" if index == 2
                                                else "FACT_UNAVAILABLE"),
                                "reason_summary": None,
                                "reason_truncated": False},)}
                  for index in range(4))
    step = {"step_id": "step-1", "step_execution_id": "step_" + "a" * 64,
            "input_version": "sha256:" + "b" * 64, "backend_ref": "vlm@1",
            "status": "completed", "counts": {"input": 4, "retained": 2,
                "rejected": 1, "review": 1, "pending": 0, "blocked": 0},
            "items": items, "rules": []}
    return {"task_id": "task-1", "revision": 2, "generation": 3,
            "available": True, "candidate_preview_available": True,
            "runs": [{"operation_id": "operation-1",
                      "output_version": "sha256:" + "c" * 64,
                      "execution_mode": "shared_input_detection", "steps": [step]}]}


class CleaningStepOutputsTest(unittest.TestCase):
    def page(self, **values):
        defaults = {"operation_id": "operation-1",
                    "step_execution_id": "step_" + "a" * 64,
                    "output_version": "sha256:" + "c" * 64}
        return build_step_output_page(complete_view(), **(defaults | values))

    def test_stable_pages_and_independent_views(self):
        first = self.page(limit=2, as_of_sequence=7)
        self.assertEqual((first["total_items"], first["as_of_sequence"]), (4, 7))
        self.assertEqual([item["candidate_id"] for item in first["items"]],
                         ["candidate_0", "candidate_1"])
        self.assertTrue(all(item["preview_status"] == "available"
                            for item in first["items"]))
        second = self.page(limit=2, cursor=first["next_cursor"])
        self.assertEqual([item["candidate_id"] for item in second["items"]],
                         ["candidate_2", "candidate_3"])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(self.page(view_type="rejected")["items"][0]["candidate_id"],
                         "candidate_2")
        self.assertEqual(self.page(view_type="review")["items"][0]["candidate_id"],
                         "candidate_3")
        self.assertEqual(self.page(view_type="pending")["items"], [])
        self.assertEqual(self.page(view_type="blocked")["items"], [])

    def test_reason_filter_and_cursor_binding(self):
        rejected = self.page(view_type="input", reason_code="MISMATCH")
        self.assertEqual(rejected["total_items"], 1)
        cursor = self.page(limit=1)["next_cursor"]
        with self.assertRaises(StepOutputCursorError):
            self.page(view_type="retained", cursor=cursor)
        with self.assertRaises(StepOutputCursorError):
            self.page(output_version="sha256:" + "d" * 64, cursor=cursor)
        with self.assertRaises(StepOutputCursorError):
            self.page(cursor=cursor[:-2] + "xx")

    def test_global_rule_search_and_cursor_binding(self):
        self.assertEqual(self.page(rule_id='rule-1', query='证据不足')['total_items'], 1)
        self.assertEqual(self.page(rule_id='missing')['items'], [])
        cursor = self.page(rule_id='rule-1', query='符合条件', limit=1)['next_cursor']
        self.assertIsNotNone(cursor)
        with self.assertRaises(StepOutputCursorError):
            self.page(rule_id='rule-1', query='证据不足', cursor=cursor)
        with self.assertRaises(StepOutputCursorError):
            self.page(cursor=cursor)
        with self.assertRaises(ValueError):
            self.page(query='x' * 201)

    def test_invalid_view_limit_and_unavailable_output_fail_closed(self):
        with self.assertRaises(ValueError):
            self.page(view_type="all")
        with self.assertRaises(ValueError):
            self.page(limit=101)
        view = complete_view()
        view["available"] = False
        with self.assertRaises(ValueError):
            build_step_output_page(view, operation_id="operation-1",
                step_execution_id="step_" + "a" * 64,
                output_version="sha256:" + "c" * 64)


if __name__ == "__main__":
    unittest.main()
