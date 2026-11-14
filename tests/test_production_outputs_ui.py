import os
import shutil
import subprocess
import unittest

from dsh_retrieval.production_board_ui import BOARD_JS, BOARD_CSS
from dsh_retrieval.production_outputs_ui import OUTPUT_JS


class ProductionOutputsTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_request_number_is_stable_and_searchable_without_changing_task_id(self):
        source = BOARD_JS[BOARD_JS.index('function requestNumber('):BOARD_JS.index('function updateRow(')]
        script = r'''
const assert=require('node:assert/strict');
const values={'line-search':'','line-filter':'','line-node-filter':'','line-target-min':'','line-gap-filter':''};
const $=id=>({value:values[id]});
const ui=new Function('$',process.env.SOURCE+';return {requestNumber,visible};')($);
const item={task_id:'rt_4218d6897a8644499d87357b4c33989c',created_at:'2026-11-02T15:32:59Z',title:'狗10张，兔子5张'};
assert.equal(ui.requestNumber(item),'20261102-2332-4C33989C');
assert.equal(ui.requestNumber({...item,created_at:'2026-11-02T23:32:59+08:00'}),ui.requestNumber(item));
assert.equal(ui.requestNumber({...item,created_at:'2026-11-02T16:00:00Z'}),'20261103-0000-4C33989C');
assert.equal(ui.requestNumber({...item,created_at:null}),'时间未知-4C33989C');
assert.equal(ui.requestNumber({...item,created_at:'invalid'}),'时间未知-4C33989C');
values['line-search']='20261102-2332-4c33989c';assert(ui.visible(item));
values['line-search']=item.task_id;assert(ui.visible(item));values['line-search']='兔子';assert(ui.visible(item));
values['line-search']='not-found';assert(!ui.visible(item));
assert.equal(item.task_id,'rt_4218d6897a8644499d87357b4c33989c');
'''
        for timezone in ('UTC', 'America/New_York'):
            result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source, 'TZ': timezone},
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_task_pagination_replaces_pages_and_preserves_failed_page(self):
        source = BOARD_JS[BOARD_JS.index('async function loadTaskPage('):BOARD_JS.index("$('line-search').oninput")]
        script = r'''
const assert=require('node:assert/strict');
let fail=false,hold,requests=[];
const api=async path=>{requests.push(path);if(fail)throw Error('offline');if(hold)await hold;const page=Number(new URL(path,'http://test').searchParams.get('cursor')||0);return {items:Array.from({length:page===2?3:20},(_,i)=>({task_id:`t${page}-${i}`,task_version:1,progress:{task_version:1}})),next_cursor:page<2?String(page+1):null}};
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,{textContent:'',closest:()=>({scrollTo(){}})});return elements.get(id)};
const ui=new Function('api','$',`
let rows=[],listEpoch=0,nextTaskCursor=null,selected=null;
const taskPaging={index:0,cursors:[null],busy:false};
const updateTaskPager=()=>{},render=()=>{},updateRow=()=>{};
${process.env.SOURCE}
return {load,moveTaskPage,taskPaging,rows:()=>rows,next:()=>nextTaskCursor};`)(api,$);
(async()=>{
await ui.load();assert.equal(ui.rows().length,20);assert.equal(ui.taskPaging.index,0);
await ui.moveTaskPage(1);assert.equal(ui.rows().length,20);assert.equal(ui.rows()[0].task_id,'t1-0');assert.equal(ui.taskPaging.index,1);
fail=true;await ui.moveTaskPage(1);assert.equal(ui.rows()[0].task_id,'t1-0');assert.equal(ui.taskPaging.index,1);assert.match($('line-status').textContent,/保留当前页/);assert.equal(ui.taskPaging.busy,false);
fail=false;await ui.moveTaskPage(1);assert.equal(ui.rows().length,3);assert.equal(ui.next(),null);const count=requests.length;await ui.moveTaskPage(1);assert.equal(requests.length,count);
await ui.moveTaskPage(-1);assert.equal(ui.rows()[0].task_id,'t1-0');await ui.moveTaskPage(-1);assert.equal(ui.rows()[0].task_id,'t0-0');const firstCount=requests.length;await ui.moveTaskPage(-1);assert.equal(requests.length,firstCount);
let resolve;hold=new Promise(r=>resolve=r);const pending=ui.moveTaskPage(1);const running=requests.length;await ui.moveTaskPage(1);assert.equal(requests.length,running);resolve();await pending;hold=null;
await ui.load();assert.equal(ui.taskPaging.index,0);assert.deepEqual(ui.taskPaging.cursors,[null]);assert.equal(ui.rows().length,20);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_delivery_publication_download_and_refill_summary(self):
        source = OUTPUT_JS[OUTPUT_JS.index('function outputDisclosure('):OUTPUT_JS.index('async function showNodeOutput')]
        script = r'''
