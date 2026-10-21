"""Server-configured fixture catalog. No public URL/path or model-supplied recipe."""
import hashlib
from pathlib import Path

from ..cleaning_models import Candidate
from ..errors import ServiceError


class LocalCleaningCatalog:
    def __init__(self, media_root, manifests, min_width=32):
        self.media_root = Path(media_root).resolve()
        # Key: tenant/project/manifest ref; value: (candidate_id, asset_version, path) tuples.
        self.manifests = {key: tuple(tuple(row) for row in rows) for key, rows in manifests.items()}
        if type(min_width) is not int or not 1 <= min_width <= 8192:
            raise ValueError("min_width must be 1..8192")
        self.min_width = min_width

    def require_scope(self, task, manifest_ref):
        if (task.tenant_id, task.project_id, manifest_ref) not in self.manifests:
            raise ServiceError("NOT_FOUND", "authorized input manifest was not found", 404)

    def resolve(self, tenant_id, project_id, manifest_ref, recipe_ref):
        if recipe_ref != "image-width@1":
            raise ServiceError("FEATURE_NOT_SUPPORTED", "recipe is not allowlisted", 422)
        rows = self.manifests.get((tenant_id, project_id, manifest_ref))
        if rows is None:
            raise ServiceError("NOT_FOUND", "authorized input manifest was not found", 404)
        if not 1 <= len(rows) <= 100:
            raise ServiceError("INPUT_LIMIT", "local slice allows 1..100 images", 422)
        candidates, seen = [], set()
        for cid, version, raw_path in rows:
            if not isinstance(cid, str) or not cid or cid in seen or not isinstance(version, str) or not version:
                raise ServiceError("INVALID_MANIFEST", "candidate identity must be unique and versioned", 422)
            path = Path(raw_path)
            if not path.is_absolute():
                path = self.media_root / path
            if path.is_symlink() or not path.resolve().is_relative_to(self.media_root) or not path.is_file():
                raise ServiceError("INPUT_NOT_AVAILABLE", "input is outside authorized local media root", 422)
            with path.open("rb") as handle:
                content = handle.read(10_000_001)
            if len(content) > 10_000_000:
                raise ServiceError("INPUT_LIMIT", "local input exceeds 10 MB", 422)
            seen.add(cid)
            candidates.append(Candidate(cid, version, str(path.resolve()), "sha256:" + hashlib.sha256(content).hexdigest()))
        return tuple(candidates), self.min_width
