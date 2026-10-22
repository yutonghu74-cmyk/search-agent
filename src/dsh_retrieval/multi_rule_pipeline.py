"""Synchronous, restartable orchestration for one already-approved local job.

The storage repository remains authoritative for authorization, task fences,
and publication. This module never accepts a caller-built job or media path.
"""

from .adapters.cleaning import ApprovedManifestAccess, GenericCleaningExecutor
from .errors import ServiceError


def run_approved_multi_rule(repo, actor, task_id, operation_id, *, gateway, detectors):
    """Return (current preview view, replayed) for an approved operation.

    The return is a preview, not a delivery. Only a trusted service/worker
    should call this entry point with server-injected actor, gateway and
    released detector registry.
    """

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
        result = repo.get_result(actor, task_id, operation_id)
    except ServiceError as exc:
        if exc.code != "NOT_FOUND":
            raise
        access = ApprovedManifestAccess(repo, actor, task_id, gateway)
        result = GenericCleaningExecutor(guard=access, reader=access,
                                         detectors=detectors).run(approval.job)
        repo.publish_result(actor, task_id, operation_id, result)

    # This is a separate recoverable transaction. On retry, a published
    # cleaning result is reused; detectors and media are not read again.
    return repo.publish_preview(actor, task_id, operation_id)
