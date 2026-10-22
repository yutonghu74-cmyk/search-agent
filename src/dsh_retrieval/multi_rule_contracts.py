"""Internal, task-bound contract for a generic cleaning worker.

Only a trusted business service may construct ApprovedCleaningJob after its
normal owner confirmation. No draft/LLM object is accepted here directly.
"""
from dataclasses import dataclass
import hashlib
import math
import re

from .query_spec import digest


_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}\Z")
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _name(value):
    return isinstance(value, str) and _IDENT.fullmatch(value) is not None


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class CleaningBinding:
    tenant_id: str
    owner_user_id: str
    project_id: str
    task_id: str
    revision: int
    generation: int
    task_version: int
    operation_id: str
    input_manifest_digest: str
    recipe_digest: str

    def __post_init__(self):
        if (not all(_name(value) for value in (self.tenant_id, self.owner_user_id, self.project_id,
                self.task_id, self.operation_id))
                or any(type(value) is not int or value < 1 for value in
                       (self.revision, self.generation, self.task_version))
                or not _hash(self.input_manifest_digest) or not _hash(self.recipe_digest)):
            raise ValueError("invalid approved cleaning binding")


@dataclass(frozen=True, slots=True)
class CleaningCandidateRef:
    candidate_id: str
    asset_version: str
    content_digest: str

    def __post_init__(self):
        if not _name(self.candidate_id) or not _name(self.asset_version) or not _hash(self.content_digest):
            raise ValueError("invalid candidate identity")


@dataclass(frozen=True, slots=True)
class DetectionStep:
    step_id: str
    backend_ref: str
    validator_ref: str
    evidence_ref: str

    def __post_init__(self):
        if not all(_name(value) for value in (
                self.step_id, self.backend_ref, self.validator_ref, self.evidence_ref)):
            raise ValueError("invalid released detection step")


@dataclass(frozen=True, slots=True)
class CleaningRule:
    rule_id: str
    step_id: str
    predicate_ref: str
    operator: str
    value: object
    hardness: str = "hard"

    def __post_init__(self):
        if (not all(_name(value) for value in (self.rule_id, self.step_id, self.predicate_ref))
                or self.operator != "eq" or self.hardness != "hard"
                or type(self.value) not in (bool, int, float, str)
                or isinstance(self.value, float) and not math.isfinite(self.value)
                or isinstance(self.value, str) and len(self.value) > 1024):
            raise ValueError("unsupported hard rule")


@dataclass(frozen=True, slots=True)
class CleaningRecipe:
    media_type: str
    source_review_digest: str
    registry_digest: str
    steps: tuple[DetectionStep, ...]
    rules: tuple[CleaningRule, ...]

    def __post_init__(self):
        if (self.media_type not in {"image", "video", "text"}
                or not _hash(self.source_review_digest) or not _hash(self.registry_digest)
                or not isinstance(self.steps, tuple) or not self.steps
                or not isinstance(self.rules, tuple) or not self.rules
                or not all(type(step) is DetectionStep for step in self.steps)
                or not all(type(rule) is CleaningRule for rule in self.rules)):
            raise ValueError("invalid cleaning recipe")
        step_ids = [step.step_id for step in self.steps]
        rule_ids = [rule.rule_id for rule in self.rules]
        if (len(step_ids) != len(set(step_ids)) or len(rule_ids) != len(set(rule_ids))
                or any(rule.step_id not in step_ids for rule in self.rules)):
            raise ValueError("duplicate or unmapped cleaning rule")

    @property
    def recipe_digest(self):
        return digest({"schema_version": "multi-rule-recipe@1", "media_type": self.media_type,
                       "source_review_digest": self.source_review_digest,
                       "registry_digest": self.registry_digest,
                       "steps": [{"step_id": s.step_id, "backend_ref": s.backend_ref,
                                  "validator_ref": s.validator_ref, "evidence_ref": s.evidence_ref}
                                 for s in self.steps],
                       "rules": [{"rule_id": r.rule_id, "step_id": r.step_id,
                                  "predicate_ref": r.predicate_ref, "operator": r.operator,
                                  "value": r.value, "hardness": r.hardness} for r in self.rules]})


@dataclass(frozen=True, slots=True)
class ApprovedCleaningJob:
    binding: CleaningBinding
    recipe: CleaningRecipe
    candidates: tuple[CleaningCandidateRef, ...]

    def __post_init__(self):
        if (type(self.binding) is not CleaningBinding or type(self.recipe) is not CleaningRecipe
                or not isinstance(self.candidates, tuple) or not 1 <= len(self.candidates) <= 100
                or not all(type(candidate) is CleaningCandidateRef for candidate in self.candidates)):
            raise ValueError("invalid approved job")
        if len({candidate.candidate_id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("duplicate candidate")
        manifest = digest({"candidates": [{"candidate_id": c.candidate_id,
            "asset_version": c.asset_version, "content_digest": c.content_digest}
            for c in self.candidates]})
        if self.binding.input_manifest_digest != manifest or self.binding.recipe_digest != self.recipe.recipe_digest:
            raise ValueError("approved binding does not match immutable inputs")


@dataclass(frozen=True, slots=True)
class DetectionResult:
    facts: dict[str, object]
    evidence_ref: str | None
    validator_ref: str
    status: str

    def __post_init__(self):
        if (self.status not in {"ok", "unknown"} or not _name(self.validator_ref)
                or not isinstance(self.facts, dict)
                or self.status == "ok" and (not _name(self.evidence_ref) or not self.facts)
                or self.status == "unknown" and (self.evidence_ref is not None or self.facts)
                or any(not _name(name) or type(value) not in (bool, int, float, str)
                       or isinstance(value, float) and not math.isfinite(value)
                       for name, value in self.facts.items())):
            raise ValueError("invalid detector result")


def bytes_digest(content):
    if not isinstance(content, bytes):
        raise TypeError("detector input must be bytes")
    return "sha256:" + hashlib.sha256(content).hexdigest()
