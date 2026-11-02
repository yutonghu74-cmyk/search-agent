"""Execute an approved recipe using only injected, pre-registered detectors.

This module deliberately does not construct approvals, locate media, persist
results, or register any real detector. Those are separate trusted boundaries.
"""

from typing import Mapping

from ...errors import ServiceError
from ...multi_rule_contracts import (
    ApprovedCleaningJob,
    CleaningCandidateVerdict as CandidateVerdict,
    CleaningJobResult as JobVerdict,
    CleaningRuleVerdict as RuleVerdict,
    DetectionResult,
    bytes_digest,
    require_complete_cleaning_result,
)
MAX_CANDIDATE_BYTES = 10 * 1024 * 1024
MAX_JOB_BYTES = 100 * 1024 * 1024


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

    def run(self, job: ApprovedCleaningJob, *, completed=(), checkpoint=None,
            partial=None, step_checkpoint=None) -> JobVerdict:
        if type(job) is not ApprovedCleaningJob:
            raise TypeError("only an approved cleaning job can be executed")
        self._preflight(job)
        self._guard.require_current(job.binding)
        total_bytes = 0
        verdicts = []
        cached = {item.candidate_id: item for item in completed}
        partial_id, partial_steps = partial if partial is not None else (None, ())
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
            # Re-read authorized bytes even on resume, but do not repeat model
            # calls for an immutable, fully persisted candidate verdict.
            if candidate.candidate_id in cached:
                verdicts.append(cached[candidate.candidate_id])
                self._guard.require_current(job.binding)
                continue

            by_rule = {}
            for step_index, step in enumerate(job.recipe.steps):
                step_rules = [rule for rule in job.recipe.rules if rule.step_id == step.step_id]
                if candidate.candidate_id == partial_id and step_index < len(partial_steps):
                    saved = partial_steps[step_index]
                    if tuple(rule.rule_id for rule in step_rules) != tuple(item.rule_id for item in saved):
                        raise ServiceError('STORAGE_CORRUPT', 'Step checkpoint mismatches recipe', 500)
                    by_rule.update((item.rule_id, item) for item in saved)
                    continue
                error = None
                try:
                    observed = self._detectors[step.backend_ref].detect(content, candidate, job.binding)
                except ServiceError:
                    raise
                except Exception:
                    observed = None
                    error = 'DETECTOR_ERROR'
                if type(observed) is not DetectionResult or observed.validator_ref != step.validator_ref:
                    observed = None
                    error = error or 'DETECTOR_OUTPUT_INVALID'
                saved = []
                for rule in step_rules:
                    fact = None if observed is None else observed.facts.get(rule.predicate_ref)
                    if observed is None or fact is None or type(fact) is not type(rule.value):
                        explicit_unknown = observed is not None and observed.status == 'unknown'
                        saved.append(RuleVerdict(rule.rule_id, 'unknown', error or 'FACT_UNAVAILABLE', None,
                                                 observed.reason_summary if explicit_unknown else None,
                                                 observed.reason_truncated if explicit_unknown else False))
                    else:
                        passed = fact == rule.value
                        saved.append(RuleVerdict(rule.rule_id, 'pass' if passed else 'fail',
                                                 'MATCH' if passed else 'MISMATCH', observed.evidence_ref,
                                                 observed.reason_summary if not passed else None,
                                                 observed.reason_truncated if not passed else False))
                by_rule.update((item.rule_id, item) for item in saved)
                self._guard.require_current(job.binding)
                if step_checkpoint is not None:
                    step_checkpoint(candidate, step, tuple(saved))
            rules = [by_rule[rule.rule_id] for rule in job.recipe.rules]
            overall = ("fail" if any(rule.verdict == "fail" for rule in rules)
                       else "unknown" if any(rule.verdict == "unknown" for rule in rules)
                       else "pass")
            verdicts.append(CandidateVerdict(candidate.candidate_id, candidate.asset_version,
                                             overall, tuple(rules)))
            self._guard.require_current(job.binding)
            if checkpoint is not None:
                checkpoint(verdicts[-1])
        self._guard.require_current(job.binding)
        return require_complete_cleaning_result(
            job, JobVerdict(job.binding, tuple(verdicts)))