const assert=require('node:assert/strict');
class El{constructor(tag){this.tag=tag;this.children=[];this.textContent=''}append(...xs){this.children.push(...xs)}replaceChildren(...xs){this.children=xs;this.textContent=''}setAttribute(k,v){this[k]=v}click(){this.clicked=true}}
const document={createElement:tag=>new El(tag),createTextNode:text=>Object.assign(new El('text'),{textContent:text})};
const outputText=(parent,tag,text)=>{const el=new El(tag);el.textContent=text;parent.append(el);return el};
const walk=el=>[el,...el.children.flatMap(walk)],text=el=>walk(el).map(x=>x.textContent).join('\n');
let images=0,request,reply={ok:true,blob:async()=>({size:100})};
const outputPage={epoch:1,abort:new AbortController(),urls:new Set()};
const ui=new Function('document','outputText','mountNodeItems','outputImage','refillStopText','nodeURL','headers','fetch','URL','outputPage',process.env.SOURCE+';return {renderPackaging,renderRefill,downloadOutputPackage}')(
 document,outputText,(parent,base,node,data,token,render)=>{const list=new El('div');parent.append(list);for(const item of data.items||[])render(list,item)},()=>images++,reason=>reason==='NO_PROGRESS'?'连续补数没有新增有效结果':reason,(task,node)=>task+'/'+node,{'X-User-Id':'user'},async(url,options)=>{request={url,options};return reply},{createObjectURL:()=> 'blob:zip'},outputPage);
