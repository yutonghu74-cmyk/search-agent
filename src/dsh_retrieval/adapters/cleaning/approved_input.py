"""Bind a generic worker to the current approval and its authorized manifest."""

from ...errors import ServiceError
from contextlib import contextmanager
from ...video_media import open_verified_video, VideoResourcePolicy
from ...integration_models import AccessContext
from ...multi_rule_contracts import CleaningBinding, CleaningCandidateRef
from .runner import MAX_CANDIDATE_BYTES


class ApprovedManifestAccess:
    """Task-scoped guard and reader; never resolves caller-supplied paths or URLs.

    Construct this with the business repository, a server-authenticated actor,
    and the media gateway. The same instance supplies both worker ports.
    """

    def __init__(self, approvals, actor, task_id, gateway):
        if not callable(getattr(approvals, "get_current", None)):
            raise ValueError("current approval repository is required")
        if not callable(getattr(approvals, "get_manifest", None)):
            raise ValueError("candidate manifest repository is required")
        if not callable(getattr(gateway, "open_read", None)):
            raise ValueError("authorized media gateway is required")
        self.approvals, self.actor, self.task_id, self.gateway = approvals, actor, task_id, gateway
        self.video_policy = getattr(gateway, "video_policy", VideoResourcePolicy())

    def _approval(self, binding):
        if type(binding) is not CleaningBinding or binding.task_id != self.task_id:
            raise ServiceError("NOT_FOUND", "Cleaning operation outside bound task", 404)
        approval = self.approvals.get_current(self.actor, self.task_id, binding.operation_id)
        if approval.job.binding != binding:
            raise ServiceError("STALE_GENERATION", "Cleaning binding is not current", 409)
        return approval

    def require_current(self, binding):
        self._approval(binding)

    def read(self, candidate, binding):
        approval = self._approval(binding)
        if type(candidate) is not CleaningCandidateRef or candidate not in approval.proposal.candidates:
            raise ServiceError("INPUT_SNAPSHOT_CHANGED", "Candidate outside approved input", 409)
        manifest = self.approvals.get_manifest(self.actor, self.task_id,
                                               approval.proposal.manifest_ref)
        item = next((item for item in manifest.proposal.items
                     if item.candidate_id == candidate.candidate_id), None)
        if (item is None or item.media.asset_version != candidate.asset_version
                or item.media.content_digest != candidate.content_digest):
            raise ServiceError("INPUT_SNAPSHOT_CHANGED", "Candidate manifest changed", 409)
        context = AccessContext(self.actor, self.task_id, "cleaning_input", "current")
        content = bytearray()
        with self.gateway.open_read(context, item.media.authorized_media_ref,
                                    max_bytes=MAX_CANDIDATE_BYTES) as stream:
            while True:
                block = stream.read(min(65536, MAX_CANDIDATE_BYTES - len(content) + 1))
                if type(block) is not bytes:
                    raise ServiceError("MEDIA_READ_INVALID", "Binary media required", 502)
                if not block:
                    break
                content.extend(block)
                if len(content) > MAX_CANDIDATE_BYTES:
                    raise ServiceError("MEDIA_LIMIT_EXCEEDED", "Approved media exceeds worker limit", 413)
        self._approval(binding)
        return bytes(content)


    @contextmanager
    def open_file(self, candidate, binding):
        approval = self._approval(binding)
        if approval.job.recipe.media_type != 'video' or candidate not in approval.proposal.candidates:
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Video outside approved input', 409)
        manifest = self.approvals.get_manifest(self.actor, self.task_id, approval.proposal.manifest_ref)
        item = next((i for i in manifest.proposal.items if i.candidate_id == candidate.candidate_id), None)
        if item is None or item.media.content_digest != candidate.content_digest or item.media.asset_version != candidate.asset_version:
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Video manifest changed', 409)
        context = AccessContext(self.actor, self.task_id, 'cleaning_input', 'current')
        with open_verified_video(self.gateway, context, item.media.authorized_media_ref,
                candidate.content_digest, policy=self.video_policy, guard=lambda: self._approval(binding)) as file:
            yield file
