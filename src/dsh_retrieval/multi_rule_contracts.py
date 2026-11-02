"""Internal, task-bound contract for a generic cleaning worker.

Only a trusted business service may construct ApprovedCleaningJob after its
normal owner confirmation. No draft/LLM object is accepted here directly.
"""
from dataclasses import dataclass
import hashlib
import math
import re
import unicodedata

from .query_spec import digest
from .near_duplicates import PerceptualFingerprint


_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}\Z")
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _validate_reason(summary, truncated):
    if (type(truncated) is not bool
            or summary is None and truncated
            or summary is not None and (
                not isinstance(summary, str) or not 1 <= len(summary) <= 50
                or summary != summary.strip()
                or any(unicodedata.category(char).startswith('C') for char in summary))):
        raise ValueError("invalid bounded cleaning reason")


def bounded_reason(value):
    """Normalize display text before persistence; never retain a full answer."""
    if not isinstance(value, str):
        return None, False
    cleaned = ' '.join(''.join(
        ' ' if unicodedata.category(char).startswith('C') else char
        for char in value).split())
    return (cleaned[:50].rstrip() or None, len(cleaned) > 50)


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
    detector_release_digest: str | None = None

    def __post_init__(self):
        if (not all(_name(value) for value in (self.tenant_id, self.owner_user_id, self.project_id,
                self.task_id, self.operation_id))
                or any(type(value) is not int or value < 1 for value in
                       (self.revision, self.generation, self.task_version))
                or not _hash(self.input_manifest_digest) or not _hash(self.recipe_digest)
                or self.detector_release_digest is not None
                and not _hash(self.detector_release_digest)):
            raise ValueError("invalid approved cleaning binding")


@dataclass(frozen=True, slots=True)
class CleaningCandidateRef:
    candidate_id: str
    asset_version: str
    content_digest: str
    # Confirmed quota groups whose queries retrieved this candidate. A hit is
    # only a reason to ask the group's check, never proof of group membership.
    group_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if not _name(self.candidate_id) or not _name(self.asset_version) or not _hash(self.content_digest):
            raise ValueError("invalid candidate identity")
        if (not isinstance(self.group_ids, tuple)
                or any(not _name(value) for value in self.group_ids)
                or list(self.group_ids) != sorted(set(self.group_ids))):
            raise ValueError("invalid candidate origin groups")


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
    group_id: str | None = None

    def __post_init__(self):
        if (not all(_name(value) for value in (self.rule_id, self.step_id, self.predicate_ref))
                or self.operator != "eq" or self.hardness != "hard"
                or type(self.value) not in (bool, int, float, str)
                or isinstance(self.value, float) and not math.isfinite(self.value)
                or isinstance(self.value, str) and len(self.value) > 1024
                or self.group_id is not None and not _name(self.group_id)):
            raise ValueError("unsupported hard rule")


@dataclass(frozen=True, slots=True)
class CleaningRecipe:
    media_type: str
    source_review_digest: str
    registry_digest: str
    steps: tuple[DetectionStep, ...]
    rules: tuple[CleaningRule, ...]
    instructions: tuple[str, ...] = ()

    def __post_init__(self):
        if (self.media_type not in {"image", "video", "text"}
                or not _hash(self.source_review_digest) or not _hash(self.registry_digest)
                or not isinstance(self.steps, tuple) or not self.steps
                or not isinstance(self.rules, tuple) or not self.rules
                or not isinstance(self.instructions, tuple)
                or self.instructions and (len(self.instructions) != len(self.steps)
                    or any(not isinstance(text, str) or not 1 <= len(text) <= 1000
                           for text in self.instructions))
                or not all(type(step) is DetectionStep for step in self.steps)
                or not all(type(rule) is CleaningRule for rule in self.rules)):
            raise ValueError("invalid cleaning recipe")
        step_ids = [step.step_id for step in self.steps]
        rule_ids = [rule.rule_id for rule in self.rules]
        if (len(step_ids) != len(set(step_ids)) or len(rule_ids) != len(set(rule_ids))
                or any(rule.step_id not in step_ids for rule in self.rules)):
            raise ValueError("duplicate or unmapped cleaning rule")
        # One detector call serves one step, so a step cannot mix quota groups:
        # the executor must be able to skip it for candidates outside the group.
        for step_id in step_ids:
            scopes = {rule.group_id for rule in self.rules if rule.step_id == step_id}
            if len(scopes) > 1:
                raise ValueError("cleaning step mixes quota group scopes")

    @property
    def recipe_digest(self):
        value = {"schema_version": "multi-rule-recipe@1", "media_type": self.media_type,
                       "source_review_digest": self.source_review_digest,
                       "registry_digest": self.registry_digest,
                       "steps": [{"step_id": s.step_id, "backend_ref": s.backend_ref,
                                  "validator_ref": s.validator_ref, "evidence_ref": s.evidence_ref}
                                 for s in self.steps],
                       "rules": [{"rule_id": r.rule_id, "step_id": r.step_id,
                                  "predicate_ref": r.predicate_ref, "operator": r.operator,
                                  "value": r.value, "hardness": r.hardness} for r in self.rules]}
        if self.instructions:
            value['schema_version'] = 'multi-rule-recipe@2'
            value['instructions'] = list(self.instructions)
        if any(rule.group_id is not None for rule in self.rules):
            value['schema_version'] = 'multi-rule-recipe@3'
            for encoded, rule in zip(value['rules'], self.rules):
                if rule.group_id is not None:
                    encoded['group_id'] = rule.group_id
        return digest(value)


