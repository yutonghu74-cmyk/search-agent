"""Whole original video ZIP delivery over the existing atomic package ledger.

A trusted deployment inspector must verify a complete video container/stream;
file names, extensions and MIME headers alone do not establish that fact.
"""
from .delivery import ImageDeliveryService, MAX_IMAGE_BYTES, digest, canonical
from contextlib import contextmanager
from .video_media import open_verified_video, inspect_video_file, VideoResourcePolicy
from .errors import ServiceError
from .integration_models import AccessContext
from .whole_video import confirmed_video_spec, require_video_candidate_mapping

MAX_VIDEO_BYTES = MAX_IMAGE_BYTES


class WholeVideoDeliveryService(ImageDeliveryService):
    media_type = 'video'
    member_directory = 'videos'

    def __init__(self, repository, gateway, *, inspector=None, file_inspector=None, policy=None):
        if not callable(inspector) and not callable(file_inspector):
            raise ValueError('trusted complete-video inspector required')
        self.inspector = inspector
        self.file_inspector = file_inspector
        self.policy = policy or getattr(gateway, "video_policy", VideoResourcePolicy())
        self.reference_delivery = callable(file_inspector)
        super().__init__(repository, gateway)

    def _require_target(self, task):
        super()._require_target(task)
        confirmed_video_spec(task)

    def _read(self, memory, retrieval, candidates, state, actor, task_id):
        task, result, record = super()._read(memory, retrieval, candidates, state, actor, task_id)
        if record is not None:
            manifest = record['manifest']
            if (manifest.get('schema') not in ('whole-video-delivery@1', 'whole-video-delivery@2')
                    or manifest.get('media_type') != 'video'
                    or manifest.get('count_unit') != 'distinct_video'
                    or manifest.get('video_conditions') != confirmed_video_spec(task)['video_conditions']):
                raise ServiceError('STORAGE_CORRUPT', 'Video manifest differs from confirmed conditions', 500)
        return task, result, record

    def _manifest_schema(self, group_rows):
        if group_rows:
            raise ServiceError('FEATURE_NOT_SUPPORTED', 'Video group delivery is not enabled', 422)
        return 'whole-video-delivery@2' if self.reference_delivery else 'whole-video-delivery@1'

    def _extend_manifest(self, manifest, proposal):
        spec = proposal['query_spec']
        return {**manifest, **({'delivery_mode': 'authorized_originals'} if self.reference_delivery else {}), 'media_type': 'video', 'count_unit': 'distinct_video',
                'video_conditions': spec['video_conditions']}

    def _archive_member(self, member, data):
        return ({**member, 'packaged_content_digest': digest(data)} if self.reference_delivery else member)

    def _metadata(self, media, inspected):
        return {'unit_type': 'whole_video', 'media_unit_id': media.media_unit_id,
                'source_namespace': media.source_namespace, 'source_asset_id': media.source_asset_id,
                'asset_version': media.asset_version, 'content_digest': media.content_digest,
                'container': inspected['container'], 'duration_ms': inspected['duration_ms'],
                **({'byte_count': inspected['byte_count']} if 'byte_count' in inspected else {})}

    def _bindings(self, actor, task_id, operation_id, items):
        pairs = self.repo.get_published_candidates(actor, task_id, operation_id,
            tuple(item.candidate_id for item in items), include_media=True)
        if len(pairs) != len(items):
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Incomplete original-video bindings', 409)
        candidates = tuple(candidate for candidate, media in pairs)
        mapping = {candidate.candidate_id: media for candidate, media in pairs}
        try:
            require_video_candidate_mapping(candidates, mapping)
        except ValueError as exc:
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Invalid original-video bindings', 409) from exc
        return dict(zip((item.candidate_id for item in items), pairs))

    def _revalidate_media(self, actor, task_id, result):
        staged = self.status(actor, task_id)
        if not staged['available']:
            raise ServiceError('REVISION_CONFLICT', 'Video package is no longer current', 409)
        members = {m['candidate_id']: m for m in staged['manifest']['members']}
        if set(members) != {item.candidate_id for item in result['accepted']}:
            raise ServiceError('STORAGE_CORRUPT', 'Video package members differ from result', 500)
        bindings = self._bindings(actor, task_id, result['operation_id'], result['accepted'])
        for item in result['accepted']:
            _, extension, metadata = self._read_member(actor, task_id, result['operation_id'], item,
                                                      bindings[item.candidate_id], reference_delivery=staged['manifest']['schema']=='whole-video-delivery@2')
            member = members[item.candidate_id]
            if member['source'] != metadata or not member['file'].endswith(extension):
                raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Video inspection changed after packaging', 409)

    def _read_member(self, actor, task_id, operation_id, item, binding, *, reference_delivery=None):
        reference_delivery = self.reference_delivery if reference_delivery is None else reference_delivery
        candidate, media = binding
        if (candidate.candidate_id != item.candidate_id or candidate.asset_version != item.asset_version
                or candidate.content_digest != item.content_digest):
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Original video binding changed', 409)
        context = AccessContext(actor, task_id, 'delivery_export', 'current')
        if reference_delivery or callable(self.file_inspector):
            with open_verified_video(self.gateway, context, media.authorized_media_ref,
                    item.content_digest, policy=self.policy) as file:
                inspected = inspect_video_file(file, file_inspector=self.file_inspector, policy=self.policy)
                metadata = self._metadata(media, inspected)
                if not reference_delivery:
                    if file.size > MAX_VIDEO_BYTES:
                        raise ServiceError('MEDIA_LIMIT_EXCEEDED', 'Legacy package video exceeds byte limit', 413)
                    metadata.pop('byte_count', None)
                    return file.path.read_bytes(), '.'+inspected['container'], metadata
                return canonical({'candidate_id': item.candidate_id, 'source': metadata,
                    'download': f'/v1/retrieval/tasks/{task_id}/delivery/original/{item.candidate_id}?source_digest=' + self.repo.get_current_result_set(actor, task_id)['result_set_digest']}), '.json', metadata
        data = bytearray()
        try:
            with self.gateway.open_read(context, media.authorized_media_ref, max_bytes=MAX_VIDEO_BYTES) as stream:
                while True:
                    block = stream.read(min(65536, MAX_VIDEO_BYTES + 1 - len(data)))
                    if type(block) is not bytes:
                        raise ServiceError('MEDIA_READ_INVALID', 'Binary video content required', 502)
                    if not block:
                        break
                    data.extend(block)
                    if len(data) > MAX_VIDEO_BYTES:
                        raise ServiceError('MEDIA_LIMIT_EXCEEDED', 'Original video exceeds delivery limit', 413)
        except OSError as exc:
            raise ServiceError('MEDIA_UNAVAILABLE', 'Original video cannot be read', 503) from exc
        content = bytes(data)
        if not content:
            raise ServiceError('MEDIA_READ_INVALID', 'Empty original video', 502)
        if digest(content) != item.content_digest:
            raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Original video digest changed', 409)
        try:
            inspected = self.inspector(content)
            # Persist only this fixed allowlist; inspector diagnostics and source
            # URLs must not leak into the permanent delivery manifest.
            if (type(inspected) is not dict or inspected.get('media_type') != 'video'
                    or inspected.get('container') not in ('mp4', 'webm')
                    or type(inspected.get('duration_ms')) is not int or inspected['duration_ms'] <= 0):
                raise ValueError('unsupported original video')
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError('VIDEO_INSPECTION_FAILED', 'Original video format verification failed', 422) from exc
        return content, '.' + inspected['container'], {
            'unit_type': 'whole_video', 'media_unit_id': media.media_unit_id,
            'source_namespace': media.source_namespace, 'source_asset_id': media.source_asset_id,
            'asset_version': media.asset_version, 'content_digest': media.content_digest,
            'container': inspected['container'], 'duration_ms': inspected['duration_ms']}


    @contextmanager
    def open_original(self, actor, task_id, candidate_id, *, source_digest=None):
        staged = self.status(actor, task_id)
        if not staged['available'] or staged['status'] != 'published':
            raise ServiceError('PACKAGE_APPROVAL_REQUIRED', 'Publish the video delivery before reading originals', 409)
        if source_digest is not None and staged['source_digest'] != source_digest:
            raise ServiceError('REVISION_CONFLICT', 'Original-video reference belongs to another result', 409)
        package_id = staged['package_id']
        record, result, _ = self._content(actor, task_id, package_id, published=True)
        item = next((i for i in result['accepted'] if i.candidate_id == candidate_id), None)
        if item is None:
            raise ServiceError('NOT_FOUND', 'Original video not in this delivery', 404)
        member = next(m for m in record['manifest']['members'] if m['candidate_id'] == candidate_id)
        _, media = self._bindings(actor, task_id, result['operation_id'], (item,))[candidate_id]
        def guard():
            current = self.status(actor, task_id)
            if not current['available'] or current['package_id'] != package_id or current['status'] != 'published':
                raise ServiceError('REVISION_CONFLICT', 'Video delivery is no longer current', 409)
        context = AccessContext(actor, task_id, 'delivery_export', 'current')
        with open_verified_video(self.gateway, context, media.authorized_media_ref, item.content_digest,
                                 policy=self.policy, guard=guard) as file:
            inspected = inspect_video_file(file, inspector=self.inspector,
                file_inspector=self.file_inspector, policy=self.policy)
            metadata = self._metadata(media, inspected)
            if record['manifest']['schema']=='whole-video-delivery@1': metadata.pop('byte_count', None)
            if member['source'] != metadata:
                raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Video inspection changed', 409)
            guard()
            yield file, inspected, guard


