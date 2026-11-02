import unittest

from dsh_retrieval.refill_planning import (RefillPosition, build_refill_position, decide_refill,
                                           parse_refill_position)


class RefillPlanningTest(unittest.TestCase):
    def setUp(self):
        self.policy = {"mode": "maintain_target", "target_count": 2,
                       "approved_library_ids": ["library-a"], "progression": "auto"}
        self.first = RefillPosition("library-a", "index-v1:q1:page-2")
        self.second = RefillPosition("library-a", "index-v1:q2:page-1")

    def decide(self, **changes):
        args = {"target_count": 2, "accepted_count": 1, "policy": self.policy,
                "node_mode": "auto", "positions": (self.first, self.second)}
        args.update(changes)
        return decide_refill(**args)

    def test_target_met_and_input_only_do_not_dispatch(self):
        self.assertEqual(self.decide(accepted_count=2).stop_reason, "TARGET_MET")
        input_only = {"mode": "input_only", "target_count": 2,
                      "approved_library_ids": [], "progression": "manual"}
        decision = self.decide(policy=input_only, positions=())
        self.assertEqual((decision.action, decision.stop_reason, decision.gap),
                         ("partial_delivery", "INPUT_SCOPE_COMPLETE", 1))

    def test_auto_chooses_only_unissued_position(self):
        decision = self.decide(issued_fingerprints=frozenset({self.first.fingerprint}))
        self.assertEqual((decision.action, decision.next_position), ("search_more", self.second))
        self.assertIsNone(decision.stop_reason)
        self.assertEqual(self.decide(issued_fingerprints=frozenset(
            {self.first.fingerprint, self.second.fingerprint})).stop_reason,
            "SEARCH_STRATEGIES_EXHAUSTED")

    def test_manual_gate_and_budget_prevent_dispatch(self):
        self.assertEqual(self.decide(node_mode="manual").action, "await_owner")
        self.assertEqual(self.decide(policy={**self.policy, "progression": "manual"}).action,
                         "await_owner")
        self.assertEqual(self.decide(remaining_batches=0).stop_reason,
                         "EXECUTION_LIMIT_REACHED")

    def test_no_progress_stops_even_when_more_positions_exist(self):
        decision = self.decide(stagnant_rounds=3, no_progress_limit=3)
        self.assertEqual(decision.stop_reason, "NO_PROGRESS")

    def test_rejects_unapproved_library_and_invalid_evidence(self):
        with self.assertRaises(ValueError):
            self.decide(positions=(RefillPosition("library-b", "q1"),))
        with self.assertRaises(ValueError):
            self.decide(accepted_count=3)
        with self.assertRaises(ValueError):
            self.decide(policy={**self.policy, "approved_library_ids": []})
        with self.assertRaises(ValueError):
            self.decide(policy={"mode": "input_only", "target_count": 2,
                                "approved_library_ids": [], "progression": "manual"})
        with self.assertRaises(ValueError):
            self.decide(remaining_batches=-1)
        with self.assertRaises(ValueError):
            self.decide(issued_fingerprints={self.first.fingerprint})

    def test_position_fingerprint_binds_index_query_parameters_and_position(self):
        first = build_refill_position(library_id="library-a", index_version="v1", query="dog",
                                      parameters={"top_k": 20}, cursor="cursor-2")
        replay = build_refill_position(library_id="library-a", index_version="v1", query="dog",
                                       parameters={"top_k": 20}, cursor="cursor-2")
        deeper = build_refill_position(library_id="library-a", index_version="v1", query="dog",
                                       parameters={"top_k": 20}, depth=40)
        self.assertEqual(first, replay)
        self.assertNotEqual(first.fingerprint, deeper.fingerprint)
        self.assertEqual(parse_refill_position(first)["position"],
                         {"cursor": "cursor-2", "depth": None})
        forged = RefillPosition(first.library_id, first.fingerprint,
                                first.binding_json.replace("cursor-2", "cursor-3"))
        with self.assertRaises(ValueError):
            parse_refill_position(forged)
        with self.assertRaises(ValueError):
            build_refill_position(library_id="library-a", index_version="v1", query="dog",
                                  parameters={}, cursor=None, depth=None)


if __name__ == "__main__":
    unittest.main()
