"""Bounded disk-spooled original video reads; no caller supplied paths or URLs."""
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
from time import monotonic

from .errors import ServiceError

HARD_VIDEO_BYTES = 2 * 1024**3
_SLOTS = BoundedSemaphore(2)


@dataclass(frozen=True)
class VideoResourcePolicy:
    max_video_bytes: int = 100_000_000
    max_job_bytes: int = 500_000_000
    max_duration_ms: int = 3_600_000
    read_seconds: int = 120

    def __post_init__(self):
        if (any(type(v) is not int or v <= 0 for v in vars(self).values())
                or self.max_video_bytes > HARD_VIDEO_BYTES
                or self.max_job_bytes > 4 * HARD_VIDEO_BYTES or self.read_seconds > 3600):
            raise ValueError('invalid server-owned video resource limits')


@dataclass(frozen=True)
class VerifiedVideoFile:
    path: Path
    size: int
    content_digest: str


def verify_file(file):
    checksum = sha256(); size = 0
    with file.path.open('rb') as stream:
        while block := stream.read(65536):
            checksum.update(block); size += len(block)
    if size != file.size or 'sha256:' + checksum.hexdigest() != file.content_digest:
        raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Verified video file changed', 409)


@contextmanager
def open_verified_video(gateway, context, media_ref, expected_digest, *, policy=None, guard=None):
    policy = policy or getattr(gateway, 'video_policy', VideoResourcePolicy())
    if not isinstance(policy, VideoResourcePolicy) or not re.fullmatch(r'sha256:[0-9a-f]{64}', expected_digest or ''):
        raise ServiceError('MEDIA_VERSION_UNVERIFIED', 'Immutable video digest required', 409)
    if not _SLOTS.acquire(timeout=2):
        raise ServiceError('VIDEO_RESOURCES_BUSY', 'Video temporary storage is busy', 503, retryable=True)
    try:
        with TemporaryDirectory(prefix='verified-video-') as root:
            path = Path(root) / 'original.bin'; checksum = sha256(); size = 0; started = monotonic()
            if guard: guard()
            with gateway.open_read(context, media_ref, max_bytes=policy.max_video_bytes) as stream, path.open('xb') as target:
                while True:
                    if monotonic() - started > policy.read_seconds:
                        raise ServiceError('VIDEO_READ_TIMEOUT', 'Video read deadline exceeded', 504, retryable=True)
                    block = stream.read(min(65536, policy.max_video_bytes-size+1))
                    if type(block) is not bytes:
                        raise ServiceError('MEDIA_READ_INVALID', 'Binary video stream required', 502)
                    if not block: break
                    size += len(block)
                    if size > policy.max_video_bytes:
                        raise ServiceError('MEDIA_LIMIT_EXCEEDED', 'Video exceeds server byte limit', 413)
                    checksum.update(block); target.write(block)
            if not size or 'sha256:' + checksum.hexdigest() != expected_digest:
                raise ServiceError('INPUT_SNAPSHOT_CHANGED', 'Original video digest changed', 409)
            if guard: guard()
            file = VerifiedVideoFile(path, size, expected_digest)
            yield file
            verify_file(file)
            if guard: guard()
    finally:
        _SLOTS.release()


def inspect_video_file(file, *, inspector=None, file_inspector=None, policy=None):
    policy = policy or VideoResourcePolicy()
    try:
        if callable(file_inspector):
            metadata = file_inspector(file.path)
        elif callable(inspector) and file.size <= 10 * 1024**2:
            metadata = inspector(file.path.read_bytes())
        else:
            raise ServiceError('VIDEO_INSPECTOR_UNAVAILABLE', 'File-based video inspection is required', 422)
        if (type(metadata) is not dict or metadata.get('media_type') != 'video'
                or metadata.get('container') not in ('mp4', 'webm')
                or type(metadata.get('duration_ms')) is not int or metadata['duration_ms'] <= 0):
            raise ValueError('invalid inspection')
        if metadata['duration_ms'] > policy.max_duration_ms:
            raise ServiceError('VIDEO_RESOURCE_LIMIT', 'Video duration exceeds server policy', 413)
        verify_file(file)
        return {**{k: metadata[k] for k in ('media_type', 'container', 'duration_ms')}, 'byte_count': file.size}
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError('VIDEO_INSPECTION_FAILED', 'Video inspection failed', 422) from exc


def byte_range(value, size):
    if value is None: return 0, size-1, False
    match = re.fullmatch(r'bytes=(\d{0,20})-(\d{0,20})', value)
    if not match or not any(match.groups()):
        raise ServiceError('RANGE_NOT_SATISFIABLE', 'Only one video byte range is supported', 416)
    left, right = match.groups()
    start, end = ((int(left), min(int(right), size-1) if right else size-1) if left else
                  (max(0, size-int(right)), size-1))
    if start < 0 or start >= size or end < start:
        raise ServiceError('RANGE_NOT_SATISFIABLE', 'Video range is outside content', 416)
    return start, end, True
