"""Read-only exact-content preview, never a published business result set."""
import json

from ..errors import ServiceError
from ..ports import CleaningCatalog, CleaningRepository, ProjectContextProvider


class CountingPreviewService:
    def __init__(self, repository: CleaningRepository, projects: ProjectContextProvider,
                 catalog: CleaningCatalog):
        self.repository, self.projects, self.catalog = repository, projects, catalog

    def preview(self, actor, task_id):
        task = self.repository.get(actor, task_id)
        self.projects.require_access(actor, task.project_id, task.dimension_id, "read")
        query = json.loads(task.confirmed_proposal_json)["query_spec"]
        if (query["intra_batch_dedup"] != {"policy_ref": "exact-content", "config_version": "1"}
                or query["cross_batch_dedup"]["scope"] != "none"
                or query["refill_policy"]["mode"] != "input_only"):
            raise ServiceError("FEATURE_NOT_SUPPORTED", "preview supports exact-content/input-only only", 422)
        operation = self.repository.cleaning_results(actor, task_id)
        if operation is None:
            raise ServiceError("CLEANING_NOT_READY", "complete cleaning before counting", 409)
        proposal = operation.proposal
        self.catalog.require_scope(task, proposal.manifest_ref)
        candidates, width = self.catalog.resolve(actor.tenant_id, task.project_id,
                                                  proposal.manifest_ref, proposal.recipe_ref)
        if candidates != proposal.candidates or width != proposal.min_width:
            raise ServiceError("INPUT_SNAPSHOT_CHANGED", "input changed after cleaning", 409)
        rows = json.loads(operation.result_json)["results"]
        verdicts = {row["candidate_id"]: row["verdict"] for row in rows}
        groups = {}
        for candidate in candidates:
            if verdicts[candidate.candidate_id] == "pass":
                groups.setdefault(candidate.content_digest, []).append(candidate)
        representatives, duplicates = [], []
        for members in groups.values():
            members.sort(key=lambda c: (c.candidate_id, c.asset_version))
            representative = members[0]
            representatives.append({"candidate_id": representative.candidate_id,
                                    "asset_version": representative.asset_version})
            duplicates.extend({"candidate_id": c.candidate_id, "asset_version": c.asset_version,
                               "representative_id": representative.candidate_id,
                               "reason_code": "EXACT_CONTENT_DUPLICATE"} for c in members[1:])
        representatives.sort(key=lambda row: (row["candidate_id"], row["asset_version"]))
        duplicates.sort(key=lambda row: (row["candidate_id"], row["asset_version"]))
        self.projects.require_access(actor, task.project_id, task.dimension_id, "read")
        current = self.repository.get(actor, task_id)
        current_op = self.repository.cleaning_results(actor, task_id)
        if (current.task_version != task.task_version or current_op != operation):
            raise ServiceError("REVISION_CONFLICT", "task changed during preview; retry", 409)
        target = query["target_count"]
        return {"preview_only": True, "accepted_count": None, "binding": operation.binding(),
                "as_of_task_version": task.task_version, "policy_ref": "exact-content@1",
                "selection_ref": "fixture-identity-order@1", "target_count": target,
                "cleaning_counts": {v: sum(row["verdict"] == v for row in rows)
                                    for v in ("pass", "fail", "unknown")},
                "unique_pass_count": len(representatives), "duplicate_count": len(duplicates),
                "preview_gap": max(0, target - len(representatives)),
                "representatives": representatives[:target], "duplicates": duplicates,
                "additional_unique_count": max(0, len(representatives) - target),
                "refill_dispatched": False,
                "scope": "one cleaning operation; width rule only; no business completion"}
