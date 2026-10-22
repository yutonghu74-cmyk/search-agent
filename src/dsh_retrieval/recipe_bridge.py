"""Convert a *fresh* reviewed intent preview into a non-executable recipe.

This is a structural bridge, not a business approval. Only a trusted service
may later attach the recipe to an explicitly approved task and manifest.
"""

import json

from .errors import ServiceError
from .models import Task, TaskLifecycle
from .multi_rule_contracts import CleaningRecipe, CleaningRule, DetectionStep
from .query_spec import digest


def _reject():
    raise ServiceError("COMPILED_PREVIEW_INVALID", "Reviewed cleaning preview is invalid", 422)


def compile_reviewed_recipe(compiler, actor, draft_id, expected_version,
                            expected_review_digest):
    """Re-read current draft, review and capability bindings via the compiler.

    A supplied preview/JSON is intentionally not accepted: the compiler must
    obtain current owner-scoped state and require an explicit review action.
    """
    preview = compiler.preview(actor, draft_id, expected_version,
                               expected_review_digest, action="confirm_preview")
    try:
        valid_digest = (type(preview) is dict and preview.get("preview_digest") == digest(
            {k: v for k, v in preview.items() if k != "preview_digest"}))
    except (TypeError, ValueError, RecursionError):
        valid_digest = False
    if (type(preview) is not dict or set(preview) != {
            "schema_version", "draft_id", "draft_version", "raw_digest",
            "review_digest", "registry_digest", "media_type", "steps", "rules",
            "preferences", "creates_task", "execution_allowed", "preview_digest"}
            or preview["schema_version"] != "intent-compile-preview@1"
            or preview["draft_id"] != draft_id or preview["draft_version"] != expected_version
            or preview["review_digest"] != expected_review_digest
            or preview["creates_task"] is not False or preview["execution_allowed"] is not False
            or type(preview["steps"]) is not list or type(preview["rules"]) is not list
            or type(preview["preferences"]) is not list or not valid_digest):
        _reject()
    try:
        steps = []
        for item in preview["steps"]:
            if type(item) is not dict or set(item) != {
                    "step_id", "backend_ref", "validator_ref", "evidence_ref"}:
                _reject()
            steps.append(DetectionStep(**item))
        rules = []
        for item in preview["rules"]:
            if (type(item) is not dict or set(item) != {
                    "item_id", "step_id", "predicate_ref", "operator", "value", "hardness", "quote"}
                    or type(item["quote"]) is not str or not item["quote"]):
                _reject()
            rules.append(CleaningRule(item["item_id"], item["step_id"],
                                      item["predicate_ref"], item["operator"],
                                      item["value"], item["hardness"]))
        recipe = CleaningRecipe(preview["media_type"], preview["review_digest"],
                                preview["registry_digest"], tuple(steps), tuple(rules))
    except (TypeError, ValueError, KeyError, AttributeError):
        _reject()
    # Preferences are deliberately not hard filters and cannot be copied into
    # the recipe. Their original review remains available by digest.
    return recipe


def require_confirmed_rules(task: Task, recipe: CleaningRecipe) -> None:
    """Check rule equivalence against the task's immutable confirmed proposal.

    The caller must obtain ``task`` from an authorized, current repository read.
    This pure check neither approves a cleaning operation nor fences its commit.
    """
    def mismatch():
        raise ServiceError("RECIPE_RULE_MISMATCH", "Cleaning rules differ from confirmed task", 409)

    if (type(task) is not Task or type(recipe) is not CleaningRecipe
            or task.lifecycle != TaskLifecycle.ACTIVE
            or type(task.confirmed_revision) is not int or task.confirmed_revision < 1
            or not isinstance(task.confirmed_proposal_json, str)):
        mismatch()
    try:
        proposal = json.loads(task.confirmed_proposal_json)
        spec = proposal["query_spec"]
        if (type(proposal) is not dict or type(spec) is not dict
                or spec["schema_version"] != "query-spec@2"
                or spec["media_type"] != task.media_type
                or spec["media_type"] != recipe.media_type
                or spec["raw_request"] != task.raw_request
                or spec["target_count"] != task.target_count
                or spec["unknown_policy"] != "hold_for_review"
                or task.request_digest != digest({"project_id": task.project_id, "proposal": proposal})
                or type(spec["rules"]) is not list):
            mismatch()
        hard = [row for row in spec["rules"] if row["hardness"] == "hard"]
        if len(hard) != len(recipe.rules):
            mismatch()
        for confirmed, compiled in zip(hard, recipe.rules):
            if (type(confirmed) is not dict
                    or (confirmed["rule_id"], confirmed["predicate_ref"], confirmed["operator"])
                    != (compiled.rule_id, compiled.predicate_ref, compiled.operator)
                    or type(confirmed["value"]) is not type(compiled.value)
                    or confirmed["value"] != compiled.value):
                mismatch()
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
        mismatch()
