import unittest

from dsh_retrieval.progress_models import build_task_progress


PROPOSAL = {"query_spec": {"target_count": 10},
            "node_modes": {"expansion": "manual", "retrieval": "manual",
                           "cleaning": "manual", "refill": "manual", "packaging": "manual"}}


def task(**changes):
    value = {"task_id": "task_1", "confirmed_revision": 1, "task_version": 2,
             "run_generation": 1, "run_status": "PLANNING", "stage": None}
    value.update(changes)
    return value


class ProgressModelTest(unittest.TestCase):
    def test_planning_shows_only_expansion_running_and_unknown_counts(self):
        view = build_task_progress(task(), PROPOSAL).to_view()
        self.assertEqual([item["node_status"] for item in view["nodes"]],
                         ["running", "pending", "pending", "pending", "pending"])
        self.assertEqual(view["counts"]["target"], 10)
        self.assertIsNone(view["counts"]["accepted"])
        self.assertIsNone(view["counts"]["gap"])
        self.assertNotIn("percent", view)

    def test_known_stages_only_advance_proven_predecessors(self):
        retrieval = build_task_progress(task(run_status="RUNNING", stage="retrieving"), PROPOSAL)
        self.assertEqual([node.node_status for node in retrieval.nodes],
                         ["succeeded", "running", "pending", "pending", "pending"])
        cleaning = build_task_progress(task(run_status="RUNNING", stage="multi_rule_cleaning"), PROPOSAL)
        self.assertEqual([node.node_status for node in cleaning.nodes],
                         ["succeeded", "succeeded", "running", "pending", "pending"])
        packaging = build_task_progress(task(run_status="RUNNING", stage="FINALIZING"), PROPOSAL)
        self.assertEqual([node.node_status for node in packaging.nodes],
                         ["succeeded", "succeeded", "succeeded", "succeeded", "running"])

    def test_finished_cleaning_waits_for_manual_gate(self):
        snapshot = build_task_progress(task(run_status="RUNNING", stage="cleaning_finished"), PROPOSAL)
        cleaning = snapshot.nodes[2]
        self.assertEqual((cleaning.node_status, cleaning.gate_status),
                         ("succeeded", "awaiting_approval"))

    def test_unknown_stage_does_not_invent_completed_nodes(self):
        snapshot = build_task_progress(task(run_status="RUNNING", stage="future_stage"), PROPOSAL)
        self.assertEqual({node.node_status for node in snapshot.nodes}, {"pending"})

    def test_invalid_binding_modes_and_target_fail_closed(self):
        for changed_task, proposal in (
            (task(task_version=True), PROPOSAL),
            (task(), {**PROPOSAL, "query_spec": {"target_count": 0}}),
            (task(), {**PROPOSAL, "node_modes": {"expansion": "manual"}}),
        ):
            with self.subTest(task=changed_task, proposal=proposal), self.assertRaises(ValueError):
                build_task_progress(changed_task, proposal)


if __name__ == "__main__":
    unittest.main()
