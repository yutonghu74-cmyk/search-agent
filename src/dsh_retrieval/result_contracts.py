"""Pure input-only image result preview; no authorization or publication side effects."""

from dataclasses import dataclass

from .multi_rule_contracts import (ApprovedCleaningJob, CleaningJobResult,
                                   require_complete_cleaning_result)
from .query_spec import digest


@dataclass(frozen=True, slots=True)
class ExactRepresentative:
    candidate_id: str
    asset_version: str
    content_digest: str


@dataclass(frozen=True, slots=True)
class ExactResultSetPreview:
    operation_id: str
    generation: int
    task_version: int
    cleaning_result_digest: str
    target_count: int
    accepted: tuple[ExactRepresentative, ...]
    reserve: tuple[ExactRepresentative, ...]
    duplicate_count: int
    fail_count: int
    unknown_count: int
    gap: int
    stop_reason: str

    @property
    def accepted_count(self):
        return len(self.accepted)

    @property
    def result_set_digest(self):
        return digest({"schema_version": "exact-result-preview@1",
                       "operation_id": self.operation_id,
                       "generation": self.generation,
                       "task_version": self.task_version,
                       "cleaning_result_digest": self.cleaning_result_digest,
                       "target_count": self.target_count,
                       "accepted": [_ref_payload(item) for item in self.accepted],
                       "reserve": [_ref_payload(item) for item in self.reserve],
                       "duplicate_count": self.duplicate_count,
                       "fail_count": self.fail_count,
                       "unknown_count": self.unknown_count,
                       "gap": self.gap,
                       "stop_reason": self.stop_reason})


def _ref_payload(item):
    return {"candidate_id": item.candidate_id, "asset_version": item.asset_version,
            "content_digest": item.content_digest}


def build_exact_result_set(job: ApprovedCleaningJob, result: CleaningJobResult,
                           target_count: int) -> ExactResultSetPreview:
    """Count only fully passing, exact-content-unique image candidates.

    This is a deterministic preview for a fixed input-only batch. It does not
    substitute for current authorization, near-duplicate policy, ranking,
    cross-batch deduplication, or a published business result set.
    """

    require_complete_cleaning_result(job, result)
    if job.recipe.media_type != "image":
        raise ValueError("exact preview currently supports images only")
    if type(target_count) is not int or target_count < 1:
        raise ValueError("target_count must be a positive integer")

    representatives = {}
    duplicate_count = fail_count = unknown_count = 0
    for candidate, verdict in zip(job.candidates, result.candidates):
        if verdict.verdict == "fail":
            fail_count += 1
        elif verdict.verdict == "unknown":
            unknown_count += 1
        else:
            ref = ExactRepresentative(candidate.candidate_id, candidate.asset_version,
                                      candidate.content_digest)
            old = representatives.get(candidate.content_digest)
            if old is None:
                representatives[candidate.content_digest] = ref
            else:
                duplicate_count += 1
                if (ref.candidate_id, ref.asset_version) < (old.candidate_id, old.asset_version):
                    representatives[candidate.content_digest] = ref

    ordered = tuple(sorted(representatives.values(),
                           key=lambda ref: (ref.candidate_id, ref.asset_version, ref.content_digest)))
    accepted, reserve = ordered[:target_count], ordered[target_count:]
    gap = max(0, target_count - len(accepted))
    return ExactResultSetPreview(job.binding.operation_id, job.binding.generation,
                                 job.binding.task_version, result.result_digest,
                                 target_count, accepted, reserve, duplicate_count,
                                 fail_count, unknown_count, gap,
                                 "TARGET_MET" if gap == 0 else "SEARCH_STRATEGIES_EXHAUSTED")
