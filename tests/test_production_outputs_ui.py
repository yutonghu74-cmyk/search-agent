import os
import shutil
import subprocess
import unittest

from dsh_retrieval.production_board_ui import BOARD_JS, BOARD_CSS
from dsh_retrieval.production_outputs_ui import OUTPUT_JS


class ProductionOutputsTest(unittest.TestCase):
    def test_separate_navigation_and_readonly_outputs(self):
        self.assertIn('body.task-page', BOARD_CSS)
        self.assertIn('node.href=nodeURL(item.task_id,key)', BOARD_JS)
        self.assertIn('box.href=nodeURL(item.task_id,node.node)', BOARD_JS)
        self.assertIn("stopped?'已停止'", BOARD_JS)
        self.assertIn('SHA-256 完全一致自动去重', BOARD_JS)
        self.assertNotIn('近重复待复核', BOARD_JS)
        self.assertIn('if(selected)loadDetailPage();else load()', BOARD_JS)
        self.assertNotIn('innerHTML', OUTPUT_JS)
        self.assertNotIn("method:'POST'", OUTPUT_JS)
        self.assertIn('result.counts?.gap', OUTPUT_JS)
        self.assertIn('data.manifest?.members', OUTPUT_JS)
        self.assertIn('member.retrieval_sources', OUTPUT_JS)
        self.assertIn('SHA-256 精确重复副本', OUTPUT_JS)
        self.assertIn('SHA-256 完全一致自动合并，无需人工复核', OUTPUT_JS)
        self.assertIn('data.manifest?.eligible_unique_count', OUTPUT_JS)
        self.assertIn('data.manifest?.exact_duplicate_count', OUTPUT_JS)
        self.assertIn('data.manifest?.packaged_file_count', OUTPUT_JS)
        self.assertNotIn('疑似近重复', OUTPUT_JS)
        self.assertIn("api(base+'/refill')", OUTPUT_JS)
        self.assertIn('新增唯一有效', OUTPUT_JS)
        self.assertIn('批准素材库', OUTPUT_JS)
        self.assertIn('retry.onclick=()=>loadDetailPage()', OUTPUT_JS)
        self.assertNotIn('retry.onclick=loadDetailPage()', OUTPUT_JS)

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_executable_expansion_search_and_navigation(self):
        script = r'''
const assert=require('node:assert/strict');
class El{
 constructor(tag='div'){this.tag=tag;this.children=[];this.textContent='';this.value='';this.isConnected=true;this.dataset={}}
 append(...items){this.children.push(...items)}
 insertBefore(el,before){this.children.splice(this.children.indexOf(before),0,el)}
 replaceChildren(...items){this.children=items}
 setAttribute(key,value){this[key]=value}
 get options(){return this.children}
 querySelectorAll(tag){return this.children.flatMap(x=>[...(x.tag===tag?[x]:[]),...x.querySelectorAll(tag)])}
}
const document={createElement:tag=>new El(tag)},window={addEventListener(){}};
const location={assign(url){this.url=url}},text=el=>[el.textContent,...el.children.map(text)].join('\n');
let handler;const api=path=>handler(path);
const source=process.env.SOURCE.replace('startProduction();','');
const ui=new Function('document','window','location','api',source+';return {confirmedSpec,renderExpansion,mountSearch,selectLine,outputPage};')(document,window,location,api);
const record={revision:2,proposal_digest:'digest',proposal:{query_spec:{raw_request:'dog',grouped_expansion:{plan_digest:'plan',groups:[{primary_label:'狗',auxiliary_terms:['<script>alert(1)</script>','牧羊犬'],package_name:'dog'}]}}}};
assert.equal(ui.confirmedSpec({items:[record]},2),record);
assert.throws(()=>ui.confirmedSpec({items:[record]},1),/正式版本/);
const expansion=new El();ui.renderExpansion(expansion,record);assert.match(text(expansion),/牧羊犬/);assert.match(text(expansion),/<script>/);assert.equal(expansion.querySelectorAll('script').length,0);
ui.selectLine('a&b');assert.equal(location.url,'/production?task=a%26b&node=expansion');
let calls=[];handler=async path=>{calls.push(path);return {search_version:'v1',raw_hit_count:3,query_terms:['狗'],next_cursor:calls.length===1?'cursor':null,items:[{candidate_id:'c1',asset_version:'v1',status:'resolved',query_text:calls.length===1?'狗':'牧羊犬',library_id:'lib'}]}};
const root=new El();ui.mountSearch(root,'t',0);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{await tick();assert.match(text(root),/狗/);const more=root.children.find(el=>el.tag==='button');await more.onclick();await tick();assert.match(calls[1],/cursor=cursor/);assert.equal(root.querySelectorAll('article').length,1);assert.match(text(root),/牧羊犬/);const cardEl=root.querySelectorAll('article')[0];assert.equal(cardEl.querySelectorAll('p').length,1);assert.match(cardEl.querySelectorAll('p')[0].textContent,/检索词：狗[\s\S]*检索词：牧羊犬/);assert.equal(more.hidden,true);assert.doesNotMatch(text(cardEl),/c1|resolved|来源：/);assert.match(cardEl._asset.lines().join(' '),/来源：lib/);
handler=async()=>{throw Error('offline')};const select=root.querySelectorAll('select')[0];select.value='狗';select.onchange();await tick();assert.match(text(root),/不代表尚未开始/);assert.equal(root.querySelectorAll('article').length,1);
let resolve;handler=()=>new Promise(r=>resolve=r);select.onchange();ui.outputPage.epoch++;resolve({items:[],search_version:'v2'});await tick();assert.equal(root.querySelectorAll('article').length,1);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': OUTPUT_JS}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_automatic_thumbnail_and_paired_scores(self):
        script = r'''
