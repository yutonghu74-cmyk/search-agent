"""Approved refill position resolvers/executors for local and combined CLIP."""

from dataclasses import asdict
import json

from .errors import ServiceError
from .query_branches import compile_query_branches
from .query_spec import digest
from .refill_planning import build_refill_position
from .retrieval_contracts import RetrievalScope
from .retrieval_coordinator import RetrievalCoordinator


def _scope_result(reservation, status):
    return {"status": status, "reservation_id": reservation.reservation_id,
            "revision": reservation.revision, "generation": reservation.generation,
            "iteration": reservation.iteration}


def _require_scope(task, revision, generation):
    if task.confirmed_revision != revision or task.run_generation != generation:
        raise ServiceError("REVISION_CONFLICT", "refill search scope changed", 409)


class LocalCLIPRefillAdapter:
    """Continue exactly one pending page of the approved LOCAL_CLIP plan set."""

    def __init__(self, first_search):
        if getattr(first_search, "mode", None) not in ("LOCAL_CLIP", "PLATFORM"):
            raise ValueError("approved plan-set first search is required")
        self.search = first_search

    def list_refill_positions(self, actor, task_id, *, revision, generation):
        task = self.search.service.repository.get(actor, task_id)
        _require_scope(task, revision, generation)
        approved = self.search.repository.get_browse_current(actor, task_id)
        if approved is None:
            return ()
        heads = self.search.repository.get_progress(
            actor, approved, allow_downstream=True)
        branches = {row["branch_id"]: row for row in compile_query_branches(
            json.loads(task.confirmed_proposal_json)["query_spec"])["branches"]}
        positions = []
        for member, head in zip(approved.members, heads):
            request = RetrievalCoordinator._next(head)
            if request is None:
                continue
            branch = branches.get(member.branch_id)
            if branch is None:
                raise ServiceError("STORAGE_CORRUPT", "approved CLIP branch disappeared", 500)
            params = {"mode": self.search.mode, "set_digest": approved.proposal_digest,
                      "plan_id": request.plan_id,
                      "binding_id": request.binding.binding_id,
                      "filter_template_ref": request.filter_template_ref,
                      "batch_size": request.batch_size,
                      "vector_digest": digest(asdict(request.vector))}
            positions.append(build_refill_position(
                library_id=request.binding.library_id,
                index_version=request.binding.index_version,
                query=branch["query_text"], parameters=params,
                cursor=request.cursor, depth=head.committed_pages
                if request.cursor is None else None))
        return tuple(positions)

    def start_refill(self, actor, *, reservation, position, key):
        current = self.list_refill_positions(
            actor, reservation.task_id, revision=reservation.revision,
            generation=reservation.generation)
        selected = next((item for item in current
                         if item.fingerprint == reservation.position_fingerprint), None)
        if selected is None:
            return _scope_result(reservation, "replayed")
        params = position["parameters"]
        approved = self.search.repository.get_browse_current(actor, reservation.task_id)
        if approved is None or approved.proposal_digest != params["set_digest"]:
            raise ServiceError("PLAN_NOT_CURRENT", "LOCAL_CLIP refill plan changed", 409)
        gateway = self.search._adapter_for_plan(actor, reservation.task_id, approved)
        result = RetrievalCoordinator(self.search.repository, gateway).tick_position(
            actor, approved, plan_id=params["plan_id"],
            expected_cursor=position["position"]["cursor"])
        if result["status"] == "retry_pending":
            raise ServiceError("DEPENDENCY_UNAVAILABLE", "LOCAL_CLIP refill retry pending",
                               503, retryable=True)
        return _scope_result(reservation, result["status"])


class CombinedCLIPRefillAdapter:
    """Continue exactly one pending request of a combined CLIP job."""

    def __init__(self, first_search):
        if getattr(first_search, "mode", None) != "CLIP_COMBINED":
            raise ValueError("combined CLIP first search is required")
        self.search = first_search

    def list_refill_positions(self, actor, task_id, *, revision, generation):
        task = self.search.service.repository.get(actor, task_id)
        _require_scope(task, revision, generation)
        job_id = self.search.jobs.current_for_browse(RetrievalScope.from_task(task))
        if job_id is None:
            return ()
        positions = []
        for branch_id, request, pages in self.search.jobs.pending_requests(job_id):
            params = {"mode": "CLIP_COMBINED", "job_id": job_id,
                      "branch_id": branch_id, "plan_id": request.plan_id,
                      "binding_id": request.binding.binding_id,
                      "filter_template_ref": request.filter_template_ref,
                      "batch_size": request.batch_size}
            positions.append(build_refill_position(
                library_id=request.binding.library_id,
                index_version=request.binding.index_version,
                query=request.query_text, parameters=params,
                cursor=request.cursor, depth=pages if request.cursor is None else None))
        return tuple(positions)

    def start_refill(self, actor, *, reservation, position, key):
        current = self.list_refill_positions(
            actor, reservation.task_id, revision=reservation.revision,
            generation=reservation.generation)
        selected = next((item for item in current
                         if item.fingerprint == reservation.position_fingerprint), None)
        if selected is None:
            return _scope_result(reservation, "replayed")
        params = position["parameters"]
        result = self.search.executor.tick_refill(
            params["job_id"], plan_id=params["plan_id"],
            expected_cursor=position["position"]["cursor"])
        outcome = result["outcomes"][0] if result["outcomes"] else None
        if outcome and outcome["status"] in {"retry_pending", "failed"}:
            raise ServiceError("DEPENDENCY_UNAVAILABLE", "combined CLIP refill retry pending",
                               503, retryable=True)
        if outcome and outcome["status"] == "superseded":
            raise ServiceError("REVISION_CONFLICT", "combined CLIP refill superseded", 409)
        return _scope_result(
            reservation, "committed" if outcome and outcome["status"] == "committed"
            else "replayed")
