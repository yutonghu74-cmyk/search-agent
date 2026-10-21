from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ServiceError, invalid


QUERY_SPEC_FIELDS = {
    "schema_version",
    "raw_request",
    "media_type",
    "count_unit",
    "target_count",
    "quotas",
    "business_domain",
    "source_constraints",
    "rules",
    "preferences",
    "query_seeds",
    "dedup_policy",
    "unknown_policy",
    "allow_auto_refill",
}
RULE_FIELDS = {"rule_id", "predicate_ref", "operator", "value", "hardness", "origin"}
PREFERENCE_FIELDS = {"field", "value"}


@dataclass(frozen=True, slots=True)
class PredicateDefinition:
    value_type: type
    operators: frozenset[str]


DEFAULT_PREDICATES: Mapping[str, PredicateDefinition] = {
    "scene.indoor@1": PredicateDefinition(bool, frozenset({"eq"})),
    "subject.home@1": PredicateDefinition(bool, frozenset({"eq"})),
    "watermark.present@1": PredicateDefinition(bool, frozenset({"eq"})),
    "text.present@1": PredicateDefinition(bool, frozenset({"eq"})),
    "person.present@1": PredicateDefinition(bool, frozenset({"eq"})),
    "style.home_lifestyle@1": PredicateDefinition(bool, frozenset({"eq"})),
    "orientation@1": PredicateDefinition(str, frozenset({"eq", "in"})),
}

APPROVED_DEDUP_POLICIES = frozenset({"visual-near-duplicate-v1"})
APPROVED_UNKNOWN_POLICIES = frozenset({"hold_for_review"})
APPROVED_PREFERENCES = {
    "orientation": frozenset({"landscape", "portrait", "square"}),
}


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise invalid("must be an object", path)
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise invalid(f"unknown field: {unknown[0]}", f"{path}.{unknown[0]}")


def _require_non_empty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise invalid("must be a non-empty string", path)
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise invalid("must be an array", path)
    return value


