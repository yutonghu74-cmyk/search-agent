"""Immutable input manifests, not cleaning approval or accepted result sets."""
from collections import Counter
from dataclasses import asdict, dataclass
import re

from .errors import ServiceError
from .integration_models import MediaRecord, SearchHit
from .materialization_models import MaterializedPage
from .query_spec import digest
from .retrieval_contracts import RetrievalOperation, RetrievalScope


def candidate_identity(media: MediaRecord) -> str:
    """Stable opaque identity for one versioned business media unit."""
    if type(media) is not MediaRecord:
        raise TypeError("MediaRecord required")
    return "candidate_" + digest((media.source_namespace, media.source_asset_id,
                                  media.media_unit_id, media.asset_version)).removeprefix("sha256:")


@dataclass(frozen=True, slots=True)
class ManifestItem:
    media: MediaRecord
    hits: tuple[SearchHit, ...]
    # Quota groups frozen with the manifest: which group checks this item owes.
    group_ids: tuple[str, ...] = ()

    @property
    def candidate_id(self):
        return candidate_identity(self.media)


@dataclass(frozen=True, slots=True)
class CandidateManifestProposal:
    scope: RetrievalScope
    operation_id: str
    plan_digest: str
    items: tuple[ManifestItem, ...]

    @property
    def proposal_digest(self):
        value = asdict(self)
        # Published manifests are addressed by this digest, so ungrouped items
        # must keep hashing exactly as they did before the field existed.
        for item in value["items"]:
            if not item["group_ids"]:
                del item["group_ids"]
        return digest(value)

    def view(self):
        return {"kind": "candidate_manifest_proposal", "operation_id": self.operation_id,
                "proposal_digest": self.proposal_digest, "expected_task_version": self.scope.task_version,
                "candidate_count": len(self.items), "accepted_count": None,
                "candidates": [{"candidate_id": i.candidate_id, "asset_version": i.media.asset_version,
                                "content_digest": i.media.content_digest,
                                **({"group_ids": list(i.group_ids)} if i.group_ids else {})}
                               for i in self.items]}

    @classmethod
    def from_materialized(cls, page: MaterializedPage):
        proposal = cls(page.operation.plan.scope, page.operation.operation_id,
                       page.operation.plan.proposal_digest,
                       tuple(ManifestItem(item.media, item.hits) for item in page.items))
        validate_manifest(proposal, page.operation)
        for source, item in zip(page.items, proposal.items):
            if (source.candidate.candidate_id != item.candidate_id
                    or source.candidate.asset_version != item.media.asset_version
                    or source.candidate.content_digest != item.media.content_digest):
                raise ServiceError("MANIFEST_INVALID", "Materialized identity mismatch", 422)
        return proposal


@dataclass(frozen=True, slots=True)
class CandidateManifest:
    manifest_ref: str
    proposal: CandidateManifestProposal

    def view(self):
        return {**self.proposal.view(), "kind": "candidate_input_manifest",
                "manifest_ref": self.manifest_ref}


def merge_manifest_items(proposals):
    """Aggregate existing page manifests without minting a synthetic source page."""
    items = {}
    for proposal in proposals:
        for item in proposal.items:
            previous = items.get(item.candidate_id)
            if previous is not None:
                if previous.media != item.media or previous.group_ids != item.group_ids:
                    raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Candidate identity changed across pages', 409)
                item = ManifestItem(item.media, previous.hits + item.hits, item.group_ids)
            items[item.candidate_id] = item
    return tuple(items.values())


def validate_manifest(proposal: CandidateManifestProposal, operation: RetrievalOperation):
    """Validate trusted backend input against the authoritative committed page.

    Does not authorize, download, or prove byte availability. Publication service
    rematerializes before confirmation; storage rechecks scope and access itself.
    """
    def reject():
        raise ServiceError("MANIFEST_INVALID", "Invalid candidate input manifest", 422)
    if (operation.page is None or proposal.scope != operation.plan.scope
            or proposal.operation_id != operation.operation_id
            or proposal.plan_digest != operation.plan.proposal_digest
            or not isinstance(proposal.items, tuple) or len(proposal.items) > 100):
        reject()
    identities, hits = set(), []
    for item in proposal.items:
        if not isinstance(item, ManifestItem) or not isinstance(item.media, MediaRecord):
            reject()
        m = item.media
        if any(not isinstance(s, str) or not s.strip() for s in
               (m.source_namespace, m.source_asset_id, m.media_unit_id, m.asset_version, m.authorized_media_ref)):
            reject()
        if not isinstance(m.content_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", m.content_digest):
            reject()
        if item.candidate_id in identities or not isinstance(item.hits, tuple) or not item.hits:
            reject()
        identities.add(item.candidate_id)
        if any(not isinstance(hit, SearchHit) for hit in item.hits):
            reject()
        hits.extend(item.hits)
    if len(hits) > 100 or Counter(digest(asdict(h)) for h in hits) != Counter(digest(asdict(h)) for h in operation.page.hits):
        reject()
