"""Application wiring for durable approved-position refill dispatch."""

from .adapters.task_storage.refill import SQLiteRefillReservationRepository
from .refill_clip import CombinedCLIPRefillAdapter, LocalCLIPRefillAdapter
from .refill_dispatch import RefillDispatchCoordinator
from .refill_search import ConfirmedRefillSearchHandler


class RefillRuntime:
    """Own the refill reservation ledger and its recoverable dispatcher."""

    def __init__(self, ledger_path, service, first_search, *, start=True,
                 poll_seconds=1.0):
        mode = getattr(first_search, "mode", None)
        if mode == "LOCAL_CLIP":
            adapter = LocalCLIPRefillAdapter(first_search)
        elif mode == "CLIP_COMBINED":
            adapter = CombinedCLIPRefillAdapter(first_search)
        else:
            raise ValueError("refill runtime requires LOCAL_CLIP or CLIP_COMBINED")
        authority = first_search._authority()
        self.reservations = SQLiteRefillReservationRepository(
            ledger_path, projects=service.projects, authorization=authority)
        self.adapter = adapter
        self.handler = ConfirmedRefillSearchHandler(adapter, adapter)
        self.dispatch = RefillDispatchCoordinator(
            self.reservations, self.handler, poll_seconds=poll_seconds, start=start)
        self.auto = None
        self.auto_intake = None

    def notify(self):
        self.dispatch.notify()

    def dispatch_reservation(self, reservation_id):
        return self.dispatch.dispatch_reservation(reservation_id)

    def enable_auto(self, result_events, refill_service, *, start=True,
                    poll_seconds=1.0):
        if self.auto is not None:
            raise RuntimeError("auto refill coordinator already enabled")
        from .refill_auto import AutoRefillCoordinator
        self.auto = AutoRefillCoordinator(
            result_events, refill_service, start=start, poll_seconds=poll_seconds)
        return self.auto

    def enable_auto_intake(self, tasks, first_search, cleaning_service,
                           cleaning_dispatch, refill_service, *, start=True,
                           poll_seconds=1.0):
        if self.auto_intake is not None:
            raise RuntimeError("auto refill intake coordinator already enabled")
        from .refill_intake import AutoRefillIntakeCoordinator, AutoRefillIntakeService
        service = AutoRefillIntakeService(
            tasks, self.reservations, first_search, cleaning_service,
            cleaning_dispatch, refill_service)
        self.auto_intake = AutoRefillIntakeCoordinator(
            self.reservations, service, start=start, poll_seconds=poll_seconds)
        return self.auto_intake

    def close(self):
        if self.auto_intake is not None:
            self.auto_intake.close()
        if self.auto is not None:
            self.auto.close()
        self.dispatch.close()


def build_refill_runtime(ledger_path, service, first_search, *, start=True,
                         poll_seconds=1.0):
    """Return an enabled runtime only for search modes with refill executors."""
    if getattr(first_search, "mode", None) not in {"LOCAL_CLIP", "CLIP_COMBINED"}:
        return None
    return RefillRuntime(ledger_path, service, first_search, start=start,
                         poll_seconds=poll_seconds)
