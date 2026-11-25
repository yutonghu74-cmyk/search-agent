"""Convert a *fresh* reviewed intent preview into a non-executable recipe.

This is a structural bridge, not a business approval. Only a trusted service
may later attach the recipe to an explicitly approved task and manifest.
"""

import json

from .errors import ServiceError
from .expansion_models import delivery_targets
from .models import Task, TaskLifecycle
from .multi_rule_contracts import CleaningRecipe, CleaningRule, DetectionStep
from .query_spec import digest
from .vlm_instruction_plan import (confirmed_instruction_plan, instruction_plan,
                                   plan_for_instructions, with_model_instructions)


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


def confirmed_rules_digest(task: Task) -> str:
    """Return the immutable review identity for one confirmed rule set.

    This gives deployment-owned detectors (notably a task-scoped VLM
    detector) a stable value to bind before a recipe is prepared.  It is
    derived only from the current confirmed task snapshot; request payloads
    cannot supply or override it.
    """

    if type(task) is Task and task.media_type == "video":
        from .whole_video import video_conditions_digest
        return video_conditions_digest(task)
    if (type(task) is not Task or type(task.confirmed_revision) is not int
            or task.confirmed_revision < 1 or not isinstance(task.confirmed_proposal_json, str)):
        raise ServiceError("RECIPE_RULE_MISMATCH", "Confirmed cleaning rules unavailable", 409)
    try:
        proposal = json.loads(task.confirmed_proposal_json)
        spec = proposal["query_spec"]
        rules = spec["rules"]
        if (type(spec) is not dict or spec["schema_version"] != "query-spec@2"
                or type(rules) is not list):
            raise ValueError
        hard = [row for row in rules if type(row) is dict and row.get("hardness") == "hard"]
    except (TypeError, ValueError, KeyError, json.JSONDecodeError, RecursionError) as exc:
        raise ServiceError("RECIPE_RULE_MISMATCH", "Confirmed cleaning rules unavailable", 409) from exc
    return digest({
        "schema_version": "confirmed-cleaning-rules@1",
        "task_id": task.task_id,
        "revision": task.confirmed_revision,
        "request_digest": task.request_digest,
        "media_type": task.media_type,
        "rules": hard,
    })


