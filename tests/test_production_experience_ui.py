import os
import shutil
import subprocess
import unittest

from dsh_retrieval.production_experience_ui import EXPERIENCE_JS


@unittest.skipUnless(shutil.which('node'), 'Node required for UI behavior tests')
class ProductionExperienceTest(unittest.TestCase):
    def test_truthful_counts_actions_and_preview_lifecycle(self):
        script = r'''
const assert=require('node:assert/strict');
class El{
 constructor(){this.children=[];this.open=true;this.disabled=false;this.textContent='';this.isConnected=true}
 append(...items){this.children.push(...items)}
 replaceChildren(...items){this.children=items}
 focus(){this.focused=true}
 showModal(){this.open=true}
}
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new El());return elements.get(id)};
const document={createElement:()=>new El(),activeElement:new El()};
let responder,revoked=[],requests=[];
const fetch=async(url,options)=>{requests.push({url,options});return responder()};
const URL={createObjectURL:()=> 'blob:image',revokeObjectURL:url=>revoked.push(url)};
const ui=new Function('document','$','fetch','URL','headers','nodeURL',process.env.SOURCE+';return {knownCount,runStatusLabel,nextAction,goalSummary,assetViewer,openAssetViewer,showAsset,closeAssetViewer};')(document,$,fetch,URL,{},(id,node)=>id+'/'+node);
const item={task_id:'t',run_status:'RUNNING',progress:{run_status:'RUNNING',counts:{target:100,accepted:null,gap:null,pending_review:null},nodes:[{node:'cleaning',label:'清洗',node_status:'running'}]}};
assert.match(ui.goalSummary(item),/有效 待计算 · 缺口 待计算/);
assert.equal(ui.knownCount(0),'0');assert.equal(ui.knownCount(-1),'待计算');
assert.equal(ui.runStatusLabel(item),'清洗中');item.progress.nodes[0].node='retrieval';assert.equal(ui.runStatusLabel(item),'检索中');item.progress.nodes[0].node='cleaning';
assert.equal(ui.nextAction(item).kind,'running');
item.progress.run_status='PLANNING';assert.equal(ui.nextAction(item).kind,'pending','A planning node is not a running production job');item.progress.run_status='RUNNING';
assert.equal(ui.nextAction({...item,error:'offline'}).retry,true);
item.progress.counts.pending_review=3;assert.equal(ui.nextAction(item).label,'查看待复核');
item.progress.counts.pending_review=0;item.progress.nodes[0].gate_status='awaiting_approval';assert.equal(ui.nextAction(item).href,'/chat?task=t');
delete item.progress.nodes[0].gate_status;item.progress.counts.accepted=0;item.progress.counts.gap=2;item.progress.nodes.push({node:'refill',detail:'缺口 2；自动补数已停止：连续补数没有新增有效结果'});assert.equal(ui.runStatusLabel(item),'补数已停止');assert.equal(ui.nextAction(item).label,'调整方案');assert.equal(ui.nextAction(item).href,'/chat?task=t');assert.equal(ui.nextAction(item).secondary.href,'t/refill');assert.match(ui.nextAction(item).message,/当前有效 0、缺口 2，无法打包/);item.progress.nodes.pop();
item.progress.run_status='CANCELLED';assert.equal(ui.nextAction(item).kind,'stopped');
item.progress.run_status='COMPLETED';item.progress.nodes=[];assert.equal(ui.nextAction(item).kind,'finished');
item.progress.run_status='FAILED';assert.equal(ui.nextAction(item).label,'查看失败原因');
const card=new El();card._asset={title:'<script>unsafe</script>',url:'/authorized-preview',lines:()=>['unknown，未入选','版本 v1']};card.parentElement={children:[card]};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 responder=async()=>({ok:true,blob:async()=>({type:'image/jpeg',size:12})});ui.openAssetViewer(card);await tick();
 assert.equal($('asset-title').textContent,'<script>unsafe</script>');assert.equal($('asset-image').children[0].src,'blob:image');assert.equal($('asset-next').disabled,true);assert.equal(requests[0].options.cache,'no-store');
 ui.closeAssetViewer();assert.deepEqual(revoked,['blob:image']);assert.equal($('asset-image').children.length,0);assert.equal(document.activeElement.focused,true);
 let resolve;responder=()=>new Promise(r=>resolve=r);ui.openAssetViewer(card);ui.closeAssetViewer();resolve({ok:true,blob:async()=>({type:'image/png',size:10})});await tick();assert.equal($('asset-image').children.length,0,'Late image cannot reopen a closed viewer');
 responder=async()=>({ok:false});ui.openAssetViewer(card);await tick();assert.equal($('asset-image').children[1].textContent,'重试图片');ui.closeAssetViewer();
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': EXPERIENCE_JS},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