class MediaDeliveryService:
    """Authorized target dispatch; absent video inspection is a visible capability gap."""
    def __init__(self, repository, gateway, *, video_inspector=None, video_file_inspector=None, policy=None):
        self.repo = repository
        self.images = ImageDeliveryService(repository, gateway)
        self.videos = (WholeVideoDeliveryService(repository, gateway, inspector=video_inspector,
                            file_inspector=video_file_inspector, policy=policy)
                       if video_inspector is not None or video_file_inspector is not None else None)

    def _select(self, actor, task_id):
        task = self.repo.tasks.get(actor, task_id)
        self.repo.manifests.retrieval.projects.require_access(actor, task.project_id, task.dimension_id, 'read')
        if task.media_type == 'image':
            return self.images
        if task.media_type == 'video' and self.videos is not None:
            return self.videos
        raise ServiceError('VIDEO_DELIVERY_UNAVAILABLE', 'Whole-video delivery inspector is not configured', 422)

    def status(self, actor, task_id):
        try:
            return self._select(actor, task_id).status(actor, task_id)
        except ServiceError as error:
            if error.code == 'VIDEO_DELIVERY_UNAVAILABLE':
                return {'available': False, 'reason_code': error.code}
            raise

    def prepare(self, actor, task_id, **body):
        return self._select(actor, task_id).prepare(actor, task_id, **body)

    def publish(self, actor, task_id, **body):
        return self._select(actor, task_id).publish(actor, task_id, **body)

    def download(self, actor, task_id, package_id):
        return self._select(actor, task_id).download(actor, task_id, package_id)


    def open_original(self, actor, task_id, candidate_id, *, source_digest=None):
        service = self._select(actor, task_id)
        if not isinstance(service, WholeVideoDeliveryService):
            raise ServiceError('FEATURE_NOT_SUPPORTED', 'Original-video delivery required', 422)
        return service.open_original(actor, task_id, candidate_id, source_digest=source_digest)
