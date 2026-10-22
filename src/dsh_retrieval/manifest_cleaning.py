"""Task-bound published inputs for the existing explicitly approved cleaning flow.

Logical candidates have an empty source_path in the business ledger. Only the
executor boundary replaces it with a bounded temporary file. The backend MUST
capture inputs before submit returns and use path-independent idempotency.
"""
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from .cleaning_models import Candidate
from .errors import ServiceError
from .integration_models import AccessContext


class PublishedManifestCatalog:
    def __init__(self, manifests, actor, task_id, *, min_width=32):
        if type(min_width) is not int or not 1 <= min_width <= 8192:
            raise ValueError("min_width must be 1..8192")
        self.manifests, self.actor, self.task_id = manifests, actor, task_id
        self.min_width = min_width

    def get(self, manifest_ref):
        return self.manifests.get(self.actor, self.task_id, manifest_ref)

    def require_scope(self, task, manifest_ref):
        if (task.tenant_id, task.owner_user_id, task.task_id) != (
                self.actor.tenant_id, self.actor.user_id, self.task_id):
            raise ServiceError("NOT_FOUND", "Manifest is outside bound task", 404)
        manifest = self.get(manifest_ref)
        if task.project_id != manifest.proposal.scope.project_id:
            raise ServiceError("NOT_FOUND", "Manifest is outside bound project", 404)

    def resolve(self, tenant_id, project_id, manifest_ref, recipe_ref):
        if tenant_id != self.actor.tenant_id:
            raise ServiceError("NOT_FOUND", "Manifest not found", 404)
        manifest = self.get(manifest_ref)
        if project_id != manifest.proposal.scope.project_id:
            raise ServiceError("NOT_FOUND", "Manifest not found", 404)
        if recipe_ref != "image-width@1":
            raise ServiceError("FEATURE_NOT_SUPPORTED", "Recipe is not allowlisted", 422)
        if not 1 <= len(manifest.proposal.items) <= 100:
            raise ServiceError("INPUT_LIMIT", "Cleaning requires 1..100 candidates", 422)
        return tuple(Candidate(i.candidate_id, i.media.asset_version, "", i.media.content_digest)
                     for i in manifest.proposal.items), self.min_width


class ManifestCleaningExecutor:
    """Bound to one actor/task. Do not share across user request contexts."""
    def __init__(self, catalog, gateway, backend, workspace_root, *,
                 max_item_bytes=10_000_000, max_total_bytes=100_000_000):
        if (getattr(backend, "captures_inputs_on_submit", False) is not True
                or getattr(backend, "path_independent_identity", False) is not True):
            raise ValueError("Backend must capture inputs and support path-independent identity")
        for value, upper in ((max_item_bytes, 10_000_000), (max_total_bytes, 100_000_000)):
            if type(value) is not int or not 0 < value <= upper:
                raise ValueError("Invalid input limits")
        self.catalog, self.gateway, self.backend = catalog, gateway, backend
        self.root = Path(workspace_root).resolve()
        self.item_limit, self.total_limit = max_item_bytes, max_total_bytes

    def _identity(self, operation):
        p, c = operation.proposal, self.catalog
        if (p.tenant_id, p.owner_user_id, p.task_id) != (c.actor.tenant_id, c.actor.user_id, c.task_id):
            raise ServiceError("NOT_FOUND", "Operation outside bound task", 404)

    def _check(self, operation):
        self._identity(operation)
        p = operation.proposal
        manifest = self.catalog.get(p.manifest_ref)
        scope = manifest.proposal.scope
        if (p.project_id, p.revision, p.generation) != (scope.project_id, scope.revision, scope.generation):
            raise ServiceError("REVISION_CONFLICT", "Cleaning input scope changed", 409)
        candidates, width = self.catalog.resolve(p.tenant_id, p.project_id, p.manifest_ref, p.recipe_ref)
        if candidates != p.candidates or width != p.min_width:
            raise ServiceError("INPUT_SNAPSHOT_CHANGED", "Cleaning input or rule changed", 409)
        return manifest

    @contextmanager
    def _inputs(self, operation):
        manifest = self._check(operation)
        context = AccessContext(self.catalog.actor, self.catalog.task_id, "cleaning_input", "current")
        self.root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="cleaning-input-", dir=self.root) as directory:
            candidates, total = [], 0
            for index, (item, candidate) in enumerate(zip(manifest.proposal.items, operation.proposal.candidates)):
                target = Path(directory) / f"{index:04d}.bin"
                checksum, size = sha256(), 0
                with self.gateway.open_read(context, item.media.authorized_media_ref, max_bytes=self.item_limit) as stream:
                    with target.open("xb") as output:
                        while True:
                            block = stream.read(min(65536, self.item_limit-size+1, self.total_limit-total+1))
                            if not isinstance(block, bytes):
                                raise ServiceError("MEDIA_READ_INVALID", "Binary media required", 502)
                            if not block:
                                break
                            size += len(block)
                            total += len(block)
                            if size > self.item_limit or total > self.total_limit:
                                raise ServiceError("MEDIA_LIMIT_EXCEEDED", "Cleaning input limit exceeded", 422)
                            checksum.update(block)
                            output.write(block)
                if "sha256:" + checksum.hexdigest() != candidate.content_digest:
                    raise ServiceError("INPUT_SNAPSHOT_CHANGED", "Cleaning input bytes changed", 409)
                candidates.append(replace(candidate, source_path=str(target)))
            if self._check(operation) != manifest:
                raise ServiceError("INPUT_SNAPSHOT_CHANGED", "Cleaning manifest changed", 409)
            yield replace(operation, proposal=replace(operation.proposal, candidates=tuple(candidates)))

    def submit(self, operation):
        with self._inputs(operation) as materialized:
            result = self.backend.submit(materialized)
            try:
                self._check(operation)
            except ServiceError:
                self.backend.cancel(operation)
                raise
            return result

    def poll(self, operation):
        self._check(operation)
        result = self.backend.poll(operation)
        self._check(operation)
        return result

    def cancel(self, operation):
        # Revoked media access must not prevent cancellation of previously
        # dispatched work. Business service/repository already authorize cancel.
        self._identity(operation)
        return self.backend.cancel(operation)
