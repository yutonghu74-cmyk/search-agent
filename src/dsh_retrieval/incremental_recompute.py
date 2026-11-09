"""Pure planning/reporting helpers for feedback-driven incremental recomputation.

The helpers deliberately separate reusable immutable evidence from a verdict that
belongs to one revision.  The current cleaning result contract stores bounded
rule verdict evidence, not arbitrary detector facts, so reuse is conservative:
only an identical released detector, instruction and rule may reuse a verdict.
Changed rules are revalidated from the task's historical *input* candidates,
including candidates that failed the previous revision.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Iterable

from .multi_rule_contracts import (CleaningApproval, CleaningCandidateVerdict,
                                   CleaningJobResult, CleaningRecipe)


def _rule_key(recipe: CleaningRecipe, rule_id: str):
    rules = {rule.rule_id: rule for rule in recipe.rules}
    rule = rules[rule_id]
    steps = {step.step_id: step for step in recipe.steps}
    step = steps[rule.step_id]
    step_index = recipe.steps.index(step)
    instruction = recipe.instructions[step_index] if recipe.instructions else None
    return (asdict(step), instruction, asdict(rule))


def recipe_changes(previous: CleaningRecipe, current: CleaningRecipe) -> tuple[dict, ...]:
    """Return stable, UI-safe changes between two immutable recipes."""
    old = {rule.rule_id: _rule_key(previous, rule.rule_id) for rule in previous.rules}
    new = {rule.rule_id: _rule_key(current, rule.rule_id) for rule in current.rules}
    rows = []
    for rule_id in sorted(old.keys() | new.keys()):
        if rule_id not in old:
            kind = "added"
        elif rule_id not in new:
            kind = "removed"
        elif old[rule_id] != new[rule_id]:
            kind = "changed"
        else:
            continue
        rows.append({"rule_id": rule_id, "change": kind})
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class CandidateRecompute:
    candidate_id: str
    asset_version: str
    previous_verdict: str
    action: str
    reusable_rule_ids: tuple[str, ...]
    revalidate_rule_ids: tuple[str, ...]

    def view(self):
        return asdict(self)


def plan_incremental_recompute(previous: CleaningApproval,
                               result: CleaningJobResult,
                               current_recipe: CleaningRecipe,
                               current_detector_release_digest: str) -> dict:
    """Plan reuse/revalidation over every historical input candidate.

    A previous fail is intentionally treated exactly like a previous pass here.
    This is what makes a relaxed rule capable of restoring an earlier rejection.
    """
    if type(previous) is not CleaningApproval or type(result) is not CleaningJobResult:
        raise TypeError("approved cleaning operation and result required")
    if result.binding != previous.job.binding:
        raise ValueError("previous cleaning result binding mismatch")
    if not isinstance(current_detector_release_digest, str):
        raise ValueError("current detector release digest required")
    old_recipe = previous.proposal.recipe
    release_compatible = (previous.proposal.detector_release_digest
                          == current_detector_release_digest)
    old_keys = {rule.rule_id: _rule_key(old_recipe, rule.rule_id)
                for rule in old_recipe.rules}
    new_keys = {rule.rule_id: _rule_key(current_recipe, rule.rule_id)
                for rule in current_recipe.rules}
    verdicts = {item.candidate_id: item for item in result.candidates}
    rows = []
    for candidate in previous.job.candidates:
        prior = verdicts[candidate.candidate_id]
        reusable = tuple(rule.rule_id for rule in current_recipe.rules
                         if release_compatible and old_keys.get(rule.rule_id)
                         == new_keys[rule.rule_id]
                         and any(saved.rule_id == rule.rule_id
                                 for saved in prior.rules))
        revalidate = tuple(rule.rule_id for rule in current_recipe.rules
                           if rule.rule_id not in reusable)
        rows.append(CandidateRecompute(
            candidate.candidate_id, candidate.asset_version, prior.verdict,
            "reuse_verdicts" if not revalidate else "revalidate",
            reusable, revalidate))
    return {
        "schema_version": "incremental-recompute-plan@1",
        "source_revision": previous.proposal.revision,
        "source_generation": previous.proposal.generation,
        "source_operation_id": previous.operation_id,
        "candidate_source": "historical_input_manifest",
        "candidate_count": len(rows),
        "reused_candidate_count": sum(row.action == "reuse_verdicts" for row in rows),
        "revalidated_candidate_count": sum(row.action == "revalidate" for row in rows),
        "restorable_rejected_count": sum(row.previous_verdict == "fail" for row in rows),
        "rule_changes": recipe_changes(old_recipe, current_recipe),
        "candidates": tuple(row.view() for row in rows),
        "note": "旧判定不直接成为新版本结果；仅兼容证据可复用，所有候选仍绑定新 revision 发布。",
    }


def _latest_by_candidate(results: Iterable[CleaningJobResult]):
    items = {}
    for result in results:
        for candidate in result.candidates:
            items[candidate.candidate_id] = candidate
    return items


def _counts(items: dict[str, CleaningCandidateVerdict]):
    values = Counter(item.verdict for item in items.values())
    return {key: values[key] for key in ("pass", "fail", "unknown")}


def build_recompute_report(previous_results: Iterable[CleaningJobResult],
                           current_results: Iterable[CleaningJobResult], *,
                           previous_accepted: int | None = None,
                           current_accepted: int | None = None) -> dict:
    """Describe which candidates were rechecked and how authoritative counts moved."""
    old = _latest_by_candidate(previous_results)
    new = _latest_by_candidate(current_results)
    common = sorted(old.keys() & new.keys())
    restored = [candidate_id for candidate_id in common
                if old[candidate_id].verdict != "pass"
                and new[candidate_id].verdict == "pass"]
    removed = [candidate_id for candidate_id in common
               if old[candidate_id].verdict == "pass"
               and new[candidate_id].verdict != "pass"]
    before, after = _counts(old), _counts(new)
    return {
        "schema_version": "incremental-recompute-report@1",
        "revalidated_candidate_ids": common,
        "restored_candidate_ids": restored,
        "newly_rejected_candidate_ids": removed,
        "counts": {
            "before": {**before, "accepted": previous_accepted},
            "after": {**after, "accepted": current_accepted},
            "delta": {key: after[key] - before[key]
                      for key in ("pass", "fail", "unknown")} | {
                          "accepted": (None if previous_accepted is None
                                       or current_accepted is None
                                       else current_accepted - previous_accepted)},
        },
    }
