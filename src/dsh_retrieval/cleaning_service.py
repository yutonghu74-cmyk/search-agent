"""Owner-confirmed cleaning service. No automatic dispatch from task creation."""
import uuid

from .cleaning_models import CleaningProposal
from .errors import ServiceError
from .service import require_key, require_version
from .ports import CleaningCatalog, CleaningExecutor, CleaningRepository, ProjectContextProvider


class CleaningService:
    def __init__(self, repository: CleaningRepository, projects: ProjectContextProvider,
                 catalog: CleaningCatalog, executor: CleaningExecutor):
        self.repository, self.projects = repository, projects
        self.catalog, self.executor = catalog, executor

    def _authorize(self, actor, task_id, action):
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, action)
        return task

    def prepare(self, actor, task_id, manifest_ref, recipe_ref="image-width@1"):
        task = self._authorize(actor, task_id, "approve")
        self.catalog.require_scope(task, manifest_ref)
        candidates, width = self.catalog.resolve(actor.tenant_id, task.project_id, manifest_ref, recipe_ref)
        proposal = CleaningProposal("cp_" + uuid.uuid4().hex, actor.tenant_id, actor.user_id,
                                    task.project_id, task_id, task.confirmed_revision, task.run_generation,
                                    task.task_version, manifest_ref, candidates, recipe_ref, width)
        self.repository.put_cleaning_proposal(actor, proposal)
        return proposal.view()

    def start(self, actor, key, proposal_id, proposal_digest, expected_task_version, *, action):
        require_key(key)
        require_version(expected_task_version)
        if action != "confirm":
            raise ServiceError("CONFIRMATION_REQUIRED", "explicit owner confirmation is required", 422)
        proposal = self.repository.get_cleaning_proposal(actor, proposal_id)
        task = self._authorize(actor, proposal.task_id, "approve")
        self.catalog.require_scope(task, proposal.manifest_ref)
        # Revalidate current authorized inputs; never execute changed bytes under an old approval.
        current, width = self.catalog.resolve(actor.tenant_id, proposal.project_id, proposal.manifest_ref, proposal.recipe_ref)
        if current != proposal.candidates or width != proposal.min_width:
            raise ServiceError("INPUT_SNAPSHOT_CHANGED", "prepare and confirm the changed inputs again", 409)
        operation, replayed = self.repository.reserve_cleaning(actor, key, proposal_id, proposal_digest, expected_task_version)
        if operation.status == "queued":
            self._authorize(actor, proposal.task_id, "approve")
            backend = self.executor.submit(operation)
            try:
                self._authorize(actor, proposal.task_id, "approve")
                operation = self.repository.attach_cleaning_backend(actor, operation.operation_id, backend)
            except ServiceError:
                # A concurrent cancellation/revocation must not leave a newly dispatched job unaddressed.
                self.executor.cancel(operation)
                raise
        return operation.view(), replayed

    def poll(self, actor, operation_id):
        operation = self.repository.get_cleaning_operation(actor, operation_id)
        self._authorize(actor, operation.proposal.task_id, "read")
        if operation.status in {"succeeded", "failed", "cancelled", "queued"}:
            return operation.view()
        response = self.executor.poll(operation)
        self._authorize(actor, operation.proposal.task_id, "read")
        if response.get("status") in {"succeeded", "failed"}:
            operation = self.repository.publish_cleaning(actor, operation_id, response)
        elif response.get("status") != "running" or response.get("binding") != operation.binding():
            raise ServiceError("INVALID_CLEANING_RESULT", "unexpected worker response", 502)
        return operation.view()

    def cancel(self, actor, operation_id, key, expected_task_version):
        require_key(key)
        require_version(expected_task_version)
        operation = self.repository.get_cleaning_operation(actor, operation_id)
        self._authorize(actor, operation.proposal.task_id, "approve")
        operation = self.repository.cancel_cleaning(actor, operation_id, key, expected_task_version)
        if operation.status == "cancelled":
            self.executor.cancel(operation)  # best effort; on error a retry uses the same tombstone
        return operation.view()

    def results(self, actor, task_id):
        self._authorize(actor, task_id, "read")
        operation = self.repository.cleaning_results(actor, task_id)
        if operation is None:
            return {"operation_id": None, "items": [], "counts": {"pass": 0, "fail": 0, "unknown": 0}}
        items = operation.view()["results"]["results"]
        return {"operation_id": operation.operation_id, "items": items,
                "counts": {v: sum(item["verdict"] == v for item in items) for v in ("pass", "fail", "unknown")},
                "accepted_count": None, "note": "cleaning verdicts only; dedup and delivery not yet applied"}
