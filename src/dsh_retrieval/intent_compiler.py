"""Deterministic multi-rule preview from a current, owner-reviewed draft."""
from dataclasses import dataclass
import re

from .errors import ServiceError, invalid
from .query_spec import digest
from .service import require_version


@dataclass(frozen=True, slots=True)
class DetectorBinding:
    predicate_ref: str
    media_type: str
    validator_ref: str
    backend_ref: str
    evidence_ref: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip() for value in (
                self.predicate_ref, self.media_type, self.validator_ref, self.backend_ref, self.evidence_ref)):
            raise ValueError("trusted detector binding is incomplete")
        if self.media_type not in {"image", "video", "text"}:
            raise ValueError("unsupported detector media type")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}", self.backend_ref):
            raise ValueError("backend_ref must be a registered logical identifier")


class IntentCompiler:
    def __init__(self, store, bindings=()):
        self.store = store
        self.bindings = tuple(bindings)
        keys = [(b.predicate_ref, b.media_type) for b in self.bindings]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate detector binding")
        self.by_key = {(b.predicate_ref, b.media_type): b for b in self.bindings}

    def preview(self, actor, draft_id, expected_version, expected_review_digest, *, action):
        require_version(expected_version)
        if action != "confirm_preview":
            raise ServiceError("CONFIRMATION_REQUIRED", "explicit owner review is required", 422)
        if not isinstance(expected_review_digest, str) or not expected_review_digest:
            raise invalid("review digest is required", "$.expected_review_digest")
        draft = self.store.get(actor, draft_id)
        review = draft["review"]
        if draft["draft_version"] != expected_version or review["review_digest"] != expected_review_digest:
            raise ServiceError("DRAFT_VERSION_CONFLICT", "reviewed draft or capability registry changed", 409)
        if review["has_blockers"] or review["issues"]:
            raise ServiceError("INTERPRETATION_BLOCKED", "clarify or register every required condition", 422)
        steps, step_ids, rules, preferences = [], {}, [], []
        for item in review["items"]:
            if item["hardness"] == "soft":
                preferences.append({"item_id": item["item_id"], "quote": item["quote"],
                                    "predicate_ref": item["predicate_ref"], "value": item["value"],
                                    "capability_status": item["capability_status"]})
                continue
            if item["capability_status"] != "automatable":
                raise ServiceError("INTERPRETATION_BLOCKED", "required condition lacks verified capability", 422)
            if item["operator"] != "eq":
                raise ServiceError("OPERATOR_NOT_SUPPORTED", "rule evaluator supports equality only", 422)
            binding = self.by_key.get((item["predicate_ref"], review["media_type"]))
            capability = next((c for c in self.store.reviewer.capabilities if
                (c.kind, c.predicate_ref, c.media_type) ==
                (item["kind"], item["predicate_ref"], review["media_type"])), None)
            if (binding is None or capability is None or capability.status != "verified"
                    or binding.validator_ref != capability.validator_ref
                    or binding.evidence_ref != capability.evidence_ref):
                raise ServiceError("DETECTOR_NOT_RELEASED", "required condition has no matching detector", 422)
            if binding.backend_ref not in step_ids:
                step_ids[binding.backend_ref] = f"step_{len(steps) + 1}"
                steps.append({"step_id": step_ids[binding.backend_ref], "backend_ref": binding.backend_ref,
                              "validator_ref": binding.validator_ref, "evidence_ref": binding.evidence_ref})
            else:
                shared = next(step for step in steps if step["backend_ref"] == binding.backend_ref)
                if shared["validator_ref"] != binding.validator_ref or shared["evidence_ref"] != binding.evidence_ref:
                    raise ServiceError("DETECTOR_NOT_RELEASED", "shared detector mapping is inconsistent", 422)
            rules.append({"item_id": item["item_id"], "step_id": step_ids[binding.backend_ref],
                          "predicate_ref": item["predicate_ref"], "operator": item["operator"],
                          "value": item["value"], "hardness": "hard", "quote": item["quote"]})
        if not rules:
            raise ServiceError("NO_EXECUTABLE_RULES", "no verified hard conditions to compile", 422)
        plan = {"schema_version": "intent-compile-preview@1", "draft_id": draft_id,
                "draft_version": expected_version, "raw_digest": review["raw_digest"],
                "review_digest": expected_review_digest, "registry_digest": review["registry_digest"],
                "media_type": review["media_type"], "steps": steps, "rules": rules,
                "preferences": preferences, "creates_task": False, "execution_allowed": False}
        plan["preview_digest"] = digest(plan)
        return plan


def evaluate_facts(preview, facts):
    """Pure tri-state check for trusted detector facts; no media or worker I/O."""
    if (not isinstance(preview, dict) or preview.get("schema_version") != "intent-compile-preview@1"
            or not isinstance(facts, dict) or not isinstance(preview.get("rules"), list)
            or not preview["rules"] or preview.get("execution_allowed") is not False):
        raise invalid("expected compiled preview and detector fact mapping")
    if preview.get("preview_digest") != digest({key: value for key, value in preview.items()
                                                if key != "preview_digest"}):
        raise invalid("compiled preview digest does not match")
    results = []
    for rule in preview["rules"]:
        if (not isinstance(rule, dict) or rule.get("hardness") != "hard"
                or rule.get("operator") != "eq" or not isinstance(rule.get("step_id"), str)
                or not isinstance(rule.get("predicate_ref"), str)
                or not isinstance(rule.get("item_id"), str)):
            raise invalid("invalid compiled rule")
        step = facts.get(rule["step_id"])
        observed = step.get(rule["predicate_ref"]) if isinstance(step, dict) else None
        if observed is None or type(observed) is not type(rule["value"]):
            verdict = "unknown"
        elif rule["operator"] == "eq":
            verdict = "pass" if observed == rule["value"] else "fail"
        else:
            raise invalid("unsupported rule operator")
        results.append({"item_id": rule["item_id"], "verdict": verdict})
    verdict = ("fail" if any(row["verdict"] == "fail" for row in results)
               else "unknown" if any(row["verdict"] == "unknown" for row in results)
               else "pass")
    return {"verdict": verdict, "rules": results,
            "evaluation_kind": "trusted_facts_only", "creates_task": False}
