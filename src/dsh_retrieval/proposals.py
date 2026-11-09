"""Confirmed v1.9 proposals with an explicitly approved refill progression."""
from copy import deepcopy
from dataclasses import dataclass
import json
import os
import re

from .errors import ServiceError, invalid
from .query_spec import normalize_query_spec, digest
from .expansion_models import delivery_targets, normalize_grouped_expansion
from .refill_planning import normalize_refill_limits

NODES = ("expansion", "retrieval", "cleaning", "refill", "packaging")
GROUP_QUOTAS_ENV = "DSH_GROUP_QUOTAS"


def _normalize_query_input(value):
    if value is None:
        return None
    if (not isinstance(value, dict)
            or set(value) != {"type", "query_image_id", "digest", "exclude_exact_content"}
            or value.get("type") != "reference_image"
            or not isinstance(value.get("query_image_id"), str)
            or re.fullmatch(r"qimg_[0-9a-f]{32}", value["query_image_id"]) is None
            or not isinstance(value.get("digest"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", value["digest"]) is None
            or type(value.get("exclude_exact_content")) is not bool):
        raise invalid("invalid immutable reference image input", "$.query_spec.query_input")
    return deepcopy(value)


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
    libraries = refill.get("approved_library_ids")
    if (not isinstance(libraries, list)
            or any(not isinstance(item, str) or not item.strip() for item in libraries)
            or len(libraries) != len(set(libraries))):
        raise invalid("approved library ids must be unique non-empty strings",
                      "$.query_spec.refill_policy.approved_library_ids")
    modes = body.get("node_modes", dict.fromkeys(NODES, "manual"))
    if (not isinstance(modes, dict) or set(modes) != set(NODES)
            or any(value not in {"manual", "auto"} for value in modes.values())
            or modes["expansion"] != "manual" or modes["packaging"] != "manual"):
        raise ServiceError("FEATURE_NOT_SUPPORTED",
                           "expansion and packaging remain manual in this slice", 422)
    if refill["mode"] == "input_only":
        if (refill.get("progression") != "manual" or libraries
                or any(modes[node] != "manual" for node in
                       ("retrieval", "cleaning", "refill"))):
            raise ServiceError("FEATURE_NOT_SUPPORTED",
                               "input_only requires manual progression and no refill libraries", 422)
    elif refill["mode"] == "maintain_target":
        if (refill.get("progression") not in {"manual", "auto"} or not libraries
                or modes["refill"] != refill["progression"]):
            raise ServiceError("FEATURE_NOT_SUPPORTED",
                               "maintain_target progression must match the confirmed refill node mode", 422)
        if ((modes["retrieval"] == "auto") != (modes["cleaning"] == "auto")
                or modes["retrieval"] == "auto" and modes["refill"] != "auto"):
            raise ServiceError(
                "FEATURE_NOT_SUPPORTED",
                "automatic candidate intake requires retrieval, cleaning and refill together", 422)
    else:
        raise invalid("unsupported refill mode", "$.query_spec.refill_policy.mode")
    try:
        refill_limits = normalize_refill_limits(spec.get("refill_limits"))
    except ValueError as error:
        raise invalid(str(error), "$.query_spec.refill_limits") from None
    grouped_expansion = spec.get("grouped_expansion")
    query_input = _normalize_query_input(spec.get("query_input"))
    legacy = {k: v for k, v in spec.items()
              if k not in required | {"grouped_expansion", "refill_limits", "query_input"}}
    legacy["schema_version"] = "query-spec@1"
    # Reuse field/rule validation, not the old wire contract or its defaults.
    legacy["allow_auto_refill"] = False
    normalized = normalize_query_spec(legacy)
    normalized.pop("allow_auto_refill", None)
    normalized.pop("dedup_policy", None)
    normalized["schema_version"] = "query-spec@2"
    normalized.update({key: deepcopy(spec[key]) for key in required})
    normalized["refill_limits"] = refill_limits
    if query_input is not None:
        normalized["query_input"] = query_input
    if grouped_expansion is not None:
        try:
            normalized["grouped_expansion"] = normalize_grouped_expansion(grouped_expansion)
        except ValueError as error:
            raise invalid(str(error), "$.query_spec.grouped_expansion") from None
        targets = delivery_targets(normalized["grouped_expansion"])
        if targets:
            if os.environ.get(GROUP_QUOTAS_ENV, "on") == "off":
                # Refuse rather than drop: silently ignoring a confirmed quota
                # would report a total as if the requested split had been met.
                raise ServiceError("QUOTA_NOT_SUPPORTED",
                                   "group quotas are disabled in this deployment", 422,
                                   field_path="$.query_spec.grouped_expansion")
            if sum(target for _, target in targets) != normalized["target_count"]:
                raise invalid("group delivery targets must sum to target_count",
                              "$.query_spec.grouped_expansion")
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
