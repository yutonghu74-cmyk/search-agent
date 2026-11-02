"""Local production preview or redirect to the unified development service.

Run with PYTHONPATH=src python3 tools/production_preview.py.
Business actions navigate to the upstream application, never through this proxy.
Add --unified to redirect old preview links to the full service on port 8094.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dsh_retrieval.production_board_ui import BOARD_CSS, BOARD_HTML, BOARD_JS

UPSTREAM = 'http://127.0.0.1:8085'


def chat_entry():
    try:
        with urlopen(UPSTREAM + '/chat', timeout=5):
            return UPSTREAM + '/chat'
    except HTTPError as error:
        if error.code != 404:
            raise
        with urlopen(UPSTREAM + '/', timeout=5) as response:
            if b'/chat.js' not in response.read():
                raise RuntimeError('Upstream has no recognized Chat entry')
        return UPSTREAM + '/'


class Preview(BaseHTTPRequestHandler):
    chat_url = ''

    def do_GET(self):
        html = (BOARD_HTML.replace('PRODUCTION WORKSPACE', '只读界面预览 · 数据来自 8085')
                .replace('href="/chat', 'href="' + self.chat_url)
                .replace('href="/catalog', 'href="' + UPSTREAM + '/catalog'))
        js = BOARD_JS.replace("'/chat?task='", repr(self.chat_url + '?task='))
        assets = {'/': ('text/html', html), '/production': ('text/html', html),
                  '/production.css': ('text/css', BOARD_CSS),
                  '/production.js': ('text/javascript', js)}
        path = self.path.split('?')[0]
        if path == '/chat':
            self.send_response(302)
            query = '?' + self.path.split('?', 1)[1] if '?' in self.path else ''
            self.send_header('Location', self.chat_url + query)
            self.end_headers()
            return
        if path in assets:
            kind, value = assets[path]
            content, code = value.encode(), 200
        elif path.startswith('/v1/'):
            try:
                try:
                    response = urlopen(Request(UPSTREAM + self.path, headers={
                        'X-Tenant-Id': 'tenant-a', 'X-User-Id': 'user-a'}), timeout=15)
                except HTTPError as error:
                    response = error
                with response:
                    code = response.status
                    kind = response.headers.get('Content-Type', 'application/json')
                    content = response.read()
            except (URLError, TimeoutError):
                code, kind = 502, 'application/json'
                content = b'{"code":"PREVIEW_UPSTREAM_UNAVAILABLE","message":"Local service unavailable"}'
        else:
            code, kind, content = 404, 'text/plain', b'Not found'
        self.send_response(code)
        self.send_header('Content-Type', kind + '; charset=utf-8' if kind.startswith('text/') else kind)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(content)


class UnifiedEntry(BaseHTTPRequestHandler):
    """Retire the preview without proxying identity or business writes."""

    def do_GET(self):
        self.send_response(302)
        self.send_header('Location', 'http://127.0.0.1:8094' + self.path)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

    do_HEAD = do_GET


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unified', action='store_true',
                        help='redirect former preview links to the full service on 8094')
    args = parser.parse_args()
    if args.unified:
        # Verify both entry points before retiring the preview.
        for path in ('/', '/chat', '/v1/build'):
            with urlopen('http://127.0.0.1:8094' + path, timeout=10):
                pass
        handler = UnifiedEntry
        print('Unified entry: http://127.0.0.1:8094/ (8098 redirects)', flush=True)
    else:
        Preview.chat_url = chat_entry()
        handler = Preview
        print('Read-only preview: http://127.0.0.1:8098/', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8098), handler).serve_forever()
