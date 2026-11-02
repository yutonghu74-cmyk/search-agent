"""Fail-closed bridge from a refill reservation to an approved search executor."""

from .errors import ServiceError
from .refill_planning import RefillPosition, parse_refill_position


class ConfirmedRefillSearchHandler:
    """Resolve the current approved position, then call an idempotent executor.

    ``approved_positions`` derives positions from server-owned current plan
    state; callers cannot make an arbitrary query approved by merely choosing
    an allowed library. ``executor`` must persist/deduplicate ``key``.
    """

    def __init__(self, approved_positions, executor):
        if not hasattr(approved_positions, "list_refill_positions"):
            raise TypeError("approved position resolver is required")
        if not hasattr(executor, "start_refill"):
            raise TypeError("refill search executor is required")
        self.approved_positions = approved_positions
        self.executor = executor

    def dispatch_refill(self, actor, reservation, *, key):
        if key != reservation.reservation_id:
            raise ServiceError("IDEMPOTENCY_CONFLICT",
                               "refill dispatch key differs from reservation", 409)
        if reservation.position_binding_json is None:
            raise ServiceError("REFILL_POSITION_UNAVAILABLE",
                               "refill position must be planned again", 409)
        reserved = RefillPosition(reservation.library_id,
                                  reservation.position_fingerprint,
                                  reservation.position_binding_json)
        try:
            binding = parse_refill_position(reserved)
            positions = self.approved_positions.list_refill_positions(
                actor, reservation.task_id, revision=reservation.revision,
                generation=reservation.generation)
        except ValueError as exc:
            raise ServiceError("REFILL_POSITION_INVALID", str(exc), 409) from None
        if (not isinstance(positions, tuple) or len(positions) > 256
                or any(type(position) is not RefillPosition for position in positions)):
            raise ServiceError("REFILL_PLAN_INVALID", "approved refill positions are invalid", 500)
        approved = next((position for position in positions
                         if position.fingerprint == reserved.fingerprint), None)
        if approved is None:
            raise ServiceError("REFILL_POSITION_NOT_APPROVED",
                               "query position is outside the current approved plan", 409)
        try:
            approved_binding = parse_refill_position(approved)
        except ValueError:
            raise ServiceError("REFILL_PLAN_INVALID",
                               "approved refill position is invalid", 500) from None
        if approved != reserved or approved_binding != binding:
            raise ServiceError("REFILL_POSITION_NOT_APPROVED",
                               "approved query position binding changed", 409)
        result = self.executor.start_refill(
            actor, reservation=reservation, position=binding, key=key)
        if (not isinstance(result, dict)
                or result.get("reservation_id") != reservation.reservation_id
                or result.get("revision") != reservation.revision
                or result.get("generation") != reservation.generation
                or result.get("iteration") != reservation.iteration
                or result.get("status") not in {"queued", "committed", "replayed"}):
            raise ServiceError("REFILL_DISPATCH_MISMATCH",
                               "search executor returned another execution scope", 502,
                               retryable=True)
        return result
