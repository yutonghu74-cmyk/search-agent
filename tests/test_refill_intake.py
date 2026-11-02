import json
import unittest

from dsh_retrieval.models import Actor
from dsh_retrieval.refill_intake import (
    AutoRefillIntakeCoordinator, AutoRefillIntakeService)


class Tasks:
    def __init__(self, automatic=True):
        modes = dict.fromkeys(
            ("expansion", "retrieval", "cleaning", "refill", "packaging"), "manual")
        if automatic:
            modes.update(retrieval="auto", cleaning="auto", refill="auto")
        self.task = type("Task", (), {
            "confirmed_revision": 1, "run_generation": 1, "task_version": 4,
            "confirmed_proposal_json": json.dumps({
                "node_modes": modes,
                "query_spec": {"refill_policy": {
                    "mode": "maintain_target", "progression": "auto"}}})})()

    def get(self, actor, task_id):
        return self.task


class Events:
    def __init__(self, stage="pending"):
        self.event = {"event_id": "intake-one", "reservation_id": "reservation-one",
                      "tenant_id": "tenant", "owner_user_id": "owner",
                      "task_id": "task", "revision": 1, "generation": 1,
                      "task_version": 4, "stage": stage,
                      "manifest_ref": "manifest-one" if stage != "pending" else None,
                      "cleaning_proposal_digest": "proposal-one"
                      if stage == "cleaning_prepared" else None,
                      "cleaning_expected_task_version": 4
                      if stage == "cleaning_prepared" else None,
                      "candidate_count": 2 if stage == "cleaning_prepared" else None,
                      "vlm_calls": 6 if stage == "cleaning_prepared" else None}
        self.updates = []
        self.delivered = []

    def advance_auto_intake(self, event_id, **values):
        self.updates.append((event_id, values))

    def pending_auto_intake_events(self):
        return (self.event,) if not self.delivered else ()

    def mark_auto_intake_delivered(self, event_id, result):
        self.delivered.append((event_id, result))
        return result

    def reserve_vlm_calls(self, actor, reservation_id, **values):
        self.updates.append((reservation_id, {"budget": values}))
        return True

    def bind_auto_intake_operation(self, event_id, operation_id):
        self.updates.append((event_id, {"operation_id": operation_id}))


class Search:
    mode = "LOCAL_CLIP"

    def __init__(self, count=2):
        self.count = count
        self.confirmed = []

    def preview_manifest(self, actor, task_id):
        return {"candidate_count": self.count, "confirmed": False,
                "proposal_digest": "manifest-proposal",
                "expected_task_version": 4}

    def confirm_manifest(self, actor, task_id, **kwargs):
        self.confirmed.append(kwargs)
        return {"manifest_ref": "manifest-one"}, False


class Cleaning:
    def __init__(self):
        self.prepared = []
        self.confirmed = []

    def prepare(self, actor, task_id, manifest_ref):
        self.prepared.append(manifest_ref)
        return {"proposal_digest": "proposal-one", "expected_task_version": 4,
                "candidate_count": 2, "estimated_vlm_calls": 6}

    def confirm(self, actor, task_id, proposal_digest, **kwargs):
        self.confirmed.append((proposal_digest, kwargs))
        return {"operation_id": "clean-one", "candidate_count": 2}, False


class Dispatch:
    def __init__(self):
        self.notifications = 0

    def notify(self):
        self.notifications += 1


class Refill:
    def advance_empty_intake(self, actor, event):
        return {"status": "stopped", "stop_reason": "NO_PROGRESS"}


class AutoRefillIntakeTest(unittest.TestCase):
    actor = Actor("tenant", "owner")

    def service(self, events, *, tasks=None, search=None):
        cleaning, dispatch = Cleaning(), Dispatch()
        service = AutoRefillIntakeService(
            tasks or Tasks(), events, search or Search(), cleaning, dispatch, Refill())
        return service, cleaning, dispatch

    def test_full_auto_chain_persists_stages_and_queues_vlm(self):
        events = Events()
        service, cleaning, dispatch = self.service(events)
        result = service.consume(self.actor, events.event)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["operation_id"], "clean-one")
        self.assertEqual([update[1]["stage"] for update in events.updates
                          if "stage" in update[1]],
                         ["manifest_confirmed", "cleaning_prepared"])
        self.assertEqual(cleaning.prepared, ["manifest-one"])
        self.assertEqual(dispatch.notifications, 1)

    def test_cleaning_prepared_stage_replays_confirmation_after_version_advance(self):
        events = Events("cleaning_prepared")
        tasks = Tasks()
        tasks.task.task_version = 5
        service, cleaning, dispatch = self.service(events, tasks=tasks)
        result = service.consume(self.actor, events.event)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(cleaning.prepared, [])
        self.assertEqual(cleaning.confirmed[0][1]["expected_task_version"], 4)
        self.assertEqual(dispatch.notifications, 1)

    def test_manual_mode_and_empty_increment_do_not_cross_gates(self):
        events = Events()
        service, cleaning, dispatch = self.service(events, tasks=Tasks(False))
        self.assertEqual(service.consume(self.actor, events.event)["status"], "manual")
        self.assertEqual(cleaning.prepared, [])
        service, cleaning, dispatch = self.service(events, search=Search(0))
        empty = service.consume(self.actor, events.event)
        self.assertEqual((empty["status"], empty["stop_reason"]),
                         ("stopped", "NO_PROGRESS"))
        self.assertEqual(cleaning.prepared, [])

    def test_coordinator_delivers_terminal_result_once(self):
        events = Events()
        service, _, _ = self.service(events)
        coordinator = AutoRefillIntakeCoordinator(events, service, start=False)
        self.addCleanup(coordinator.close)
        coordinator.drain()
        coordinator.drain()
        self.assertEqual(len(events.delivered), 1)


if __name__ == "__main__":
    unittest.main()
