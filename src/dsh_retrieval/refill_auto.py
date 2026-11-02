"""Durable result-publication to approved refill-position coordinator."""

import logging
import threading

from .errors import ServiceError
from .models import Actor


class AutoRefillCoordinator:
    """Drain result events only after the task has confirmed auto refill scope."""

    def __init__(self, events, refill_service, *, poll_seconds=1.0, start=True):
        if (not callable(getattr(events, "pending_auto_refill_events", None))
                or not callable(getattr(events, "mark_auto_refill_delivered", None))):
            raise TypeError("auto refill event repository required")
        if not callable(getattr(refill_service, "advance_auto_event", None)):
            raise TypeError("auto refill service required")
        self.events = events
        self.refill_service = refill_service
        self.poll_seconds = poll_seconds
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        if start:
            self._thread = threading.Thread(
                target=self._work, name="auto-refill", daemon=True)
            self._thread.start()

    def notify(self):
        self._wake.set()

    def drain(self):
        if not self._lock.acquire(blocking=False):
            return
        try:
            finalize = getattr(self.refill_service, "finalize_pending_stops", None)
            if callable(finalize):
                finalize()
            for event in self.events.pending_auto_refill_events():
                if self._closed.is_set():
                    break
                actor = Actor(event["tenant_id"], event["owner_user_id"])
                result = self.refill_service.advance_auto_event(actor, event)
                self.events.mark_auto_refill_delivered(event["event_id"], result)
        finally:
            self._lock.release()

    def _work(self):
        while not self._closed.is_set():
            try:
                self.drain()
            except ServiceError:
                # The durable event remains pending until dependencies recover.
                pass
            except Exception:
                logging.getLogger(__name__).warning("auto refill polling unavailable")
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def close(self):
        self._closed.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.poll_seconds * 2))
