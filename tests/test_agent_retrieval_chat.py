import os
import shutil
import subprocess
import unittest

import test_agent_retrieval_window as fixtures
from dsh_retrieval.agent_retrieval_chat import AGENT_RETRIEVAL_CHAT_JS
from dsh_retrieval.chat_ui import CHAT_JS
from dsh_retrieval.adapters.harness.composition import build_retrieval_agent_jobs
from dsh_retrieval.agent_jobs import AgentJobDispatcher
from dsh_retrieval.integration_models import SearchHit, SearchPage


class AgentRetrievalChatTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AgentRetrievalWindowTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.node = shutil.which('node')
        self.assertIsNotNone(self.node, 'Node is required to verify the existing Chat scripts')

    def run_js(self, script, *, extra=None):
        env = {**os.environ, 'WINDOW_SOURCE': AGENT_RETRIEVAL_CHAT_JS, 'WINDOW_API': self.f.base,
               'CHAT_PREPARE': CHAT_JS[CHAT_JS.index('async function prepareTask(){'):CHAT_JS.index('function dialogueCommandScope(')]}
        if extra: env.update(extra)
        result = subprocess.run([self.node, '-e', script], env=env, capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_original_send_and_confirmation_cards_use_the_real_admitted_job_service(self):
        self.exercise_original_send()

    def exercise_original_send(self, *, expected_searches=1):
        script = r"""
const assert=require('node:assert/strict');
const state={},storage=new Map(),fields=new Map(),cards=[],paths=[];
function element(){return {value:'',checked:false,children:[],textContent:'',classList:{add(){},remove(){}},
 append(...args){this.children.push(...args)},replaceChildren(...args){this.children=args}}}
const $=id=>{if(!fields.has(id))fields.set(id,element());return fields.get(id)};
$('request-text').value='找 2 张客厅图片';
const store={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
const document={hidden:false,createElement:element};
async function api(path,options={}){
 paths.push([path,options.method||'GET']);
 const response=await fetch(process.env.WINDOW_API+path,{method:options.method||'GET',
  headers:{'Content-Type':'application/json','X-Tenant-Id':'tenant','X-User-Id':'owner','Idempotency-Key':options.keyValue||'node-read'},
  body:options.body?JSON.stringify(options.body):undefined});
 const value=await response.json();if(!response.ok){const error=Error(value.message);error.code=value.code;throw error}return value;
}
let sequence=0,controller,candidateOpens=0;
function card(title,rows,actions=[]){const value={title,rows,actions,children:[],append(...items){this.children.push(...items)},remove(){},querySelector(){return {remove(){}}}};cards.push(value);return value}
async function refresh(){const value=await api(`/v1/retrieval/tasks/${state.task.task_id}/workspace`);controller.renderAgentWindowWorkspace(value);return value}
async function openTask(id){return controller.openAgentWindowWorkspace(await api(`/v1/retrieval/tasks/${id}/workspace`))}
controller=new Function('state','$','api','setBusy','message','card','key','sessionStorage','localStorage','openTask',
 'stopTaskEvents','resetResultWorkspace','nodeHeader','document','openCandidateBrowser','refreshTaskSidebar','clearTimeout','setTimeout',
 process.env.WINDOW_SOURCE+';return {loadAgentWindowProfile,prepareAgentCandidateRequest,openAgentWindowWorkspace,renderAgentWindowWorkspace,showAgentWindowArtifacts};')(
 state,$,api,()=>{},()=>{},card,()=>`node-ui-${++sequence}`,store,store,openTask,()=>{},()=>{},()=>element(),document,
 ()=>{candidateOpens++},refresh,()=>{},()=>1);
const send=new Function('state','$','prepareAgentCandidateRequest',process.env.CHAT_PREPARE+';return prepareTask')(
 state,$,controller.prepareAgentCandidateRequest);
async function wait(phase){for(let i=0;i<150;i++){const value=await refresh();if(value.agent_job?.phase===phase)return value;await new Promise(r=>setTimeout(r,20))}throw Error('phase timeout '+phase)}
async function click(label){const action=state.agentJobCard.actions.find(a=>a[0]===label);assert(action,'missing '+label);await action[1]()}
(async()=>{
 await controller.loadAgentWindowProfile();assert.equal(state.agentWindowProfile.enabled,true);
 await send();assert(state.agentDraftCard);assert.equal(state.task,undefined);
 assert(!paths.some(([p])=>p==='/v1/retrieval/tasks'));
 const body=state.agentDraftRecord.body;assert.equal(body.query_spec.agent_requirements.processing.length,0);
 assert.equal(body.query_spec.refill_policy.mode,'input_only');assert(!body.query_spec.grouped_expansion);
 await state.agentDraftCard.actions[0][1]();await refresh();
 assert.equal(state.task.agent_job.confirmation.checkpoint,'planning');
 await click('生成检索词');await wait('retrieval');
 assert.equal(state.task.agent_job.confirmation.checkpoint,'retrieval');assert(state.task.agent_job.query_terms.length);assert(state.agentJobCard.rows.some(([name,value])=>name==='检索词'&&value===state.task.agent_job.query_terms[0]));await click('开始查找');
 await wait('candidates');assert.equal(state.task.agent_job.confirmation.checkpoint,'candidates');
 await click('查看图片');assert.equal(candidateOpens,1);
 await click('确认这些图片');await refresh();assert.equal(state.task.agent_job.status,'completed');
 assert.equal(state.task.progress.counts.accepted,null);assert.equal(state.task.candidate_count,Number(process.env.EXPECTED_HITS));
 if(state.task.agent_job.retrieval_progress)assert.equal(state.task.agent_job.retrieval_progress.candidate_count,1);
 await click('查看已确认图片');assert(cards.some(c=>c.title==='已发布候选清单'));
 const taskId=state.task.task_id,writeCount=paths.filter(([,m])=>m==='POST').length;
 state.task=null;await openTask(taskId);await refresh();
 assert.equal(state.task.agent_job.status,'completed');
 assert.equal(paths.filter(([,m])=>m==='POST').length,writeCount,'reopening must not confirm or start');
 assert(!paths.some(([p,m])=>m==='POST'&&/first-search|cleaning|refill|dialogue-command/.test(p)));
 const candidates=await api(`/v1/retrieval/tasks/${state.task.task_id}/candidates?limit=60&query=`);
 assert.equal(candidates.items.length,Number(process.env.EXPECTED_HITS));assert.equal(candidates.accepted_count,null);
 console.log('same Chat send/cards/candidate API completed');
})().catch(error=>{console.error(error);process.exit(1)});
"""
        self.run_js(script,extra={'EXPECTED_HITS':str(expected_searches)})
        self.assertEqual((self.f.f.encodings, self.f.f.searches, len(self.f.f.f.calls)), (1, expected_searches, 1))
        self.assertEqual(self.f.legacy_calls, [])

    def test_original_window_multi_page_confirmations_and_aggregate_artifacts(self):
        f=self.f.f;self.f.worker.close()
        jobs=build_retrieval_agent_jobs(planning=f.f.app,compiler=f.compiler,executor=f.executor,
            materializer=f.materializer,manifests=f.manifests,batch_size=2,max_pages=3)
        worker=AgentJobDispatcher(jobs,start=True,poll_seconds=.05);self.addCleanup(worker.close)
        self.f.window.jobs=jobs;self.f.window.workflow=jobs.registry.capability('retrieval');self.f.window.dispatcher=worker
        self.f.server.RequestHandlerClass.agent_jobs=jobs
        def search(context,request):
            f.searches+=1
            return SearchPage((SearchHit(request.binding,'synthetic-vector',.9),),
                'next' if request.cursor is None else None,'more_available' if request.cursor is None else 'exhausted','snapshot','snapshot')
        f.search=search
        self.exercise_original_send(expected_searches=2)

    def test_count_scope_is_not_silently_reduced_or_reinterpreted(self):
        self.run_js(r"""
const assert=require('node:assert/strict');const state={};const fields={'target-count':{value:''}};
const count=new Function('state','$',process.env.WINDOW_SOURCE+';return agentRequestCount')(state,id=>fields[id]);
assert.equal(count('找 2 张猫图片',{max_candidates:2}),2);
for(const text of ['找 3 张猫图片','找猫图片','猫 1 张，狗 1 张'])assert.throws(()=>count(text,{max_candidates:2}));
state.targetCountTouched=true;fields['target-count'].value='1';assert.throws(()=>count('找 2 张猫图片',{max_candidates:2}));
""")

    def test_planning_wait_and_deadline_are_explicit_without_inventing_terms(self):
        self.run_js(r"""
const assert=require('node:assert/strict');
const describe=new Function(process.env.WINDOW_SOURCE+';return agentWindowPresentation')();
const job={status:'running',phase:'planning',query_terms:[],planning_progress:{elapsed_seconds:75,max_seconds:120,deadline_reached:false}};
assert.equal(describe({agent_job:job}).title,'正在生成检索词');assert(describe({agent_job:job}).text.includes('75 秒'));
job.status='unknown';job.planning_progress.deadline_reached=true;
assert.equal(describe({agent_job:job}).title,'生成检索词超时');assert(describe({agent_job:job}).text.includes('不会自动重试'));
job.status='waiting_confirmation';job.confirmation={checkpoint:'retrieval'};job.query_terms=['一只骆驼'];
assert.equal(describe({agent_job:job}).title,'检索词已准备好');
""")

    def test_late_draft_or_confirmation_does_not_reopen_previous_chat(self):
        self.run_js(r"""
const assert=require('node:assert/strict');
async function scenario(confirm){
 const state={historyOpenToken:1,agentWindowProfile:{enabled:true,available:true,max_candidates:5,scope_ref:'scope',actions:[],methods:[],library_ids:[]}};
 const fields=new Map(),$=id=>{if(!fields.has(id))fields.set(id,{value:'',checked:false});return fields.get(id)};
 let resolve,opens=0,cards=0;const pending=new Promise(r=>resolve=r);
 const store={getItem:()=>null,setItem(){},removeItem(){}};
 const ui=new Function('state','$','api','setBusy','message','card','key','sessionStorage','openTask',
 process.env.WINDOW_SOURCE+';return {prepareAgentCandidateRequest,confirmAgentCandidateTask}')(state,$,()=>pending,()=>{},()=>{cards++},()=>{cards++},()=> 'key',store,()=>{opens++});
 const work=confirm?ui.confirmAgentCandidateTask({draft:{draft_id:'old',draft_version:1,proposal_digest:'digest'}}):ui.prepareAgentCandidateRequest('骆驼5张');
 state.historyOpenToken=2;state.task=null;
 resolve(confirm?{task_id:'old-task'}:{draft_id:'old',proposal:{query_spec:{raw_request:'骆驼5张'}}});await work;
 assert.equal(opens,0);assert.equal(cards,0);assert.equal(state.task,null);assert.equal(state.draft,undefined);
}
(async()=>{await scenario(false);await scenario(true)})().catch(e=>{console.error(e);process.exit(1)});
""")

    def test_unknown_state_uses_plain_message_and_collapsed_diagnostics(self):
        self.run_js(r"""
const assert=require('node:assert/strict');
const fields=new Map(),cards=[];
const element=()=>({children:[],classList:{remove(){}},append(...items){this.children.push(...items)},replaceChildren(){}});
const $=id=>{if(!fields.has(id))fields.set(id,element());return fields.get(id)};
const state={task:{task_id:'task'},agentActionBusy:false};
const card=(title,rows,actions)=>{const value={...element(),title,rows,actions};cards.push(value);return value};
const ui=new Function('state','$','card','document','resetResultWorkspace','nodeHeader',
 process.env.WINDOW_SOURCE+';return {renderAgentWindowWorkspace,agentWindowPresentation}')(state,$,card,{createElement:element},()=>{},element);
ui.renderAgentWindowWorkspace({task_id:'task',agent_retrieval:true,target_count:5,candidate_count:0,
 progress:{counts:{requested:5},nodes:[]},agent_job:{job_ref:'job',status:'unknown',code:'AGENT_PLANNING_UNKNOWN',
 retrieval_progress:{page_count:0,raw_hit_count:0,candidate_count:0}}});
const current=cards[0];assert.equal(current.title,'这次查找已暂停');
assert(!JSON.stringify(current.rows).includes('AGENT_PLANNING_UNKNOWN'));
assert(!JSON.stringify(current.rows).includes('0 页'));
const details=current.children[0];assert.equal(details.open,undefined);assert.equal(details.children[0].textContent,'查看详情');
assert(JSON.stringify(details).includes('AGENT_PLANNING_UNKNOWN'));
assert(current.actions.some(([label])=>label==='新建对话'));
assert(!fields.get('task-sidebar-summary').textContent.includes('undefined'));
""")

    def test_conversation_entry_preserves_clarification_terms_questions_and_edits(self):
        source = CHAT_JS[CHAT_JS.index('async function prepareTask(){'):CHAT_JS.index('async function reviseGuidedExpansion(')]
        a=source.index('function dialogueCommandScope(');b=source.index('async function applyDialogueTurn(')
        source=source[:a]+"function pendingDialogueCommand(){return null} async function sendDialogueCommand(body,selection,signal){return api('/dialogue-command',{body,signal})}"+source[b:]
        self.run_js(r"""
const assert=require('node:assert/strict');
const state={agentWindowProfile:{enabled:true,chat_entry:'conversation',model_profile_ref:'agent-only-model'},agentWindowError:'agent unavailable',agentWindowLoading:true};
const fields={'request-text':{value:'找骆驼图片'},'llm-model':{value:'user-model'},'vlm-model':{value:'user-vision'},'target-count':{value:''},status:{},'stop-request':{classList:{add(){},remove(){}}}};
let calls=0,expansions=0,revisions=0,agent=0;const messages=[];
const api=async(path,{body})=>{
 assert.equal(path,'/dialogue-command');assert.equal(body.llm_id,'user-model');assert.equal(body.generate_terms_when_complete,true);calls++;
 if(calls===1)return {session_id:'dialogue',version:1,stage:'request',action:'clarify',reply:'需要多少张？',request:{}};
 if(calls===2)return {session_id:'dialogue',version:2,stage:'terms',action:'update',request:{effective_request:'找5张骆驼图片',target_count:5}};
 assert.equal(body.current_target_count,5);assert.equal(body.current_groups[0].primary_label,'骆驼');
 return {session_id:'dialogue',version:calls,stage:'terms',action:calls===3?'answer':'revise_terms',reply:'覆盖不同场景。',request:{target_count:5},expansion_instruction:'增加两个场景'};
};
const expand=async(raw,count,signal,selection)=>{expansions++;assert.equal(count,5);assert.equal(selection.llm,'user-model');state.proposedExpansion={raw,count,lines:'骆驼 | 沙漠中的骆驼，行走的骆驼',plan:{groups:[]}}};
const send=new Function('state','$','api','message','setDialogueBusy','saveDialogueSession','groupInputBody','prepareGuidedExpansion','reviseGuidedExpansion','prepareAgentCandidateRequest',
 process.env.CONVERSATION_SOURCE+';return prepareTask')(state,id=>fields[id],api,(...args)=>messages.push(args),()=>{},()=>{},()=>({groups:[{primary_label:'骆驼',auxiliary_terms:['沙漠中的骆驼','行走的骆驼']}]}),expand,async()=>{revisions++},()=>agent++);
(async()=>{
 await send();assert.equal(expansions,0);assert(messages.some(m=>m[2]==='需要多少张？'));
 fields['request-text'].value='5张';await send();assert.equal(expansions,1);assert.equal(state.dialogueId,'dialogue');
 fields['request-text'].value='为什么这样写？';await send();assert.equal(expansions,1);assert.equal(revisions,0);
 fields['request-text'].value='增加两个场景';await send();assert.equal(revisions,1);assert.equal(agent,0);assert.equal(state.task,undefined);
})().catch(e=>{console.error(e);process.exit(1)});
""", extra={'CONVERSATION_SOURCE':source})

    def test_enabled_agent_api_does_not_take_over_normal_chat_or_block_it_on_failure(self):
        self.run_js(r"""
const assert=require('node:assert/strict');
for(const profile of [{enabled:true,chat_entry:'conversation'},{enabled:true},null]){
 const state={agentWindowProfile:profile,agentWindowLoading:true,agentWindowError:'agent unavailable'};
 const fields={'request-text':{value:'找骆驼图片，5张'},'llm-model':{value:''},status:{}};
 let agent=0,messages=[];
 const send=new Function('state','$','prepareAgentCandidateRequest','pendingDialogueCommand','message',
 process.env.CHAT_PREPARE+';return prepareTask')(state,id=>fields[id],()=>agent++,()=>null,(...args)=>messages.push(args));
 send();
 assert.equal(agent,0);assert.equal(messages.length,1);assert(messages[0][2].includes('选择 LLM'));
}
""")

    def test_default_term_review_discloses_and_passes_execution_policy_and_rejects_stale_policy(self):
        source=CHAT_JS[CHAT_JS.index('function showTermReview('):CHAT_JS.index('async function prepareGuidedExpansion(')]
        self.run_js(r"""
const assert=require('node:assert/strict');
const policy={policy_digest:'sha256:'+'a'.repeat(64),max_calls:8,max_hit_slots:32};
const proposal={raw:'狗图片5张',count:5,lines:'狗 | 户外的狗',plan:{groups:[]},version:1};
const state={proposedExpansion:proposal,agentWindowProfile:{confirmed_terms_execution:policy,library_ids:['dogs']}};
const actions=[],messages=[],details=[],confirmed=[];
const show=new Function('state','$','message','reviewDetails','guidedAction','refillFormValue','refillSummary','confirmGuidedExpansion',
 process.env.REVIEW_SOURCE+';return showTermReview')(state,()=>({checked:false}),(...args)=>{messages.push(args);return {querySelector(){return {remove(){}}}}},
 (bubble,label,text)=>details.push(text),(bubble,label,handler)=>actions.push({label,handler}),
 ()=>({enabled:false}),()=>'',async(...args)=>confirmed.push(args));
(async()=>{
 show(proposal);assert(messages[0][2].includes('检索 Agent 按这份完整词表'));assert(details[0].includes('8 次检索、32 条原始命中'));
 const button=actions.find(a=>a.label==='确认检索词并开始检索');await button.handler();assert.equal(confirmed.length,1);assert.equal(confirmed[0][3],policy);
 confirmed.length=0;state.agentWindowProfile.confirmed_terms_execution={...policy,policy_digest:'sha256:'+'b'.repeat(64)};
 await button.handler();assert.equal(confirmed.length,0);assert(messages.length>1);
})().catch(e=>{console.error(e);process.exit(1)});
""",extra={'REVIEW_SOURCE':source})

    def test_disabled_project_send_keeps_the_legacy_dialogue_entry(self):
        self.run_js(r"""
const assert=require('node:assert/strict');const state={agentWindowProfile:{enabled:false}};
const fields={'request-text':{value:'找图片'},'llm-model':{value:''},status:{}};let agent=0,messages=0;
const send=new Function('state','$','prepareAgentCandidateRequest','pendingDialogueCommand','message',
 process.env.CHAT_PREPARE+';return prepareTask')(state,id=>fields[id],()=>agent++,()=>null,()=>messages++);
(async()=>{await send();assert.equal(agent,0);assert.equal(messages,1)})().catch(e=>{console.error(e);process.exit(1)});
""")