def compile_confirmed_recipe(task: Task, registry) -> CleaningRecipe:
    """Compile the current confirmed hard rules through a released registry.

    Unlike ``compile_reviewed_recipe``, this entry point is for an already
    confirmed task.  It never accepts client-provided steps, backend names or
    release digests.  The released registry remains authoritative for every
    executable binding and performs the final artifact/capability preflight.
    """

    from .released_detectors import ReleasedDetectorRegistry

    if type(registry) is not ReleasedDetectorRegistry:
        raise ValueError("trusted released detector registry required")
    registry.preflight()
    if type(task) is Task and task.media_type == "video":
        return _compile_video_recipe(task, registry)
    review_digest = confirmed_rules_digest(task)
    try:
        proposal = json.loads(task.confirmed_proposal_json)
        hard = [row for row in proposal["query_spec"]["rules"]
                if row["hardness"] == "hard"]
    except (TypeError, ValueError, KeyError, json.JSONDecodeError, RecursionError) as exc:
        raise ServiceError("RECIPE_RULE_MISMATCH", "Confirmed cleaning rules unavailable", 409) from exc
    if not hard:
        raise ServiceError("NO_EXECUTABLE_RULES", "No confirmed hard cleaning rules", 422)

    # A single confirmed semantic image rule can be evaluated through several
    # source-grounded checks. Each is a separate VLM call and rule verdict.
    plan = getattr(registry, 'instruction_plan', ())
    if ('cleaning_conditions' in proposal['query_spec']
            or delivery_targets(proposal["query_spec"].get("grouped_expansion"))):
        expected = confirmed_instruction_plan(proposal["query_spec"])
        if (len(hard) != 1 or hard[0].get('predicate_ref') != 'image.semantic-match@1'
                or hard[0].get('operator') != 'eq' or hard[0].get('value') is not True
                or tuple((node['node_id'], node['backend_ref'], node['predicate_ref'],
                          node.get('group_id')) for node in plan)
                != tuple((node['node_id'], node['backend_ref'], node['predicate_ref'],
                          node.get('group_id')) for node in expected)):
            raise ServiceError('RECIPE_RULE_MISMATCH',
                               'Group checks differ from confirmed quotas', 409)
        steps = tuple(DetectionStep(node['node_id'], node['backend_ref'],
                                    'vlm.boolean@1', 'vlm.observation@1') for node in plan)
        rules = tuple(CleaningRule(node['rule_id'], node['node_id'], node['predicate_ref'],
                                   'eq', True, group_id=node.get('group_id')) for node in plan)
        recipe = CleaningRecipe(task.media_type, review_digest,
                                registry.registry_digest, steps, rules,
                                registry.frozen_instructions)
        registry.require_recipe(recipe)
        require_confirmed_rules(task, recipe)
        return recipe
    if len(plan) > 1:
        if (len(hard) != 1 or hard[0].get('predicate_ref') != 'image.semantic-match@1'
                or hard[0].get('operator') != 'eq' or hard[0].get('value') is not True
                or tuple((node['node_id'], node['source'], node['backend_ref'], node['predicate_ref'])
                         for node in plan) != tuple((node['node_id'], node['source'],
                         node['backend_ref'], node['predicate_ref'])
                         for node in instruction_plan(task.raw_request))):
            raise ServiceError('RECIPE_RULE_MISMATCH',
                               'Multi-check plan differs from confirmed request', 409)
        steps = tuple(DetectionStep(node['node_id'], node['backend_ref'],
                                    'vlm.boolean@1', 'vlm.observation@1') for node in plan)
        rules = tuple(CleaningRule(node['rule_id'], node['node_id'], node['predicate_ref'],
                                   'eq', True) for node in plan)
        recipe = CleaningRecipe(task.media_type, review_digest,
                                registry.registry_digest, steps, rules,
                                registry.frozen_instructions)
        registry.require_recipe(recipe)
        require_confirmed_rules(task, recipe)
        return recipe

    bindings = {(item.predicate_ref, item.media_type): item for item in registry.bindings}
    steps_by_backend = {}
    rules = []
    for row in hard:
        binding = bindings.get((row.get("predicate_ref"), task.media_type))
        if binding is None:
            raise ServiceError("DETECTOR_NOT_RELEASED",
                               "Confirmed rule has no released detector", 409)
        step = steps_by_backend.get(binding.backend_ref)
        if step is None:
            step = DetectionStep(f"step_{len(steps_by_backend) + 1}", binding.backend_ref,
                                 binding.validator_ref, binding.evidence_ref)
            steps_by_backend[binding.backend_ref] = step
        elif (step.validator_ref, step.evidence_ref) != (
                binding.validator_ref, binding.evidence_ref):
            raise ServiceError("DETECTOR_NOT_RELEASED",
                               "Released detector bindings are inconsistent", 409)
        try:
            rules.append(CleaningRule(row["rule_id"], step.step_id,
                                      row["predicate_ref"], row["operator"],
                                      row["value"], row["hardness"]))
        except (TypeError, ValueError, KeyError) as exc:
            raise ServiceError("RECIPE_RULE_MISMATCH",
                               "Confirmed cleaning rule is invalid", 409) from exc
    recipe = CleaningRecipe(task.media_type, review_digest, registry.registry_digest,
                            tuple(steps_by_backend.values()), tuple(rules),
                            getattr(registry, 'frozen_instructions', ()))
    registry.require_recipe(recipe)
    require_confirmed_rules(task, recipe)
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
    if task.media_type == "video":
        _require_video_recipe(task, recipe)
        return
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
        if recipe.instructions:
            plan_for_instructions(spec, recipe.instructions)
        if 'cleaning_conditions' in spec or delivery_targets(spec.get("grouped_expansion")):
            plan = confirmed_instruction_plan(spec)
            if (len(hard) != 1 or hard[0].get('predicate_ref') != 'image.semantic-match@1'
                    or hard[0].get('operator') != 'eq' or hard[0].get('value') is not True
                    or len(plan) != len(recipe.rules) or len(plan) != len(recipe.steps)):
                mismatch()
            for node, step, rule in zip(plan, recipe.steps, recipe.rules):
                if ((step.step_id, step.backend_ref, step.validator_ref, step.evidence_ref)
                        != (node['node_id'], node['backend_ref'], 'vlm.boolean@1', 'vlm.observation@1')
                        or (rule.rule_id, rule.step_id, rule.predicate_ref, rule.operator,
                            rule.value, rule.hardness, rule.group_id)
                        != (node['rule_id'], node['node_id'], node['predicate_ref'],
                            'eq', True, 'hard', node.get('group_id'))):
                    mismatch()
            return
        if any(rule.group_id is not None for rule in recipe.rules):
            mismatch()
        if (len(hard) == 1 and hard[0].get('predicate_ref') == 'image.semantic-match@1'
                and len(recipe.rules) > 1):
            plan = instruction_plan(task.raw_request)
            if (len(plan) != len(recipe.rules) or len(plan) != len(recipe.steps)
                    or hard[0].get('operator') != 'eq' or hard[0].get('value') is not True):
                mismatch()
            for node, step, rule in zip(plan, recipe.steps, recipe.rules):
                if ((step.step_id, step.backend_ref, step.validator_ref, step.evidence_ref)
                        != (node['node_id'], node['backend_ref'], 'vlm.boolean@1', 'vlm.observation@1')
                        or (rule.rule_id, rule.step_id, rule.predicate_ref,
                            rule.operator, rule.value, rule.hardness)
                        != (node['rule_id'], node['node_id'], node['predicate_ref'],
                            'eq', True, 'hard')):
                    mismatch()
        else:
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


