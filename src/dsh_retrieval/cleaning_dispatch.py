"""Restartable background delivery for approved local cleaning operations."""

from __future__ import annotations

import threading
import time
import logging

from .errors import ServiceError
from .models import Actor


class CleaningDispatchCoordinator:
    """Drain the approval ledger's derived outbox on one bounded worker.

    Approval and result publication remain authoritative in the repository.
    The coordinator owns no business result state; after a crash, a fresh
    instance rediscovers unfinished approvals and the pipeline resumes from an
    already-persisted result when available.
    """

    def __init__(self, repository, cleaning_service, *, poll_seconds=1.0, start=True, clock=time.time):
        if (not callable(getattr(repository, 'pending_approved_operations', None))
                or not callable(getattr(repository, 'cleaning_dispatch_state', None))):
            raise TypeError('repository does not expose approved cleaning work')
        if not callable(getattr(cleaning_service, 'execute', None)):
            raise TypeError('confirmed cleaning service required')
        self.repository = repository
        self.cleaning_service = cleaning_service
        self.poll_seconds = poll_seconds
        self.clock = clock
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._drain_lock = threading.Lock()
        self._thread = None
        if start:
            self._thread = threading.Thread(
                target=self._work, name='cleaning-dispatch', daemon=True)
            self._thread.start()

    def notify(self):
        self._wake.set()

    def status(self, actor, task_id, operation_id):
        return self.repository.cleaning_dispatch_state(
            actor, task_id, operation_id, details=True)

    def _drain(self):
        if not self._drain_lock.acquire(blocking=False):
            return
        try:
            self._drain_pending()
        finally:
            self._drain_lock.release()

    def _drain_pending(self):
        for item in self.repository.pending_approved_operations():
            if self._closed.is_set():
                break
            operation_id = item['operation_id']
            actor = Actor(item['tenant_id'], item['owner_user_id'])
            try:
                token = self.repository.claim_cleaning_attempt(
                    actor, item['task_id'], operation_id, self.clock())
                if token is None:
                    continue
            except ServiceError:
                # Scope may change between outbox discovery and reservation.
                continue
            code, retryable = None, False
            try:
                self.cleaning_service.execute(
                    actor, item['task_id'], operation_id)
            except ServiceError as exc:
                code, retryable = exc.code, exc.retryable
            except Exception:
                code, retryable = 'CLEANING_EXECUTION_ERROR', True
            finally:
                try:
                    self.repository.finish_cleaning_attempt(
                        actor, item['task_id'], operation_id, token, self.clock(),
                        code=code, retryable=retryable)
                except ServiceError:
                    # Reservation remains durable on storage or scope failure.
                    logging.getLogger(__name__).warning('Cleaning attempt finalization deferred')

    def _work(self):
        while not self._closed.is_set():
            try:
                self._drain()
            except Exception:
                logging.getLogger(__name__).warning('Cleaning dispatch polling unavailable')
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def close(self):
        self._closed.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.poll_seconds * 2))
