"""Build one released VLM detector registry from a confirmed task snapshot."""

from contextlib import nullcontext
from hashlib import sha256
import json
from pathlib import Path

from .detector_artifacts import (
    DetectorArtifact, DetectorArtifactBinding, DetectorArtifactManifest,
)
from .errors import ServiceError
from .intent_compiler import DetectorBinding
from .intent_review import Capability
from .model_runtime import ModelRuntime
from .models import Task
from .recipe_bridge import confirmed_rules_digest
from .released_detectors import ReleasedDetectorRegistry
from .vlm_detector import VLMRequirementDetector
from .vlm_node_detector import VLMNodeDetector
from .vlm_instruction_plan import (confirmed_instruction_plan, plan_for_instructions,
                                   with_model_instructions)


class ConfirmedTaskVLMRegistry:
    """Task-scoped release provider for the confirmed semantic image rule.

    The requirement and model selection are both read from the immutable task
    revision.  Rebuilding the provider after a restart therefore yields the
    same release digest, while model configuration or source changes fail the
    already-approved operation closed before media is read.
    """

    predicate_ref = VLMRequirementDetector.predicate_ref

    def __init__(self, runtime: ModelRuntime):
        if not isinstance(runtime, ModelRuntime):
            raise ValueError("trusted model runtime required")
        self.runtime = runtime
        self._artifact_root = Path(__file__).resolve().parent
        self._artifact_name = "vlm_detector.py"

    def scope(self, actor):
        settings = getattr(self.runtime, "settings", None)
        return (settings.scope(actor.tenant_id, actor.user_id)
                if settings is not None else nullcontext())

    @staticmethod
    def _confirmed(task):
        if type(task) is not Task or not isinstance(task.confirmed_proposal_json, str):
            raise ServiceError("RECIPE_RULE_MISMATCH",
                               "Confirmed VLM rule unavailable", 409)
        try:
            proposal = json.loads(task.confirmed_proposal_json)
            spec = proposal["query_spec"]
            rules = [row for row in spec["rules"]
                     if row["hardness"] == "hard"]
            semantic = [row for row in rules
                        if row["predicate_ref"] == VLMRequirementDetector.predicate_ref]
            binding = spec["grouped_expansion"]["model_binding"]
            requirement = spec["raw_request"].strip()
        except (TypeError, ValueError, KeyError, json.JSONDecodeError,
                AttributeError, RecursionError) as exc:
            raise ServiceError("VLM_BINDING_REQUIRED",
                               "Confirmed task has no frozen VLM selection", 409) from exc
        if (len(semantic) != 1 or semantic[0].get("operator") != "eq"
                or semantic[0].get("value") is not True):
            raise ServiceError("VLM_RULE_REQUIRED",
                               "Confirm the semantic image cleaning rule first", 409)
        if not requirement or len(requirement) > 2000:
            raise ServiceError("VLM_RULE_REQUIRED",
                               "Confirmed semantic requirement is invalid", 409)
        spec = {**spec, 'raw_request': requirement}
        return confirmed_instruction_plan(spec), binding, spec

    def generate(self, task):
        plan, binding, _ = self._confirmed(task)
        drafted = self.runtime.plan_vlm_cleaning(
            tuple(node['source'] for node in plan), binding)
        instructions = tuple(
            '只以节点原文判断当前图片；无法观察则 unknown；模型整理仅用于解释，不增加新条件。'
            f'节点原文：{node["source"]}。模型整理：{text}'
            for node, text in zip(plan, drafted))
        return tuple(node['instruction'] for node in
                     with_model_instructions(plan, instructions))

    def build(self, actor, task, *, instructions=None):
        plan, model_binding, spec = self._confirmed(task)
        if instructions is not None:
            plan = plan_for_instructions(spec, instructions)
        implementation = self._artifact_root / self._artifact_name
        try:
            content_digest = "sha256:" + sha256(implementation.read_bytes()).hexdigest()
        except OSError as exc:
            raise ServiceError("DETECTOR_NOT_RELEASED",
                               "VLM detector implementation unavailable", 503) from exc
        artifact = DetectorArtifact("implementation", self._artifact_name, content_digest)
        node_artifact = None
        # A one-group quota still needs its group's own predicate identity.
        if 'cleaning_conditions' in spec or len(plan) > 1 or plan[0].get('group_id') is not None:
            node_path = self._artifact_root / 'vlm_node_detector.py'
            try:
                node_artifact = DetectorArtifact('implementation', node_path.name,
                    'sha256:' + sha256(node_path.read_bytes()).hexdigest())
            except OSError as exc:
                raise ServiceError('DETECTOR_NOT_RELEASED',
                                   'VLM node implementation unavailable', 503) from exc
        review_digest = confirmed_rules_digest(task)
        artifact_bindings = []
        capabilities = []
        bindings = []
        detectors = {}
        for node in plan:
            artifacts = (artifact,) if node_artifact is None else (artifact, node_artifact)
            artifact_binding = DetectorArtifactBinding(
                node['backend_ref'], VLMRequirementDetector.validator_ref,
                VLMRequirementDetector.evidence_ref, artifacts)
            if node_artifact is None:
                detector = VLMRequirementDetector(
                    self.runtime, node['instruction'], model_binding, review_digest,
                    artifact_binding.binding_digest)
            else:
                detector = VLMNodeDetector(
                    self.runtime, node['instruction'], model_binding, review_digest,
                    artifact_binding.binding_digest, backend_ref=node['backend_ref'],
                    predicate_ref=node['predicate_ref'])
            artifact_bindings.append(artifact_binding)
            capabilities.append(Capability(
                detector.predicate_ref, 'constraint', 'image', ('eq',),
                'boolean', 'verified', detector.validator_ref, detector.evidence_ref))
            bindings.append(DetectorBinding(
                detector.predicate_ref, 'image', detector.validator_ref,
                detector.backend_ref, detector.evidence_ref))
            detectors[detector.backend_ref] = detector
        registry = ReleasedDetectorRegistry(
            tuple(capabilities), tuple(bindings), detectors,
            DetectorArtifactManifest(self._artifact_root, tuple(artifact_bindings)))
        registry.instruction_plan = plan
        registry.frozen_instructions = instructions or ()
        return registry

    def describe(self, task, *, instructions=None):
        """Return the frozen, user-readable execution inputs without secrets."""
        plan, model_binding, spec = self._confirmed(task)
        if instructions is not None:
            plan = plan_for_instructions(spec, instructions)
        profile = self.runtime.select(model_binding['vlm'], 'vlm')
        if model_binding != self.runtime.binding(model_binding['llm'], model_binding['vlm']):
            raise ServiceError('MODEL_BINDING_CHANGED', 'Model configuration changed; reconfirm required', 409)
        return {
            'backend_ref': plan[0]['backend_ref'] if len(plan) == 1 else 'multiple-vlm-checks@1',
            'instruction': plan[0]['instruction'] if len(plan) == 1 else f'共 {len(plan)} 个已确认画面检查节点；逐项指令见 nodes。',
            'nodes': [{'node_id': node['node_id'], 'source': node['source'],
                       'backend_ref': node['backend_ref'],
                       'instruction': node['instruction']}
                      for node in plan],
            'prompt_version': model_binding['prompt_version'],
            'config_digest': model_binding['config_digest'],
            'model': {
                'id': model_binding['vlm'],
                'provider': profile.config.provider,
                'name': profile.config.model,
            },
        }
