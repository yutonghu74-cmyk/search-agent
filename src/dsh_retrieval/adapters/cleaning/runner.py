"""Execute an approved recipe using only injected, pre-registered detectors.

This module deliberately does not construct approvals, locate media, persist
results, or register any real detector. Those are separate trusted boundaries.
"""

from dataclasses import dataclass
from typing import Mapping

from ...errors import ServiceError
from ...multi_rule_contracts import ApprovedCleaningJob, CleaningBinding, DetectionResult, bytes_digest


MAX_CANDIDATE_BYTES = 10 * 1024 * 1024
MAX_JOB_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RuleVerdict:
    rule_id: str
    verdict: str
    reason_code: str
    evidence_ref: str | None


@dataclass(frozen=True, slots=True)
class CandidateVerdict:
    candidate_id: str
    asset_version: str
    verdict: str
    rules: tuple[RuleVerdict, ...]


@dataclass(frozen=True, slots=True)
class JobVerdict:
    binding: CleaningBinding
    candidates: tuple[CandidateVerdict, ...]


class GenericCleaningExecutor:
    """The caller supplies a current-approval guard, media reader and registry.

    guard.require_current(binding) must reject stale revisions/generations,
    revoked authority and unapproved tasks on *every* call. reader.read must
    resolve logical candidate IDs only within that binding. No result should
    be published without an additional current-state check at the publisher.
    """

    def __init__(self, *, guard, reader, detectors: Mapping[str, object]):
        if not callable(getattr(guard, "require_current", None)):
            raise ValueError("current approval guard is required")
        if not callable(getattr(reader, "read", None)):
            raise ValueError("authorized media reader is required")
        if not isinstance(detectors, Mapping) or not detectors:
            raise ValueError("registered detectors are required")
        self._guard = guard
        self._reader = reader
        self._detectors = dict(detectors)

    def _preflight(self, job):
        for step in job.recipe.steps:
            detector = self._detectors.get(step.backend_ref)
            if (detector is None or getattr(detector, "backend_ref", None) != step.backend_ref
                    or getattr(detector, "validator_ref", None) != step.validator_ref
                    or getattr(detector, "evidence_ref", None) != step.evidence_ref
                    or not callable(getattr(detector, "detect", None))):
                raise ServiceError("DETECTOR_NOT_RELEASED", "Approved detector unavailable", 409)

    def run(self, job: ApprovedCleaningJob) -> JobVerdict:
        if type(job) is not ApprovedCleaningJob:
            raise TypeError("only an approved cleaning job can be executed")
        self._preflight(job)
        self._guard.require_current(job.binding)
        total_bytes = 0
        verdicts = []
        for candidate in job.candidates:
            self._guard.require_current(job.binding)
            content = self._reader.read(candidate, job.binding)
            if type(content) is not bytes:
                raise ServiceError("INVALID_MEDIA_RESPONSE", "Authorized media was not bytes", 502)
            total_bytes += len(content)
            if len(content) > MAX_CANDIDATE_BYTES or total_bytes > MAX_JOB_BYTES:
                raise ServiceError("MEDIA_LIMIT_EXCEEDED", "Approved media exceeds worker limit", 413)
            if bytes_digest(content) != candidate.content_digest:
                raise ServiceError("INPUT_SNAPSHOT_CHANGED", "Approved media digest changed", 409)

            # Each step runs once per candidate even if several rules use it.
            results = {}
            for step in job.recipe.steps:
                try:
                    observed = self._detectors[step.backend_ref].detect(content, candidate, job.binding)
                except ServiceError:
                    # Authorization or infrastructure errors may not become a
                    # successful cleaning verdict; the caller must handle them.
                    raise
                except Exception:
                    observed = None
                if (type(observed) is not DetectionResult
                        or observed.validator_ref != step.validator_ref
                        or observed.status != "ok"):
                    results[step.step_id] = None
                else:
                    results[step.step_id] = observed

            rules = []
            for rule in job.recipe.rules:
                observed = results[rule.step_id]
                fact = None if observed is None else observed.facts.get(rule.predicate_ref)
                if observed is None or fact is None or type(fact) is not type(rule.value):
                    rules.append(RuleVerdict(rule.rule_id, "unknown", "FACT_UNAVAILABLE", None))
                else:
                    passed = fact == rule.value
                    rules.append(RuleVerdict(rule.rule_id, "pass" if passed else "fail",
                                             "MATCH" if passed else "MISMATCH", observed.evidence_ref))
            overall = ("fail" if any(rule.verdict == "fail" for rule in rules)
                       else "unknown" if any(rule.verdict == "unknown" for rule in rules)
                       else "pass")
            verdicts.append(CandidateVerdict(candidate.candidate_id, candidate.asset_version,
                                             overall, tuple(rules)))
            self._guard.require_current(job.binding)
        self._guard.require_current(job.binding)
        return JobVerdict(job.binding, tuple(verdicts))
