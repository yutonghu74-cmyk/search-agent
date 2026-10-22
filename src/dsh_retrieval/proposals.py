"""First v1.9 slice: text queries for images, no historical dedup or auto dispatch."""
from copy import deepcopy
from dataclasses import dataclass
import json

from .errors import ServiceError, invalid
from .query_spec import normalize_query_spec, digest
from .expansion_models import normalize_grouped_expansion

NODES = ("expansion", "retrieval", "cleaning", "refill", "packaging")


def normalize_proposal(body: dict) -> dict:
    if (not isinstance(body, dict) or not {"dimension_id", "query_spec"} <= set(body)
            or set(body) - {"dimension_id", "query_spec", "node_modes"}):
        raise invalid("expected dimension_id, query_spec and node_modes", "$")
    dimension = body["dimension_id"]
    if not isinstance(dimension, str) or not dimension.strip():
        raise invalid("dimension_id is required", "$.dimension_id")
    spec = body["query_spec"]
    if not isinstance(spec, dict):
        raise invalid("query_spec must be an object", "$.query_spec")
    if {"dedup_policy", "allow_auto_refill", "auto_refill"} & set(spec):
        raise ServiceError("SCHEMA_CONFLICT", "legacy dedup/refill fields are not accepted", 422)
    if spec.get("schema_version") != "query-spec@2":
        raise ServiceError("SCHEMA_CONFLICT", "new drafts require query-spec@2", 422)
    constraints = spec.get("source_constraints", [])
    if not isinstance(constraints, list) or any(not isinstance(item, dict) for item in constraints):
        raise invalid("source_constraints must be an array of objects", "$.query_spec.source_constraints")
    required = {"intra_batch_dedup", "cross_batch_dedup", "refill_policy"}
    if not required <= set(spec):
        raise invalid("explicit dedup and refill policies are required", "$.query_spec")
    if spec["intra_batch_dedup"] != {"policy_ref": "exact-content", "config_version": "1"}:
        raise ServiceError("FEATURE_NOT_SUPPORTED", "only exact-content@1 is enabled", 422)
    if spec["cross_batch_dedup"] != {"scope": "none", "references": [],
                                    "comparison_methods": [], "reference_digest": None}:
        raise ServiceError("FEATURE_NOT_SUPPORTED", "historical dedup is not connected", 422)
    refill = spec["refill_policy"]
    if not isinstance(refill, dict) or set(refill) != {"mode", "target_count", "approved_library_ids", "progression"}:
        raise invalid("invalid refill policy", "$.query_spec.refill_policy")
    if type(refill["target_count"]) is not int or refill["target_count"] != spec.get("target_count"):
        raise invalid("refill target must equal main target", "$.query_spec.refill_policy.target_count")
    if refill["mode"] != "input_only" or refill["progression"] != "manual" or refill["approved_library_ids"] != []:
        raise ServiceError("FEATURE_NOT_SUPPORTED", "this slice supports input_only/manual with no library expansion", 422)
    modes = body.get("node_modes", dict.fromkeys(NODES, "manual"))
    if modes != dict.fromkeys(NODES, "manual"):
        raise ServiceError("FEATURE_NOT_SUPPORTED", "this slice requires five explicit manual gates", 422)
    grouped_expansion = spec.get("grouped_expansion")
    legacy = {k: v for k, v in spec.items() if k not in required | {"grouped_expansion"}}
    legacy["schema_version"] = "query-spec@1"
    # Reuse field/rule validation, not the old wire contract or its defaults.
    legacy["allow_auto_refill"] = False
    normalized = normalize_query_spec(legacy)
    normalized.pop("allow_auto_refill", None)
    normalized.pop("dedup_policy", None)
    normalized["schema_version"] = "query-spec@2"
    normalized.update({key: deepcopy(spec[key]) for key in required})
    if grouped_expansion is not None:
        try:
            normalized["grouped_expansion"] = normalize_grouped_expansion(grouped_expansion)
        except ValueError as error:
            raise invalid(str(error), "$.query_spec.grouped_expansion") from None
    return {"dimension_id": dimension, "query_spec": normalized, "node_modes": dict(modes)}


@dataclass(frozen=True, slots=True)
class Draft:
    draft_id: str
    tenant_id: str
    owner_user_id: str
    project_id: str
    dimension_id: str
    proposal_json: str
    draft_version: int = 1
    confirmed_task_id: str | None = None
    conversation_id: str | None = None

    @property
    def proposal_digest(self) -> str:
        return digest({"project_id": self.project_id, "proposal": json.loads(self.proposal_json)})

    def to_summary(self) -> dict:
        return {"draft_id": self.draft_id, "draft_version": self.draft_version,
                "project_id": self.project_id, "dimension_id": self.dimension_id,
                "conversation_id": self.conversation_id,
                "proposal": json.loads(self.proposal_json), "proposal_digest": self.proposal_digest,
                "status": "confirmed" if self.confirmed_task_id else "draft",
                "confirmed_task_id": self.confirmed_task_id}
