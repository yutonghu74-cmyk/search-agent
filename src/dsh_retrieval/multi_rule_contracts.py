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


def candidate_manifest_digest(candidates):
    return digest({"candidates": [{"candidate_id": c.candidate_id,
        "asset_version": c.asset_version, "content_digest": c.content_digest}
        for c in candidates]})


@dataclass(frozen=True, slots=True)
class CleaningApprovalProposal:
    tenant_id: str
    owner_user_id: str
    project_id: str
    task_id: str
    revision: int
    generation: int
    expected_task_version: int
    confirmed_request_digest: str
    manifest_ref: str
    recipe: CleaningRecipe
    candidates: tuple[CleaningCandidateRef, ...]

    def __post_init__(self):
        if (not all(_name(value) for value in (self.tenant_id, self.owner_user_id,
                self.project_id, self.task_id, self.manifest_ref))
                or any(type(value) is not int or value < 1 for value in
                       (self.revision, self.generation, self.expected_task_version))
                or not _hash(self.confirmed_request_digest)
                or type(self.recipe) is not CleaningRecipe
                or not isinstance(self.candidates, tuple)
                or not 1 <= len(self.candidates) <= 100
                or not all(type(candidate) is CleaningCandidateRef for candidate in self.candidates)
                or len({candidate.candidate_id for candidate in self.candidates}) != len(self.candidates)):
            raise ValueError("invalid cleaning approval proposal")

    @property
    def input_manifest_digest(self):
        return candidate_manifest_digest(self.candidates)

    @property
    def proposal_digest(self):
        return digest({"schema_version": "cleaning-approval-proposal@1",
                       "tenant_id": self.tenant_id, "owner_user_id": self.owner_user_id,
                       "project_id": self.project_id, "task_id": self.task_id,
                       "revision": self.revision, "generation": self.generation,
                       "expected_task_version": self.expected_task_version,
                       "confirmed_request_digest": self.confirmed_request_digest,
                       "manifest_ref": self.manifest_ref,
                       "input_manifest_digest": self.input_manifest_digest,
                       "recipe_digest": self.recipe.recipe_digest})


@dataclass(frozen=True, slots=True)
class CleaningApproval:
    proposal: CleaningApprovalProposal
    operation_id: str
    task_version: int

    def __post_init__(self):
        if (type(self.proposal) is not CleaningApprovalProposal
                or not _name(self.operation_id)
                or type(self.task_version) is not int
                or self.task_version != self.proposal.expected_task_version + 1):
            raise ValueError("invalid approved cleaning operation")

    @property
    def job(self):
        p = self.proposal
        return ApprovedCleaningJob(CleaningBinding(
            p.tenant_id, p.owner_user_id, p.project_id, p.task_id,
            p.revision, p.generation, self.task_version, self.operation_id,
            p.input_manifest_digest, p.recipe.recipe_digest), p.recipe, p.candidates)


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
        manifest = candidate_manifest_digest(self.candidates)
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


@dataclass(frozen=True, slots=True)
class CleaningRuleVerdict:
    rule_id: str
    verdict: str
    reason_code: str
    evidence_ref: str | None

    def __post_init__(self):
        if (not _name(self.rule_id) or self.verdict not in {"pass", "fail", "unknown"}
                or not _name(self.reason_code)
                or (self.verdict == "unknown" and self.evidence_ref is not None)
                or (self.verdict != "unknown" and not _name(self.evidence_ref))):
            raise ValueError("invalid cleaning rule verdict")


@dataclass(frozen=True, slots=True)
class CleaningCandidateVerdict:
    candidate_id: str
    asset_version: str
    verdict: str
    rules: tuple[CleaningRuleVerdict, ...]

    def __post_init__(self):
        if (not _name(self.candidate_id) or not _name(self.asset_version)
                or self.verdict not in {"pass", "fail", "unknown"}
                or not isinstance(self.rules, tuple) or not self.rules
                or not all(type(rule) is CleaningRuleVerdict for rule in self.rules)
                or len({rule.rule_id for rule in self.rules}) != len(self.rules)):
            raise ValueError("invalid cleaning candidate verdict")
        expected = ("fail" if any(rule.verdict == "fail" for rule in self.rules)
                    else "unknown" if any(rule.verdict == "unknown" for rule in self.rules)
                    else "pass")
        if self.verdict != expected:
            raise ValueError("candidate verdict does not match rule verdicts")


@dataclass(frozen=True, slots=True)
class CleaningJobResult:
    binding: CleaningBinding
    candidates: tuple[CleaningCandidateVerdict, ...]

    def __post_init__(self):
        if (type(self.binding) is not CleaningBinding
                or not isinstance(self.candidates, tuple) or not self.candidates
                or not all(type(candidate) is CleaningCandidateVerdict for candidate in self.candidates)
                or len({candidate.candidate_id for candidate in self.candidates}) != len(self.candidates)):
            raise ValueError("invalid cleaning job result")

    @property
    def result_digest(self):
        binding = self.binding
        return digest({"schema_version": "multi-rule-cleaning-result@1",
                       "binding": {"tenant_id": binding.tenant_id,
                                   "owner_user_id": binding.owner_user_id,
                                   "project_id": binding.project_id,
                                   "task_id": binding.task_id,
                                   "revision": binding.revision,
                                   "generation": binding.generation,
                                   "task_version": binding.task_version,
                                   "operation_id": binding.operation_id,
                                   "input_manifest_digest": binding.input_manifest_digest,
                                   "recipe_digest": binding.recipe_digest},
                       "candidates": [{"candidate_id": candidate.candidate_id,
                                       "asset_version": candidate.asset_version,
                                       "verdict": candidate.verdict,
                                       "rules": [{"rule_id": rule.rule_id,
                                                  "verdict": rule.verdict,
                                                  "reason_code": rule.reason_code,
                                                  "evidence_ref": rule.evidence_ref}
                                                 for rule in candidate.rules]}
                                      for candidate in self.candidates]})


def require_complete_cleaning_result(job, result):
    """Reject any worker output that is not an exact result for ``job``."""

    if type(job) is not ApprovedCleaningJob or type(result) is not CleaningJobResult:
        raise TypeError("approved job and cleaning result required")
    if result.binding != job.binding:
        raise ValueError("cleaning result binding mismatch")
    expected_rules = tuple(rule.rule_id for rule in job.recipe.rules)
    expected_candidates = tuple((candidate.candidate_id, candidate.asset_version)
                                for candidate in job.candidates)
    actual_candidates = tuple((candidate.candidate_id, candidate.asset_version)
                              for candidate in result.candidates)
    if actual_candidates != expected_candidates:
        raise ValueError("cleaning result candidates are incomplete or reordered")
    if any(tuple(rule.rule_id for rule in candidate.rules) != expected_rules
           for candidate in result.candidates):
        raise ValueError("cleaning result rules are incomplete or reordered")
    return result


def bytes_digest(content):
    if not isinstance(content, bytes):
        raise TypeError("detector input must be bytes")
    return "sha256:" + hashlib.sha256(content).hexdigest()
