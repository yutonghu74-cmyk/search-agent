"""Server-owned release gate between reviewed capabilities and worker code.

This checks release metadata and bound local artifact integrity, not detector
quality. The service injects the registry from trusted deployment configuration.
"""

from .errors import ServiceError
from .detector_artifacts import DetectorArtifactManifest
from .intent_compiler import DetectorBinding
from .intent_review import Capability, IntentReviewService
from .multi_rule_contracts import CleaningRecipe


_VALUE_TYPES = {"boolean": bool, "integer": int, "number": (int, float), "string": str}


class ReleasedDetectorRegistry:
    def __init__(self, capabilities, bindings, detectors, artifact_manifest):
        self.capabilities = tuple(capabilities)
        self.bindings = tuple(bindings)
        if (not all(type(item) is Capability for item in self.capabilities)
                or not all(type(item) is DetectorBinding for item in self.bindings)
                or type(detectors) is not dict
                or type(artifact_manifest) is not DetectorArtifactManifest):
            raise ValueError("trusted release definitions required")
        # Use exactly the review service's canonical capability digest.
        self.registry_digest = IntentReviewService(self.capabilities).registry_digest
        keys = [(item.predicate_ref, item.media_type) for item in self.bindings]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate detector binding")
        self.detectors = dict(detectors)
        self.artifact_manifest = artifact_manifest

    def require_recipe(self, recipe: CleaningRecipe):
        def unavailable():
            raise ServiceError("DETECTOR_NOT_RELEASED", "Approved detector release unavailable", 409)

        if type(recipe) is not CleaningRecipe or recipe.registry_digest != self.registry_digest:
            unavailable()
        steps = {step.step_id: step for step in recipe.steps}
        if set(steps) != {rule.step_id for rule in recipe.rules}:
            unavailable()
        for rule in recipe.rules:
            matches = [cap for cap in self.capabilities if
                       cap.predicate_ref == rule.predicate_ref and
                       cap.media_type == recipe.media_type and cap.kind == "constraint"]
            if len(matches) != 1:
                unavailable()
            cap = matches[0]
            expected_type = _VALUE_TYPES.get(cap.value_type)
            if (cap.status != "verified" or rule.operator not in cap.operators
                    or expected_type is None or type(rule.value) not in
                    (expected_type if type(expected_type) is tuple else (expected_type,))):
                unavailable()
            bindings = [binding for binding in self.bindings if
                        binding.predicate_ref == rule.predicate_ref and
                        binding.media_type == recipe.media_type]
            step = steps[rule.step_id]
            if (len(bindings) != 1 or bindings[0].backend_ref != step.backend_ref
                    or bindings[0].validator_ref != step.validator_ref
                    or bindings[0].evidence_ref != step.evidence_ref
                    or cap.validator_ref != step.validator_ref
                    or cap.evidence_ref != step.evidence_ref):
                unavailable()
        for step in recipe.steps:
            detector = self.detectors.get(step.backend_ref)
            if (detector is None or getattr(detector, "backend_ref", None) != step.backend_ref
                    or getattr(detector, "validator_ref", None) != step.validator_ref
                    or getattr(detector, "evidence_ref", None) != step.evidence_ref
                    or not callable(getattr(detector, "detect", None))):
                unavailable()
        self.artifact_manifest.require_detectors(
            self.detectors, tuple(step.backend_ref for step in recipe.steps))
        return dict(self.detectors)

    def preflight(self):
        """Validate the complete trusted release before serving any work."""

        released_backends = {binding.backend_ref for binding in self.bindings}
        if set(self.detectors) != released_backends:
            raise ServiceError("DETECTOR_NOT_RELEASED", "Approved detector release unavailable", 409)
        self.artifact_manifest.preflight(self.detectors)
        return self
