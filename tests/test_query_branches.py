from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dsh_retrieval.api import create_handler
from dsh_retrieval.expansion_models import preview_edited_groups
from dsh_retrieval.models import Actor
from dsh_retrieval.service import InMemoryTaskRepository
from dsh_retrieval.query_branches import compile_query_branches
from draft_helpers import BODY, make_service, confirmation


class QueryBranchesTest(unittest.TestCase):
    def query(self):
        query = deepcopy(BODY['query_spec'])
        query['raw_request'] = '狗 不要人物 SKU-Ab12'
        query['grouped_expansion'] = preview_edited_groups('动物', [
            {'primary_label': '狗', 'auxiliary_terms': ['柯基', '狗 特写', '狗  特写']},
            {'primary_label': '狗 特写', 'auxiliary_terms': []}])['plan']
        return query

    def test_original_dedup_and_all_origins(self):
        query = self.query()
        result = compile_query_branches(query)
        self.assertEqual(result['original_request'], query['raw_request'])
        self.assertEqual(result['branches'][0]['query_text'], query['raw_request'])
        self.assertEqual([b['query_text'] for b in result['branches']],
                         ['狗 不要人物 SKU-Ab12', '狗', '狗 柯基', '狗 特写'])
        self.assertEqual(len(result['branches'][-1]['origins']), 3)
        self.assertEqual(result['branches'][0]['origins'][0]['source'], 'original_request')
        self.assertEqual(result['branches'][2]['origins'][0]['source'], 'confirmed_expansion')
        self.assertEqual(result['branches'][2]['origins'][0]['source_term'], '柯基')
        self.assertFalse(result['executable'])
        self.assertTrue(all(not b['intended_libraries'] for b in result['branches']))
        self.assertEqual(result, compile_query_branches(query))

    def test_constraints_bound_and_detached(self):
        query = self.query()
        query['rules'] = [{'rule_id': 'exclude-person', 'value': False}]
        query['source_constraints'] = [{'source': 'approved-only'}]
        result = compile_query_branches(query)
        self.assertEqual(result['branches'][0]['constraint_refs'],
                         ['rule:exclude-person', 'source_constraints:0'])
        query['rules'][0]['value'] = True
        changed = compile_query_branches(query)
        self.assertNotEqual(result['preview_digest'], changed['preview_digest'])
        self.assertNotEqual(result['branches'][0]['query_fingerprint'],
                            changed['branches'][0]['query_fingerprint'])
        self.assertFalse(result['constraints']['rules'][0]['value'])

    def test_invalid_or_tampered_input_rejected(self):
        for query in (None, {}, {'raw_request': ' '}, {'raw_request': '狗', 'rules': [{}]}):
            with self.assertRaises(ValueError):
                compile_query_branches(query)
        query = self.query()
        query['grouped_expansion']['groups'][0]['auxiliary_terms'][0] = '篡改'
        with self.assertRaises(ValueError):
            compile_query_branches(query)

    def test_ungrouped_request_remains_original(self):
        result = compile_query_branches({'raw_request': '不要人物，不要改写 AB-12'})
        self.assertEqual(len(result['branches']), 1)
        self.assertEqual(result['branches'][0]['query_role'], 'original')

    def test_http_authority_binding_and_no_mutation(self):
        service = make_service(InMemoryTaskRepository())
        actor = Actor('tenant', 'owner')
        body = deepcopy(BODY)
        body['query_spec'] = self.query()
        task, _ = service.create_task(actor, 'confirm-branches',
                                     confirmation(service, actor, body=body))
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(service))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}/v1/retrieval/tasks/{task["task_id"]}/query-branches'

        def get(headers):
            try:
                with urlopen(Request(url, headers=headers)) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.load(error)
        try:
            before = service.get_task(actor, task['task_id'])
            self.assertEqual(get({})[0], 401)
            self.assertEqual(get({'X-Tenant-Id': 'tenant', 'X-User-Id': 'other'})[0], 404)
            status, result = get({'X-Tenant-Id': 'tenant', 'X-User-Id': 'owner'})
            self.assertEqual(status, 200)
            self.assertEqual(result['revision'], 1)
            self.assertEqual(result['task_version'], before['task_version'])
            self.assertFalse(result['preview']['executable'])
            self.assertEqual(service.get_task(actor, task['task_id']), before)
            original_get = service.get_task
            calls = 0

            def changing_get(*args):
                nonlocal calls
                calls += 1
                value = original_get(*args)
                # current_read_binding supplies the first snapshot; this read is its fence.
                if calls == 1:
                    value['task_version'] += 1
                return value

            service.get_task = changing_get
            self.assertEqual(get({'X-Tenant-Id': 'tenant', 'X-User-Id': 'owner'})[0], 409)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
