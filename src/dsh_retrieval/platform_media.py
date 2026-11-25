"""Bounded reads of platform-issued temporary media URLs, without bearer forwarding."""
from contextlib import contextmanager
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError

from .adapters.platform_access import _NoRedirect
from .errors import ServiceError


class _BoundedStream:
    def __init__(self, source, limit):
        self.source, self.remaining = source, limit

    def read(self, size=-1):
        take = self.remaining + 1 if size is None or size < 0 else min(size, self.remaining + 1)
        chunk = self.source.read(take)
        if not isinstance(chunk, bytes):
            raise ServiceError('MEDIA_READ_INVALID', 'invalid media byte stream', 503)
        if len(chunk) > self.remaining:
            raise ServiceError('MEDIA_LIMIT_EXCEEDED', 'platform media exceeds byte limit', 413)
        self.remaining -= len(chunk)
        return chunk


class PlatformMediaReader:
    def __init__(self, preview_url, *, allowed_origins, opener=None, timeout=10):
        self.preview_url = preview_url
        self.allowed = frozenset(allowed_origins)
        if not self.allowed:
            raise ValueError('explicit platform media origins required')
        for origin in self.allowed:
            p = urlsplit(origin)
            if p.scheme != 'https' or not p.hostname or p.username or p.password or p.path or p.query or p.fragment:
                raise ValueError('media origins must be exact HTTPS origins')
        self.opener = opener or build_opener(_NoRedirect()).open
        self.timeout = timeout

    @contextmanager
    def __call__(self, context, project_id, media_ref, max_bytes):
        if type(max_bytes) is not int or not 0 < max_bytes <= 2 * 1024**3:
            raise ServiceError('MEDIA_LIMIT_EXCEEDED', 'invalid media byte limit', 422)
        # Resolver must call the platform preview endpoint with actor/project authorization.
        url = self.preview_url(context, project_id, media_ref)
        try:
            if not isinstance(url, str) or any(c.isspace() for c in url) or "\\" in url:
                raise ValueError()
            p = urlsplit(url)
            if (p.scheme != 'https' or p.username or p.password or p.fragment
                    or p.scheme + '://' + p.netloc not in self.allowed):
                raise ValueError()
        except (TypeError, ValueError):
            raise ServiceError('MEDIA_ACCESS_DENIED', 'platform media URL rejected', 403) from None
        try:
            # No platform PAT, cookies or refresh credentials are sent to object storage.
            with self.opener(Request(url, headers={'Accept': 'application/octet-stream'}), timeout=self.timeout) as response:
                if response.status != 200:
                    raise ValueError()
                declared = response.headers.get('Content-Length')
                if declared is not None and (not declared.isdecimal() or int(declared) > max_bytes):
                    raise ServiceError('MEDIA_LIMIT_EXCEEDED', 'platform media exceeds byte limit', 413)
                yield _BoundedStream(response, max_bytes)
        except ServiceError:
            raise
        except (OSError, URLError, HTTPError, ValueError):
            raise ServiceError('MEDIA_UNAVAILABLE', 'platform media unavailable', 503) from None
