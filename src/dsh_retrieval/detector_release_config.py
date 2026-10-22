"""Load a server-selected detector release file and preflight its artifacts.

The configuration path and deployment root are trusted service settings. This
module does not accept request data, import detector code, or download files.
"""

import json
import os
from pathlib import Path
import stat

from .detector_artifacts import (
    DetectorArtifact, DetectorArtifactBinding, DetectorArtifactManifest,
)
from .released_detectors import ReleasedDetectorRegistry


MAX_RELEASE_CONFIG_BYTES = 1024 * 1024
MAX_RELEASED_DETECTORS = 100
MAX_ARTIFACTS_PER_DETECTOR = 32
_ROOT_KEYS = {"schema_version", "detectors"}
_DETECTOR_KEYS = {"backend_ref", "validator_ref", "evidence_ref", "artifacts"}
_ARTIFACT_KEYS = {"role", "relative_path", "content_digest"}


def _invalid(message="invalid trusted detector release configuration"):
    raise ValueError(message)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _read_config(path):
    if not isinstance(path, Path) or not path.is_absolute():
        _invalid("trusted detector release path required")
    try:
        if path.is_symlink():
            _invalid("trusted detector release file may not be a symlink")
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_RELEASE_CONFIG_BYTES:
            _invalid()
        with path.open("rb") as stream:
            content = stream.read(MAX_RELEASE_CONFIG_BYTES + 1)
            opened = os.fstat(stream.fileno())
        after = path.stat()
    except OSError as exc:
        raise ValueError("trusted detector release file unavailable") from exc
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if (len(content) > MAX_RELEASE_CONFIG_BYTES or identity(before) != identity(opened)
            or identity(opened) != identity(after)):
        _invalid()
    try:
        return json.loads(content.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid trusted detector release configuration") from exc


def load_detector_artifact_manifest(config_path, deployment_root):
    """Return a manifest from one strict, server-owned JSON release file."""

    data = _read_config(config_path)
    if (type(data) is not dict or set(data) != _ROOT_KEYS
            or data.get("schema_version") != "detector-artifact-manifest@1"
            or type(data.get("detectors")) is not list
            or not 1 <= len(data["detectors"]) <= MAX_RELEASED_DETECTORS):
        _invalid()
    bindings = []
    for item in data["detectors"]:
        if (type(item) is not dict or set(item) != _DETECTOR_KEYS
                or type(item["artifacts"]) is not list
                or not 1 <= len(item["artifacts"]) <= MAX_ARTIFACTS_PER_DETECTOR):
            _invalid()
        artifacts = []
        for artifact in item["artifacts"]:
            if type(artifact) is not dict or set(artifact) != _ARTIFACT_KEYS:
                _invalid()
            try:
                artifacts.append(DetectorArtifact(
                    artifact["role"], artifact["relative_path"], artifact["content_digest"]))
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid trusted detector release configuration") from exc
        try:
            bindings.append(DetectorArtifactBinding(
                item["backend_ref"], item["validator_ref"], item["evidence_ref"],
                tuple(artifacts)))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid trusted detector release configuration") from exc
    try:
        return DetectorArtifactManifest(deployment_root, tuple(bindings))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid trusted detector release configuration") from exc


def load_released_detector_registry(*, config_path, deployment_root,
                                    capabilities, bindings, detectors):
    """Build and fully preflight the trusted registry used by a service."""

    manifest = load_detector_artifact_manifest(config_path, deployment_root)
    registry = ReleasedDetectorRegistry(capabilities, bindings, detectors, manifest)
    return registry.preflight()
