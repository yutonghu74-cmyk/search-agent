"""Recoverable delivery from confirmed term revisions to first-search jobs."""
from __future__ import annotations

import threading
import json

from .errors import ServiceError
from .models import Actor


class SearchDispatchCoordinator:
    """Drain the task-ledger outbox and retry transient delivery in the background."""

    def __init__(self, repository, first_search, *, poll_seconds=1.0, start=True, respect_manual_retrieval=False,
                 confirmed_terms_agent=None):
        if type(respect_manual_retrieval) is not bool:
            raise ValueError('manual retrieval policy must be a boolean')
        required = ("pending_search_dispatches", "mark_search_dispatched",
                    "search_dispatch_result")
        if any(not hasattr(repository, name) for name in required):
            raise TypeError("repository does not support the search dispatch outbox")
        self.repository = repository
        self.first_search = first_search
        self.confirmed_terms_agent = confirmed_terms_agent
        self.respect_manual_retrieval = respect_manual_retrieval
        self.poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._thread = None
        if start:
            self._thread = threading.Thread(target=self._work, name="search-dispatch", daemon=True)
            self._thread.start()

    def notify(self):
        self._wake.set()

    def dispatch_task(self, task_id):
        try:
            self._drain(task_id=task_id)
            state = self.repository.search_dispatch_result(task_id)
            if state and state["status"] == "delivered":
                return state["result"]
        except ServiceError:
            pass
        self.notify()
        return {"status": "queued", "task_id": task_id}

    def _drain(self, *, task_id=None):
        with self._lock:
            for event in self.repository.pending_search_dispatches():
                if task_id is not None and event["task_id"] != task_id:
                    continue
                try:
                    self._dispatch_event(event)
                except ServiceError:
                    if task_id is not None:
                        raise
                    # Preserve this pending event and allow other users to proceed.
                    continue

    def _dispatch_event(self, event):
        actor = Actor(event["tenant_id"], event["owner_user_id"])
        task = self.repository.get(actor, event["task_id"])
        current = (task.confirmed_revision, task.run_generation, task.task_version)
        expected = (event["revision"], event["generation"], event["task_version"])
        if current[:2] == expected[:2] and 'retrieval_execution' in json.loads(task.confirmed_proposal_json)['query_spec']:
            if self.confirmed_terms_agent is None:
                raise ServiceError('AGENT_EXECUTION_UNAVAILABLE', 'Confirmed terms Agent is not installed', 503)
            result = self.confirmed_terms_agent.enqueue(actor, task.task_id, key=event['dispatch_key'])
            self.repository.mark_search_dispatched(event['event_id'], result)
            return
        if current != expected:
            self.repository.mark_search_dispatched(event["event_id"], {
                "status": "superseded", "task_id": event["task_id"],
                "revision": event["revision"],
            })
            return
        if 'agent_requirements' in json.loads(task.confirmed_proposal_json)['query_spec']:
            self.repository.mark_search_dispatched(event['event_id'], {
                'status': 'agent_managed', 'task_id': task.task_id})
            return
        if (task.media_type == 'video' or self.respect_manual_retrieval) and json.loads(task.confirmed_proposal_json)['node_modes']['retrieval'] != 'auto':
            self.repository.mark_search_dispatched(event['event_id'], {
                'status': 'manual', 'task_id': task.task_id, 'target_media_type': 'video'})
            return
        result = self.first_search.auto_start(
            actor, event["task_id"], key=event["dispatch_key"])
        self.repository.mark_search_dispatched(event["event_id"], result)

    def _work(self):
        while not self._closed.is_set():
            try:
                self._drain()
            except ServiceError:
                # The event remains pending and is retried after the polling interval.
                pass
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def close(self):
        self._closed.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.poll_seconds * 2))