const assert=require('node:assert/strict');
class El {
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.isConnected=true}
 append(...items){this.children.push(...items)}
 replaceChildren(...items){this.children=items}
 setAttribute(key,value){this[key]=value}
}
const document={createElement:tag=>new El(tag)},window={addEventListener(){}};
let requests=[],reply=async()=>({ok:true,blob:async()=>({type:'image/jpeg',size:100})}),opened;
const fetch=async(url,options)=>{requests.push({url,options});return reply()};
let revoked=[];const URL={createObjectURL:()=> 'blob:test',revokeObjectURL:url=>revoked.push(url)};
const ui=new Function('document','window','fetch','URL','headers','openAssetViewer',process.env.SOURCE+';return {createSearchCard,addSearchHit,outputPage};')(document,window,fetch,URL,{'X-User-Id':'test'},card=>opened=card);
ui.outputPage.abort=new AbortController();
const item={candidate_id:'private-id',media_unit_id:'media-id',asset_version:'v1',status:'resolved',preview_status:'available',preview_ref:'signed',query_text:'狗',score:0.480340058,library_id:'lib'};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const grid=new El('div'),card=ui.createSearchCard(grid,'task',item,0);ui.addSearchHit(card,item);await tick();
 assert.equal(requests.length,1);assert.equal(requests[0].options.cache,'no-store');assert.equal(requests[0].options.headers['X-User-Id'],'test');
 const thumb=card.children[0];assert.equal(thumb.children[0].src,'blob:test');assert.equal(thumb.disabled,false);thumb.onclick();assert.equal(opened,card);
 ui.addSearchHit(card,{...item,query_text:'猫',score:0.32});ui.addSearchHit(card,item);
 assert.equal(card._sources.size,2);assert.match(card._hitLine.textContent,/狗\n分数：0.4803\n\n检索词：猫\n分数：0.3200/);
 assert.doesNotMatch(card._hitLine.textContent,/private-id|media-id|lib|resolved/);assert.match(card._asset.lines().join(' '),/media-id.*v1.*resolved.*lib/);assert.match(card._asset.lines().join(' '),/0.480340058/);
 reply=async()=>({ok:false});const failed=ui.createSearchCard(grid,'task',item,0);await tick();assert.match(failed.children[0].textContent,/点击重试/);assert.equal(failed.children[0].disabled,false);
 reply=async()=>({ok:true,blob:async()=>({type:'image/png',size:100})});await failed.children[0].onclick();assert.equal(failed.children[0].children[0].src,'blob:test');
 let resolve;reply=()=>new Promise(r=>resolve=r);const stale=ui.createSearchCard(grid,'task',item,0);stale.isConnected=false;resolve({ok:true,blob:async()=>({type:'image/jpeg',size:100})});await tick();assert.equal(stale.children[0].children.length,0);
 const count=requests.length;const missing=ui.createSearchCard(grid,'task',{...item,preview_status:'unavailable'},0);await tick();assert.equal(requests.length,count);assert.match(missing.children[0].textContent,/暂无/);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': OUTPUT_JS},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_bundle_syntax(self):
        result = subprocess.run(['node', '--check'], input=BOARD_JS, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