def normalize_query_spec(
    raw: Mapping[str, Any],
    *,
    predicates: Mapping[str, PredicateDefinition] = DEFAULT_PREDICATES,
    stage: str = "P1",
) -> dict[str, Any]:
    spec = _require_object(dict(raw), "$")
    _reject_unknown(spec, QUERY_SPEC_FIELDS, "$")
    required = {
        "schema_version", "raw_request", "media_type", "target_count", "business_domain",
    }
    missing = sorted(required - set(spec))
    if missing:
        raise invalid("required field is missing", f"$.{missing[0]}")
    if spec["schema_version"] != "query-spec@1":
        raise invalid("unsupported QuerySpec version", "$.schema_version")

    media_type = spec["media_type"]
    count_unit = spec.get("count_unit", "image" if media_type == "image" else None)
    if stage in {"P1", "P2"} and (media_type != "image" or count_unit != "image"):
        raise ServiceError(
            "FEATURE_NOT_SUPPORTED", "only image/image is enabled in the current stage", 422,
            field_path="$.media_type",
        )
    if media_type not in {"image", "video"}:
        raise invalid("unsupported media_type", "$.media_type")
    if (media_type, count_unit) not in {
        ("image", "image"), ("video", "clip"), ("video", "distinct_video"),
    }:
        raise invalid("media_type and count_unit are incompatible", "$.count_unit")

    target_count = spec["target_count"]
    if isinstance(target_count, bool) or not isinstance(target_count, int) or target_count <= 0:
        raise invalid("must be a positive integer", "$.target_count")

    quotas = _require_list(spec.get("quotas", []), "$.quotas")
    if stage in {"P1", "P2"} and quotas:
        raise ServiceError(
            "QUOTA_NOT_SUPPORTED", "group quotas are not enabled in the current stage", 422,
            field_path="$.quotas",
        )

    normalized_rules: list[dict[str, Any]] = []
    rule_ids: set[str] = set()
    for index, raw_rule in enumerate(_require_list(spec.get("rules", []), "$.rules")):
        path = f"$.rules[{index}]"
        rule = _require_object(raw_rule, path)
        _reject_unknown(rule, RULE_FIELDS, path)
        for field in RULE_FIELDS:
            if field not in rule:
                raise invalid("required field is missing", f"{path}.{field}")
        rule_id = _require_non_empty_string(rule["rule_id"], f"{path}.rule_id")
        if rule_id in rule_ids:
            raise invalid("rule_id must be unique", f"{path}.rule_id")
        rule_ids.add(rule_id)
        predicate_ref = _require_non_empty_string(rule["predicate_ref"], f"{path}.predicate_ref")
        definition = predicates.get(predicate_ref)
        if definition is None:
            raise ServiceError("UNSUPPORTED_RULE", "predicate is not registered", 422, field_path=f"{path}.predicate_ref")
        if rule["operator"] not in definition.operators:
            raise invalid("operator is not valid for predicate", f"{path}.operator")
        if type(rule["value"]) is not definition.value_type:
            raise invalid("value type does not match predicate", f"{path}.value")
        if rule["hardness"] not in {"hard", "soft"}:
            raise invalid("hardness must be hard or soft", f"{path}.hardness")
        if rule["origin"] not in {"user_explicit", "user_confirmed", "skill_suggested", "system_default"}:
            raise invalid("unsupported rule origin", f"{path}.origin")
        normalized_rules.append(dict(rule))

    preferences: list[dict[str, Any]] = []
    for index, raw_preference in enumerate(_require_list(spec.get("preferences", []), "$.preferences")):
        path = f"$.preferences[{index}]"
        preference = _require_object(raw_preference, path)
        _reject_unknown(preference, PREFERENCE_FIELDS, path)
        if set(preference) != PREFERENCE_FIELDS:
            raise invalid("preference requires field and value", path)
        allowed_values = APPROVED_PREFERENCES.get(preference["field"])
        if allowed_values is None or preference["value"] not in allowed_values:
            raise invalid("preference is not registered", path)
        preferences.append(dict(preference))

    seeds = _require_list(spec.get("query_seeds", []), "$.query_seeds")
    for index, seed in enumerate(seeds):
        _require_non_empty_string(seed, f"$.query_seeds[{index}]")
    dedup_policy = spec.get("dedup_policy", "visual-near-duplicate-v1")
    if dedup_policy not in APPROVED_DEDUP_POLICIES:
        raise invalid("dedup policy is not approved", "$.dedup_policy")
    unknown_policy = spec.get("unknown_policy", "hold_for_review")
    if unknown_policy not in APPROVED_UNKNOWN_POLICIES:
        raise invalid("unknown policy is not approved", "$.unknown_policy")
    allow_auto_refill = spec.get("allow_auto_refill", True)
    if not isinstance(allow_auto_refill, bool):
        raise invalid("must be a boolean", "$.allow_auto_refill")

    return {
        "schema_version": "query-spec@1",
        "raw_request": _require_non_empty_string(spec["raw_request"], "$.raw_request"),
        "media_type": media_type,
        "count_unit": count_unit,
        "target_count": target_count,
        "quotas": quotas,
        "business_domain": _require_non_empty_string(spec["business_domain"], "$.business_domain"),
        "source_constraints": _require_list(spec.get("source_constraints", []), "$.source_constraints"),
        "rules": normalized_rules,
        "preferences": preferences,
        "query_seeds": list(seeds),
        "dedup_policy": dedup_policy,
        "unknown_policy": unknown_policy,
        "allow_auto_refill": allow_auto_refill,
    }


def canonical_json(value: Mapping[str, Any]) -> bytes:
    def reject_non_finite(item: Any) -> None:
        if isinstance(item, float) and not math.isfinite(item):
            raise invalid("NaN and Infinity are forbidden")
        if isinstance(item, dict):
            for child in item.values():
                reject_non_finite(child)
        elif isinstance(item, list):
            for child in item:
                reject_non_finite(child)

    reject_non_finite(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()