def _compile_video_recipe(task, registry):
    """One released aggregate predicate binds the entire confirmed video condition set.

    The task-scoped detector must evaluate clauses with three-state all/any
    semantics; no image detector or similarity score is a video verifier.
    """
    from .whole_video import VIDEO_PREDICATE, video_conditions_digest, video_detector_config_digest
    review = video_conditions_digest(task)
    matches = [b for b in registry.bindings if b.predicate_ref == VIDEO_PREDICATE and b.media_type == 'video']
    if len(matches) != 1:
        raise ServiceError('DETECTOR_NOT_RELEASED', 'No verified whole-video condition evaluator', 409)
    binding = matches[0]
    detector = registry.detectors.get(binding.backend_ref)
    if (getattr(detector, 'source_review_digest', None) != review
            or getattr(detector, 'release_config_digest', None) != video_detector_config_digest(task)):
        raise ServiceError('DETECTOR_NOT_RELEASED', 'Video evaluator is not bound to confirmed conditions', 409)
    recipe = CleaningRecipe('video', review, registry.registry_digest,
        (DetectionStep('video-content', binding.backend_ref, binding.validator_ref, binding.evidence_ref),),
        (CleaningRule('video-content', 'video-content', VIDEO_PREDICATE, 'eq', True),))
    registry.require_recipe(recipe)
    _require_video_recipe(task, recipe)
    return recipe


def _require_video_recipe(task, recipe):
    from .whole_video import VIDEO_PREDICATE, video_conditions_digest
    if (recipe.media_type != 'video' or recipe.source_review_digest != video_conditions_digest(task)
            or len(recipe.steps) != 1 or recipe.steps[0].step_id != 'video-content'
            or recipe.rules != (CleaningRule('video-content', 'video-content', VIDEO_PREDICATE, 'eq', True),)
            or recipe.instructions):
        raise ServiceError('RECIPE_RULE_MISMATCH', 'Video recipe differs from confirmed conditions', 409)
