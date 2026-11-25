"""Execute the shipped request helper against controlled overlapping transports."""
import os
import shutil
import subprocess
import unittest

from dsh_retrieval.chat_ui import CHAT_JS


@unittest.skipUnless(shutil.which('node'), 'Node required for request concurrency tests')
class ChatReadCoalescingTest(unittest.TestCase):
    def test_overlap_invalidation_identity_failure_and_cancellation(self):
        source = CHAT_JS[CHAT_JS.index('async function api('):CHAT_JS.index('function stopTaskEvents(')]
        script = r'''
const assert=require('node:assert/strict');
const headers={'X-User-Id':'one'},requests=[],traces=new WeakMap();
const fetch=(path,options)=>new Promise((resolve,reject)=>requests.push({path,options,resolve,reject}));
const linkPipelineRender=(target,source)=>traces.set(target,traces.get(source));
const api=new Function('headers','fetch','performance','pipelineResponseTraces','linkPipelineRender','recordPipelineTiming','pipelineEndpoint',
 process.env.SOURCE+';return api')(
 headers,fetch,performance,traces,linkPipelineRender,()=>{},()=> 'workspace');
const path='/v1/retrieval/tasks/t/workspace';
const reply=(index,value={task_version:index,progress:{items:[]}})=>requests[index].resolve({ok:true,status:200,headers:{get:()=>null},json:async()=>value});
(async()=>{
 // Three simultaneous UI consumers issue one HTTP read and receive independent data.
 const first=api(path),second=api(path),third=api(path);assert.equal(requests.length,1);
 reply(0);const [a,b,c]=await Promise.all([first,second,third]);assert.deepEqual(a,b);
 a.progress.items.push('local');assert.deepEqual(b.progress.items,[]);assert.deepEqual(c.progress.items,[]);
 const after=api(path);assert.equal(requests.length,2);reply(1);await after;
 // A write invalidates reads at both entry and completion, including failed writes.
 const old=api(path),write=api('/v1/retrieval/tasks/t/confirm',{method:'POST'}),during=api(path);
 assert.equal(requests.length,5);requests[3].reject(Error('disconnected'));
 await assert.rejects(write,e=>e.code==='NETWORK_UNAVAILABLE');
 const next=api(path);assert.equal(requests.length,6);
 reply(2);await old; // Old cleanup must not evict the newer in-flight request.
 const joined=api(path);assert.equal(requests.length,6);
 reply(4);reply(5);await Promise.all([during,next,joined]);
 // Different users/tasks/endpoints cannot join, fresh validation always uses a new read.
 const one=api(path);headers['X-User-Id']='two';const two=api(path);
 const other=api('/v1/retrieval/tasks/other/workspace'),progress=api('/v1/retrieval/tasks/t/progress'),fresh=api(path,{fresh:true});
 assert.equal(requests.length,11);for(let i=6;i<11;i++)reply(i);await Promise.all([one,two,other,progress,fresh]);
 // Failure is shared only while pending; retry performs transport again.
 const failed=api(path),alsoFailed=api(path);assert.equal(requests.length,12);
 requests[11].reject(Error('offline'));await Promise.all([assert.rejects(failed),assert.rejects(alsoFailed)]);
 const retry=api(path);assert.equal(requests.length,13);reply(12);await retry;
 // Cancellable callers have separate transports and cannot cancel ordinary consumers.
 const controller=new AbortController(),cancel=api(path,{signal:controller.signal}),normal=api(path);
 assert.equal(requests.length,15);assert.equal(requests[13].options.signal,controller.signal);
 requests[13].reject(Object.assign(Error('cancelled'),{name:'AbortError'}));reply(14);
 await assert.rejects(cancel,e=>e.name==='AbortError');await normal;
 // Other GET endpoints are intentionally outside the sharing policy.
 const detail=api('/v1/retrieval/tasks/t'),detail2=api('/v1/retrieval/tasks/t');assert.equal(requests.length,17);
 reply(15);reply(16);await Promise.all([detail,detail2]);
})().catch(error=>{console.error(error);process.exit(1)});
'''
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_events_and_post_result_validation_bypass_pending_reads(self):
        self.assertIn('function stopTaskEvents(){invalidateTaskReads();', CHAT_JS)
        self.assertIn('if(state.eventTaskId!==taskId)return;invalidateTaskReads();eventRefreshDirty=true;', CHAT_JS)
        self.assertIn('const latest=await api(`/v1/retrieval/tasks/${taskId}/workspace`,{fresh:true});', CHAT_JS)
