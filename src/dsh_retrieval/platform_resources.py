"""Project-scoped adapters over verified platform data callbacks.

Callbacks are deployment-owned protocol adapters, never model/browser supplied.
No cross-project permissions are inferred from the user's dataset union.
"""
from contextlib import contextmanager
from dataclasses import dataclass, replace, asdict
import base64
import hashlib
import hmac
import json
import os
import re
from .query_spec import digest
from tempfile import SpooledTemporaryFile

from .errors import ServiceError
from .integration_models import MediaResolution


@dataclass(frozen=True)
class DatasetVersionRef:
    library_id: str
    version_id: str


class PlatformResources:
    def __init__(self, *, tasks, projects, project_datasets, user_datasets,
                 libraries, resolve_hits, search, open_media, media_version, cursor_secret=None):
        self.cursor_secret = cursor_secret or os.urandom(32)
        if not isinstance(self.cursor_secret, bytes) or len(self.cursor_secret) < 32:
            raise ValueError("persistent cursor secret must contain at least 32 bytes")
        self.tasks, self.projects = tasks, projects
        self.project_datasets, self.user_datasets = project_datasets, user_datasets
        self.libraries, self.resolver, self.searcher = libraries, resolve_hits, search
        self.media_reader, self.media_version = open_media, media_version

    def _scope(self, context, action='read'):
        task = self.tasks.get(context.actor, context.task_id)
        return task, self.allowed_versions(context.actor, task.project_id, task.dimension_id, action)

    def allowed_versions(self, actor, project_id, dimension_id='default', action='read'):
        self.projects.require_access(actor, project_id, dimension_id, action)
        project = set(self.project_datasets(actor, project_id))
        personal = set(self.user_datasets(actor))
        if any(not isinstance(item, DatasetVersionRef) or not item.library_id or not item.version_id
               for item in project | personal):
            raise ServiceError('PLATFORM_MAPPING_INVALID', 'dataset mapping is invalid', 503)
        return frozenset(project & personal)

    def require_access(self, context, resource_ref, action):
        if action not in ('discover', 'search', 'read', 'preview', 'export', 'cleaning_input',
                          'candidate_preview', 'delivery_export', 'materialization', 'retrieval', 'refill'):
            raise ServiceError('ACCESS_DENIED', 'unsupported resource operation', 403)
        task, allowed = self._scope(context)
        if resource_ref.startswith('library:'):
            if resource_ref[8:] in {ref.library_id for ref in allowed}:
                return
        else:
            ref = self.media_version(context.actor, task.project_id, resource_ref)
            if isinstance(ref, DatasetVersionRef) and ref in allowed:
                return
        raise ServiceError('ACCESS_DENIED', 'resource outside current project scope', 403)

    def find_libraries(self, context, topic, media_type):
        task, allowed = self._scope(context)
        result = tuple(lib for lib in self.libraries(context.actor, task.project_id)
                       if lib.library_id in {ref.library_id for ref in allowed} and lib.media_type == media_type)
        if self._scope(context)[1] != allowed:
            raise ServiceError('AUTHORIZATION_CHANGED', 'dataset authorization changed', 409)
        return result

    def _cursor(self, value, fingerprint, *, encode=False):
        if value is None:
            return None
        try:
            if encode:
                payload = json.dumps({'scope': fingerprint, 'cursor': value}, separators=(',', ':')).encode()
                signature = hmac.digest(self.cursor_secret, payload, 'sha256')
                return base64.urlsafe_b64encode(signature + payload).decode()
            if not isinstance(value, str) or len(value) > 32768:
                raise ValueError()
            raw = base64.b64decode(value, altchars=b'-_', validate=True)
            if not hmac.compare_digest(raw[:32], hmac.digest(self.cursor_secret, raw[32:], 'sha256')):
                raise ValueError()
            payload = json.loads(raw[32:])
            if payload['scope'] != fingerprint:
                raise ValueError()
            return payload['cursor']
        except (ValueError, TypeError, KeyError):
            raise ServiceError('INVALID_CURSOR', 'cursor is invalid or belongs to another scope', 409) from None

    def search(self, context, request):
        task, allowed = self._scope(context)
        registered = {binding for lib in self.libraries(context.actor, task.project_id) for binding in lib.bindings}
        if request.binding not in registered:
            raise ServiceError('ACCESS_DENIED', 'search index binding is not registered', 403)
        versions = frozenset(ref.version_id for ref in allowed if ref.library_id == request.binding.library_id)
        if not versions:
            raise ServiceError('ACCESS_DENIED', 'search library is not authorized', 403)
        fingerprint = digest({'tenant': context.actor.tenant_id, 'user': context.actor.user_id,
            'task': context.task_id, 'project': task.project_id,
            'revision': getattr(task, 'confirmed_revision', None), 'generation': getattr(task, 'run_generation', None),
            'task_version': getattr(task, 'task_version', None), 'request': asdict(replace(request, cursor=None)),
            'versions': sorted(versions)})
        backend_request = replace(request, cursor=self._cursor(request.cursor, fingerprint))
        # Backend callback must constrain recall to these versions, before querying.
        page = self.searcher(context, backend_request, task.project_id, versions)
        if any(hit.binding != request.binding for hit in page.hits):
            raise ServiceError('PLATFORM_MAPPING_INVALID', 'search binding mismatch', 503)
        resolutions = self.resolve_hits(context, page.hits)
        if any(resolution.status != 'resolved' for resolution in resolutions):
            raise ServiceError('ACCESS_DENIED', 'search returned unauthorized or unmapped media', 403)
        if self._scope(context)[1] != allowed:
            raise ServiceError('AUTHORIZATION_CHANGED', 'search authorization changed', 409)
        return replace(page, next_cursor=self._cursor(page.next_cursor, fingerprint, encode=True))

    def resolve_hits(self, context, hits):
        task, allowed = self._scope(context)
        result = tuple(self.resolver(context, hits, task.project_id))
        if len(result) != len(hits):
            raise ServiceError('PLATFORM_MAPPING_INVALID', 'incomplete hit mapping', 503)
        for hit, item in zip(hits, result):
            if not isinstance(item, MediaResolution) or item.hit != hit:
                raise ServiceError('PLATFORM_MAPPING_INVALID', 'hit mapping mismatch', 503)
            if item.status != 'resolved' and item.media is not None:
                raise ServiceError('PLATFORM_MAPPING_INVALID', 'unresolved hit exposed media', 503)
            if item.status == 'resolved':
                if item.media is None:
                    raise ServiceError('PLATFORM_MAPPING_INVALID', 'media mapping missing', 503)
                if not isinstance(item.media.content_digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', item.media.content_digest):
                    raise ServiceError('PLATFORM_MAPPING_INVALID', 'immutable media digest required', 503)
                ref = self.media_version(context.actor, task.project_id, item.media.authorized_media_ref)
                if ref not in allowed:
                    raise ServiceError('ACCESS_DENIED', 'mapped media is outside project scope', 403)
        if self._scope(context)[1] != allowed:
            raise ServiceError('AUTHORIZATION_CHANGED', 'mapping authorization changed', 409)
        return result

    @contextmanager
    def open_read(self, context, media_ref, max_bytes):
        if type(max_bytes) is not int or not 0 < max_bytes <= 100_000_000:
            raise ServiceError('MEDIA_LIMIT_EXCEEDED', 'invalid media read limit', 422)
        self.require_access(context, media_ref, context.purpose)
        task, allowed = self._scope(context)
        before = self.media_version(context.actor, task.project_id, media_ref)
        # The callback must use the platform preview/download service, not arbitrary OBS paths.
        with SpooledTemporaryFile(max_size=min(max_bytes, 1_000_000)) as target:
            with self.media_reader(context, task.project_id, media_ref, max_bytes) as source:
                size = 0
                while True:
                    chunk = source.read(min(65536, max_bytes - size + 1))
                    if not isinstance(chunk, bytes):
                        raise ServiceError('MEDIA_READ_INVALID', 'invalid media stream', 503)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ServiceError('MEDIA_LIMIT_EXCEEDED', 'media exceeds read limit', 413)
                    target.write(chunk)
            self.require_access(context, media_ref, context.purpose)
            if self.media_version(context.actor, task.project_id, media_ref) != before or self._scope(context)[1] != allowed:
                raise ServiceError('AUTHORIZATION_CHANGED', 'media authorization changed', 409)
            target.seek(0)
            yield target
            self.require_access(context, media_ref, context.purpose)
