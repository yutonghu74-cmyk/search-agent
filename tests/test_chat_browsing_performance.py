import os
import shutil
import subprocess
import unittest
from dsh_retrieval.chat_ui import CHAT_JS

@unittest.skipUnless(shutil.which('node'), 'Node required')
class ChatBrowsingPerformanceTest(unittest.TestCase):
    def run_js(self, source, script):
        result=subprocess.run(['node','-e',script],env={**os.environ,'SOURCE':source},capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_event_bursts_coalesce_and_only_changed_outputs_reload(self):
        source=CHAT_JS[CHAT_JS.index('function stopTaskEvents()'):CHAT_JS.index('async function pollTaskEvents()')]
        self.run_js(source,r'''
const assert=require('node:assert/strict');
const state={task:{task_id:'t'},eventTaskId:'t',eventSequence:9,currentResults:{available:true}};
let reads=[],renders=0,pending=null,release;
const api=async path=>{reads.push(path);if(path.endsWith('/summary')){if(pending){await pending;pending=null}return {task_id:'t',task_version:3,progress:{nodes:[]}}}return {available:true}};
const ui=new Function('state','api','showProgress','showResults','$',process.env.SOURCE+';return {scheduleTaskEventRefresh,stopTaskEvents}')(state,api,()=>{},()=>renders++,()=>({}));
const wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 for(let i=0;i<100;i++)ui.scheduleTaskEventRefresh('t',{payload:{output_versions:{cleaning:'c1',packaging:'p1'}}});await wait(260);
 assert.equal(reads.filter(p=>p.includes('/results?')).length,1);assert.equal(renders,1);
 reads=[];for(let i=0;i<100;i++)ui.scheduleTaskEventRefresh('t',{payload:{output_versions:{cleaning:'c1',packaging:'p1',retrieval:'r2'}}});await wait(260);
 assert.deepEqual(reads,['/v1/retrieval/tasks/t/summary']);assert.equal(renders,1);
 pending=new Promise(r=>release=r);ui.scheduleTaskEventRefresh('t');await wait(220);ui.stopTaskEvents();state.task={task_id:'other'};release();await wait(30);assert.equal(renders,1);
})().catch(e=>{console.error(e);process.exitCode=1;ui.stopTaskEvents()});
''')

    def test_history_page_replaces_dom_and_discards_late_response(self):
        source=CHAT_JS[CHAT_JS.index('let historyEpoch='):CHAT_JS.index('\nfunction ',CHAT_JS.index('let historyEpoch='))]
        self.run_js(source,r'''
const assert=require('node:assert/strict');class El{constructor(){this.children=[]}append(...xs){this.children.push(...xs)}replaceChildren(){this.children=[]}}
const box=new El(),pager=new El(),prev=new El(),next=new El(),label=new El(),state={task:null};const fields={'history-list':box,'history-pager':pager,'history-prev':prev,'history-next':next,'history-page-label':label};let late,release,requests=[];
const api=async path=>{requests.push(path);const q=new URL(path,'http://test').searchParams;const p=Number(q.get('cursor')||0);if(late){await new Promise(r=>release=r);late=false}return {items:Array.from({length:20},(_,i)=>({id:'dlg_'+(p*20+i),kind:'dialogue',title:'Chat',created_at:'2026-01-01',message_count:2})),next_cursor:p<99?String(p+1):null}};
const historyEntry=x=>x;
const load=new Function('document','$','state','api','savedChatNames','chatStarted','currentDialogueSessionId','updateChatHeader','historyEntry','openDialogueSession','openConversation','openTask','deleteHistoryChat',process.env.SOURCE+';return refreshHistory')({createElement:()=>new El()},id=>fields[id],state,api,()=>({}),x=>x,()=>null,()=>{},historyEntry,()=>{},()=>{},()=>{},()=>{});
(async()=>{await load();assert.equal(box.children.length,20);assert.equal(pager.hidden,false);assert.equal(label.textContent,'第 1 页');for(let p=1;p<100;p++){await next.onclick();assert.equal(box.children.length,20)}assert.equal(next.disabled,true);await prev.onclick();assert.equal(box.children[0].id,'dlg_1960');late=true;const old=load();await new Promise(r=>setImmediate(r));late=false;await load('5',5);release();await old;assert.equal(box.children[0].id,'dlg_100');assert(requests.every(p=>p.includes('chat-history?limit=20')));})().catch(e=>{console.error(e);process.exitCode=1});
''')

    def test_task_text_precedes_heavy_reads_and_stale_open_is_discarded(self):
        source=CHAT_JS[CHAT_JS.index('async function openTask(taskId)'):CHAT_JS.index('\nfunction searchUnavailableReason')]
        self.run_js(source,r"""
const assert=require('node:assert/strict');const state={};let heavy=[],requests=[],texts=[];
const timeline={dataset:{}},el={textContent:'',classList:{add(){},remove(){}}};
const api=async path=>{requests.push(path);if(path.endsWith('/summary'))return {task_id:'t',raw_request:'需求文字',progress:{nodes:[]}};return new Promise(r=>heavy.push(r))};
const ui=new Function('state','api','performance','timeline','$','setBusy','showHistoryLoading','resetTimeline',process.env.SOURCE+';return openTask')(state,api,performance,timeline,()=>el,()=>{},()=>{},(title,text)=>texts.push(text));
(async()=>{const opening=ui('t');await new Promise(r=>setImmediate(r));assert.deepEqual(texts,['需求文字']);assert(Number(timeline.dataset.firstTextMs)<100);assert(requests.includes('/v1/retrieval/tasks/t/revisions'));state.historyOpenToken++;heavy.forEach(r=>r({}));assert.equal(await opening,false);assert.equal(state.task,undefined)})().catch(e=>{console.error(e);process.exitCode=1});
""")

    def test_history_summary_is_visible_while_full_conversation_is_pending(self):
        source=CHAT_JS[CHAT_JS.index('async function openConversation('):CHAT_JS.index('\nfunction confirmedQuery')]
        self.run_js(source,r"""
const assert=require('node:assert/strict');const state={},timeline={dataset:{}},el={textContent:''};let texts=[],paths=[];
const open=new Function('state','timeline','performance','$','setBusy','showHistoryLoading','resetTimeline','api',process.env.SOURCE+';return openConversation')(state,timeline,performance,()=>el,()=>{},()=>{},(title,text)=>texts.push([title,text]),path=>{paths.push(path);return new Promise(()=>{})});
open('c','已经授权读取的历史需求');assert.deepEqual(texts,[['历史需求摘要','已经授权读取的历史需求']]);assert.equal(timeline.dataset.firstTextSource,'history-summary');assert(Number(timeline.dataset.firstTextMs)<100);assert.deepEqual(paths,['/v1/retrieval/conversations/c']);assert.equal(timeline.dataset.fullTextMs,undefined);
""")
