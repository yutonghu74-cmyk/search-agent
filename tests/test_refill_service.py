from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from dsh_retrieval.adapters.task_storage.refill import SQLiteRefillReservationRepository
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.refill_planning import build_refill_position
from dsh_retrieval.refill_service import ConfirmedRefillService
from draft_helpers import BODY, confirmation, make_service


class Authority:
    def require_access(self, context, resource_ref, action):
        if (context.purpose, resource_ref, action) != (
                "refill", "library:library-a", "search"):
            raise ServiceError("ACCESS_DENIED", "denied", 403)


class Adapter:
    def __init__(self, position):
        self.positions = (position,)

    def list_refill_positions(self, actor, task_id, *, revision, generation):
        return self.positions


class Results:
    def __init__(self, view=None):
        self.view = view or {"accepted_count": 1}

    def get_current_result_set(self, actor, task_id):
        return self.view


class Runtime:
    def __init__(self, reservations, position):
        self.reservations = reservations
        self.adapter = Adapter(position)
        self.calls = []

    def dispatch_reservation(self, reservation_id):
        self.calls.append(reservation_id)
        return {"status": "committed", "reservation_id": reservation_id}


class ConfirmedRefillServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tasks.sqlite3"
        self.tasks = SQLiteTaskRepository(self.path)
        self.service = make_service(self.tasks)
        self.actor = Actor("tenant", "owner")
        body = deepcopy(BODY)
        body["query_spec"]["target_count"] = 2
        body["query_spec"]["refill_policy"] = {
            "mode": "maintain_target", "target_count": 2,
            "approved_library_ids": ["library-a"], "progression": "manual",
        }
        created, _ = self.service.create_task(
            self.actor, "task", confirmation(self.service, self.actor, body=body))
        self.task_id = created["task_id"]
        self.position = build_refill_position(
            library_id="library-a", index_version="index-v1", query="原确认检索词",
            parameters={"plan_id": "plan-1"}, cursor="cursor-2")
        reservations = SQLiteRefillReservationRepository(
            self.path, projects=self.service.projects, authorization=Authority())
        self.runtime = Runtime(reservations, self.position)
        self.refill = ConfirmedRefillService(self.tasks, Results(), self.runtime)

    def test_preview_reuses_confirmed_query_and_confirmation_reserves_once(self):
        view = self.refill.preview(self.actor, self.task_id)
        self.assertEqual((view["action"], view["stop_reason"], view["gap"]),
                         ("await_owner", "NEED_USER_INPUT", 1))
        self.assertEqual(view["next_position"]["query"], "原确认检索词")
        args = dict(expected_revision=view["revision"],
                    expected_generation=view["generation"],
                    expected_task_version=view["task_version"],
                    position_fingerprint=view["next_position"]["position_fingerprint"],
                    action="confirm", key="confirm-refill")
        first = self.refill.confirm_next(self.actor, self.task_id, **args)
        waiting = self.refill.preview(self.actor, self.task_id)
        second = self.refill.confirm_next(self.actor, self.task_id, **args)
        self.assertEqual(first["reservation_id"], second["reservation_id"])
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual((waiting["action"], waiting["active_reservation_id"]),
                         ("wait", first["reservation_id"]))
        self.assertEqual(len(self.runtime.calls), 2)

    def test_changed_position_or_missing_confirmation_fails_closed(self):
        view = self.refill.preview(self.actor, self.task_id)
        common = dict(expected_revision=view["revision"],
                      expected_generation=view["generation"],
                      expected_task_version=view["task_version"],
                      position_fingerprint="sha256:" + "0" * 64,
                      key="confirm-refill")
        with self.assertRaises(ServiceError) as error:
            self.refill.confirm_next(self.actor, self.task_id, action="confirm", **common)
        self.assertEqual(error.exception.code, "REFILL_POSITION_NOT_APPROVED")
        with self.assertRaises(ServiceError) as error:
            self.refill.confirm_next(self.actor, self.task_id, action="preview", **common)
        self.assertEqual(error.exception.code, "CONFIRMATION_REQUIRED")

    def test_confirmed_auto_event_reserves_exact_position_idempotently(self):
        body = deepcopy(BODY)
        body["query_spec"]["target_count"] = 2
        body["query_spec"]["refill_policy"] = {
            "mode": "maintain_target", "target_count": 2,
            "approved_library_ids": ["library-a"], "progression": "auto",
        }
        body["node_modes"]["refill"] = "auto"
        created, _ = self.service.create_task(
            self.actor, "auto-task", confirmation(
                self.service, self.actor, key="auto-task", body=body))
        task = self.tasks.get(self.actor, created["task_id"])
        runtime = Runtime(self.runtime.reservations, self.position)
        results = Results({"accepted_count": 1, "result_set_id": "result-auto",
                           "as_of_task_version": task.task_version})
        service = ConfirmedRefillService(self.tasks, results, runtime)
        event = {"task_id": task.task_id, "revision": task.confirmed_revision,
                 "generation": task.run_generation, "task_version": task.task_version,
                 "result_set_id": "result-auto"}
        first = service.advance_auto_event(self.actor, event)
        second = service.advance_auto_event(self.actor, event)
        self.assertEqual(first["reservation_id"], second["reservation_id"])
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["position_fingerprint"], self.position.fingerprint)

    def test_confirmed_round_budget_stops_before_another_position(self):
        body = deepcopy(BODY)
        body["query_spec"]["target_count"] = 2
        body["query_spec"]["refill_policy"] = {
            "mode": "maintain_target", "target_count": 2,
            "approved_library_ids": ["library-a"], "progression": "auto"}
        body["query_spec"]["refill_limits"] = {
            "max_rounds": 1, "max_vlm_calls": None, "no_progress_rounds": 2}
        body["node_modes"]["refill"] = "auto"
        created, _ = self.service.create_task(
            self.actor, "limited", confirmation(
                self.service, self.actor, key="limited", body=body))
        task = self.tasks.get(self.actor, created["task_id"])
        runtime = Runtime(self.runtime.reservations, self.position)
        results = Results({"accepted_count": 1, "result_set_id": "limited-result",
                           "as_of_task_version": task.task_version})
        service = ConfirmedRefillService(self.tasks, results, runtime)
        event = {"task_id": task.task_id, "revision": task.confirmed_revision,
                 "generation": task.run_generation, "task_version": task.task_version,
                 "result_set_id": "limited-result"}
        first = service.advance_auto_event(self.actor, event)
        self.assertEqual(first["status"], "committed")
        runtime.adapter.positions = (build_refill_position(
            library_id="library-a", index_version="index-v1", query="第二位置",
            parameters={"plan_id": "plan-2"}, depth=40),)
        waiting = service.preview(self.actor, task.task_id)
        self.assertEqual((waiting["action"], waiting["active_reservation_id"]),
                         ("wait", first["reservation_id"]))
        runtime.reservations.complete_empty_iteration(
            self.actor, first["reservation_id"], 1)
        stopped = service.advance_auto_event(self.actor, event)
        self.assertEqual((stopped["status"], stopped["stop_reason"]),
                         ("stopped", "EXECUTION_LIMIT_REACHED"))
        self.assertEqual(runtime.reservations.execution_evidence(
            self.actor, task.task_id)["stop_reason"], "EXECUTION_LIMIT_REACHED")


if __name__ == "__main__":
    unittest.main()
