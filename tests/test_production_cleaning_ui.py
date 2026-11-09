import os
import shutil
import subprocess
import unittest

from dsh_retrieval.production_cleaning_ui import CLEANING_JS
from dsh_retrieval.production_board_ui import BOARD_HTML, BOARD_JS, BOARD_CSS


class ProductionCleaningUiTest(unittest.TestCase):
    def test_workbench_mounts_readonly_inspector(self):
        self.assertIn('id="board-cleaning"', BOARD_HTML)
        self.assertIn('mountBoardCleaning(item,body)', BOARD_JS)
        self.assertIn('.board-cleaning', BOARD_CSS)
        self.assertIn('class="board-cleaning"', BOARD_HTML)
        self.assertNotIn('<dialog id="board-cleaning"', BOARD_HTML)
        self.assertIn('cleaning-step-nav', BOARD_CSS)
        self.assertNotIn('innerHTML', CLEANING_JS)
        self.assertNotIn("method:'POST'", CLEANING_JS)
        self.assertNotIn('openTask(', CLEANING_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for executable UI tests')
    def test_render_filter_pagination_snapshot_and_stale_response(self):
        script = r'''
const assert=require('node:assert/strict');
class El {
 constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.open=false;this.isConnected=true;this.value='';this.dataset={}}
 append(...xs){for(const x of xs)x.parentElement=this;this.children.push(...xs)}
 replaceChildren(...xs){for(const x of this.children)x.isConnected=false;this.children=xs;this.textContent=''}
 setAttribute(k,v){this[k]=v} addEventListener(k,v){this[k]=v}
 querySelectorAll(tag){return this.children.flatMap(x=>[...(x.tag===tag?[x]:[]),...x.querySelectorAll(tag)])}
}
const els=new Map(),$=id=>{if(!els.has(id))els.set(id,new El());return els.get(id)},document={createElement:tag=>new El(tag)};
const walk=el=>[el,...el.children.flatMap(walk)],text=el=>walk(el).map(x=>x.textContent).join('\n'),tick=()=>new Promise(r=>setImmediate(r));
let handler,holdPages=false,latePages=[],activeImages=0,maxActive=0,imageRequests=[],pageRequests=[],failImage=false,holdImages=false,lateImages=[],revoked=[],urls=0;
const api=p=>handler(p),NativeURL=URL;
const reason={rule_id:'semantic',verdict:'fail',reason_code:'MISMATCH',reason_summary:'<img src=x onerror=alert(1)>',reason_truncated:true};
const items=Array.from({length:1200},(_,i)=>({candidate_id:'c'+i,asset_version:'v1',status:i===1199?'rejected':'retained',preview_status:'available',reasons:[i===1199?reason:{rule_id:'semantic',verdict:'pass',reason_code:'MATCH'}]}));
const task={task_version:4,run_generation:1,confirmed_revision:1,active_cleaning_operation_id:'op'};
const step={step_id:'semantic',step_execution_id:'s1',backend_ref:'vlm@1',counts:{input:1200,retained:1199,rejected:1,review:0},rules:[{rule_id:'semantic',predicate:'dog',operator:'eq',value:true}]};
const data={revision:1,generation:1,available:true,candidate_preview_available:true,runs:[{operation_id:'op',status:'completed',revision:1,generation:1,output_version:'hash',execution:{model:{id:'m',name:'saved-model',provider:'saved-provider'},instruction:'saved instruction'},steps:[step,{...step,step_id:'second',step_execution_id:'s2'}]}]};
const fetch=async(path,options)=>{
 if(path.includes('/outputs?')){
  const q=new NativeURL(path,'http://test').searchParams;pageRequests.push(q);assert.equal(q.get('limit'),'12');
  let filtered=items.filter(x=>(q.get('view_type')==='input'||x.status===q.get('view_type'))&&(!q.get('reason_code')||x.reasons.some(r=>r.reason_code===q.get('reason_code')))&&(!q.get('rule_id')||x.reasons.some(r=>r.rule_id===q.get('rule_id')))&&(!q.get('query')||x.reasons.some(r=>(r.reason_summary||'').includes(q.get('query')))));
  const offset=Number(q.get('cursor')||0),page={task_id:'task',revision:1,generation:1,operation_id:'op',step_execution_id:q.get('step_execution_id'),output_version:'hash',items:filtered.slice(offset,offset+12),total_items:filtered.length,next_cursor:offset+12<filtered.length?String(offset+12):null};
  if(holdPages)await new Promise(r=>latePages.push(r));return {ok:true,json:async()=>page};
 }
 imageRequests.push(options);activeImages++;maxActive=Math.max(maxActive,activeImages);
 try{if(holdImages)await new Promise((resolve,reject)=>{lateImages.push(resolve);options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})),{once:true})});else await tick();return {ok:!failImage,blob:async()=>({type:'image/jpeg',size:100})}}finally{activeImages--}
};
const URLmock={createObjectURL:()=> 'blob:'+ ++urls,revokeObjectURL:u=>revoked.push(u)};
const ui=new Function('document','$','api','headers','fetch','URL','openAssetViewer',process.env.SOURCE+';return {cleaningPanel,readBoardCleaning,closeBoardCleaning,boardCleaningReason};')(document,$,api,{},fetch,URLmock,()=>{});
const settle=async()=>{for(let i=0;i<15;i++)await tick()};
function normal(){handler=async p=>{if(p.endsWith('/steps?summary=true'))return data;if(p.endsWith('/read-binding'))return {task};throw Error('Unexpected full read '+p)}}
async function open(){normal();$('board-cleaning').hidden=false;ui.cleaningPanel.taskId='task';await ui.readBoardCleaning();await settle()}
(async()=>{
 await open();const body=$('board-cleaning-body');assert(text(body).includes('saved-model'));assert(text(body).includes('saved instruction'));assert(!text(body).includes('节点连线表示配方顺序'));assert.equal(walk(body).filter(x=>x.className==='cleaning-step-tab').length,2);
 const nodes=walk(body).filter(x=>x.className==='cleaning-step'),grid=walk(nodes[0]).find(x=>x.className==='cleaning-grid'),next=walk(nodes[0]).find(x=>x.textContent==='下一页图片'),prev=walk(nodes[0]).find(x=>x.textContent==='上一页图片');
 assert.equal(grid.children.length,12);assert.equal(pageRequests.length,1);assert.equal(nodes[1].open,false);assert.equal(imageRequests[0].cache,'no-store');assert(maxActive<=4);
 for(let p=1;p<100;p++){await next.onclick();await settle();assert.equal(grid.children.length,12);assert(ui.cleaningPanel.urls.size<=12);assert(ui.cleaningPanel.imageRows.size<=12)}assert(next.disabled);await prev.onclick();await settle();assert(grid.children[0]._asset.title.endsWith('c1176'));assert(revoked.length>1000);
 const select=walk(nodes[0]).find(x=>x.tag==='select'),tags=walk(nodes[0]).filter(x=>x.tag==='select')[1],query=walk(nodes[0]).find(x=>x.tag==='input');
 select.value='rejected';await select.onchange();await settle();assert.equal(grid.children.length,1);assert(text(grid).includes('<img src=x onerror=alert(1)>'));assert(text(grid).includes('摘要已截断'));assert.equal(prev.disabled,true);
 tags.value='reason:MATCH';await tags.onchange();assert.equal(grid.children.length,0);tags.value='all';await tags.onchange();select.value='all';await select.onchange();query.value='onerror';query.oninput();await new Promise(r=>setTimeout(r,275));await settle();assert.equal(grid.children.length,1);assert.equal(pageRequests.at(-1).get('query'),'onerror');
 nodes[1].open=true;nodes[1].toggle();await settle();assert.equal(nodes[0].open,false);assert.equal(grid.children.length,0);assert.equal(walk(nodes[1]).find(x=>x.className==='cleaning-grid').children.length,12);assert(ui.cleaningPanel.urls.size<=12);
 // Rapid page response arrives after collapse and cannot restore released cards.
 holdPages=true;const secondNext=walk(nodes[1]).find(x=>x.textContent==='下一页图片');const pending=secondNext.onclick();await tick();nodes[1].open=false;nodes[1].toggle();latePages.splice(0).forEach(r=>r());await pending;assert.equal(walk(nodes[1]).find(x=>x.className==='cleaning-grid').children.length,0);holdPages=false;
 ui.closeBoardCleaning();assert.equal(body.children.length,0);assert.equal(ui.cleaningPanel.urls.size,0);
 // Refresh preserves the selected node and each node's filters.
 await open();let refreshedNodes=walk(body).filter(x=>x.className==='cleaning-step');refreshedNodes[1].open=true;refreshedNodes[1].toggle();await settle();const secondSelect=walk(refreshedNodes[1]).find(x=>x.tag==='select');secondSelect.value='rejected';await secondSelect.onchange();await settle();await ui.readBoardCleaning();await settle();refreshedNodes=walk(body).filter(x=>x.className==='cleaning-step');assert.equal(refreshedNodes[0].open,false);assert.equal(refreshedNodes[1].open,true);assert.equal(walk(refreshedNodes[1]).find(x=>x.className==='cleaning-grid').children.length,1);ui.closeBoardCleaning();
 // Failed images can retry; disabled previews never fetch.
 ui.cleaningPanel.filters.clear();failImage=true;await open();const retry=walk(body).find(x=>x.className==='cleaning-thumbnail');assert(retry.textContent.includes('点击重试'));failImage=false;await retry.onclick();assert.equal(retry.children[0].tag,'img');ui.closeBoardCleaning();
 const before=imageRequests.length;data.candidate_preview_available=false;await open();assert.equal(imageRequests.length,before);assert(text(body).includes('暂无可用缩略图'));ui.closeBoardCleaning();data.candidate_preview_available=true;
 // Closing aborts in-flight images; stale completions never allocate new blobs.
 holdImages=true;await open();assert(activeImages<=4);ui.closeBoardCleaning();await settle();assert.equal(activeImages,0);assert.equal(ui.cleaningPanel.urls.size,0);holdImages=false;
 // A version race discards the view.
 let reads=0;handler=async p=>p.endsWith('/steps?summary=true')?data:{task:{...task,task_version:++reads===1?4:5}};$('board-cleaning').hidden=false;ui.cleaningPanel.taskId='task';await ui.readBoardCleaning();assert(text($('board-cleaning-status')).includes('版本已变化'));assert.equal(body.children.length,0);ui.closeBoardCleaning();
 // Partial checkpoints remain visible with pending distinguished from review.
 handler=async p=>p.endsWith('/steps?summary=true')?{revision:1,generation:1,available:false,runs:[]}:p.includes('/status?')?{status:'running',progress:{completed:1,total:3,pending:2,passed:0,rejected:0,review:1,candidate_results:[{candidate_id:'c1',verdict:'unknown'}]}}:{task};
 $('board-cleaning').hidden=false;ui.cleaningPanel.taskId='task';await ui.readBoardCleaning();assert(text($('board-cleaning-status')).includes('未完成 2'));assert(text(body).includes('尚未发布完整步骤结果'));ui.closeBoardCleaning();
 // A persisted running checkpoint must still poll its active operation.
 data.runs[0].status='running';handler=async p=>p.endsWith('/steps?summary=true')?data:p.includes('/status?')?{status:'running',progress:{completed:1,total:3,pending:2,passed:0,rejected:0,review:1}}:{task};$('board-cleaning').hidden=false;ui.cleaningPanel.taskId='task';await ui.readBoardCleaning();await settle();assert(text($('board-cleaning-status')).includes('未完成 2'));assert(ui.cleaningPanel.timer);ui.closeBoardCleaning();data.runs[0].status='completed';
 const delayed=[];handler=p=>new Promise(r=>delayed.push(r));$('board-cleaning').hidden=false;ui.cleaningPanel.taskId='task';const reading=ui.readBoardCleaning();ui.closeBoardCleaning();delayed[0]({task});delayed[1](data);await reading;assert.equal(body.children.length,0);
 assert(ui.boardCleaningReason({rule_id:'r',verdict:'unknown',reason_code:'FACT_UNAVAILABLE'}).includes('未保存具体原因摘要'));
})().catch(e=>{console.error(e);process.exitCode=1;ui.closeBoardCleaning()});

'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True,
                                env={**os.environ, 'SOURCE': CLEANING_JS}, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
