"""Task-scoped VLM detector for an already-reviewed image rule.

The trusted service must derive the requirement from the owner's confirmed
review; this adapter never accepts a draft, image URL, or public API input.
"""

import re

from .errors import ServiceError
from .model_runtime import ModelRuntime, PROMPT_VERSION
from .multi_rule_contracts import DetectionResult
from .query_spec import digest


class VLMRequirementDetector:
    backend_ref = "vlm.requirement@1"
    validator_ref = "vlm.boolean@1"
    evidence_ref = "vlm.observation@1"
    predicate_ref = "image.semantic-match@1"

    def __init__(self, runtime: ModelRuntime, requirement: str, model_binding: dict,
                 source_review_digest: str, artifact_binding_digest: str):
        if type(runtime) is not ModelRuntime:
            raise ValueError("trusted model runtime required")
        if not isinstance(requirement, str) or not 1 <= len(requirement.strip()) <= 2000:
            raise ValueError("bounded reviewed requirement required")
        if (not isinstance(source_review_digest, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", source_review_digest) is None):
            raise ValueError("review digest required")
        if (not isinstance(artifact_binding_digest, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", artifact_binding_digest) is None):
            raise ValueError("artifact binding required")
        if (not isinstance(model_binding, dict)
                or model_binding != runtime.binding(model_binding.get("llm"), model_binding.get("vlm"))):
            raise ServiceError("MODEL_BINDING_CHANGED", "Model configuration changed; reconfirm required", 409)
        self._runtime = runtime
        self._requirement = requirement
        self._model_binding = dict(model_binding)
        self.source_review_digest = source_review_digest
        self.artifact_binding_digest = artifact_binding_digest
    @property
    def release_config_digest(self):
        return digest({
            "schema_version": "vlm-requirement-release@1",
            "requirement": self._requirement,
            "model_binding": self._model_binding,
            "source_review_digest": self.source_review_digest,
            "prompt_version": PROMPT_VERSION,
            "predicate_ref": self.predicate_ref,
        })

    def detect(self, content, candidate, binding):
        # The approved worker checks candidate digest and current generation on
        # both sides of this call. MIME is inferred only from validated bytes.
        if not isinstance(content, bytes) or not content:
            raise ServiceError("INVALID_MEDIA", "Bounded PNG/JPEG bytes required", 422)
        mime = ("image/png" if content.startswith(b"\x89PNG\r\n\x1a\n") else
                "image/jpeg" if content.startswith(b"\xff\xd8\xff") else None)
        if mime is None:
            raise ServiceError("INVALID_MEDIA", "Bounded PNG/JPEG bytes required", 422)
        result = self._runtime.assess(self._requirement, content, mime, self._model_binding)
        if result["verdict"] == "unknown":
            return DetectionResult({}, None, self.validator_ref, "unknown")
        return DetectionResult({self.predicate_ref: result["verdict"] == "pass"},
                               self.evidence_ref, self.validator_ref, "ok")
