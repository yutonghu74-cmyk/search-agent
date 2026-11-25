"""Platform identity/project boundary. No signing keys or credential persistence.

The deployment supplies verified identity and per-user delegated credentials;
PAT support and UserInfo shape must be confirmed before enabling this adapter.
"""
import json
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from ..errors import ServiceError
from ..models import Actor


def single_header(headers, name, *, required=False):
    """Reject ambiguous identity headers before consulting any credential source."""
    values = (headers.get_all(name, []) if hasattr(headers, 'get_all')
              else [value for key, value in headers.items() if key.lower() == name.lower()])
    if len(values) > 1 or (required and not values):
        raise ServiceError('UNAUTHENTICATED', 'unambiguous authentication headers required', 401)
    return values[0] if values else ''


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PlatformClient:
    def __init__(self, base_url, *, opener=None, timeout=5):
        if not isinstance(base_url, str) or any(c.isspace() for c in base_url) or "\\" in base_url or "%" in base_url:
            raise ValueError("invalid platform API URL")
        parts = urlsplit(base_url)
        try:
            parts.port
        except ValueError:
            raise ValueError("invalid platform API port") from None
        if (parts.scheme != 'https' or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment or ".." in parts.path):
            raise ValueError('platform API requires an administrator-configured HTTPS URL')
        self.base_url = base_url.rstrip('/')
        self.opener = opener or build_opener(_NoRedirect()).open
        self.timeout = timeout

    def get(self, path, token, *, project_id=None):
        return self._request(path, token, project_id=project_id)

    def post(self, path, token, body, *, project_id=None, credential_exchange=False, max_body_bytes=1_000_000):
        # Only reviewed integration code can select an unauthenticated exchange;
        # refresh credentials belong in the JSON body, never query parameters.
        try:
            data = json.dumps(body, allow_nan=False).encode()
            if (not isinstance(body, dict) or type(max_body_bytes) is not int
                    or not 0 < max_body_bytes <= 32 * 1024 * 1024 or len(data) > max_body_bytes):
                raise ValueError()
        except (ValueError, TypeError):
            raise ServiceError('INVALID_PLATFORM_REQUEST', 'invalid platform request body', 422) from None
        return self._request(path, token, project_id=project_id, data=data,
                             credential_exchange=credential_exchange)

    def _request(self, path, token, *, project_id=None, data=None, credential_exchange=False):
        if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or '#' in path or '..' in path:
            raise ServiceError('INVALID_PLATFORM_REQUEST', 'invalid platform API path', 422)
        if not credential_exchange and (not isinstance(token, str) or not token or any(c.isspace() for c in token)):
            raise ServiceError('UNAUTHENTICATED', 'personal platform credential required', 401)
        headers = {'Accept': 'application/json'}
        if token:
            if not isinstance(token, str) or any(c.isspace() for c in token):
                raise ServiceError('UNAUTHENTICATED', 'invalid platform credential', 401)
            headers['Authorization'] = 'Bearer ' + token
        if data is not None:
            headers['Content-Type'] = 'application/json'
        if project_id is not None:
            headers['x-app-id'] = canonical_project_id(project_id)
        request = Request(self.base_url + path, headers=headers, data=data)
        try:
            with self.opener(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ServiceError('PLATFORM_UNAVAILABLE', 'platform request failed', 503)
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError('response too large')
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError('invalid envelope')
            code = body.get('code')
            if type(code) is not int:
                raise ValueError('invalid code')
            if code in (401, 403):
                raise ServiceError('UNAUTHENTICATED' if code == 401 else 'PROJECT_ACCESS_DENIED',
                                   'platform access denied', code)
            if code != 200 or 'data' not in body:
                raise ValueError('invalid envelope')
            return body['data']
        except HTTPError as exc:
            status = exc.code if exc.code in (401, 403) else 503
            exc.close()
            raise ServiceError('UNAUTHENTICATED' if status == 401 else
                               'PROJECT_ACCESS_DENIED' if status == 403 else 'PLATFORM_UNAVAILABLE',
                               'platform request rejected', status) from None
        except (URLError, OSError, ValueError, TypeError):
            raise ServiceError('PLATFORM_UNAVAILABLE', 'platform response unavailable or invalid', 503) from None


def canonical_project_id(value):
    # One stable representation prevents header/path aliases and key collisions.
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or value.startswith('0') or len(value) > 20:
        raise ServiceError('INVALID_PROJECT', 'positive canonical platform project ID required', 422)
    return value


class PlatformIdentity:
    def __init__(self, client, tenant_id, *, extract_user_id):
        if not tenant_id:
            raise ValueError('server-side platform namespace required')
        self.client, self.tenant_id = client, tenant_id
        # UserInfo wire schema is not yet confirmed; never guess a field or trust JWT payload locally.
        self.extract_user_id = extract_user_id

    def authenticate(self, headers):
        scheme, _, token = single_header(headers, 'Authorization', required=True).partition(' ')
        if scheme.lower() != 'bearer' or not token:
            raise ServiceError('UNAUTHENTICATED', 'personal bearer credential required', 401)
        data = self.client.get('/user/info', token)
        try:
            uid = self.extract_user_id(data)
            if isinstance(uid, bool) or not isinstance(uid, (int, str)) or not str(uid).strip():
                raise ValueError('invalid identity')
        except (KeyError, TypeError, ValueError):
            raise ServiceError('UNAUTHENTICATED', 'platform identity not verified', 401) from None
        return Actor(self.tenant_id, str(uid))


class PlatformProjectAccess:
    def __init__(self, client, tenant_id, *, credential_for_actor, allowed_actions, extract_user_id, credential_invalidated=None):
        self.client, self.tenant_id = client, tenant_id
        # Trusted resolver must return the actor's own credential/delegation, never a service fallback.
        self.credential_for_actor = credential_for_actor
        self.credential_invalidated = credential_invalidated
        self.allowed_actions = frozenset(allowed_actions)
        self.identity = PlatformIdentity(client, tenant_id, extract_user_id=extract_user_id)
        self._request = ContextVar('platform_project_request', default=None)

    @contextmanager
    def request_scope(self, headers):
        values = headers.get_all('x-app-id', []) if hasattr(headers, 'get_all') else [headers.get('x-app-id', '')]
        if len(values) != 1:
            raise ServiceError('INVALID_PROJECT', 'exactly one x-app-id header required', 422)
        project = canonical_project_id(values[0])
        authorization = headers.get('Authorization', '')
        actor = self.identity.authenticate(headers)
        marker = self._request.set((actor, project, authorization.partition(' ')[2]))
        try:
            self.require_access(actor, project, 'default', 'read')
            yield actor
        finally:
            self._request.reset(marker)


    def require_access(self, actor, project_id, dimension_id, action):
        project_id = canonical_project_id(project_id)
        if (actor.tenant_id != self.tenant_id or dimension_id != 'default'
                or action not in self.allowed_actions):
            raise ServiceError('PROJECT_ACCESS_DENIED', 'project operation is not authorized', 403)
        request = self._request.get()
        if request is not None:
            request_actor, request_project, token = request
            if actor != request_actor or project_id != request_project:
                raise ServiceError('PROJECT_ACCESS_DENIED', 'request project does not match resource', 403)
        else:
            token = self.credential_for_actor(actor)
        try:
            if not token or self.identity.authenticate({"Authorization": "Bearer " + token}) != actor:
                raise ServiceError("UNAUTHENTICATED", "delegated identity does not match actor", 401)
        except ServiceError as error:
            if error.status == 401 and self.credential_invalidated is not None:
                self.credential_invalidated(actor)
            raise
        # Revalidate membership on every operation, without caching authorization.
        data = self.client.get('/app/info/' + project_id, token, project_id=project_id)
        if not isinstance(data, dict) or str(data.get('id')) != project_id or data.get('status') != 'normal':
            raise ServiceError('PROJECT_ACCESS_DENIED', 'project is unavailable or mismatched', 403)
