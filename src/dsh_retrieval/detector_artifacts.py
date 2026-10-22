"""Trusted, local artifact bindings for released detector implementations.

The deployment service constructs these objects from its own configuration.
Recipes and callers never provide artifact roots, paths, or digests.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat

from .errors import ServiceError
from .query_spec import digest


_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}\Z")
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ROLES = {"implementation", "model"}


def _unavailable():
    raise ServiceError("DETECTOR_NOT_RELEASED", "Approved detector artifact unavailable", 409)


@dataclass(frozen=True, slots=True)
class DetectorArtifact:
    role: str
    relative_path: str
    content_digest: str

    def __post_init__(self):
        path = PurePosixPath(self.relative_path) if isinstance(self.relative_path, str) else None
        if (self.role not in _ROLES or path is None or not 1 <= len(self.relative_path) <= 1024
                or "\x00" in self.relative_path or path.is_absolute()
                or not path.parts or any(part in {"", ".", ".."} for part in path.parts)
                or not _HASH.fullmatch(self.content_digest)):
            raise ValueError("invalid trusted detector artifact")


@dataclass(frozen=True, slots=True)
class DetectorArtifactBinding:
    backend_ref: str
    validator_ref: str
    evidence_ref: str
    artifacts: tuple[DetectorArtifact, ...]

    def __post_init__(self):
        if (not all(isinstance(value, str) and _IDENT.fullmatch(value) for value in
                    (self.backend_ref, self.validator_ref, self.evidence_ref))
                or not isinstance(self.artifacts, tuple) or not self.artifacts
                or not all(type(item) is DetectorArtifact for item in self.artifacts)
                or len({item.relative_path for item in self.artifacts}) != len(self.artifacts)
                or not any(item.role == "implementation" for item in self.artifacts)):
            raise ValueError("invalid trusted detector artifact binding")

    @property
    def binding_digest(self):
        return digest({"schema_version": "detector-artifact-binding@1",
                       "backend_ref": self.backend_ref,
                       "validator_ref": self.validator_ref,
                       "evidence_ref": self.evidence_ref,
                       "artifacts": [{"role": item.role,
                                      "relative_path": item.relative_path,
                                      "content_digest": item.content_digest}
                                     for item in sorted(self.artifacts,
                                         key=lambda value: (value.relative_path,
                                                            value.role,
                                                            value.content_digest))]})


class DetectorArtifactManifest:
    """Verify trusted detector files against one fixed deployment directory."""

    def __init__(self, root, bindings):
        if (not isinstance(root, Path) or not root.is_absolute()
                or not isinstance(bindings, tuple) or not bindings):
            raise ValueError("trusted detector artifact manifest required")
        if not all(type(item) is DetectorArtifactBinding for item in bindings):
            raise ValueError("trusted detector artifact bindings required")
        backend_refs = [item.backend_ref for item in bindings]
        if len(backend_refs) != len(set(backend_refs)):
            raise ValueError("duplicate detector artifact binding")
        try:
            if root.is_symlink():
                raise OSError("artifact root may not be a symlink")
            resolved = root.resolve(strict=True)
            root_stat = resolved.stat()
            if not stat.S_ISDIR(root_stat.st_mode):
                raise OSError("artifact root is not a directory")
        except OSError as exc:
            raise ValueError("trusted detector artifact root unavailable") from exc
        self._root = resolved
        self._root_identity = (root_stat.st_dev, root_stat.st_ino)
        self._bindings = {item.backend_ref: item for item in bindings}
        self._manifest_digest = digest({
            "schema_version": "detector-artifact-manifest@1",
            "detectors": [{
                "backend_ref": binding.backend_ref,
                "validator_ref": binding.validator_ref,
                "evidence_ref": binding.evidence_ref,
                "artifacts": [{
                    "role": artifact.role,
                    "relative_path": artifact.relative_path,
                    "content_digest": artifact.content_digest,
                } for artifact in sorted(binding.artifacts,
                    key=lambda value: (value.relative_path, value.role, value.content_digest))],
            } for binding in sorted(bindings, key=lambda value: value.backend_ref)],
        })

    @property
    def manifest_digest(self):
        return self._manifest_digest

    @property
    def detector_count(self):
        return len(self._bindings)

    @property
    def artifact_count(self):
        return sum(len(binding.artifacts) for binding in self._bindings.values())

    def _require_stable_root(self):
        try:
            if self._root.is_symlink():
                _unavailable()
            current = self._root.stat()
        except OSError:
            _unavailable()
        if (current.st_dev, current.st_ino) != self._root_identity or not stat.S_ISDIR(current.st_mode):
            _unavailable()

    def _require_file(self, artifact):
        path = self._root.joinpath(*PurePosixPath(artifact.relative_path).parts)
        try:
            relative = path.relative_to(self._root)
            current = self._root
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    _unavailable()
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                _unavailable()
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    hasher.update(chunk)
                opened = os.fstat(stream.fileno())
            after = path.stat()
        except (OSError, ValueError):
            _unavailable()
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        if (identity(before) != identity(opened) or identity(opened) != identity(after)
                or "sha256:" + hasher.hexdigest() != artifact.content_digest):
            _unavailable()

    def require_detectors(self, detectors, backend_refs):
        self._require_stable_root()
        for backend_ref in backend_refs:
            binding = self._bindings.get(backend_ref)
            detector = detectors.get(backend_ref)
            if (binding is None or detector is None
                    or getattr(detector, "backend_ref", None) != binding.backend_ref
                    or getattr(detector, "validator_ref", None) != binding.validator_ref
                    or getattr(detector, "evidence_ref", None) != binding.evidence_ref
                    or getattr(detector, "artifact_binding_digest", None) != binding.binding_digest
                    or not callable(getattr(detector, "detect", None))):
                _unavailable()
            for artifact in binding.artifacts:
                self._require_file(artifact)

    def preflight(self, detectors):
        if type(detectors) is not dict or set(detectors) != set(self._bindings):
            _unavailable()
        self.require_detectors(detectors, tuple(self._bindings))
        return self
