"""Task-bound adapter for a deployment-owned whole-video condition evaluator.

No model endpoint is assumed. The evaluator must decode/verify the video and
persist evidence; returning a similarity score is not this protocol.
"""
import json

from ...errors import ServiceError
from ...multi_rule_contracts import CleaningBinding, DetectionResult
from ...whole_video import (VIDEO_PREDICATE, aggregate_video_condition_results,
                            confirmed_video_spec, video_conditions_digest,
                            video_detector_config_digest)


class WholeVideoConditionDetector:
    backend_ref = 'video.conditions@1'
    validator_ref = 'video.boolean@1'
    evidence_ref = 'video.observation@1'

    def __init__(self, task, evaluate, *, evaluate_file=None):
        spec = confirmed_video_spec(task)
        if not callable(evaluate):
            raise ValueError('trusted video evaluator required')
        self._scope = (task.tenant_id, task.owner_user_id, task.project_id, task.task_id,
                       task.confirmed_revision, task.run_generation)
        self._conditions = json.dumps(spec['video_conditions'], sort_keys=True)
        self._review = video_conditions_digest(task)
        self._config = video_detector_config_digest(task)
        self._evaluate = evaluate
        self._evaluate_file = evaluate_file
        # Supplied by trusted artifact release configuration, never public input.
        self.artifact_binding_digest = None

    @property
    def source_review_digest(self):
        return self._review

    @property
    def release_config_digest(self):
        return self._config

    def detect(self, content, candidate, binding):
        if (type(binding) is not CleaningBinding
                or (binding.tenant_id, binding.owner_user_id, binding.project_id, binding.task_id,
                    binding.revision, binding.generation) != self._scope):
            raise ServiceError('STALE_GENERATION', 'Video evaluator does not match the current run', 409)
        if type(content) is not bytes or not content:
            return DetectionResult({}, None, self.validator_ref, 'unknown')
        conditions = json.loads(self._conditions)
        # The evaluator returns ordered per-condition CleaningRuleVerdict rows
        # and a durable evidence reference for the complete observation set.
        rows, evidence = self._evaluate(content, candidate, binding, conditions)
        verdict = aggregate_video_condition_results(json.loads(self._conditions), rows)
        if verdict == 'unknown':
            return DetectionResult({}, None, self.validator_ref, 'unknown')
        return DetectionResult({VIDEO_PREDICATE: verdict == 'pass'}, evidence,
                               self.validator_ref, 'ok')


    def detect_file(self, path, candidate, binding):
        if not callable(self._evaluate_file):
            if path.stat().st_size <= 10 * 1024**2:
                return self.detect(path.read_bytes(), candidate, binding)
            raise ServiceError('VIDEO_EVALUATOR_UNAVAILABLE', 'File-based video evaluator required', 422)
        if (binding.tenant_id, binding.owner_user_id, binding.project_id, binding.task_id,
                binding.revision, binding.generation) != self._scope:
            raise ServiceError('STALE_GENERATION', 'Video evaluator belongs to another run', 409)
        rows, evidence = self._evaluate_file(path, candidate, binding, json.loads(self._conditions))
        verdict = aggregate_video_condition_results(json.loads(self._conditions), rows)
        return (DetectionResult({}, None, self.validator_ref, 'unknown') if verdict == 'unknown' else
                DetectionResult({VIDEO_PREDICATE: verdict == 'pass'}, evidence, self.validator_ref, 'ok'))
