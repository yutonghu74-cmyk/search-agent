"""Recoverable delivery of committed refill reservations to an idempotent handler."""

import threading

from .errors import ServiceError


class RefillDispatchCoordinator:
    def __init__(self, repository, handler, *, poll_seconds=1.0, start=True):
        required = ("pending_dispatches", "validate_dispatch", "mark_dispatched",
                    "dispatch_result")
        if any(not hasattr(repository, name) for name in required):
            raise TypeError("repository does not support refill dispatch")
        if not hasattr(handler, "dispatch_refill"):
            raise TypeError("handler must implement dispatch_refill")
        self.repository = repository
        self.handler = handler
        self.poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._thread = None
        if start:
            self._thread = threading.Thread(
                target=self._work, name="refill-dispatch", daemon=True)
            self._thread.start()

    def notify(self):
        self._wake.set()

    def dispatch_reservation(self, reservation_id):
        try:
            self._drain(reservation_id=reservation_id)
            state = self.repository.dispatch_result(reservation_id)
            if state and state["status"] == "delivered":
                return state["result"]
        except ServiceError:
            pass
        self.notify()
        return {"status": "queued", "reservation_id": reservation_id}

    def _drain(self, *, reservation_id=None):
        with self._lock:
            for event in self.repository.pending_dispatches():
                if reservation_id is not None and event["reservation_id"] != reservation_id:
                    continue
                validated = self.repository.validate_dispatch(event)
                if validated is None:
                    self.repository.mark_dispatched(event["event_id"], {
                        "status": "superseded",
                        "reservation_id": event["reservation_id"],
                        "task_id": event["task_id"],
                        "revision": event["revision"],
                        "generation": event["generation"],
                    })
                    continue
                actor, reservation = validated
                result = self.handler.dispatch_refill(
                    actor, reservation, key=event["dispatch_key"])
                self.repository.mark_dispatched(event["event_id"], result)

    def _work(self):
        while not self._closed.is_set():
            try:
                self._drain()
            except ServiceError:
                # Pending events remain durable and are retried after recovery.
                pass
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def close(self):
        self._closed.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.poll_seconds * 2))
