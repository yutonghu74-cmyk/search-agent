"""Internal ephemeral candidate preview; never a published cleaning manifest."""
from dataclasses import asdict, dataclass

from .cleaning_models import Candidate
from .integration_models import MediaRecord, SearchHit
from .query_spec import digest
from .retrieval_contracts import RetrievalOperation


@dataclass(frozen=True, slots=True)
class MaterializedCandidate:
    candidate: Candidate
    media: MediaRecord
    hits: tuple[SearchHit, ...]


@dataclass(frozen=True, slots=True)
class MaterializedPage:
    """Files are usable only inside CandidateMaterializer.materialize's context.

    Internal media refs/paths must not be sent to clients. view() intentionally
    omits them. Preview counts are NOT accepted_count or cleaning verdicts.
    """
    operation: RetrievalOperation
    items: tuple[MaterializedCandidate, ...]

    @property
    def preview_digest(self) -> str:
        return digest({"operation_id": self.operation.operation_id,
                       "plan_digest": self.operation.plan.proposal_digest,
                       "items": [{"candidate": {k: v for k, v in asdict(item.candidate).items()
                                                 if k != "source_path"},
                                  "media": asdict(item.media),
                                  "hits": [asdict(hit) for hit in item.hits]} for item in self.items]})

    def view(self) -> dict:
        return {"kind": "ephemeral_candidate_preview", "operation_id": self.operation.operation_id,
                "task_id": self.operation.plan.scope.task_id,
                "candidate_count": len(self.items),
                "hit_count": sum(len(item.hits) for item in self.items),
                "preview_digest": self.preview_digest, "accepted_count": None,
                "candidates": [{"candidate_id": item.candidate.candidate_id,
                                "asset_version": item.candidate.asset_version,
                                "content_digest": item.candidate.content_digest} for item in self.items]}