def _candidate_digest_payload(candidate):
    payload = {"candidate_id": candidate.candidate_id,
               "asset_version": candidate.asset_version,
               "content_digest": candidate.content_digest}
    # Absent for ungrouped jobs so existing manifest digests stay byte-stable.
    if candidate.group_ids:
        payload["group_ids"] = list(candidate.group_ids)
    return payload


def candidate_manifest_digest(candidates):
    return digest({"candidates": [_candidate_digest_payload(c) for c in candidates]})


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
    detector_release_digest: str | None = None

    def __post_init__(self):
        if (not all(_name(value) for value in (self.tenant_id, self.owner_user_id,
                self.project_id, self.task_id, self.manifest_ref))
                or any(type(value) is not int or value < 1 for value in
                       (self.revision, self.generation, self.expected_task_version))
                or not _hash(self.confirmed_request_digest)
                or self.detector_release_digest is not None
                and not _hash(self.detector_release_digest)
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
        value = {"schema_version": "cleaning-approval-proposal@1",
                 "tenant_id": self.tenant_id, "owner_user_id": self.owner_user_id,
                 "project_id": self.project_id, "task_id": self.task_id,
                 "revision": self.revision, "generation": self.generation,
                 "expected_task_version": self.expected_task_version,
                 "confirmed_request_digest": self.confirmed_request_digest,
                 "manifest_ref": self.manifest_ref,
                 "input_manifest_digest": self.input_manifest_digest,
                 "recipe_digest": self.recipe.recipe_digest}
        if self.detector_release_digest is not None:
            value["schema_version"] = "cleaning-approval-proposal@2"
            value["detector_release_digest"] = self.detector_release_digest
        return digest(value)


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
            p.input_manifest_digest, p.recipe.recipe_digest,
            p.detector_release_digest), p.recipe, p.candidates)


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
    reason_summary: str | None = None
    reason_truncated: bool = False

    def __post_init__(self):
        _validate_reason(self.reason_summary, self.reason_truncated)
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
    reason_summary: str | None = None
    reason_truncated: bool = False
    group_id: str | None = None

    def __post_init__(self):
        _validate_reason(self.reason_summary, self.reason_truncated)
        without_evidence = self.verdict in {"unknown", "not_applicable"}
        if (not _name(self.rule_id)
                or self.verdict not in {"pass", "fail", "unknown", "not_applicable"}
                or not _name(self.reason_code)
                or (without_evidence and self.evidence_ref is not None)
                or (not without_evidence and not _name(self.evidence_ref))
                or self.group_id is not None and not _name(self.group_id)
                or (self.verdict == "not_applicable" and self.group_id is None)):
            raise ValueError("invalid cleaning rule verdict")


