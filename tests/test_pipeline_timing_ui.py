import os
import shutil
import subprocess
import unittest

from dsh_retrieval.chat_ui import CHAT_JS
from dsh_retrieval.candidate_ui import CANDIDATE_JS


class PipelineTimingUiTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for real browser timing helpers')
    def test_request_render_failure_and_abort_are_correlated_bounded_and_content_free(self):
        source = CHAT_JS[CHAT_JS.index('const pipelineResponseTraces='):CHAT_JS.index('const headers=')]
        source += CHAT_JS[CHAT_JS.index('async function api('):CHAT_JS.index('function stopTaskEvents(')]
        source += CANDIDATE_JS[CANDIDATE_JS.index('function candidateRender(view)'):CANDIDATE_JS.index('function candidateRenderContent(view)')]
        script = r'''
(async()=>{
const assert=require('node:assert/strict');const saved=new Map(),window={},frames=[];let now=0,mode='ok';
const sessionStorage={getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)};
const performance={now:()=>now},requestAnimationFrame=callback=>frames.push(callback),headers={};
const trace='a'.repeat(32),data={private:'secret-reply',progress:{items:[]}};
const fetch=async()=>{now+=100;if(mode!=='ok'){const error=Error('private-endpoint and private-token');if(mode==='abort')error.name='AbortError';throw error}
 return {ok:true,status:200,headers:{get:name=>name==='X-Performance-Trace-Id'?trace:'pipeline;dur=70.000'},json:async()=>{now+=25;return data}}};
const functions=new Function('window','sessionStorage','performance','requestAnimationFrame','headers','fetch','candidateCurrent','candidateRenderContent',process.env.SOURCE+
 ';return {api,measureChatRender,pipelineTimingRecords,recordPipelineTiming,linkPipelineRender,candidateRender}')(
 window,sessionStorage,performance,requestAnimationFrame,headers,fetch,()=>true,()=>{now+=4});
const response=await functions.api('/v1/retrieval/tasks/private-task/workspace?secret-query');
assert.equal(response,data);
functions.measureChatRender('progress_render',()=>{now+=3},response.progress);
now+=16;frames.shift()();now+=16;frames.shift()();
let rows=functions.pipelineTimingRecords();assert.equal(rows[0].headers_ms,100);assert.equal(rows[0].body_ms,25);assert.equal(rows[0].server_ms,70);
assert.equal(rows[0].ms,125);assert.equal(rows[0].endpoint,'workspace');assert.equal(rows[1].ms,3);assert.equal(rows[2].ms,35);
assert(rows.every(row=>row.trace_id===trace));
const view={};functions.linkPipelineRender(view,response);functions.candidateRender(view);
rows=functions.pipelineTimingRecords();assert.equal(rows.at(-1).stage,'candidate_gallery_render');assert.equal(rows.at(-1).ms,4);assert.equal(rows.at(-1).trace_id,trace);
mode='failed';await assert.rejects(functions.api('/private-path',{method:'POST',body:{prompt:'secret-prompt'}}),e=>e.code==='NETWORK_UNAVAILABLE');
mode='abort';await assert.rejects(functions.api('/private-path'),e=>e.name==='AbortError');
rows=functions.pipelineTimingRecords();assert.equal(rows.at(-2).outcome,'failed');assert.equal(rows.at(-1).outcome,'aborted');
for(const secret of ['secret-reply','private-task','secret-query','private-endpoint','private-token','secret-prompt','private-path'])assert(!JSON.stringify(rows).includes(secret));
for(let index=0;index<250;index++)functions.recordPipelineTiming({stage:'http',ms:index});
assert.equal(functions.pipelineTimingRecords().length,200);
saved.set('dsh-pipeline-timing-v1','invalid-json');functions.recordPipelineTiming({stage:'http',ms:1});assert.equal(functions.pipelineTimingRecords().length,1);
saved.set('dsh-pipeline-timing-v1','{}');functions.recordPipelineTiming({stage:'http',ms:2});assert.equal(functions.pipelineTimingRecords().length,1);
})().catch(error=>{console.error(error);process.exit(1)});
'''
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
