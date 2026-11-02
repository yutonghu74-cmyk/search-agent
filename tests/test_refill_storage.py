from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from dsh_retrieval.adapters.task_storage.refill import SQLiteRefillReservationRepository
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository, dump
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.refill_dispatch import RefillDispatchCoordinator
from dsh_retrieval.refill_planning import RefillPosition, build_refill_position
from dsh_retrieval.refill_search import ConfirmedRefillSearchHandler
from draft_helpers import BODY, confirmation, make_service


class Authority:
    def __init__(self):
        self.denied = set()

    def require_access(self, context, resource_ref, action):
        if resource_ref in self.denied:
            raise ServiceError("ACCESS_DENIED", "revoked", 403)


class Handler:
    def __init__(self):
        self.calls = []
        self.error = None

    def dispatch_refill(self, actor, reservation, *, key):
        self.calls.append((actor, reservation, key))
        if self.error is not None:
            raise self.error
        return {"status": "accepted", "reservation_id": reservation.reservation_id,
                "iteration": reservation.iteration}


class RefillStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.tasks = SQLiteTaskRepository(self.path)
        self.service = make_service(self.tasks)
        self.actor = Actor("tenant", "owner")
        body = deepcopy(BODY)
        body["query_spec"]["target_count"] = 2
        body["query_spec"]["refill_policy"] = {
            "mode": "maintain_target", "target_count": 2,
            "approved_library_ids": ["library-a"], "progression": "manual",
        }
        task, _ = self.service.create_task(
            self.actor, "task", confirmation(self.service, self.actor, body=body))
        self.task_id = task["task_id"]
        self.authority = Authority()
        self.repo = self.reopen()
        self.first = build_refill_position(
            library_id="library-a", index_version="index-v1", query="dog",
            parameters={"top_k": 20}, cursor="cursor-2")
        self.second = build_refill_position(
            library_id="library-a", index_version="index-v1", query="dog closeup",
            parameters={"top_k": 20}, depth=40)

    def reopen(self):
        return SQLiteRefillReservationRepository(
            self.path, projects=self.service.projects, authorization=self.authority)

    def binding(self):
        task = self.tasks.get(self.actor, self.task_id)
        return dict(expected_revision=task.confirmed_revision,
                    expected_generation=task.run_generation,
                    expected_task_version=task.task_version)

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as error:
            call()
        self.assertEqual(error.exception.code, code)

    def change_task(self, **changes):
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            memory = self.tasks._load(db.execute(
                "SELECT format,payload FROM local_ledger WHERE id=1").fetchone())
            memory._tasks[self.task_id] = replace(memory._tasks[self.task_id], **changes)
            db.execute("UPDATE local_ledger SET payload=? WHERE id=1", (dump(memory),))

    def test_reservation_is_atomic_replayable_and_recovers_after_restart(self):
        first, replayed = self.repo.reserve(self.actor, self.task_id, self.first, **self.binding())
        self.assertFalse(replayed)
        self.assertEqual(first.iteration, 1)
        self.assertEqual(first.position_binding_json, self.first.binding_json)
        self.assertEqual(self.repo.reserve(self.actor, self.task_id, self.first, **self.binding()),
                         (first, True))
        second, replayed = self.reopen().reserve(
            self.actor, self.task_id, self.second, **self.binding())
        self.assertFalse(replayed)
        self.assertEqual(second.iteration, 2)
        self.assertEqual(self.reopen().list_current(self.actor, self.task_id), (first, second))

    def test_concurrent_replay_allocates_only_one_iteration(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(
                lambda _: self.reopen().reserve(
                    self.actor, self.task_id, self.first, **self.binding()), range(12)))
        self.assertEqual(sum(not replayed for _, replayed in results), 1)
        self.assertEqual({record.iteration for record, _ in results}, {1})

    def test_scope_authority_and_generation_are_fail_closed(self):
        outside = build_refill_position(
            library_id="library-b", index_version="v1", query="dog",
            parameters={}, depth=1)
        self.assert_code("REFILL_SCOPE_EXCEEDED", lambda: self.repo.reserve(
            self.actor, self.task_id, outside, **self.binding()))
        self.authority.denied.add("library:library-a")
        self.assert_code("ACCESS_DENIED", lambda: self.repo.reserve(
            self.actor, self.task_id, self.first, **self.binding()))
        self.authority.denied.clear()
        stale = self.binding()
        self.change_task(run_generation=stale["expected_generation"] + 1)
        self.assert_code("REVISION_CONFLICT", lambda: self.repo.reserve(
            self.actor, self.task_id, self.first, **stale))
        self.assertEqual(self.repo.list_current(self.actor, self.task_id), ())

    def test_fixed_input_task_cannot_reserve_refill_work(self):
        other_tasks = SQLiteTaskRepository(Path(self.temp.name) / "fixed.sqlite3")
        service = make_service(other_tasks)
        task, _ = service.create_task(self.actor, "fixed", confirmation(service, self.actor))
        repo = SQLiteRefillReservationRepository(
            other_tasks.path, projects=service.projects, authorization=self.authority)
        current = other_tasks.get(self.actor, task["task_id"])
        self.assert_code("REFILL_NOT_APPROVED", lambda: repo.reserve(
            self.actor, task["task_id"], self.first,
            expected_revision=current.confirmed_revision,
            expected_generation=current.run_generation,
            expected_task_version=current.task_version))

    def test_hard_delete_conversation_purges_reservations(self):
        self.repo.reserve(self.actor, self.task_id, self.first, **self.binding())
        task = self.tasks.get(self.actor, self.task_id)
        self.tasks.hard_delete_conversation(self.actor, task.conversation_id)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM refill_reservations WHERE task_id=?",
                (self.task_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM refill_dispatch_outbox WHERE task_id=?",
                (self.task_id,)).fetchone()[0], 0)
        # The refill table is a recognized sibling ledger on the next open.
        SQLiteTaskRepository(self.path)

    def test_dispatch_outbox_recovers_and_delivers_once(self):
        reservation, _ = self.repo.reserve(
            self.actor, self.task_id, self.first, **self.binding())
        handler = Handler()
        coordinator = RefillDispatchCoordinator(self.reopen(), handler, start=False)
        result = coordinator.dispatch_reservation(reservation.reservation_id)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(len(handler.calls), 1)
        self.assertEqual(handler.calls[0][2], reservation.reservation_id)
        self.assertEqual(self.reopen().pending_dispatches(), ())
        self.assertEqual(coordinator.dispatch_reservation(reservation.reservation_id), result)
        self.assertEqual(len(handler.calls), 1)

    def test_committed_dispatch_creates_durable_auto_intake_event(self):
        reservation, _ = self.repo.reserve(
            self.actor, self.task_id, self.first, **self.binding())
        event = self.repo.pending_dispatches()[0]
        self.repo.mark_dispatched(event["event_id"], {
            "status": "committed", "reservation_id": reservation.reservation_id})
        intake = self.reopen().pending_auto_intake_events()
        self.assertEqual(len(intake), 1)
        self.assertEqual(intake[0]["reservation_id"], reservation.reservation_id)
        self.assertEqual(intake[0]["stage"], "pending")
        self.repo.advance_auto_intake(
            intake[0]["event_id"], stage="manifest_confirmed",
            manifest_ref="manifest-one")
        self.repo.advance_auto_intake(
            intake[0]["event_id"], stage="cleaning_prepared",
            cleaning_proposal_digest="sha256:" + "a" * 64,
            cleaning_expected_task_version=3)
        recovered = self.reopen().pending_auto_intake_events()[0]
        self.assertEqual(recovered["manifest_ref"], "manifest-one")
        self.assertEqual(recovered["cleaning_expected_task_version"], 3)
        result = {"status": "queued", "operation_id": "clean-one"}
        self.assertEqual(self.repo.mark_auto_intake_delivered(
            recovered["event_id"], result), result)
        self.assertEqual(self.reopen().pending_auto_intake_events(), ())

    def test_iteration_evidence_persists_no_progress_and_vlm_budget_stop(self):
        first, _ = self.repo.reserve(
            self.actor, self.task_id, self.first, **self.binding())
        self.repo.record_iteration_baseline(self.actor, first.reservation_id, 1)
        self.assertTrue(self.repo.reserve_vlm_calls(
            self.actor, first.reservation_id, candidate_count=2,
            vlm_calls=4, max_vlm_calls=5))
        self.repo.complete_empty_iteration(self.actor, first.reservation_id, 1)
        second, _ = self.repo.reserve(
            self.actor, self.task_id, self.second, **self.binding())
        self.repo.record_iteration_baseline(self.actor, second.reservation_id, 1)
        self.assertFalse(self.repo.reserve_vlm_calls(
            self.actor, second.reservation_id, candidate_count=1,
            vlm_calls=2, max_vlm_calls=5))
        evidence = self.reopen().execution_evidence(self.actor, self.task_id)
        self.assertEqual(evidence["issued_rounds"], 2)
        self.assertEqual(evidence["completed_rounds"], 1)
        self.assertEqual(evidence["stagnant_rounds"], 1)
        self.assertEqual(evidence["vlm_calls"], 4)
        self.assertEqual(evidence["stop_reason"], "VLM_CALL_LIMIT_REACHED")
        self.assertEqual(evidence["rounds"], [
            {"iteration": 1, "baseline_accepted": 1, "candidate_count": 2,
             "vlm_calls": 4, "outcome": "empty", "accepted_after": 1,
             "new_unique_accepted": 0},
            {"iteration": 2, "baseline_accepted": 1, "candidate_count": 1,
             "vlm_calls": 0, "outcome": "stopped", "accepted_after": None,
             "new_unique_accepted": None},
        ])

    def test_transient_dispatch_failure_stays_pending_then_recovers(self):
        reservation, _ = self.repo.reserve(
            self.actor, self.task_id, self.first, **self.binding())
        handler = Handler()
        handler.error = ServiceError("DEPENDENCY_UNAVAILABLE", "temporary", 503,
                                     retryable=True)
        coordinator = RefillDispatchCoordinator(self.repo, handler, start=False)
        self.assertEqual(coordinator.dispatch_reservation(reservation.reservation_id)["status"],
                         "queued")
        self.assertEqual(len(self.repo.pending_dispatches()), 1)
        handler.error = None
        self.assertEqual(coordinator.dispatch_reservation(reservation.reservation_id)["status"],
                         "accepted")
        self.assertEqual(len(handler.calls), 2)

    def test_stale_generation_is_superseded_before_handler(self):
        reservation, _ = self.repo.reserve(
            self.actor, self.task_id, self.first, **self.binding())
        current = self.binding()
        self.change_task(run_generation=current["expected_generation"] + 1)
        handler = Handler()
        result = RefillDispatchCoordinator(
            self.reopen(), handler, start=False).dispatch_reservation(reservation.reservation_id)
        self.assertEqual(result["status"], "superseded")
        self.assertEqual(handler.calls, [])

    def test_pre_outbox_reservation_is_backfilled_on_reopen(self):
        reservation, _ = self.repo.reserve(
            self.actor, self.task_id, self.first, **self.binding())
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM refill_dispatch_outbox WHERE reservation_id=?",
                       (reservation.reservation_id,))
        reopened = self.reopen()
        self.assertEqual(reopened.pending_dispatches()[0]["reservation_id"],
                         reservation.reservation_id)

    def test_revoked_library_keeps_dispatch_pending(self):
        reservation, _ = self.repo.reserve(
            self.actor, self.task_id, self.first, **self.binding())
        self.authority.denied.add("library:library-a")
        handler = Handler()
        result = RefillDispatchCoordinator(
            self.repo, handler, start=False).dispatch_reservation(reservation.reservation_id)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(handler.calls, [])
        self.assertEqual(len(self.repo.pending_dispatches()), 1)

    def test_forged_or_legacy_position_never_reaches_handler(self):
        forged = RefillPosition(self.first.library_id, self.first.fingerprint,
                                self.first.binding_json.replace("cursor-2", "cursor-3"))
        self.assert_code("INVALID_SPEC", lambda: self.repo.reserve(
            self.actor, self.task_id, forged, **self.binding()))
        reservation, _ = self.repo.reserve(
            self.actor, self.task_id, self.first, **self.binding())
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE refill_reservations SET position_json=NULL WHERE reservation_id=?",
                       (reservation.reservation_id,))
        handler = Handler()
        result = RefillDispatchCoordinator(
            self.reopen(), handler, start=False).dispatch_reservation(reservation.reservation_id)
        self.assertEqual(result["status"], "superseded")
        self.assertEqual(handler.calls, [])

    def test_outbox_through_confirmed_position_bridge_reaches_executor(self):
        reservation, _ = self.repo.reserve(
            self.actor, self.task_id, self.first, **self.binding())

        class Resolver:
            def list_refill_positions(inner, actor, task_id, *, revision, generation):
                return (self.first,)

        class SearchExecutor:
            def __init__(inner):
                inner.calls = []

            def start_refill(inner, actor, *, reservation, position, key):
                inner.calls.append((actor, reservation, position, key))
                return {"status": "queued", "reservation_id": reservation.reservation_id,
                        "revision": reservation.revision,
                        "generation": reservation.generation,
                        "iteration": reservation.iteration}

        executor = SearchExecutor()
        handler = ConfirmedRefillSearchHandler(Resolver(), executor)
        result = RefillDispatchCoordinator(
            self.repo, handler, start=False).dispatch_reservation(reservation.reservation_id)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(executor.calls[0][2]["index_version"], "index-v1")
        self.assertEqual(executor.calls[0][3], reservation.reservation_id)


if __name__ == "__main__":
    unittest.main()