def aggregate_rule_verdicts(rules):
    """Combine task-wide rules (all must pass) with per-group checks (any may pass).

    A candidate retrieved for two groups may legitimately be a dog and not a
    cat, so one failing group check must not reject it. A candidate with no
    applicable group check has no evidence of membership and stays unknown.
    """
    shared = [rule.verdict for rule in rules if rule.group_id is None]
    scoped = [rule.verdict for rule in rules if rule.group_id is not None]
    applicable = [verdict for verdict in scoped if verdict != "not_applicable"]
    if "fail" in shared:
        return "fail"
    if scoped and "pass" not in applicable:
        if "unknown" not in applicable and applicable:
            return "fail"
        return "unknown"
    return "unknown" if "unknown" in shared else "pass"


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
        if self.verdict != aggregate_rule_verdicts(self.rules):
            raise ValueError("candidate verdict does not match rule verdicts")

    @property
    def passed_group_ids(self):
        """Groups this candidate may count toward; empty unless it passed overall."""
        if self.verdict != "pass":
            return ()
        return tuple(sorted({rule.group_id for rule in self.rules
                             if rule.group_id is not None and rule.verdict == "pass"}))


@dataclass(frozen=True, slots=True)
class CleaningJobResult:
    binding: CleaningBinding
    candidates: tuple[CleaningCandidateVerdict, ...]
    fingerprints: tuple[PerceptualFingerprint, ...] = ()

    def __post_init__(self):
        if (type(self.binding) is not CleaningBinding
                or not isinstance(self.candidates, tuple) or not self.candidates
                or not all(type(candidate) is CleaningCandidateVerdict for candidate in self.candidates)
                or len({candidate.candidate_id for candidate in self.candidates}) != len(self.candidates)
                or not isinstance(self.fingerprints, tuple)
                or any(type(item) is not PerceptualFingerprint for item in self.fingerprints)
                or len({item.candidate_id for item in self.fingerprints}) != len(self.fingerprints)
                or tuple(item.candidate_id for item in self.fingerprints)
                != tuple(candidate.candidate_id for candidate in self.candidates
                         if any(item.candidate_id == candidate.candidate_id
                                for item in self.fingerprints))):
            raise ValueError("invalid cleaning job result")

    @property
    def result_digest(self):
        binding = self.binding
        value = {"schema_version": "multi-rule-cleaning-result@1",
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
                                for candidate in self.candidates]}
        if binding.detector_release_digest is not None:
            value["schema_version"] = "multi-rule-cleaning-result@2"
            value["binding"]["detector_release_digest"] = binding.detector_release_digest
        # Keep legacy digests byte-for-byte stable when no summary was saved.
        # New explanations are immutable output, not mutable UI decorations.
        for candidate, encoded in zip(self.candidates, value["candidates"]):
            for rule, encoded_rule in zip(candidate.rules, encoded["rules"]):
                if rule.reason_summary is not None:
                    value["schema_version"] = "multi-rule-cleaning-result@3"
                    encoded_rule.update(reason_summary=rule.reason_summary,
                                        reason_truncated=rule.reason_truncated)
        if self.fingerprints:
            value["schema_version"] = "multi-rule-cleaning-result@4"
            value["perceptual_fingerprints"] = [
                {"candidate_id": item.candidate_id, "algorithm": item.algorithm,
                 "bit_length": item.bit_length, "value": item.value}
                for item in self.fingerprints]
        for candidate, encoded in zip(self.candidates, value["candidates"]):
            for rule, encoded_rule in zip(candidate.rules, encoded["rules"]):
                if rule.group_id is not None:
                    value["schema_version"] = "multi-rule-cleaning-result@5"
                    encoded_rule["group_id"] = rule.group_id
        return digest(value)


def require_released_cleaning_job(job):
    """Reject legacy approved jobs that are not bound to a detector release."""

    if type(job) is not ApprovedCleaningJob:
        raise TypeError("approved cleaning job required")
    if job.binding.detector_release_digest is None:
        raise ValueError("approved cleaning job has no detector release binding")
    return job


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
    for reference, candidate in zip(job.candidates, result.candidates):
        for rule, saved in zip(job.recipe.rules, candidate.rules):
            if saved.group_id != rule.group_id:
                raise ValueError("cleaning result rule group differs from recipe")
            # Skipping is only legal outside the origin groups, and a group
            # verdict outside them would credit a check that was never owed.
            if rule.group_id is not None and (
                    (saved.verdict == "not_applicable")
                    != (rule.group_id not in reference.group_ids)):
                raise ValueError("cleaning result group applicability is invalid")
    return result


def bytes_digest(content):
    if not isinstance(content, bytes):
        raise TypeError("detector input must be bytes")
    return "sha256:" + hashlib.sha256(content).hexdigest()
