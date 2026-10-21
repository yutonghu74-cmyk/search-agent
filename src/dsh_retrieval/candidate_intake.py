"""In-memory fixed-input intake, not semantic search or a durable result ledger."""
from dataclasses import asdict
import threading
import uuid

from .errors import ServiceError
from .models import RunStatus
from .query_spec import digest
from .service import require_key, require_version


class CandidateIntake:
    """Server configured synthetic sources; implements CleaningCatalog.

    Published references are immutable and task-scoped. Sources are revalidated
    before use. This single-process adapter is not a production authorization
    transaction, queue, or cross-process idempotency implementation.
    """
    def __init__(self, repository, projects, sources):
        self.repository, self.projects, self.sources = repository, projects, sources
        self._lock = threading.RLock()
        self._manifests, self._keys, self._publications = {}, {}, {}

    @staticmethod
    def _binding(task):
        return (task.tenant_id, task.owner_user_id, task.project_id, task.task_id,
                task.confirmed_revision, task.run_generation, task.confirmed_proposal_json)

    def preview(self, actor, task_id, source_ref):
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, "read")
        if task.run_status not in (RunStatus.PLANNING, RunStatus.RUNNING):
            raise ServiceError("INVALID_STATE_TRANSITION", "task cannot select candidates", 409)
        candidates, width = self.sources.resolve(task.tenant_id, task.project_id, source_ref, "image-width@1")
        snapshot = digest({"binding": self._binding(task), "source_ref": source_ref,
                           "candidates": [asdict(c) for c in candidates], "min_width": width})
        self.projects.require_access(actor, task.project_id, task.dimension_id, "read")
        if self.repository.get(actor, task_id) != task:
            raise ServiceError("REVISION_CONFLICT", "task changed during source preview", 409)
        return {"source_ref": source_ref, "source_mode": "simulated_fixed_input",
                "candidate_count": len(candidates), "proposal_digest": snapshot,
                "expected_task_version": task.task_version,
                "candidates": [{"candidate_id": c.candidate_id, "asset_version": c.asset_version}
                               for c in candidates]}

    def confirm(self, actor, task_id, source_ref, proposal_digest, expected_task_version, key, *, action):
        require_key(key)
        require_version(expected_task_version)
        if action != "confirm":
            raise ServiceError("CONFIRMATION_REQUIRED", "confirm the displayed fixed input", 422)
        with self._lock:
            task = self.repository.get(actor, task_id)
            self.projects.require_access(actor, task.project_id, task.dimension_id, "approve")
            view = self.preview(actor, task_id, source_ref)
            if view["proposal_digest"] != proposal_digest:
                raise ServiceError("INPUT_SNAPSHOT_CHANGED", "source or task changed; preview again", 409)
            scope = (actor.tenant_id, actor.user_id, task_id, key)
            fingerprint = digest([source_ref, proposal_digest, expected_task_version])
            if scope in self._keys:
                old, ref = self._keys[scope]
                if old != fingerprint:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "changed intake request", 409)
                self.require_scope(task, ref)
                return dict(self._manifests[ref][3]), True
            if task.task_version != expected_task_version:
                raise ServiceError("REVISION_CONFLICT", "task version changed", 409)
            candidates, width = self.sources.resolve(task.tenant_id, task.project_id, source_ref, "image-width@1")
            checked = digest({"binding": self._binding(task), "source_ref": source_ref,
                              "candidates": [asdict(c) for c in candidates], "min_width": width})
            if checked != proposal_digest or self.repository.get(actor, task_id) != task:
                raise ServiceError("INPUT_SNAPSHOT_CHANGED", "source/task changed during confirmation", 409)
            self.projects.require_access(actor, task.project_id, task.dimension_id, "approve")
            ref = self._publications.get(proposal_digest)
            replayed = ref is not None
            if ref is None:
                ref = "manifest_" + uuid.uuid4().hex
                result = {"manifest_ref": ref, "candidate_count": len(candidates),
                          "input_manifest_digest": digest([{k: v for k, v in asdict(c).items()
                                                           if k != "source_path"} for c in candidates]),
                          "source_mode": "simulated_fixed_input"}
                self._manifests[ref] = (self._binding(task), source_ref, (candidates, width), result)
                self._publications[proposal_digest] = ref
            self._keys[scope] = fingerprint, ref
            return dict(self._manifests[ref][3]), replayed

    def require_scope(self, task, manifest_ref):
        with self._lock:
            record = self._manifests.get(manifest_ref)
            if record is None or record[0] != self._binding(task):
                raise ServiceError("MANIFEST_SCOPE_MISMATCH", "manifest is not current for this task", 409)

    def resolve(self, tenant_id, project_id, manifest_ref, recipe_ref):
        with self._lock:
            record = self._manifests.get(manifest_ref)
            if record is None or (record[0][0], record[0][2]) != (tenant_id, project_id):
                raise ServiceError("NOT_FOUND", "manifest not found", 404)
            current = self.sources.resolve(tenant_id, project_id, record[1], recipe_ref)
            if current != record[2]:
                raise ServiceError("INPUT_SNAPSHOT_CHANGED", "published input changed", 409)
            return current
