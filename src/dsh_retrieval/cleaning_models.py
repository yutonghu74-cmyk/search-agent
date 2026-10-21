"""Immutable business contracts; no DJ SDK types cross this boundary."""
from dataclasses import dataclass, asdict
import json

from .query_spec import digest


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    asset_version: str
    source_path: str  # server-side materialization only; never accepted from public requests
    content_digest: str


@dataclass(frozen=True)
class CleaningProposal:
    proposal_id: str
    tenant_id: str
    owner_user_id: str
    project_id: str
    task_id: str
    revision: int
    generation: int
    task_version: int
    manifest_ref: str
    candidates: tuple[Candidate, ...]
    recipe_ref: str
    min_width: int

    @property
    def manifest_digest(self):
        return digest([{k: v for k, v in asdict(c).items() if k != "source_path"} for c in self.candidates])

    @property
    def recipe_digest(self):
        return digest({"recipe_ref": self.recipe_ref, "min_width": self.min_width, "rule_id": "width"})

    @property
    def plan_digest(self):
        return digest({"tenant": self.tenant_id, "owner": self.owner_user_id, "project": self.project_id,
                       "task": self.task_id, "revision": self.revision, "generation": self.generation,
                       "manifest": self.manifest_digest, "recipe": self.recipe_digest})

    def view(self):
        return {"proposal_id": self.proposal_id, "task_id": self.task_id, "revision": self.revision,
                "run_generation": self.generation, "expected_task_version": self.task_version,
                "manifest_ref": self.manifest_ref, "input_manifest_digest": self.manifest_digest,
                "candidate_count": len(self.candidates), "recipe_ref": self.recipe_ref,
                "recipe_digest": self.recipe_digest, "rule": {"id": "width", "min_width": self.min_width},
                "proposal_digest": self.plan_digest}


@dataclass(frozen=True)
class CleaningOperation:
    operation_id: str
    proposal: CleaningProposal
    status: str = "queued"
    backend_json: str | None = None
    result_json: str | None = None

    def binding(self):
        p = self.proposal
        return {"operation_id": self.operation_id, "tenant_id": p.tenant_id, "project_id": p.project_id,
                "task_id": p.task_id, "revision": p.revision, "run_generation": p.generation,
                "plan_digest": p.plan_digest, "input_manifest_digest": p.manifest_digest,
                "recipe_digest": p.recipe_digest, "attempt_id": self.operation_id + ":1"}

    def job(self):
        return {"binding": self.binding(), "candidates": [asdict(c) for c in self.proposal.candidates],
                "recipe_ref": self.proposal.recipe_ref, "min_width": self.proposal.min_width,
                "deadline_seconds": 60}

    def view(self):
        return {**self.binding(), "status": self.status,
                "backend": json.loads(self.backend_json) if self.backend_json else None,
                "results": json.loads(self.result_json) if self.result_json else None}
