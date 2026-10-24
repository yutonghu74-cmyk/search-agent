from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import test_catalog_api as catalog_fixture
from draft_helpers import BODY, confirmation
from dsh_retrieval.chat_ui import CHAT_JS, CHAT_HTML
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.local_catalog import build_local_catalog
from dsh_retrieval.models import Actor
from dsh_retrieval.routing_preview import LocalRoutingPreview, load_capabilities
from dsh_retrieval.text_catalog_config import CatalogConfigScope


class RoutingPreviewTest(unittest.TestCase):
    stop = catalog_fixture.CatalogApiTest.stop
    write_source = catalog_fixture.CatalogApiTest.write_source
    call = catalog_fixture.CatalogApiTest.call

    def setUp(self):
        catalog_fixture.CatalogApiTest.setUp(self)
        self.actor = Actor('tenant-a', 'user-a')
        self.service = self.handler.service
        body = deepcopy(BODY)
        body['query_spec']['raw_request'] = '宠物'
        result, _ = self.service.create_task(self.actor, 'fixture', confirmation(self.service, self.actor, body=body))
        self.task_id = result['task_id']
        self.route_path = '/v1/retrieval/tasks/' + self.task_id + '/query-routing'
        self.scope = CatalogConfigScope('tenant-a', 'user-a', 'demo')
        proposal = self.catalog.preview(self.scope, '猫狗', 'text')
        self.catalog.save(self.scope, **proposal)
        self.cap_path = Path(self.temp.name) / 'capabilities.json'
        self.caps = {'schema': 'text-query-capabilities@1', 'items': [{
            'binding': self.data['libraries'][0]['bindings'][0], 'media_type': 'image',
            'filter_template_ref': 'internal-filter', 'max_batch_size': 20}]}
        self.write_caps()
        self.provider = LocalRoutingPreview(self.service, self.catalog, self.path, self.cap_path)
        self.handler.routing_preview = self.provider

    def write_caps(self):
        self.cap_path.write_text(json.dumps(self.caps), encoding='utf-8')

    def test_http_preview_no_encoding_or_task_mutation(self):
        before = self.service.repository.get(self.actor, self.task_id)
        count = len(self.transport.calls)
        status, result = self.call(path=self.route_path)
        self.assertEqual(status, 200)
        self.assertFalse(result['execution_enabled'])
        self.assertEqual(result['status'], 'unapproved')
        self.assertEqual(result['routes'][0]['options'][0]['encoder_ref'], 'encoder@1')
        self.assertNotIn('internal-filter', json.dumps(result))
        self.assertEqual(len(self.transport.calls), count)
        self.assertEqual(self.service.repository.get(self.actor, self.task_id), before)

    def test_identity_and_disabled_router(self):
        self.assertEqual(self.call(path=self.route_path, user='other')[0], 404)
        self.assertEqual(self.call(path=self.route_path, user='')[0], 401)
        self.handler.routing_preview = None
        self.assertEqual(self.call(path=self.route_path)[0], 503)

    def test_branch_selection_and_unknown_parameter(self):
        self.assertEqual(self.call(path=self.route_path + '?encoder_ref=arbitrary')[0], 422)
        self.assertEqual(self.call(path=self.route_path + '?branch_id=missing')[0], 422)
        branch = self.call(path=self.route_path)[1]['routes'][0]['branch_id']
        self.assertEqual(self.call(path=self.route_path + '?branch_id=' + branch)[0], 200)

    def test_empty_or_mismatching_capabilities_no_automatic_registration(self):
        self.caps['items'][0]['binding'] = dict(self.caps['items'][0]['binding'], encoder_ref='other-encoder')
        self.write_caps()
        self.assertEqual(self.call(path=self.route_path)[1]['routes'][0]['options'], [])
        self.caps['items'] = []
        self.write_caps()
        self.assertEqual(self.call(path=self.route_path)[1]['routes'][0]['options'], [])

    def test_live_grant_revocation_and_missing_source(self):
        self.data['grants'][0]['can_configure'] = False
        self.write_source()
        self.assertEqual(self.call(path=self.route_path)[0], 403)
        self.path.unlink()
        self.assertEqual(self.call(path=self.route_path)[0], 503)

    def test_no_saved_config_does_not_return_all_libraries(self):
        self.provider.configs = build_local_catalog(self.path, Path(self.temp.name) / 'empty.sqlite', self.catalog.runtime)
        status, result = self.call(path=self.route_path)
        self.assertEqual((status, result['code']), (409, 'CATALOG_SELECTION_REQUIRED'))

    def test_drift_after_discovery_fails_closed(self):
        from dsh_retrieval.query_routing import QueryRoutingService
        original = QueryRoutingService.discover
        def discover(router, *args, **kwargs):
            result = original(router, *args, **kwargs)
            self.caps['items'][0]['max_batch_size'] = 10
            self.write_caps()
            return result
        with patch.object(QueryRoutingService, 'discover', discover):
            status, result = self.call(path=self.route_path)
        self.assertEqual((status, result['code']), (409, 'ROUTING_CHANGED'))
        self.assertNotIn('routes', result)

    def test_capability_loader_validation(self):
        self.assertEqual(asdict(load_capabilities(self.cap_path)[0]), self.caps['items'][0])
        original = deepcopy(self.caps)
        for field, value in [('max_batch_size', True), ('media_type', 'video'), ('filter_template_ref', '')]:
            self.caps = deepcopy(original)
            self.caps['items'][0][field] = value
            self.write_caps()
            with self.assertRaises(ServiceError):
                load_capabilities(self.cap_path)
        self.caps = deepcopy(original)
        self.caps['items'] *= 2
        self.write_caps()
        with self.assertRaises(ServiceError):
            load_capabilities(self.cap_path)

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_ui_shows_preview_and_ignores_changed_task(self):
        self.assertIn('查看选库结果（未执行）', CHAT_HTML)
        check = subprocess.run(['node', '--check'], input=CHAT_JS, text=True, capture_output=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        function = next(line for line in CHAT_JS.splitlines() if line.startswith('async function previewRouting'))
        harness = '''
const assert=require('node:assert/strict');
const state={task:{task_id:'one'}}, status={textContent:''}, cards=[];
const $=()=>status, setBusy=()=>{}, card=(...args)=>cards.push(args);
let switchTask=false;
const api=async()=>{if(switchTask)state.task={task_id:'two'};return {revision:1,generation:1,task_version:1,routing_digest:'digest',catalog_config_digest:'config',note:'未执行',routes:[{branch_id:'b',query_text:'猫',options:[]}]}};
'''
        checks = '''
(async()=>{await previewRouting();assert.equal(cards.length,2);assert.match(JSON.stringify(cards),/无匹配/);cards.length=0;switchTask=true;await previewRouting();assert.equal(cards.length,0)})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node'], input=harness + function + checks, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
