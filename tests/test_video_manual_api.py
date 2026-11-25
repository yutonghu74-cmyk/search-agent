"""Manual fixed-input video workflow through the existing public HTTP API."""
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest

from dsh_retrieval.api import create_handler
import test_video_runtime_flow as flow


class ManualVideoAPITest(unittest.TestCase):
    def setUp(self):
        f=self.f=flow.VideoRuntimeFlowTest();f.setUp();self.addCleanup(f.doCleanups)
        self.tid=f.create(auto=False,fixed=True)
        f.components['search_dispatch'].dispatch_task(self.tid)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),create_handler(f.service,**f.components))
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def call(self,suffix,body=None,key='manual',user='user-a'):
        request=Request(f'http://127.0.0.1:{self.server.server_port}/v1/retrieval/tasks/{self.tid}'+suffix,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'X-Tenant-Id':'tenant-a','X-User-Id':user,'Content-Type':'application/json','Idempotency-Key':key})
        try:response=urlopen(request,timeout=10)
        except HTTPError as error:response=error
        with response:
            payload=response.read()
            value=json.loads(payload) if response.headers.get_content_type()=='application/json' else payload
            self.assertLess(response.status,400,value)
            return value

    def test_manual_plan_confirmation_validation_and_partial_original_delivery(self):
        self.assertEqual(self.call('/first-search/progress')['status'],'unapproved')
        routes=self.call('/first-search')
        choices=[{'branch_id':r['branch_id'],'binding_id':r['options'][0]['binding_id'],'batch_size':1} for r in routes['routes']]
        plan=self.call('/first-search/prepare',{'routing_digest':routes['routing_digest'],'choices':choices})
        self.assertEqual(self.call('/first-search/progress')['status'],'unapproved')
        body={'proposal_digest':plan['proposal_digest'],'expected_task_version':plan['expected_task_version'],'action':'confirm'}
        self.call('/first-search/confirm',body,key='search');self.call('/first-search/confirm',body,key='search')
        self.call('/first-search/tick',{'max_requests':1})
        self.assertTrue(self.call('/first-search/progress')['initial_window_complete'])
        manifest=self.call('/first-search/manifest')
        manifest=self.call('/first-search/manifest',{'proposal_digest':manifest['proposal_digest'],
            'expected_task_version':manifest['expected_task_version'],'action':'confirm'},key='manifest')
        cleaning=self.call('/cleaning/prepare',{'manifest_ref':manifest['manifest_ref']})
        self.call('/cleaning/confirm',{'proposal_digest':cleaning['proposal_digest'],
            'expected_task_version':cleaning['expected_task_version'],'action':'confirm'},key='clean')
        self.f.components['cleaning_dispatch']._drain()
        result=self.call('/results?view=accepted&limit=12')
        self.assertEqual((result['counts']['accepted'],result['counts']['gap']),(1,1))
        task=self.call('');self.assertEqual(task['run_status'],'AWAITING_INPUT')
        package=self.call('/delivery/prepare',{'expected_task_version':task['task_version'],
            'result_set_digest':result['result_set_digest']})
        self.assertEqual(package['manifest']['completion'],'partial')
        self.call('/delivery/publish',{'package_id':package['package_id'],'manifest_digest':package['manifest_digest'],
            'expected_task_version':package['current_version'],'action':'confirm'},key='publish')
        self.assertEqual(self.call('')['run_status'],'PARTIAL')
        self.assertTrue(self.call('/first-search/progress')['initial_window_complete'])
        cid=package['manifest']['members'][0]['candidate_id']
        original=self.call(f'/delivery/original/{cid}?source_digest={package["source_digest"]}')
        self.assertEqual(original,b'pass-one')
