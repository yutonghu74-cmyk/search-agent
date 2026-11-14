from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import subprocess
import unittest

from dsh_retrieval.api import create_handler
from dsh_retrieval.adapters.platform_access import PlatformIdentity, PlatformProjectAccess
from dsh_retrieval.platform_browser import PlatformBrowser
from dsh_retrieval.platform_credentials import DelegatedCredentials
from dsh_retrieval.platform_ui import PLATFORM_JS, platform_html
from dsh_retrieval.service import TaskService, InMemoryTaskRepository
from test_platform_credentials import Identity


class BrowserTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.member = True
        self.identity = Identity()
        self.identity.tenant_id = 'platform'
        self.vault = DelegatedCredentials(Path(self.temp.name)/'vault', Path(self.temp.name)/'key',
            identity=self.identity, exchange=lambda refresh: ('token-a', 100))
        self.addCleanup(self.vault.close)
        class Client:
            def get(_, path, token, **kwargs):
                if path == '/user/info':
                    return {'id': 'a'}
                if not self.member:
                    from dsh_retrieval.errors import ServiceError
                    raise ServiceError('PROJECT_ACCESS_DENIED', 'removed', 403)
                return {'id': 101, 'status': 'normal'}
        self.projects = PlatformProjectAccess(Client(), 'platform', credential_for_actor=self.vault.credential_for_actor,
            allowed_actions={'read', 'create'}, extract_user_id=lambda data: data['id'])
        browser = PlatformBrowser(self.vault, self.projects,
            list_projects=lambda actor, token: [{'id': 101, 'name': 'Project A', 'secret': 'must-not-return'}],
            frame_origins=('https://platform.invalid',))
        self.handler = create_handler(TaskService(InMemoryTaskRepository(), self.projects),
            identity_provider=self.identity, identity_mode='platform', platform_browser=browser)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def call(self, path, method='GET', headers=None, body=None):
        request = Request(self.base+path, method=method, headers=headers or {},
            data=json.dumps(body).encode() if body is not None else None)
        try:
            result = urlopen(request, timeout=5)
        except HTTPError as error:
            result = error
        with result:
            return result.status, result.headers, result.read()

    def login(self):
        status, headers, body = self.call('/v1/platform/session', 'POST',
            {'Authorization': 'Bearer token-a'}, {'refresh_key': 'refresh'})
        self.assertEqual(status, 200)
        self.assertIn('Secure; HttpOnly; SameSite=Lax', headers['Set-Cookie'])
        self.cookie = headers['Set-Cookie'].split(';')[0]
        status, _, raw = self.call('/v1/platform/context', headers={'Cookie': self.cookie})
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertNotIn(b'must-not-return', raw)
        self.assertNotIn(b'token-a', raw)
        self.csrf = data['csrf']

    def test_cookie_flow_csrf_logout_and_project_revocation(self):
        self.login()
        headers = {'Cookie': self.cookie, 'x-app-id': '101'}
        self.assertEqual(self.call('/v1/retrieval/projects/101/catalog-config', headers=headers)[0], 503)
        self.assertEqual(self.call('/v1/retrieval/projects/101/drafts', 'POST', headers, {})[0], 403)
        self.assertEqual(self.call('/v1/platform/logout', 'POST', {'Cookie': self.cookie})[0], 403)
        self.member = False
        self.assertEqual(self.call('/v1/platform/check', headers=headers)[0], 403)
        self.assertEqual(self.call('/v1/platform/logout', 'POST',
            {'Cookie': self.cookie, 'X-CSRF-Token': self.csrf})[0], 200)
        self.assertEqual(self.call('/v1/platform/context', headers={'Cookie': self.cookie})[0], 401)

    def test_cross_project_and_account_switch_rejected(self):
        self.login()
        headers = {'Cookie': self.cookie, 'x-app-id': '101', 'X-Agent-User': 'different-user'}
        self.assertEqual(self.call('/v1/platform/check', headers=headers)[0], 401)
        headers['X-Agent-User'] = 'a'
        self.assertEqual(self.call('/v1/retrieval/projects/102/drafts', headers=headers)[0], 403)

    def test_pages_bootstrap_before_business_scripts_and_allow_exact_frame_origin(self):
        status, headers, body = self.call('/chat')
        self.assertEqual(status, 200)
        self.assertIn(b'application/x-platform-script', body)
        self.assertNotIn(b'<script src="/chat.js"', body)
        self.assertIn(b'<script src="/platform.js"', body)
        self.assertIn('frame-ancestors https://platform.invalid', headers['Content-Security-Policy'])
        status, _, body = self.call('/platform.js')
        self.assertEqual(status, 200)
        self.assertIn(b'X-CSRF-Token', body)

    def test_unauthenticated_and_bad_session_payload(self):
        self.assertEqual(self.call('/v1/platform/context')[0], 401)
        self.assertEqual(self.call('/v1/platform/session', 'POST', body={})[0], 401)
        self.assertEqual(self.call('/v1/platform/session', 'POST',
            {'Authorization': 'Bearer token-a'}, {'user_id': 'admin'})[0], 422)


