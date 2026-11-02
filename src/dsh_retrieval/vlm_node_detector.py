"""A released VLM check with its own node and predicate identity."""

from .vlm_detector import VLMRequirementDetector


class VLMNodeDetector(VLMRequirementDetector):
    def __init__(self, runtime, requirement, model_binding, source_review_digest,
                 artifact_binding_digest, *, backend_ref, predicate_ref):
        self.backend_ref = backend_ref
        self.predicate_ref = predicate_ref
        super().__init__(runtime, requirement, model_binding, source_review_digest,
                         artifact_binding_digest)
