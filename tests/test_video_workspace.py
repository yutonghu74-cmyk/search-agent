import os
import shutil
import subprocess
import unittest
from dsh_retrieval.video_workspace import VIDEO_WORKSPACE_JS


@unittest.skipUnless(shutil.which('node'),'Node required')
class VideoWorkspaceTest(unittest.TestCase):
    def run_js(self,script):
        source=VIDEO_WORKSPACE_JS[:VIDEO_WORKSPACE_JS.index('for(const [id,action]')]
        result=subprocess.run(['node','-e',script],env={**os.environ,'SOURCE':source},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_failed_write_retries_same_receipt_without_duplicate_mutation(self):
        self.run_js(r"""
(async()=>{const assert=require('assert/strict');const store=new Map(),storage={getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)};
let n=0,calls=[],fail=true;const api=async(p,o)=>{calls.push(o.keyValue);if(fail){fail=false;throw Error('network')}return {ok:true}};
const f=new Function('api','sessionStorage','key',process.env.SOURCE+';return {videoWrite}')(api,storage,()=>String(++n));
await assert.rejects(f.videoWrite('/task',{action:'confirm'}));await f.videoWrite('/task',{action:'confirm'});
assert.equal(calls[0],calls[1]);assert.equal(store.size,0);
})().catch(e=>{console.error(e);process.exit(1)});
""")

    def test_unchanged_poll_does_not_destroy_video_players(self):
        self.run_js(r"""
(async()=>{const assert=require('assert/strict');let clears=0,revoked=[],digest='one';const fields={};const $=id=>fields[id]??={value:'accepted',replaceChildren(){clears++}};
const f=new Function('$','api','URL',process.env.SOURCE+';return {showVideoPage,state:videoWork,nativeVideoUrl}')($,async()=>({items:[],result_set_digest:digest}),{revokeObjectURL:u=>revoked.push(u)});
f.state.id='rt_'+'a'.repeat(32);f.state.task={project_id:'101'};await f.showVideoPage();const epoch=f.state.epoch;
f.state.urls.push('blob:video');await f.showVideoPage();assert.equal(clears,1);assert.equal(f.state.epoch,epoch);assert.deepEqual(revoked,[]);
digest='two';await f.showVideoPage();assert.equal(clears,2);assert.deepEqual(revoked,['blob:video']);
assert.equal(f.nativeVideoUrl('/v1/retrieval/tasks/task/candidates/id/video'),'/v1/retrieval/projects/101/tasks/task/candidates/id/video');
})().catch(e=>{console.error(e);process.exit(1)});
""")

    def test_unsaved_relation_cannot_execute_an_old_draft(self):
        self.run_js(r"""
(async()=>{const assert=require('assert/strict');let posts=0;const fields={
'video-draft-query':{value:'喝水'},'video-draft-count':{value:'2'},'video-condition-relation':{value:'any'},'video-auto-refill':{checked:false},'video-max-rounds':{value:''},'video-max-calls':{value:''}};
const draft={draft_id:'draft',video_summary:{execution:{available:true}},proposal:{query_spec:{target_count:2,video_conditions:{clauses:[{predicate:'喝水'}],relation:{operator:'all'}},refill_policy:{mode:'input_only'}}}};
const f=new Function('$','api','videoDraftState',process.env.SOURCE+';return {confirmVideoDraft}')(id=>fields[id],async(p,o)=>{if(o?.method==='POST')posts++;return draft},{draft});
await assert.rejects(f.confirmVideoDraft(),/保存当前修改/);assert.equal(posts,0);
})().catch(e=>{console.error(e);process.exit(1)});
""")

    def test_manual_search_prepares_only_selected_sources_with_bounded_batch(self):
        self.run_js(r"""
(async()=>{const assert=require('assert/strict');const calls=[],fields={};const $=id=>fields[id]??={};
const api=async(path,options)=>{calls.push({path,...options});return {proposal_digest:'p',expected_task_version:1,members:options.body.choices}};
const store=new Map(),sessionStorage={getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)};
const f=new Function('$','api','sessionStorage','key',process.env.SOURCE+';return {prepareVideoSearch,state:videoWork}')($,api,sessionStorage,()=> 'key');
f.state.id='rt_'+'a'.repeat(32);f.state.binding={proposal:{query_spec:{target_count:20,video_conditions:{clauses:[{},{}]},refill_limits:{max_vlm_calls:8}}}};
f.state.routes={routing_digest:'r',routes:[{branch_id:'one'},{branch_id:'two'}]};
f.state.routeChoices=[{branch_id:'one',binding_id:'a',max_batch_size:8,selected:true},{branch_id:'one',binding_id:'unselected',max_batch_size:8,selected:false},{branch_id:'two',binding_id:'b',max_batch_size:8,selected:true}];
await f.prepareVideoSearch();assert.equal(calls.length,1);assert.ok(calls[0].path.endsWith('/prepare'));
assert.deepEqual(calls[0].body.choices,[{branch_id:'one',binding_id:'a',batch_size:2},{branch_id:'two',binding_id:'b',batch_size:2}]);
f.state.routeChoices[2].selected=false;await assert.rejects(f.prepareVideoSearch(),/每条查询/);assert.equal(calls.length,1);
f.state.routeChoices[2].selected=true;f.state.binding.proposal.query_spec.refill_limits.max_vlm_calls=3;
await assert.rejects(f.prepareVideoSearch(),/预算/);assert.equal(calls.length,1);
})().catch(e=>{console.error(e);process.exit(1)});
""")

    def test_unapproved_manual_task_refresh_does_not_request_candidates(self):
        self.run_js(r"""
(async()=>{const assert=require('assert/strict');let pages=0;const id='rt_'+'a'.repeat(32),fields={
'video-task-id':{value:id},'video-work-view':{value:'candidates'}};const $=id=>fields[id]??={replaceChildren(){}};
const api=async path=>{if(path.endsWith('/read-binding'))return {proposal:{query_spec:{media_type:'video',target_count:2}}};
if(path.endsWith('/first-search/progress'))return {status:'unapproved',initial_window_complete:false};
if(path.endsWith('/results?limit=1'))return {available:false,counts:{}};
if(path.endsWith('/delivery'))return {available:false};if(path.includes('/candidates')){pages++;throw Error('no approved plan')}return {stage:'planning',run_status:'PLANNING'}};
const f=new Function('$','api','sessionStorage',process.env.SOURCE+';return {refreshVideoTask,state:videoWork}')($,api,{setItem(){}});
await f.refreshVideoTask();assert.equal(pages,0);assert.equal(f.state.id,id);assert.match(fields['video-search-status'].textContent,/尚未确认/);
})().catch(e=>{console.error(e);process.exit(1)});
""")
