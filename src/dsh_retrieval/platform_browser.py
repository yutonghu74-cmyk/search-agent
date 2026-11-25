"""Same-origin browser session bridge. Personal tokens never enter page scripts."""
from email.message import Message
from http.cookies import SimpleCookie, CookieError

from .errors import ServiceError
from .adapters.platform_access import canonical_project_id, single_header


class PlatformBrowser:
    cookie_name = '__Host-agent-session'

    def __init__(self, credentials, projects, *, list_projects, frame_origins=(), reference_search=False):
        self.credentials, self.projects, self.list_projects = credentials, projects, list_projects
        self.reference_search = reference_search
        from urllib.parse import urlsplit
        for origin in frame_origins:
            value = urlsplit(origin)
            if value.scheme != 'https' or not value.hostname or value.path or value.query or value.fragment or value.username:
                raise ValueError('frame origins must be exact HTTPS origins')
        self.frame_origins = tuple(frame_origins)

    def cookie(self, headers):
        value = SimpleCookie()
        try:
            raw = single_header(headers, 'Cookie', required=True)
            # Cookie parsers silently choose the last duplicate; never accept that ambiguity.
            if sum(part.strip().split('=', 1)[0] == self.cookie_name for part in raw.split(';')) != 1:
                raise ServiceError('UNAUTHENTICATED', 'unambiguous application session required', 401)
            value.load(raw)
            return value[self.cookie_name].value
        except (KeyError, ValueError, CookieError):
            raise ServiceError('UNAUTHENTICATED', 'platform application session required', 401) from None

    def request_headers(self, headers, method):
        # Reject ambiguous page identity before any authentication or CSRF processing.
        for name in ('Authorization', 'Cookie', 'X-Agent-User', 'X-CSRF-Token'):
            single_header(headers, name)
        # Explicit Bearer is for trusted service clients; browser session needs CSRF for mutations.
        if single_header(headers, 'Authorization'):
            return headers
        actor, token, _ = self.credentials.session(self.cookie(headers),
            csrf=single_header(headers, 'X-CSRF-Token'), mutation=method not in ('GET', 'HEAD'))
        expected_user = single_header(headers, 'X-Agent-User')
        if expected_user and expected_user != actor.user_id:
            raise ServiceError('UNAUTHENTICATED', 'application user changed; reload required', 401)
        result = Message()
        for key, value in headers.items():
            result[key] = value
        result['Authorization'] = 'Bearer ' + token
        return result

    def handle(self, handler, path):
        from urllib.parse import urlsplit
        if urlsplit(handler.path).query:
            raise ServiceError('INVALID_SPEC', 'platform session routes do not accept query parameters', 422)
        authorization = single_header(handler.headers, 'Authorization')
        if path == '/v1/platform/session' and handler.command == 'POST':
            # Platform/gateway delivers verified personal credentials via HTTPS POST.
            # No login secrets in URLs, logs or page scripts.
            if not authorization.startswith('Bearer '):
                raise ServiceError('UNAUTHENTICATED', 'personal bearer credential required', 401)
            body = handler._body()
            if set(body) - {'refresh_key', 'expires_in'}:
                raise ServiceError('INVALID_SPEC', 'invalid session payload', 422)
            actor = self.credentials.register(authorization[7:], body.get('refresh_key'),
                                              ttl=body.get('expires_in', 3600))
            cookie, _ = self.credentials.create_session(actor)
            handler._json(200, {'user_id': actor.user_id}, extra_headers={
                'Set-Cookie': self.cookie_name + '=' + cookie + '; Path=/; Secure; HttpOnly; SameSite=Lax'})
            return
        cookie = self.cookie(handler.headers)
        actor, token, csrf = self.credentials.session(cookie, csrf=single_header(handler.headers, 'X-CSRF-Token'),
                                                      mutation=handler.command != 'GET')
        expected_user = single_header(handler.headers, 'X-Agent-User')
        if expected_user and expected_user != actor.user_id:
            raise ServiceError('UNAUTHENTICATED', 'application user changed; reload required', 401)
        if path == '/v1/platform/context' and handler.command == 'GET':
            items = tuple(self.list_projects(actor, token))
            seen = set()
            for item in items:
                project = canonical_project_id(str(item['id']))
                if project in seen:
                    raise ServiceError('PLATFORM_MAPPING_INVALID', 'duplicate project mapping', 503)
                seen.add(project)
            items = tuple({key: item[key] for key in ('id', 'name', 'alias') if key in item} for item in items)
            handler._json(200, {'tenant_id': actor.tenant_id, 'user_id': actor.user_id,
                                'csrf': csrf, 'projects': items, 'capabilities': {'reference_image_search': self.reference_search}})
        elif path == '/v1/platform/check' and handler.command == 'GET':
            with self.projects.request_scope(self.request_headers(handler.headers, 'GET')):
                handler._json(200, {'authorized': True})
        elif path == '/v1/platform/logout' and handler.command == 'POST':
            self.credentials.logout(cookie)
            handler._json(200, {'logged_out': True}, extra_headers={
                'Set-Cookie': self.cookie_name + '=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0'})
        else:
            raise ServiceError('NOT_FOUND', 'platform route not found', 404)
