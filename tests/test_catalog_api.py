from dataclasses import asdict
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.api import create_handler
from dsh_retrieval.catalog_ui import CATALOG_HTML, CATALOG_JS
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.integration_models import Library, IndexBinding
from dsh_retrieval.local_catalog import build_local_catalog, LocalCatalogSource
from dsh_retrieval.model_runtime import ModelRuntime, ModelProfile
from dsh_retrieval.intent_model import ModelConfig
from dsh_retrieval.service import InMemoryTaskRepository, TaskService
from test_text_catalog_config import Transport


class CatalogApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'source.json'
        self.data = {'schema': 'local-catalog@1', 'libraries': [asdict(Library('animals', '动物图片',
            'image', (IndexBinding('idx', 'animals', 'v1', 'encoder@1', 512, 'COSINE'),)))],
            'grants': [{'tenant_id': 'tenant-a', 'owner_id': 'user-a', 'project_id': 'demo',
                        'can_configure': True, 'library_ids': ['animals']}]}
        self.write_source()
        self.transport = Transport({'entries': [{'library_id': 'animals', 'description': '猫狗',
            'aliases': ['宠物'], 'binding_ids': ['idx']}]})
        runtime = ModelRuntime([ModelProfile('text', 'llm', ModelConfig('fixture', 'fixture',
            'https://fixture.invalid/v1', 'test-secret'))], opener=self.transport)
        self.catalog = build_local_catalog(self.path, Path(self.temp.name) / 'config.sqlite', runtime)
        service = TaskService(InMemoryTaskRepository(), LocalProjectAccess([
            ('tenant-a', 'user-a', 'demo', 'default'), ('tenant-a', 'other', 'demo', 'default')]))
        self.handler = create_handler(service, model_runtime=runtime, catalog_config=self.catalog)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def write_source(self):
        self.path.write_text(json.dumps(self.data), encoding='utf-8')

    def call(self, suffix='', body=None, user='user-a', path=None):
        headers = {'X-Tenant-Id': 'tenant-a', 'X-User-Id': user, 'Content-Type': 'application/json'}
        request = Request(self.base + (path or '/v1/retrieval/projects/demo/catalog-config' + suffix),
            data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            response = urlopen(request)
        except HTTPError as error:
            response = error
        with response:
            data = response.read().decode()
            return response.status, json.loads(data) if response.headers.get_content_type() == 'application/json' else data

    def preview(self):
        status, draft = self.call('/preview', {'text': '动物图片库查猫狗', 'llm_id': 'text'})
        self.assertEqual(status, 200)
        return {key: draft[key] for key in ('proposal', 'proposal_digest')}

    def test_http_preview_confirm_reload_and_replay(self):
        self.assertIsNone(self.call()[1]['config'])
        draft = self.preview()
        self.assertIsNone(self.call()[1]['config'])
        for _ in range(2):
            status, result = self.call('/confirm', draft)
            self.assertEqual((status, result['version']), (200, 1))
        self.assertEqual(self.call()[1]['config']['entries'][0]['aliases'], ['宠物'])

    def test_project_access_does_not_grant_configuration(self):
        self.assertEqual(self.call(user='other')[0], 403)
        self.assertEqual(self.call('/preview', {'text': 'cats', 'llm_id': 'text'}, user='other')[0], 403)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.call(user='')[0], 401)

    def test_disabled_and_strict_payload(self):
        self.assertEqual(self.call('/preview', {'text': 'cat', 'llm_id': 'text', 'owner_id': 'other'})[0], 422)
        self.handler.catalog_config = None
        self.assertEqual(self.call()[0], 503)

    def test_live_source_revocation_and_corruption(self):
        draft = self.preview()
        self.data['grants'][0]['can_configure'] = False
        self.write_source()
        self.assertEqual(self.call('/confirm', draft)[0], 403)
        self.path.write_text('{}')
        self.assertEqual(self.call()[0], 503)

    def test_bad_source_rejected(self):
        self.data['grants'][0]['library_ids'] = ['invented']
        self.write_source()
        with self.assertRaises(ServiceError):
            LocalCatalogSource(self.path)

    def test_assets_and_js_syntax(self):
        self.assertIn('已有索引配置', self.call(path='/')[1])
        self.assertEqual(self.call(path='/catalog'), (200, CATALOG_HTML))
        self.assertEqual(self.call(path='/catalog.js'), (200, CATALOG_JS))
        self.assertIn('id="main-content"', CATALOG_HTML)
        self.assertIn('class="skip-link"', CATALOG_HTML)
        self.assertIn('配置理解模型', CATALOG_HTML)
        self.assertIn('仅登记，不执行', CATALOG_HTML)
        self.assertIn('aria-live="polite"', CATALOG_HTML)
        self.assertNotIn('innerHTML', CATALOG_JS)
        self.assertIn("addEventListener('input',reset)", CATALOG_JS)
        self.assertIn('未执行检索', CATALOG_JS)
        if shutil.which('node'):
            result = subprocess.run(['node', '--check'], input=CATALOG_JS, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for UI behavior test')
    def test_ui_invalidates_preview_and_recovers_stale_config(self):
        prelude = '''
const assert=require('node:assert/strict');
const nodes={};
global.document={getElementById(id){return nodes[id]??=( {value:'',disabled:false,textContent:'',events:{},addEventListener(name,fn){this.events[name]=fn},append(option){this.value=option.value}} )},createElement(){return {}}};
let failPreview=false;
global.fetch=async(path,options)=>{let data,status=200;
if(path.endsWith('/models'))data={items:[{id:'text',kind:'llm',model:'fixture'}]};
else if(path.endsWith('/preview')){status=failPreview?502:200;data=failPreview?{code:'MODEL_FAILED',message:'failed'}:{proposal:{entries:[{description:'cats'}]},proposal_digest:'digest'};}
else {status=409;data={code:'CATALOG_CHANGED',message:'changed'}};
return {ok:status===200,status,json:async()=>data};};
'''
        checks = '''
(async()=>{
await new Promise(resolve=>setImmediate(resolve));
assert.equal(nodes['catalog-preview'].disabled,false);
assert.match(nodes['catalog-current'].textContent,/已失效/);
nodes['catalog-text'].value='cats';
await nodes['catalog-preview'].events.click();
assert.equal(nodes['catalog-save'].disabled,false);
nodes['catalog-text'].events.input();
assert.equal(nodes['catalog-save'].disabled,true);
await nodes['catalog-preview'].events.click();
failPreview=true;
await nodes['catalog-preview'].events.click();
assert.equal(nodes['catalog-save'].disabled,true);
assert.equal(nodes['catalog-proposal'].textContent,'');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result = subprocess.run(['node'], input=prelude + CATALOG_JS + checks,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
