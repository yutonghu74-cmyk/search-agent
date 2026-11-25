"""Authorized whole-video GET, ranges and project-scoped native media URLs."""
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import unittest

from dsh_retrieval.api import create_handler
import test_video_runtime_flow as flow


class VideoStreamAPITest(unittest.TestCase):
    def setUp(self):
        self.fixture=flow.VideoRuntimeFlowTest();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;self.tid=f.create();f.start_window(self.tid);f.drain_workflow()
        c=f.components;delivery=c['delivery_service'];result=c['cleaning_reader'].get_current_result_set(f.actor,self.tid)
        package,_=delivery.prepare(f.actor,self.tid,expected_task_version=result['as_of_task_version'],result_set_digest=result['result_set_digest'])
        delivery.publish(f.actor,self.tid,package_id=package['package_id'],manifest_digest=package['manifest_digest'],expected_task_version=package['current_version'],action='confirm')
        self.cid=package['manifest']['members'][0]['candidate_id'];self.digest=package['source_digest']
        handler=create_handler(f.service,first_search=c['first_search'],cleaning_service=c['cleaning_service'],delivery_service=delivery)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)
        self.path=f'/v1/retrieval/tasks/{self.tid}/delivery/original/{self.cid}?source_digest={self.digest}'

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def call(self,path=None,headers=None,method='GET',user='user-a'):
        request=Request(f'http://127.0.0.1:{self.server.server_port}'+(path or self.path),method=method,
            headers={'X-Tenant-Id':'tenant-a','X-User-Id':user,**(headers or {})})
        try:response=urlopen(request,timeout=5)
        except HTTPError as error:response=error
        with response:return response.status,response.headers,response.read()

    def test_get_ranges_and_fail_closed_reads(self):
        status,headers,body=self.call();self.assertEqual(status,200);self.assertTrue(body.startswith(b'pass-'))
        self.assertEqual(headers['Cache-Control'],'private, no-store')
        status,headers,part=self.call(headers={'Range':'bytes=1-3'})
        self.assertEqual((status,part),(206,body[1:4]));self.assertEqual(headers['Content-Range'],f'bytes 1-3/{len(body)}')
        self.assertEqual(self.call(headers={'Range':'bytes=-2'})[2],body[-2:])
        status,headers,part=self.call(headers={'Range':'bytes=999-'})
        self.assertEqual((status,part),(416,b''));self.assertEqual(headers['Content-Range'],f'bytes */{len(body)}')
        self.assertEqual(self.call(user='other')[0],404)
        self.assertEqual(self.call(path=self.path.split('?')[0])[0],422)
        self.assertEqual(self.call(path=self.path.replace(self.digest,'sha256:'+'0'*64))[0],409)
        self.assertEqual(self.call(method='POST')[0],404)

    def test_current_cleaning_video_preview_uses_get_and_range(self):
        path=f'/v1/retrieval/projects/demo/tasks/{self.tid}/cleaning/candidates/{self.cid}/video'
        status,headers,body=self.call(path,headers={'Range':'bytes=0-3'})
        self.assertEqual((status,body),(206,b'pass'))
        self.assertEqual(headers['Content-Type'],'video/mp4')
        self.assertEqual(self.call(path,user='other')[0],404)

    def test_native_route_binds_actual_task_project(self):
        native=self.path.replace('/tasks/','/projects/demo/tasks/',1)
        self.assertEqual(self.call(native)[0],200)
        self.assertEqual(self.call(native.replace('/projects/demo/','/projects/other/'))[0],404)
