"""Bounded, authorized ephemeral files from committed retrieval pages."""

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from ...cleaning_models import Candidate
from ...errors import ServiceError
from ...integration_models import AccessContext, MediaRecord, MediaResolution
from ...materialization_models import MaterializedCandidate, MaterializedPage
from ...ports import AuthorizationService, MediaAccessGateway, MediaRepository, RetrievalPlanRepository
from ...query_spec import digest


def reject(code):
    # Never include internal refs, source paths or gateway error text.
    raise ServiceError(code, "Candidate preview unavailable", 409)


class LocalCandidateMaterializer:
    def __init__(self, *, ledger: RetrievalPlanRepository, media: MediaRepository,
                 gateway: MediaAccessGateway, authorization: AuthorizationService,
                 workspace_root: Path, max_item_bytes=10_000_000,
                 max_total_bytes=100_000_000, media_type="image"):
        for value, ceiling in ((max_item_bytes, 10_000_000), (max_total_bytes, 100_000_000)):
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError("Invalid materialization byte limit")
        if media_type not in ("image", "video"):
            raise ValueError("Unsupported materialization target")
        self.media_type = media_type
        self.ledger, self.media, self.gateway, self.authorization = ledger, media, gateway, authorization
        self.root = Path(workspace_root).resolve()
        self.item_limit, self.total_limit = max_item_bytes, max_total_bytes

    @contextmanager
    def materialize(self, actor, task_id, operation_id):
        operation = self.ledger.get_operation(actor, task_id, operation_id)
        if operation.page is None:
            reject("RETRIEVAL_NOT_COMMITTED")
        hits = operation.page.hits
        if len(hits) > 100:
            reject("MEDIA_LIMIT_EXCEEDED")
        context = AccessContext(actor, task_id, "materialization", "current")
        if not hits:
            if self.ledger.get_operation(actor, task_id, operation_id) != operation:
                reject("INPUT_SNAPSHOT_CHANGED")
            yield MaterializedPage(operation, ())
            return
        resolutions = self.media.resolve_hits(context, hits)
        if not isinstance(resolutions, tuple) or len(resolutions) != len(hits):
            reject("MEDIA_MAPPING_INCOMPLETE")
        groups = {}
        for hit, resolution in zip(hits, resolutions):
            if (not isinstance(resolution, MediaResolution) or resolution.hit != hit
                    or resolution.status != "resolved" or not isinstance(resolution.media, MediaRecord)):
                reject("MEDIA_MAPPING_INCOMPLETE")
            media = resolution.media
            identity = (media.source_namespace, media.source_asset_id, media.media_unit_id, media.asset_version)
            if any(not isinstance(s, str) or not s.strip() for s in (*identity, media.authorized_media_ref)):
                reject("MEDIA_MAPPING_INCOMPLETE")
            if not isinstance(media.content_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", media.content_digest):
                reject("MEDIA_VERSION_UNVERIFIED")
            if identity in groups:
                if groups[identity][0] != media:
                    reject("MEDIA_MAPPING_CONFLICT")
                groups[identity][1].append(hit)
            else:
                groups[identity] = (media, [hit])
        if self.media_type == "video":
            from ...whole_video import require_whole_video_records
            try:
                require_whole_video_records(tuple(media for media, _ in groups.values()))
            except ValueError:
                reject("MEDIA_MAPPING_CONFLICT")
        self.root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="materialized-", dir=self.root) as directory:
            items, total = [], 0
            for index, (identity, (media, sources)) in enumerate(groups.items()):
                self.authorization.require_access(context, media.authorized_media_ref, "read")
                target = Path(directory) / f"{index:04d}.bin"
                checksum, size = sha256(), 0
                with self.gateway.open_read(context, media.authorized_media_ref, max_bytes=self.item_limit) as stream:
                    with target.open("xb") as output:
                        while True:
                            block = stream.read(min(65536, self.item_limit - size + 1, self.total_limit - total + 1))
                            if not isinstance(block, bytes):
                                reject("MEDIA_READ_INVALID")
                            if not block:
                                break
                            size += len(block)
                            total += len(block)
                            if size > self.item_limit or total > self.total_limit:
                                reject("MEDIA_LIMIT_EXCEEDED")
                            checksum.update(block)
                            output.write(block)
                actual = "sha256:" + checksum.hexdigest()
                if actual != media.content_digest:
                    reject("INPUT_SNAPSHOT_CHANGED")
                self.authorization.require_access(context, media.authorized_media_ref, "read")
                candidate = Candidate("candidate_" + digest(identity).removeprefix("sha256:"),
                                      media.asset_version, str(target), actual)
                items.append(MaterializedCandidate(candidate, media, tuple(sources)))
            if self.media.resolve_hits(context, hits) != resolutions:
                reject("INPUT_SNAPSHOT_CHANGED")
            for item in items:
                self.authorization.require_access(context, item.media.authorized_media_ref, "read")
            if self.ledger.get_operation(actor, task_id, operation_id) != operation:
                reject("INPUT_SNAPSHOT_CHANGED")
            yield MaterializedPage(operation, tuple(items))
