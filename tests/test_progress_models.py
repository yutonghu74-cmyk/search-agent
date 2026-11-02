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
    def test_raw_hit_count_is_separate_from_cleaned_effective_count(self):
        snapshot = build_task_progress(task(), PROPOSAL, raw_returned=5)
        self.assertEqual(snapshot.counts['returned'], 5)
        self.assertIsNone(snapshot.counts['accepted'])
        for invalid in (-1, True, '5'):
            with self.assertRaises(ValueError):
                build_task_progress(task(), PROPOSAL, raw_returned=invalid)

    def test_published_preview_counts_and_target_met_do_not_claim_delivery(self):
        from test_result_views import published, TASK
        proposal = {**PROPOSAL, "query_spec": {"target_count": 3}}
        snapshot = build_task_progress({**TASK, "run_status": "RUNNING", "stage": "refill"}, proposal, published())
        self.assertEqual(snapshot.counts["accepted"], 3)
        self.assertEqual(snapshot.counts["reserve"], 1)
        self.assertEqual(snapshot.counts["passed"], 6)
        self.assertEqual(snapshot.counts["duplicate"], 2)
        self.assertEqual(snapshot.counts["pending_review"], 1)
        self.assertEqual(snapshot.counts["near_duplicate_review"], 0)
        self.assertEqual(snapshot.nodes[3].node_status, "succeeded")
        self.assertEqual(snapshot.nodes[4].node_status, "pending")
        self.assertIn("尚不代表", snapshot.note)

    def test_stale_publication_cannot_supply_progress_counts(self):
        from test_result_views import published, TASK
        with self.assertRaises(ValueError):
            build_task_progress({**TASK, "run_generation": 4},
                                {**PROPOSAL, "query_spec": {"target_count": 3}}, published())

    def test_shortfall_is_waiting_not_running_an_unimplemented_refill(self):
        from test_result_views import published, TASK
        result = {**published(), "target_count": 5, "gap": 2}
        snapshot = build_task_progress({**TASK, "run_status": "RUNNING", "stage": "refill"},
                                      {**PROPOSAL, "query_spec": {"target_count": 5}}, result)
        self.assertEqual(snapshot.nodes[3].node_status, "pending")
        self.assertIn("缺口 2", snapshot.nodes[3].detail)

    def test_auto_refill_shortfall_reports_its_real_stop_reason(self):
        from test_result_views import published, TASK
        proposal = {**PROPOSAL,
            'query_spec': {'target_count': 5, 'refill_policy': {
                'mode': 'maintain_target', 'target_count': 5,
                'approved_library_ids': ['library-a'], 'progression': 'auto'}},
            'node_modes': {**PROPOSAL['node_modes'], 'refill': 'auto'}}
        result = {**published(), 'target_count': 5, 'gap': 2,
                  'stop_reason': 'NO_PROGRESS'}
        snapshot = build_task_progress(
            {**TASK, 'run_status': 'RUNNING', 'stage': 'refill'}, proposal, result)
        self.assertIn('自动补数已停止：连续补数没有新增有效结果',
                      snapshot.nodes[3].detail)
        self.assertNotIn('固定输入', snapshot.nodes[3].detail)

    def test_stopped_refill_waits_for_partial_delivery_choice(self):
        from test_result_views import published, TASK
        proposal = {**PROPOSAL,
            'query_spec': {'target_count': 5, 'refill_policy': {
                'mode': 'maintain_target', 'target_count': 5,
                'approved_library_ids': ['library-a'], 'progression': 'auto'}},
            'node_modes': {**PROPOSAL['node_modes'], 'refill': 'auto'}}
        result = {**published(), 'target_count': 5, 'gap': 2,
                  'stop_reason': 'NO_PROGRESS'}
        snapshot = build_task_progress(
            {**TASK, 'run_status': 'AWAITING_INPUT', 'stage': 'refill'}, proposal, result)
        self.assertEqual(snapshot.nodes[3].node_status, 'succeeded')
        self.assertEqual(snapshot.nodes[4].gate_status, 'awaiting_approval')
        self.assertIn('部分交付', snapshot.nodes[4].detail)

    def test_stopped_refill_with_no_valid_media_does_not_offer_empty_delivery(self):
        from test_result_views import published, TASK
        proposal = {**PROPOSAL,
            'query_spec': {'target_count': 5, 'refill_policy': {
                'mode': 'maintain_target', 'target_count': 5,
                'approved_library_ids': ['library-a'], 'progression': 'auto'}},
            'node_modes': {**PROPOSAL['node_modes'], 'refill': 'auto'}}
        result = {**published(), 'accepted': (), 'reserve': (),
                  'accepted_count': 0, 'reserve_count': 0,
                  'eligible_unique_count': 0, 'target_count': 5, 'gap': 5,
                  'stop_reason': 'NO_PROGRESS'}
        snapshot = build_task_progress(
            {**TASK, 'run_status': 'AWAITING_INPUT', 'stage': 'refill'}, proposal, result)
        self.assertIn('不能生成空交付包', snapshot.nodes[4].detail)
        self.assertNotIn('可部分交付', snapshot.nodes[3].detail)

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
