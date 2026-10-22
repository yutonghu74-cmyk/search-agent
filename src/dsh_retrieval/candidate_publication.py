"""Trusted orchestration; no HTTP endpoint or automatic cleaning dispatch."""
from .candidate_contracts import CandidateManifestProposal
from .errors import ServiceError
from .service import require_key, require_version


class CandidatePublicationService:
    def __init__(self, materializer, manifests):
        self.materializer, self.manifests = materializer, manifests

    def preview(self, actor, task_id, operation_id):
        with self.materializer.materialize(actor, task_id, operation_id) as page:
            proposal = CandidateManifestProposal.from_materialized(page)
            self.manifests.put_proposal(actor, proposal)
            return proposal.view()

    def confirm(self, actor, task_id, operation_id, *, proposal_digest,
                expected_task_version, key, action):
        require_key(key)
        require_version(expected_task_version)
        if action != "confirm":
            raise ServiceError("CONFIRMATION_REQUIRED", "Explicit owner confirmation required", 422)
        # Re-download outside the storage transaction. A persisted reference is
        # not evidence that current bytes and access still match the preview.
        with self.materializer.materialize(actor, task_id, operation_id) as page:
            proposal = CandidateManifestProposal.from_materialized(page)
            if proposal.proposal_digest != proposal_digest:
                raise ServiceError("INPUT_SNAPSHOT_CHANGED", "Preview changed; review again", 409)
            manifest, replayed = self.manifests.confirm(
                actor, proposal, key=key, expected_task_version=expected_task_version, action=action)
            return manifest.view(), replayed
