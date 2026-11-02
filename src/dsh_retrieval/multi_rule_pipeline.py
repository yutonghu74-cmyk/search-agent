"""Synchronous, restartable orchestration for one already-approved local job.

The storage repository remains authoritative for authorization, task fences,
and publication. This module never accepts a caller-built job or media path.
"""

from .adapters.cleaning import ApprovedManifestAccess, GenericCleaningExecutor
from .errors import ServiceError
from .multi_rule_contracts import require_released_cleaning_job
from .released_detectors import ReleasedDetectorRegistry


def prepare_released_multi_rule(repo, actor, task_id, manifest_ref, recipe, *, registry):
    """Prepare an approval proposal from one server-owned detector release."""

    if type(registry) is not ReleasedDetectorRegistry:
        raise ValueError("trusted released detector registry required")
    registry.preflight()
    registry.require_recipe(recipe)
    return repo.prepare(actor, task_id, manifest_ref, recipe,
                        detector_release_digest=registry.release_digest)


def run_approved_multi_rule(repo, actor, task_id, operation_id, *, gateway, registry, execution=None):
    """Return (current preview view, replayed) for an approved operation.

    The return is a preview, not a delivery. Only a trusted service/worker
    should call this entry point with server-injected actor, gateway and
    released detector registry.
    """

    if type(registry) is not ReleasedDetectorRegistry:
        raise ValueError("trusted released detector registry required")
    try:
        approval = repo.get_current(actor, task_id, operation_id)
    except ServiceError as exc:
        if exc.code != "STALE_GENERATION":
            raise
        # A completed preview clears the active operation. Recheck current
        # authorization and result-set pointer before treating it as replay.
        view = repo.get_current_result_set(actor, task_id)
        if view["operation_id"] != operation_id:
            raise exc
        return view, True

    try:
        job = require_released_cleaning_job(approval.job)
    except (TypeError, ValueError) as exc:
        raise ServiceError("DETECTOR_NOT_RELEASED",
                           "Approved detector release unavailable", 409) from exc
    if job.binding.detector_release_digest != registry.release_digest:
        raise ServiceError("DETECTOR_NOT_RELEASED",
                           "Approved detector release unavailable", 409)
    detectors = registry.require_recipe(job.recipe)
    try:
        result = repo.get_result(actor, task_id, operation_id)
    except ServiceError as exc:
        if exc.code != "NOT_FOUND":
            raise
        access = ApprovedManifestAccess(repo, actor, task_id, gateway)
        completed = repo.start_cleaning_checkpoint(actor, task_id, operation_id,
            execution or {'backend_refs': [step.backend_ref for step in job.recipe.steps]})
        partial = (repo.get_cleaning_step_checkpoint(actor, task_id, operation_id)
                   if callable(getattr(repo, 'get_cleaning_step_checkpoint', None)) else None)
        save_step = getattr(repo, 'save_cleaning_step_checkpoint', None)
        result = GenericCleaningExecutor(guard=access, reader=access,
                                         detectors=detectors).run(job, completed=completed,
            checkpoint=lambda item: repo.save_cleaning_checkpoint(actor, task_id, operation_id, item),
            partial=partial,
            step_checkpoint=(lambda candidate, step, verdicts: save_step(
                actor, task_id, operation_id, candidate, step, verdicts))
                if callable(save_step) else None)
        repo.publish_result(actor, task_id, operation_id, result)

    # This is a separate recoverable transaction. On retry, a published
    # cleaning result is reused; detectors and media are not read again.
    return repo.publish_preview(actor, task_id, operation_id)