class PlatformUITest(unittest.TestCase):
    def test_javascript_request_scope_storage_and_switch(self):
        script = r'''
const vm=require('vm');
const assert=require('assert');
class Storage{constructor(){this.map=new Map()}getItem(k){return this.map.get(k)||null}setItem(k,v){this.map.set(k,String(v))}removeItem(k){this.map.delete(k)}}
class Element{constructor(tag){this.tag=tag;this.children=[];this.dataset={}}append(...items){this.children.push(...items)}prepend(...items){this.children.unshift(...items)}setAttribute(){}replaceChildren(...items){this.children=items}}
const body=new Element('body'), requests=[];const imagePanel={classList:{add(value){imagePanel.hiddenClass=value}}},imageInput={disabled:false,closest(){return imagePanel}};
const location={href:'https://agent.invalid/chat',origin:'https://agent.invalid',pathname:'/chat',search:'?project=101',replace(url){this.replaced=url}};
const fetch=async(input,options={})=>{requests.push({input,options});if(input==='/v1/platform/context')return new Response(JSON.stringify({user_id:'a',tenant_id:'platform',csrf:'csrf',projects:[{id:101,name:'A'},{id:102,name:'B'}]}));return new Response('{}')};
const sessionStorage=new Storage(),localStorage=new Storage();
const context={URL,URLSearchParams,Request,Response,Headers,AbortController,Storage,sessionStorage,localStorage,location,fetch,console,document:{body,createElement:tag=>new Element(tag),addEventListener(){},querySelectorAll(){return[]},getElementById(id){return id==='query-image-file'?imageInput:null}},setInterval(){return 1},clearInterval(){},addEventListener(){}};
context.window=context;vm.createContext(context);
(async()=>{await vm.runInContext(SOURCE,context);
 assert.equal(imageInput.disabled,true);assert.equal(imagePanel.hiddenClass,'hidden');
 await context.fetch('/v1/retrieval/projects/demo/tasks',{headers:{'X-User-Id':'admin','Authorization':'Bearer secret'}});
 const last=requests.at(-1);assert.equal(last.input.pathname,'/v1/retrieval/projects/101/tasks');assert.equal(last.options.headers.get('x-app-id'),'101');assert.equal(last.options.headers.get('Authorization'),null);assert.equal(last.options.headers.get('X-User-Id'),null);assert.equal(last.options.headers.get('X-CSRF-Token'),'csrf');
 context.localStorage.setItem('dsh-task-id','task-a');assert.equal(localStorage.map.get('platform:platform:a:101:dsh-task-id'),'task-a');
 await assert.rejects(()=>context.fetch('https://evil.invalid/'));
 const select=body.children[0].children[0].children[0];select.value='102';select.onchange();assert.equal(location.replaced,'/chat?project=102');await assert.rejects(()=>context.fetch('/v1/retrieval/tasks/old'));
})().catch(e=>{console.error(e);process.exitCode=1});
'''.replace('SOURCE', json.dumps(PLATFORM_JS))
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
