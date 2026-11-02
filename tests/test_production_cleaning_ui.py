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
        self.assertIn('#board-cleaning', BOARD_CSS)
        self.assertNotIn('innerHTML', CLEANING_JS)
        self.assertNotIn("method:'POST'", CLEANING_JS)
        self.assertNotIn('openTask(', CLEANING_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for executable UI tests')
    def test_render_filter_pagination_snapshot_and_stale_response(self):
        script = r'''
const assert=require('node:assert/strict');
class El {
  constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.open=true;this.isConnected=true;this.value='';this.dataset={};}
  append(...items){for(const item of items)item.parentElement=this;this.children.push(...items)}
  replaceChildren(...items){this.children=items;this.textContent=''}
  setAttribute(k,v){this[k]=v}
  querySelectorAll(tag){return this.children.flatMap(x=>[...(x.tag===tag?[x]:[]),...x.querySelectorAll(tag)])}
  addEventListener(k,v){this[k]=v}
  remove(){this.isConnected=false;if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(item=>item!==this)}
  showModal(){this.open=true}
}
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new El());return elements.get(id)};
const document={createElement:tag=>new El(tag)};
let handler;
const api=path=>handler(path);
let imageRequests=[],revoked=[],failImage=false;
const fetch=async(url,options)=>{imageRequests.push({url,options});return {ok:!failImage,blob:async()=>({type:'image/jpeg',size:100})}};
const URL={createObjectURL:()=> 'blob:cleaning-'+imageRequests.length,revokeObjectURL:url=>revoked.push(url)};
const ui=new Function('document','$','api','headers','fetch','URL','openAssetViewer',process.env.SOURCE+';return {cleaningPanel,readBoardCleaning,closeBoardCleaning,boardCleaningItems,boardCleaningReason};')(document,$,api,{},fetch,URL,()=>{});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const walk=el=>[el,...el.children.flatMap(walk)];
const text=el=>walk(el).map(x=>x.textContent).join('\n');
const task={task_version:4,run_generation:1,confirmed_revision:1,active_cleaning_operation_id:'op'};
const execution={model:{id:'vlm',name:'saved-model',provider:'saved-provider'},instruction:'saved instruction',backend_ref:'vlm@1',prompt_version:'v1',config_digest:'digest'};
const reason={rule_id:'semantic',verdict:'fail',reason_code:'MISMATCH',reason_summary:'<img src=x onerror=alert(1)>',reason_truncated:true};
const items=Array.from({length:25},(_,i)=>({candidate_id:'c'+i,asset_version:'v1',status:i===0?'rejected':'retained',reasons:[i===0?reason:{rule_id:'semantic',verdict:'pass',reason_code:'MATCH'}]}));
const step={step_id:'semantic',backend_ref:'vlm@1',counts:{input:25,retained:24,rejected:1,review:0},rules:[{rule_id:'semantic',predicate:'has_dog',operator:'eq',value:true}],items};
const data={revision:1,generation:1,available:true,candidate_preview_available:true,runs:[{operation_id:'op',revision:1,generation:1,output_version:'hash',execution,note:'本步保留不等于入选',steps:[step,{...step,step_id:'second'}]}]};
const status={status:'completed',progress:{total:25,completed:25,passed:24,rejected:1,review:0,pending:0}};
(async()=>{
  handler=async path=>path.endsWith('/steps')?data:path.includes('/status?')?status:task;
  ui.cleaningPanel.taskId='task';await ui.readBoardCleaning();
  const body=$('board-cleaning-body');
  assert(text(body).includes('saved instruction'));assert(text(body).includes('saved-model'));
  assert(text(body).includes('<img src=x onerror=alert(1)>'));assert(text(body).includes('摘要已截断'));
  await tick();
  assert.equal(walk(body).filter(x=>x.tag==='img').length,12,'Expanded node loads thumbnails automatically');
  assert.equal(imageRequests.length,12,'Collapsed nodes do not request images');
  assert.equal(imageRequests[0].options.cache,'no-store');
  assert(text(body).includes('节点连线表示配方顺序'));
  const nodes=walk(body).filter(x=>x.className==='cleaning-step');assert.equal(nodes.length,2);assert.equal(nodes[1].open,false);
  nodes[1].open=true;nodes[1].toggle();await tick();assert.equal(imageRequests.length,24);
  const secondGrid=walk(nodes[1]).find(x=>x.className==='cleaning-grid');assert.equal(secondGrid.children.length,12);
  nodes[1].open=false;nodes[1].toggle();
  const grid=walk(body).find(x=>x.className==='cleaning-grid');
  assert.equal(grid.children.length,12);
  const firstCard=grid.children[0];walk(body).find(x=>x.textContent==='加载更多图片').onclick();assert.equal(grid.children.length,24);assert.equal(grid.children[0],firstCard,'Loading more preserves existing image cards');
  const select=walk(body).find(x=>x.tag==='select');select.value='rejected';select.onchange();assert.equal(grid.children.length,1);
  assert(revoked.length>0,'Filtered-out thumbnails are released');
  const tags=walk(nodes[0]).filter(x=>x.tag==='select')[1];tags.value='reason:MATCH';tags.onchange();assert.equal(grid.children.length,0,'Status and reason tag filters combine');
  select.value='all';select.onchange();assert.equal(grid.children.length,12);assert.equal(secondGrid.children.length,12,'Node filters remain independent');
  tags.value='reason:MISMATCH';tags.onchange();assert.equal(grid.children.length,1);tags.value='rule:semantic';tags.onchange();assert.equal(grid.children.length,12);
  const query=walk(body).find(x=>x.tag==='input');query.value='不存在';query.oninput();assert.equal(grid.children.length,0);
  assert(ui.boardCleaningReason({rule_id:'r',verdict:'unknown',reason_code:'FACT_UNAVAILABLE'}).includes('未保存具体原因摘要'));
  const beforeRefresh=imageRequests.length;await ui.readBoardCleaning();await tick();const refreshed=walk(body).filter(x=>x.className==='cleaning-step');assert.equal(refreshed[1].open,false);assert.equal(imageRequests.length,beforeRefresh,'Refresh preserves collapsed state and active filters');
  ui.closeBoardCleaning();assert.equal(body.children.length,0);

  // Failed authorized thumbnail reads are retryable; unavailable previews never fetch.
  ui.cleaningPanel.filters.clear();failImage=true;ui.cleaningPanel.taskId='task';await ui.readBoardCleaning();await tick();
  const retry=walk(body).find(x=>x.className==='cleaning-thumbnail');assert(retry.textContent.includes('点击重试'));assert.equal(retry.disabled,false);
  failImage=false;await retry.onclick();assert.equal(retry.children[0].tag,'img');
  ui.closeBoardCleaning();const imageCount=imageRequests.length;data.candidate_preview_available=false;
  ui.cleaningPanel.taskId='task';await ui.readBoardCleaning();await tick();assert.equal(imageRequests.length,imageCount);assert(text(body).includes('暂无可用缩略图'));ui.closeBoardCleaning();data.candidate_preview_available=true;

  // Partial checkpoints: do not invent completed steps or treat pending as review.
  const partial={status:'running',execution,progress:{total:3,completed:1,pending:2,passed:0,rejected:0,review:1,candidate_results:[{candidate_id:'c1',verdict:'unknown'}]}};
  handler=async path=>path.endsWith('/steps')?{revision:1,generation:1,available:false,runs:[]}:path.includes('/status?')?partial:task;
  ui.cleaningPanel.taskId='task';await ui.readBoardCleaning();
  assert($('board-cleaning-status').textContent.includes('未完成 2'));
  assert(text(body).includes('尚未发布完整步骤结果'));assert.equal(walk(body).filter(x=>x.tag==='details').length,0);
  ui.closeBoardCleaning();

  // Version drift drops the entire mixed-version view.
  let taskReads=0;
  handler=async path=>path.endsWith('/steps')?data:path.includes('/status?')?status:{...task,task_version:++taskReads===1?4:5};
  ui.cleaningPanel.taskId='task';await ui.readBoardCleaning();
  assert($('board-cleaning-status').textContent.includes('任务版本已变化'));assert.equal(body.children.length,0);
  ui.closeBoardCleaning();

  // A late response after closing cannot resurrect private task content.
  const pending=[];handler=path=>new Promise(resolve=>pending.push(resolve));
  ui.cleaningPanel.taskId='task';const reading=ui.readBoardCleaning();ui.closeBoardCleaning();
  pending[0](task);pending[1](data);await reading;assert.equal(body.children.length,0);
})().catch(error=>{console.error(error);process.exitCode=1;ui.closeBoardCleaning()});
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True,
                                env={**os.environ, 'SOURCE': CLEANING_JS}, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
