"""Per-attempt model egress: admit durably BEFORE forwarding every HTTP call.

Retrieval/cleaning planners currently use OpenAI-compatible chat completions.
No alternate route or direct-provider fallback. Errors/logs never contain keys.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler


class ModelGateway:
    def __init__(self, upstream, deadline, admit):
        parsed = urlsplit(upstream)
        if (parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('invalid model upstream')
        self.sequence, self.lock = 0, threading.Lock()
        gateway = self
        base = parsed.path.rstrip('/')
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def reject(self, status, code):
                body = json.dumps({'error': {'code': code, 'message': code}}).encode()
                self.send_response(status); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                if self.path != base + '/chat/completions':
                    self.reject(404, 'MODEL_ROUTE_DENIED'); return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 2000000: raise ValueError()
                    payload = self.rfile.read(length)
                    if len(payload) != length: raise ValueError()
                    if time.time() >= deadline: raise ValueError()
                except ValueError:
                    self.reject(400, 'MODEL_REQUEST_INVALID'); return
                try:
                    with gateway.lock:
                        admit(gateway.sequence + 1)
                        gateway.sequence += 1
                except Exception:
                    self.reject(429, 'MODEL_ADMISSION_DENIED'); return
                # urllib's redirect handler must not resend credentials or bypass a second admission.
                from urllib.request import HTTPRedirectHandler
                class NoRedirect(HTTPRedirectHandler):
                    def redirect_request(self, *args, **kwargs): return None
                client = build_opener(ProxyHandler({}), NoRedirect())
                headers = {k: v for k, v in self.headers.items()
                           if k.lower() not in ('host', 'connection', 'content-length', 'transfer-encoding')}
                try:
                    request = Request(upstream.rstrip('/') + '/chat/completions', payload, headers, method='POST')
                    with client.open(request, timeout=max(.1, min(90, deadline - time.time()))) as response:
                        self.send_response(response.status)
                        self.send_header('Content-Type', response.headers.get('Content-Type', 'application/octet-stream'))
                        self.send_header('Connection', 'close'); self.end_headers()
                        self.close_connection = True
                        while True:
                            chunk = response.read1(65536)
                            if not chunk: break
                            self.wfile.write(chunk); self.wfile.flush()
                except HTTPError as error:
                    self.reject(error.code, 'MODEL_UPSTREAM_REJECTED')
                except (URLError, OSError, TimeoutError):
                    # The admission remains charged even if network completion is unknown.
                    try: self.reject(502, 'MODEL_UPSTREAM_UNAVAILABLE')
                    except OSError: pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f'http://127.0.0.1:{self.server.server_port}' + base

    def start(self): self.thread.start()
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
