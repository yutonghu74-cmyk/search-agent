"""Recoverable automatic intake of one committed LOCAL_CLIP refill page."""

import json
import logging
import threading

from .errors import ServiceError
from .models import Actor
from .refill_planning import normalize_refill_limits


class AutoRefillIntakeService:
    """Fix an incremental manifest and approve its VLM cleaning operation.

    The durable outbox owns the stage transitions.  Stable confirmation keys
    make the two approvals replayable across a process crash.
    """

    def __init__(self, tasks, events, first_search, cleaning_service,
                 cleaning_dispatch, refill_service):
        self.tasks = tasks
        self.events = events
        self.first_search = first_search
        self.cleaning_service = cleaning_service
        self.cleaning_dispatch = cleaning_dispatch
        self.refill_service = refill_service
        if getattr(first_search, "mode", None) not in ("LOCAL_CLIP", "PLATFORM"):
            raise ValueError("automatic refill intake requires an approved plan-set search")

    @staticmethod
    def _automatic(task):
        try:
            proposal = json.loads(task.confirmed_proposal_json)
            modes = proposal["node_modes"]
            policy = proposal["query_spec"]["refill_policy"]
        except (TypeError, ValueError, KeyError):
            raise ServiceError("TASK_CORRUPT", "Confirmed automatic intake is unavailable", 500) from None
        return (modes.get("retrieval") == "auto"
                and modes.get("cleaning") == "auto"
                and modes.get("refill") == "auto"
                and policy.get("mode") == "maintain_target"
                and policy.get("progression") == "auto")

    def consume(self, actor, event):
        required = {"event_id", "task_id", "revision", "generation", "task_version",
                    "stage", "manifest_ref", "cleaning_proposal_digest",
                    "cleaning_expected_task_version", "candidate_count", "vlm_calls"}
        if not isinstance(event, dict) or not required <= set(event):
            raise ValueError("invalid auto refill intake event")
        task = self.tasks.get(actor, event["task_id"])
        if not self._automatic(task):
            return {"status": "manual", "task_id": event["task_id"]}

        stage = event["stage"]
        if stage != "cleaning_prepared" and (
                task.confirmed_revision, task.run_generation, task.task_version) != (
                event["revision"], event["generation"], event["task_version"]):
            return {"status": "superseded", "task_id": event["task_id"]}

        manifest_ref = event["manifest_ref"]
        if stage == "pending":
            preview = self.first_search.preview_manifest(actor, event["task_id"])
            if preview["candidate_count"] == 0:
                return {"candidate_count": 0,
                        **self.refill_service.advance_empty_intake(actor, event)}
            if preview.get("confirmed"):
                manifest_ref = preview["manifest_ref"]
            else:
                manifest, _ = self.first_search.confirm_manifest(
                    actor, event["task_id"],
                    proposal_digest=preview["proposal_digest"],
                    expected_task_version=preview["expected_task_version"],
                    key="auto-refill-manifest:" + event["reservation_id"],
                    action="confirm")
                manifest_ref = manifest["manifest_ref"]
            self.events.advance_auto_intake(
                event["event_id"], stage="manifest_confirmed",
                manifest_ref=manifest_ref)
            stage = "manifest_confirmed"

        proposal_digest = event["cleaning_proposal_digest"]
        expected_version = event["cleaning_expected_task_version"]
        if stage == "manifest_confirmed":
            proposal = self.cleaning_service.prepare(
                actor, event["task_id"], manifest_ref)
            proposal_digest = proposal["proposal_digest"]
            expected_version = proposal["expected_task_version"]
            candidate_count = proposal["candidate_count"]
            vlm_calls = proposal.get("estimated_vlm_calls") or 0
            limits = normalize_refill_limits(json.loads(
                task.confirmed_proposal_json)["query_spec"].get("refill_limits"))
            if not self.events.reserve_vlm_calls(
                    actor, event["reservation_id"], candidate_count=candidate_count,
                    vlm_calls=vlm_calls, max_vlm_calls=limits["max_vlm_calls"]):
                finalize = getattr(self.refill_service, "finalize_stop", None)
                if callable(finalize):
                    finalize(actor, event["task_id"], "VLM_CALL_LIMIT_REACHED")
                return {"status": "stopped", "task_id": event["task_id"],
                        "stop_reason": "VLM_CALL_LIMIT_REACHED",
                        "candidate_count": candidate_count, "vlm_calls": vlm_calls}
            self.events.advance_auto_intake(
                event["event_id"], stage="cleaning_prepared",
                manifest_ref=manifest_ref,
                cleaning_proposal_digest=proposal_digest,
                cleaning_expected_task_version=expected_version,
                candidate_count=candidate_count, vlm_calls=vlm_calls)

        approval, replayed = self.cleaning_service.confirm(
            actor, event["task_id"], proposal_digest,
            key="auto-refill-cleaning:" + event["reservation_id"],
            expected_task_version=expected_version, action="confirm")
        self.events.bind_auto_intake_operation(
            event["event_id"], approval["operation_id"])
        self.cleaning_dispatch.notify()
        return {"status": "queued", "task_id": event["task_id"],
                "manifest_ref": manifest_ref,
                "operation_id": approval["operation_id"],
                "candidate_count": approval["candidate_count"],
                "vlm_calls": event["vlm_calls"] if stage == "cleaning_prepared" else vlm_calls,
                "replayed": replayed}


class AutoRefillIntakeCoordinator:
    def __init__(self, events, service, *, poll_seconds=1.0, start=True):
        required = ("pending_auto_intake_events", "mark_auto_intake_delivered")
        if any(not callable(getattr(events, name, None)) for name in required):
            raise TypeError("auto refill intake outbox required")
        self.events = events
        self.service = service
        self.poll_seconds = poll_seconds
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        if start:
            self._thread = threading.Thread(
                target=self._work, name="auto-refill-intake", daemon=True)
            self._thread.start()

    def notify(self):
        self._wake.set()

    def drain(self):
        if not self._lock.acquire(blocking=False):
            return
        try:
            for event in self.events.pending_auto_intake_events():
                if self._closed.is_set():
                    break
                actor = Actor(event["tenant_id"], event["owner_user_id"])
                result = self.service.consume(actor, event)
                self.events.mark_auto_intake_delivered(event["event_id"], result)
        finally:
            self._lock.release()

    def _work(self):
        while not self._closed.is_set():
            try:
                self.drain()
            except ServiceError:
                pass
            except Exception:
                logging.getLogger(__name__).warning("auto refill intake polling unavailable")
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def close(self):
        self._closed.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.poll_seconds * 2))