const data={available:true,status:'published',package_id:'package_test',byte_count:1926599,manifest:{completion:'partial',accepted_count:10,target_count:15,gap:5,stop_reason:'NO_PROGRESS',groups:[{group_id:'dog',label:'狗',accepted_count:10,target_count:10,gap:0},{group_id:'rabbit',label:'兔子',accepted_count:0,target_count:5,gap:5}]},items:[{candidate_id:'candidate_test',file:'images/dog/0001.jpg',group_id:'dog',content_digest:'sha256:test',retrieval_sources:[]}]};
const item={task_id:'task',run_status:'PARTIAL',progress:{run_status:'PARTIAL',counts:{accepted:10}}};
(async()=>{
 const parent=new El('div');ui.renderPackaging(parent,'/base',item,data,1);assert.match(text(parent),/已部分交付/);assert.match(text(parent),/缺 5 张/);assert.match(text(parent),/连续补数没有新增有效结果/);assert.equal(images,1);
 const audit=walk(parent).find(x=>x.tag==='details');assert(!audit.open);assert.match(text(audit),/package_test/);
 const draft=new El('div');ui.renderPackaging(draft,'/base',item,{...data,status:'prepared'},1);assert(!walk(draft).some(x=>x.tag==='button'&&x.textContent.startsWith('下载')));assert.equal(images,1);
 const button=walk(parent).find(x=>x.tag==='button'&&x.textContent.startsWith('下载')),status=new El('p');await ui.downloadOutputPackage('/base',data,button,status,1);assert.equal(request.url,'/base/delivery/package_test/download');assert.equal(request.options.headers['X-User-Id'],'user');assert(status.children[0].clicked);assert.equal(button.disabled,false);
 reply={ok:false};await ui.downloadOutputPackage('/base',data,button,status,1);assert.match(status.textContent,/下载失败/);
 const refill=new El('div');ui.renderRefill(refill,'/base',item,{proposal:{query_spec:{refill_policy:{mode:'maintain_target',progression:'auto',approved_library_ids:['lib']}}}},{counts:{gap:5}},{stop_reason:'NO_PROGRESS',execution:{completed_rounds:2},item_count:0},1);assert.match(text(refill),/补数已结束/);assert.match(text(refill),/已完成 2 轮/);assert(!walk(refill).find(x=>x.tag==='details').open);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_detail_uses_summary_and_rejects_mixed_versions(self):
        source = OUTPUT_JS[OUTPUT_JS.index('async function loadDetailPage()'):
                           OUTPUT_JS.index('function startProduction()')]
        script = r"""
const assert=require('node:assert/strict');
const els=new Map(),$=id=>{if(!els.has(id))els.set(id,{dataset:{},classList:{remove(){}},replaceChildren(){}});return els.get(id)};
let requests=[],shown=0,version=7;
const task={task_id:'t',task_version:7,confirmed_revision:1,run_generation:1};
const api=async path=>{requests.push(path);if(path.endsWith('/read-binding'))return {task,proposal:{query_spec:{raw_request:'test'}}};if(path.endsWith('/summary'))return {progress:{task_version:version,revision:1,run_generation:1}};throw Error('unexpected '+path)};
const load=new Function('document','$','selected','api','showDetail','showNodeOutput','outputText','let detailEpoch=0;function releaseOutputs(){} function recordNodeReadable(){};'+process.env.SOURCE+';return loadDetailPage')({body:{classList:{add(){}}}},$,'t',api,()=>shown++,async()=>{},()=>({}));
(async()=>{await load();assert.equal(shown,1);assert.equal(requests.length,3);assert(!requests.some(p=>p.endsWith('/progress')));version=8;await load();assert.equal(shown,1);assert.match($('detail-status').textContent,/版本已变化/);})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result = subprocess.run(['node', '-e', script],
            env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_overview_with_persisted_progress_uses_one_request(self):
        source = BOARD_JS[BOARD_JS.index('async function loadTaskPage('):
                          BOARD_JS.index('async function load(){')]
        script = r'''
const assert=require('node:assert/strict');
let requests=[];
const load=new Function('api',`
let rows=[],listEpoch=1,nextTaskCursor=null,selected=null;
const $=()=>({classList:{toggle(){}}});
const render=()=>{},updateRow=()=>{},showDetail=()=>{};
${process.env.SOURCE}
return loadTaskPage;`)(async path=>{
 requests.push(path);
 assert.match(path,/projects\/demo\/tasks/);
 return {items:[{task_id:'task-1',task_version:2,progress:{task_version:2,nodes:[],counts:{}}}],next_cursor:null};
});
(async()=>{await load(1,null,true);assert.equal(requests.length,1)})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script],
            env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_node_pages_replace_items_and_preserve_page_on_version_conflict(self):
        source = OUTPUT_JS[OUTPUT_JS.index('function mountNodeItems('):OUTPUT_JS.index('async function showNodeOutput')]
        script = r"""
const assert=require('node:assert/strict');
class El {constructor(){this.children=[]}append(...xs){this.children.push(...xs)}replaceChildren(){this.children=[]}}
const document={createElement:()=>new El()},outputPage={epoch:1};
const outputText=(parent,tag,text)=>{const el=new El();el.textContent=text;parent.append(el);return el};
let version='v1',calls=0;
const data=p=>({output_version:version,item_count:2400,items:Array.from({length:24},(_,i)=>p*24+i),next_cursor:p<99?String(p+1):null});
const api=async path=>{calls++;return data(Number(new URL('http://test'+path).searchParams.get('cursor')||0))};
const mount=new Function('document','outputText','outputPage','api',process.env.SOURCE+';return mountNodeItems')(document,outputText,outputPage,api);
const parent=new El();mount(parent,'/task','refill',data(0),1,(list,item)=>outputText(list,'p',item));
const [list,controls]=parent.children,[prev,status,next]=controls.children;
(async()=>{for(let p=1;p<100;p++){await next.onclick();assert.equal(list.children.length,24);assert.equal(list.children[0].textContent,p*24)}assert.equal(calls,99);assert.equal(next.disabled,true);await prev.onclick();assert.equal(list.children[0].textContent,98*24);version='v2';await prev.onclick();assert.equal(list.children[0].textContent,98*24);assert.match(status.textContent,/版本已变化/);})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE':source}, capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_separate_navigation_and_readonly_outputs(self):
        self.assertIn('body.task-page', BOARD_CSS)
        self.assertIn('node.href=nodeURL(item.task_id,key)', BOARD_JS)
        self.assertIn('box.href=nodeURL(item.task_id,node.node)', BOARD_JS)
        self.assertIn("stopped?'已停止'", BOARD_JS)
        self.assertNotIn('数量未知不按零计算', BOARD_JS)
        self.assertNotIn('近重复待复核', BOARD_JS)
        self.assertIn('if(selected)loadDetailPage();else load()', BOARD_JS)
        self.assertNotIn('innerHTML', OUTPUT_JS)
        self.assertNotIn("method:'POST'", OUTPUT_JS)
        self.assertIn('result.counts?.gap', OUTPUT_JS)
        self.assertIn("mountNodeItems(parent,base,'packaging'", OUTPUT_JS)
        self.assertIn('member.retrieval_sources', OUTPUT_JS)
        self.assertIn('SHA-256 精确重复副本', OUTPUT_JS)
        self.assertNotIn('去重规则：SHA-256 完全一致自动合并，无需人工复核', OUTPUT_JS)
        self.assertIn('data.manifest?.eligible_unique_count', OUTPUT_JS)
        self.assertIn('data.manifest?.exact_duplicate_count', OUTPUT_JS)
        self.assertIn('data.manifest?.packaged_file_count', OUTPUT_JS)
        self.assertNotIn('疑似近重复', OUTPUT_JS)
        self.assertIn("api(base+'/nodes/refill/items?limit=24')", OUTPUT_JS)
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

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_same_task_node_navigation_preserves_links_and_history(self):
        source=OUTPUT_JS[OUTPUT_JS.index('function navigateProductionNode'):OUTPUT_JS.index("window.addEventListener('click',navigateProductionNode")]
        script=r"""
const assert=require('node:assert/strict');let loads=0,pushes=[],stamps=[],prevented=0;
const location={href:'http://app/production?task=t&node=expansion',origin:'http://app'};
const link={href:'http://app/production?task=t&node=refill',classList:{contains:()=>true}};
const event={button:0,target:{closest:()=>link},preventDefault(){prevented++}};
const navigate=new Function('location','selected','sessionStorage','history','loadDetailPage',process.env.SOURCE+';return navigateProductionNode')(location,'t',{setItem:(k,v)=>stamps.push(JSON.parse(v))},{pushState:(a,b,url)=>{pushes.push(url);location.href=url}},()=>loads++);
navigate({...event,ctrlKey:true});assert.equal(loads,0);
navigate(event);assert.equal(loads,1);assert.equal(prevented,1);assert.equal(pushes.length,1);assert.equal(stamps[0].path,'/production?task=t&node=refill');
navigate(event);assert.equal(loads,2);assert.equal(pushes.length,1);
link.href='http://app/production?task=other&node=refill';navigate(event);assert.equal(loads,2);assert.equal(prevented,2);
"""
        result=subprocess.run(['node','-e',script],env={**os.environ,'SOURCE':source},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_late_detail_response_does_not_replace_new_node(self):
        source=OUTPUT_JS[OUTPUT_JS.index('async function loadDetailPage()'):OUTPUT_JS.index('function startProduction()')]
        script=r"""
const assert=require('node:assert/strict');const els=new Map(),$=id=>{if(!els.has(id))els.set(id,{dataset:{},classList:{remove(){}},replaceChildren(){}});return els.get(id)};
let requests=0,release,shown=0;
const task={task_id:'t',task_version:1,confirmed_revision:1,run_generation:1},binding={task,proposal:{query_spec:{raw_request:'test'}}};
const api=async path=>{if(path.endsWith('/summary'))return {progress:{task_version:1,revision:1,run_generation:1}};if(++requests===1)return new Promise(r=>release=r);return binding};
const load=new Function('document','$','selected','api','showDetail','showNodeOutput','outputText','let detailEpoch=0;function releaseOutputs(){} function recordNodeReadable(){};'+process.env.SOURCE+';return loadDetailPage')({body:{classList:{add(){}}}},$,'t',api,()=>shown++,async()=>{},()=>({}));
(async()=>{const older=load();await load();assert.equal(shown,1);release(binding);await older;assert.equal(shown,1)})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result=subprocess.run(['node','-e',script],env={**os.environ,'SOURCE':source},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
