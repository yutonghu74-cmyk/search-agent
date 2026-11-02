from dataclasses import replace
import unittest

from dsh_retrieval.errors import ServiceError
from dsh_retrieval.models import Actor
from dsh_retrieval.refill_planning import RefillReservation, build_refill_position
from dsh_retrieval.refill_search import ConfirmedRefillSearchHandler


class Positions:
    def __init__(self, positions):
        self.positions = positions
        self.calls = []

    def list_refill_positions(self, actor, task_id, *, revision, generation):
        self.calls.append((actor, task_id, revision, generation))
        return self.positions


class Executor:
    def __init__(self):
        self.calls = []
        self.override = {}

    def start_refill(self, actor, *, reservation, position, key):
        self.calls.append((actor, reservation, position, key))
        return {"status": "queued", "reservation_id": reservation.reservation_id,
                "revision": reservation.revision, "generation": reservation.generation,
                "iteration": reservation.iteration, **self.override}


class RefillSearchHandlerTest(unittest.TestCase):
    def setUp(self):
        self.actor = Actor("tenant", "owner")
        self.position = build_refill_position(
            library_id="library-a", index_version="index-v1", query="dog",
            parameters={"top_k": 20}, cursor="cursor-2")
        from datetime import UTC, datetime
        self.reservation = RefillReservation(
            "refill-1", "task-1", 2, 3, 4, 1, self.position.library_id,
            self.position.fingerprint, "policy", datetime.now(UTC),
            self.position.binding_json)
        self.positions = Positions((self.position,))
        self.executor = Executor()
        self.handler = ConfirmedRefillSearchHandler(self.positions, self.executor)

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as error:
            call()
        self.assertEqual(error.exception.code, code)

    def dispatch(self, **changes):
        reservation = replace(self.reservation, **changes)
        return self.handler.dispatch_refill(
            self.actor, reservation, key=reservation.reservation_id)

    def test_exact_approved_position_dispatches_with_stable_scope(self):
        result = self.dispatch()
        self.assertEqual(result["status"], "queued")
        self.assertEqual(self.positions.calls[0][1:], ("task-1", 2, 3))
        self.assertEqual(self.executor.calls[0][2]["query"], "dog")
        self.assertEqual(self.executor.calls[0][3], "refill-1")

    def test_unapproved_query_or_changed_binding_never_calls_executor(self):
        self.positions.positions = ()
        self.assert_code("REFILL_POSITION_NOT_APPROVED", self.dispatch)
        self.positions.positions = (build_refill_position(
            library_id="library-a", index_version="index-v1", query="cat",
            parameters={"top_k": 20}, cursor="cursor-2"),)
        self.assert_code("REFILL_POSITION_NOT_APPROVED", self.dispatch)
        self.assertEqual(self.executor.calls, [])

    def test_key_and_executor_scope_mismatch_remain_fail_closed(self):
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: self.handler.dispatch_refill(
            self.actor, self.reservation, key="other"))
        self.executor.override = {"generation": 2}
        self.assert_code("REFILL_DISPATCH_MISMATCH", self.dispatch)

    def test_legacy_reservation_requires_replanning(self):
        self.assert_code("REFILL_POSITION_UNAVAILABLE", lambda: self.dispatch(
            position_binding_json=None))


if __name__ == "__main__":
    unittest.main()
