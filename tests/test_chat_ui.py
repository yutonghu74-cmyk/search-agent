import os
import shutil
import subprocess
import unittest

from dsh_retrieval.chat_ui import CHAT_CSS, CHAT_HTML, CHAT_JS
from dsh_retrieval.production_board_ui import BOARD_CSS, BOARD_HTML, BOARD_JS


class ChatUiTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for delegated candidate review choices')
    def test_term_confirmation_discloses_automatic_cleaning_and_optional_candidate_review(self):
        source = CHAT_JS[CHAT_JS.index('function showTermReview('):CHAT_JS.index('async function prepareGuidedExpansion(')]
        script = r"""
(async()=>{
const assert=require('node:assert/strict');let checkbox,selected,copy='';const actions=[];
const auto={schema_version:'confirmed-terms-execution@2',candidate_intake:'auto',policy_digest:'auto',max_calls:8,max_hit_slots:80};
const manual={...auto,candidate_intake:'manual',policy_digest:'manual'};
const proposal={count:20,lines:'狗',raw:'狗20张',plan:{groups:[{primary_label:'狗'}]}};
const state={proposedExpansion:proposal,agentWindowProfile:{confirmed_terms_execution:auto,candidate_review_execution:manual,library_ids:['dogs']}};
const document={createElement:tag=>{const item={append(){},addEventListener:(event,fn)=>item[event]=fn};if(tag==='input')checkbox=item;return item},createTextNode:text=>({textContent:text})};
const message=(role,title,text)=>{copy=text;return {append(){},querySelector(){return null}}};
const fields={'query-image-exclude':{checked:false},status:{}};
const show=new Function('state','document','$','message','reviewDetails','guidedAction','refillFormValue','refillSummary','confirmGuidedExpansion',process.env.SOURCE+';return showTermReview')(
 state,document,id=>fields[id],message,()=>{},(bubble,label,fn)=>actions.push({label,fn}),()=>({enabled:false}),()=>'',async(...args)=>selected=args[3]);
show(proposal);assert(copy.includes('现有清洗流程'));assert(copy.includes('图片会发送给本次选定的清洗模型'));
assert.equal(checkbox.checked,false);await actions[0].fn();assert.equal(selected,auto);
checkbox.checked=true;checkbox.change();await actions[0].fn();assert.equal(selected,manual);
assert(!copy.includes('清洗 Agent 已接通'));
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node','-e',script],env={**os.environ,'SOURCE':source},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for failed dialogue send recovery')
    def test_failed_dialogue_releases_new_input_but_uncertain_work_remains_pending(self):
        source = (CHAT_JS[CHAT_JS.index('function taskTurnStorageKey('):CHAT_JS.index('async function sendTaskTurn(')] +
                  CHAT_JS[CHAT_JS.index('function dialogueCommandScope('):CHAT_JS.index('async function reviseGuidedExpansion(')])
        script = r"""
const assert=require('node:assert/strict');
const state={dialogueId:'dlg_one',newChatToken:'token'};const saved=new Map(),headers={};
const sessionStorage={getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)};
let status='failed',posts=0,actions=[];
const api=async(path,options)=>{if(options?.method==='POST'){posts++;assert.equal(options.retryFailed,true);assert.equal(options.keyValue,'original-key');return {schema_version:'dialogue-command@1',status:'failed',error:{code:'MODEL_RATE_LIMIT',message:'429'}}}return {status,error:{message:'429'}}};
const funcs=new Function('state','headers','sessionStorage','key','api','setDialogueBusy','saveDialogueSession','restoreDialogueSession','message','guidedAction','$',process.env.SOURCE+';return {saveTaskTurn,pendingDialogueCommand,recoverDialogueCommand};')(
state,headers,sessionStorage,()=>{throw Error('must reuse key')},api,()=>{},()=>{},async()=>{},()=>({remove(){}}),(...a)=>actions.push(a),()=>({textContent:''}));
const pending={taskId:'dialogue:dlg_one',key:'original-key',body:{session_id:'dlg_one',message:'old'},selection:{llm:'original'}};
(async()=>{
 // Check the scope used by production rather than duplicating its format.
 pending.taskId='dialogue:'+state.dialogueId;
 funcs.saveTaskTurn(pending);assert(funcs.pendingDialogueCommand());
 for(status of ['understanding','interrupted']){
   assert.notEqual(await funcs.recoverDialogueCommand(false,true),true);
   assert(funcs.pendingDialogueCommand());assert.equal(state.requestController,null);
 }
 status='failed';await funcs.recoverDialogueCommand(true);assert.equal(posts,1);
 assert(funcs.pendingDialogueCommand());assert.equal(state.requestController,null);
 assert.equal(await funcs.recoverDialogueCommand(false,true),true);
 assert.equal(funcs.pendingDialogueCommand(),null);assert.equal(posts,1);
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for semantic draft page transition')
    def test_category_edit_regenerates_from_effective_draft_and_missing_count_stops_preview(self):
        source = CHAT_JS[CHAT_JS.index('async function applyDialogueTurn('):CHAT_JS.index('async function reviseGuidedExpansion(')]
        script = r"""
(async()=>{
const assert=require('node:assert/strict');
const proposal={count:60,raw:'旧兔子睡觉',plan:{groups:[{delivery_target:20}]}};
const state={proposedExpansion:proposal};let generated=[],revised=0,replies=[];
const apply=new Function('state','saveDialogueSession','message','prepareGuidedExpansion','reviseGuidedExpansion',process.env.SOURCE+';return applyDialogueTurn')(
 state,()=>{},(...args)=>replies.push(args),async(...args)=>generated.push(args),async()=>revised++);
const turn={action:'revise_terms',stage:'terms',session_id:'dlg_x',version:2,requirement_edits:[{op:'remove_category'}],
 request:{target_count:50,effective_request:'毛驴20张、狗30张'}};
await apply(turn,proposal,'兔子不要了',{llm:'selected'},null);
assert.equal(generated.length,1);assert.equal(generated[0][0],'毛驴20张、狗30张');assert.equal(generated[0][1],50);
assert.equal(revised,0);
await apply({...turn,requirement_edits:[],planning_change:'cleaning',request:{target_count:60,effective_request:'狗20张、猫40张；不要水印'}},proposal,'不要水印',{llm:'selected'},null);
assert.equal(generated.length,2);assert.equal(generated[1][0],'狗20张、猫40张；不要水印');assert.equal(revised,0);
state.proposedExpansion=proposal;
await apply({...turn,stage:'request',request:{target_count:null},reply:'新增猫图片需要多少张？'},proposal,'再加猫',{},null);
assert.equal(generated.length,2);assert.equal(state.proposedExpansion,null);assert.equal(state.guidedPhase,'input');
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for task async receipt tests')
    def test_task_async_acceptance_polls_without_reposting_and_keeps_failed_key(self):
        source = CHAT_JS[CHAT_JS.index('function taskTurnStorageKey('):CHAT_JS.index('async function recoverTaskTurn(')]
        script = r"""
(async()=>{
const assert=require('node:assert/strict');const state={},saved=new Map(),headers={'X-Tenant-Id':'t','X-User-Id':'u'};
const sessionStorage={getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)};
let posts=0,reads=0,accepted=0,fail=false;
const result={kind:'answer',reply:'完成',is_current:true};
const api=async(path,options)=>{if(options){posts++;assert.equal(options.asyncCommand,true);return {schema_version:'task-conversation-command@1',status:fail?'failed':'understanding',error:fail?{code:'MODEL_RATE_LIMIT',message:'稍后重试'}:null}}
 reads++;return {schema_version:'task-conversation-command@1',status:reads===1?'understanding':'completed',result}};
const funcs=new Function('state','headers','sessionStorage','api','key','setTimeout',process.env.SOURCE+';return {sendTaskTurn,pendingTaskTurn}')(
 state,headers,sessionStorage,api,()=> 'original',cb=>{cb();return 1});
const body={text:'解释条件',expected_task_version:1};
assert.deepEqual(await funcs.sendTaskTurn('task',body,{},false,()=>accepted++),result);
assert.equal(posts,1);assert.equal(reads,2);assert.equal(accepted,1);assert.equal(funcs.pendingTaskTurn('task'),null);
fail=true;await assert.rejects(funcs.sendTaskTurn('task',body,{}),e=>e.code==='MODEL_RATE_LIMIT');
assert.equal(funcs.pendingTaskTurn('task').key,'original');assert.equal(posts,2);
await assert.rejects(funcs.sendTaskTurn('task',{text:'新消息'},{}),e=>e.code==='COMMAND_UNRESOLVED');assert.equal(posts,2);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for workspace snapshot reuse test')
    def test_status_workspace_is_reused_but_older_mutation_snapshot_is_reread(self):
        source = CHAT_JS[CHAT_JS.index('async function refreshTaskSidebar(workspaceSeed=null){'):CHAT_JS.index('function showResults(')]
        script = r"""
(async()=>{
const assert=require('node:assert/strict');let workspaceReads=0,resultReads=0;
const task={task_id:'t',confirmed_revision:1,run_generation:1,task_version:7,mutation_sequence:5};
const workflow={binding:{task_version:7},summary:'候选已确认',nodes:[],available_actions:[]};
const seed={...task,workflow,progress:{counts:{returned:3},nodes:[],workflow},manifest:null,search:null};
const state={task:{...task}};
const api=async path=>{if(path.endsWith('/workspace')){workspaceReads++;return {...seed,mutation_sequence:10}}resultReads++;return {available:false}};
const fields={'retrieval-live-status':{},'task-sidebar-summary':{}};
const refresh=new Function('state','api','$','showProgress',process.env.SOURCE+';return refreshTaskSidebar')(state,api,id=>fields[id],()=>{});
assert.equal(await refresh(seed),workflow);assert.equal(workspaceReads,0);assert.equal(resultReads,1);
state.task.mutation_sequence=9;await refresh(seed);assert.equal(workspaceReads,1);assert.equal(state.task.mutation_sequence,10);
await refresh({...seed,task_id:'other'});assert.equal(workspaceReads,2);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for unified draft UI test')
    def test_mixed_change_review_displays_total_and_confirmation_follows_search(self):
        source = CHAT_JS[CHAT_JS.index('function showFeedbackReview('):CHAT_JS.index('async function showCleaningSuggestion(')]
        source += CHAT_JS[CHAT_JS.index('async function decideFeedback('):CHAT_JS.index('async function prepareUpdatedCleaning(')]
        script = r"""
(async()=>{
const assert=require('node:assert/strict');const messages=[],actions=[],events=[],details=[];
const state={task:{task_id:'t',task_version:1},feedback:{draft_id:'d',draft_version:1,proposal_digest:'digest',base_task_version:1}};
const fields={'feedback-box':{classList:{remove(){}}},'feedback-text':{value:''},status:{}};
const message=(role,title,text)=>{const b={title,text};messages.push(b);return b};
const api=async()=>({task:{task_id:'t',task_version:2},search_enqueue:{status:'queued'}});
const funcs=new Function('state','$','message','guidedAction','editFeedback','api','key','setBusy','refreshTaskSidebar','refreshHistory','guidedTick','guidedStartSearch','prepareUpdatedCleaning','reviewDetails',process.env.SOURCE+';return {showFeedbackReview,decideFeedback}')(
 state,id=>fields[id],message,(b,l,cb)=>actions.push({l,cb}),()=>{},api,()=> 'key',()=>{},async()=>{},async()=>{},async()=>events.push('search'),async()=>events.push('start'),async()=>events.push('cleaning'),(b,l,t)=>details.push(t));
funcs.showFeedbackReview({feedback_status:'ready',cleaning_plan_preview:{target:{before:10,after:25},retrieval:{changed:true,after:[{primary_label:'猫',auxiliary_terms:['窗边的猫']}]},before_count:1,after_count:2,changes:[{op:'add',after:'无水印'}],nodes:[{source:'狗'},{source:'无水印'}]}});
assert.equal(messages[0].title,'需求修改待确认');assert.match(messages[0].text,/总目标：10 → 25 张/);assert.match(messages[0].text,/新增：无水印/);assert.match(details[0],/猫 \| 窗边的猫/);assert.match(messages[0].text,/检索主体：.*→ 猫/);assert.equal(actions[0].l,'确认更新需求');
await actions[0].cb();assert.deepEqual(events,['search']);assert.equal(state.feedback,null);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for workflow action tests')
    def test_status_reply_uses_refreshed_sidebar_and_recovery_is_historical(self):
        source = CHAT_JS[CHAT_JS.index('async function applyTaskTurnResult('):CHAT_JS.index('function addWorkflowAction(')]
        script = r"""
(async()=>{
const assert=require('node:assert/strict');
const state={task:{task_id:'t',task_version:7}};let reads=0;const messages=[],actions=[];
const latest={status_reply:'候选清单已确认，等待清洗。',available_actions:[{id:'prepare_cleaning'}],binding:{task_version:7}};
const apply=new Function('state','message','refreshTaskSidebar','addWorkflowAction',process.env.SOURCE+';return applyTaskTurnResult')(
 state,(role,title,text)=>{const b={title,text};messages.push(b);return b},async()=>{reads++;return latest},(...args)=>actions.push(args));
const intent={status_requested:true,workflow:{status_reply:'正在检索'},reply:'旧回复'};
await apply(intent,'t',7,'下一步',{});assert.equal(reads,1);assert.equal(messages[0].text,latest.status_reply);assert.equal(actions.length,1);
await apply(intent,'t',7,'下一步',{},true);assert.equal(reads,1);assert.match(messages[1].title,/当时状态/);assert.equal(messages[1].text,'正在检索');assert.equal(actions.length,1);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for workflow action tests')
    def test_workflow_actions_are_allowlisted_and_stale_confirmations_are_blocked(self):
        source = CHAT_JS[CHAT_JS.index('function addWorkflowAction('):CHAT_JS.index('async function routeTaskFollowup(')]
        script = r"""
const assert=require('node:assert/strict');let confirm=0,clicks=0;const callbacks=[];
const state={task:{task_id:'t',task_version:7,confirmed_revision:1,run_generation:2}},status={};
const card={tagName:'DETAILS',open:false,querySelector:()=>({click:()=>clicks++}),scrollIntoView:()=>{}};
const add=new Function('state','guidedAction','showCandidateManifestReview','prepareVlmCleaning','guidedStartSearch','openCandidateBrowser','openDelivery','refreshTaskSidebar','api','showCleaningSuggestion','$','document',process.env.SOURCE+';return addWorkflowAction')(
 state,(b,l,cb)=>callbacks.push(cb),()=>confirm++,()=>{},()=>{},()=>{},()=>{},()=>{},()=>{},()=>{},()=>status,{querySelector:()=>card});
const binding={task_version:7,revision:1,generation:2};
add({}, {id:'confirm_candidates',label:'确认'},'t',binding);callbacks[0]();assert.equal(confirm,1);
state.task.task_version=8;callbacks[0]();assert.equal(confirm,1);assert.match(status.textContent,/状态已更新/);
add({}, {id:'toString'},'t',binding);add({}, {id:'arbitrary_url'},'t',binding);assert.equal(callbacks.length,1);
add({}, {id:'view_cleaning',label:'清洗'},'t',{...binding,task_version:8});callbacks[1]();assert.equal(card.open,true);assert.equal(clicks,1);
state.task.task_id='other';callbacks[1]();assert.equal(clicks,1);
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for accepted command polling test')
    def test_accepted_dialogue_command_polls_and_stop_waiting_does_not_resubmit(self):
        source = CHAT_JS[CHAT_JS.index('async function awaitDialogueCommand('):CHAT_JS.index('async function sendDialogueCommand(')]
        script = r"""
const assert=require('node:assert/strict');let reads=0;
const result={session_id:'dlg_one',version:1};
const api=async(path,options)=>{reads++;assert.equal(path,'/v1/retrieval/projects/demo/dialogue-commands/key');assert.notEqual(options?.method,'POST');return {schema_version:'dialogue-command@1',status:'completed',result}};
const wait=new Function('api','dialogueCommandPath',process.env.SOURCE+';return awaitDialogueCommand')(api,key=>'/v1/retrieval/projects/demo/dialogue-commands/'+key);
const accepted={schema_version:'dialogue-command@1',status:'understanding'};
const guard=setTimeout(()=>process.exit(9),2500);
(async()=>{assert.equal(await wait(accepted,'key'),result);assert.equal(reads,1);
 const controller=new AbortController();const pending=wait(accepted,'key',controller.signal);controller.abort();
 await assert.rejects(pending,error=>error.name==='AbortError');assert.equal(reads,1);
 await assert.rejects(wait({schema_version:'dialogue-command@1',status:'failed',error:{code:'MODEL_RATE_LIMIT',message:'busy'}},'key'),error=>error.code==='MODEL_RATE_LIMIT'&&error.commandStatus==='failed');
 assert.equal(reads,1);clearTimeout(guard);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for cleaning handoff status')
    def test_available_cleaning_checkpoint_never_claims_completion(self):
        source = CHAT_JS[CHAT_JS.index('function showCleaningHandoff('):CHAT_JS.index('async function guidedStartSearch(')]
        script = r"""
const assert=require('node:assert/strict');let title;
const show=new Function('message',process.env.SOURCE+';return showCleaningHandoff')((role,value)=>{title=value});
for(const status of ['running','failed','unknown']){show({available:true,runs:[{status}]});assert.equal(title,'候选已进入清洗')}
show({available:true,runs:[{status:'completed'}]});assert.equal(title,'清洗检测已结束');
show({available:true,runs:[]});assert.equal(title,'候选已进入清洗');
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for dialogue failure routing')
    def test_terminal_dialogue_failure_retries_while_network_loss_only_checks(self):
        source = CHAT_JS[CHAT_JS.index('function showDialogueFailure('):CHAT_JS.index('async function awaitDialogueCommand(')]
        script = r"""
const assert=require('node:assert/strict');let pending=true,callback,view,retries=[],sends=0;
const state={};const show=new Function('state','pendingDialogueCommand','message','guidedAction','recoverDialogueCommand','prepareTask',process.env.SOURCE+';return showDialogueFailure')(
 state,()=>pending,(role,title,text)=>{view={title,text};return {remove(){}}},(bubble,label,fn)=>{view.label=label;callback=fn},retry=>retries.push(retry),()=>sends++);
show({commandStatus:'failed'},'模型繁忙，原消息已保留');assert.equal(view.title,'消息暂未完成');assert.equal(view.label,'重试这条消息');callback();assert.deepEqual(retries,[true]);
show({code:'NETWORK_UNAVAILABLE'},'连接中断');assert.equal(view.title,'消息结果待核对');callback();assert.deepEqual(retries,[true,false]);
pending=false;show({},'未提交');callback();assert.equal(sends,1);
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for first-message recovery test')
    def test_first_chat_response_loss_and_reload_restore_one_session_without_reposting(self):
        source = (CHAT_JS[CHAT_JS.index('function taskTurnStorageKey('):CHAT_JS.index('async function sendTaskTurn(')] +
                  CHAT_JS[CHAT_JS.index('function dialogueCommandScope('):CHAT_JS.index('async function reviseGuidedExpansion(')])
        script = r"""
const assert=require('node:assert/strict');
const state={dialogueId:null,dialogueVersion:0,newChatToken:'token'};
const saved=new Map([['dsh-fresh-chat','token']]),headers={'X-Tenant-Id':'tenant','X-User-Id':'owner'};
const sessionStorage={getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value),removeItem:key=>saved.delete(key)};
const body={session_id:null,expected_version:0,message:'想找狗图片',llm_id:'original',phase:'request'};
const turn={session_id:'dlg_one',version:1,action:'clarify',stage:'request',reply:'需要多少张？',is_current:true};
let posts=0,reads=0,replies=0;
const api=async(path,options)=>{if(options?.method==='POST'){posts++;assert.equal(path,'/v1/retrieval/projects/demo/dialogue-command');assert.equal(options.keyValue,'dialogue-command-id');assert.deepEqual(options.body,body);throw Object.assign(Error('lost'),{code:'NETWORK_UNAVAILABLE'})}
 reads++;return {status:'completed',result:turn}};
const funcs=new Function('state','headers','sessionStorage','key','api','setDialogueBusy','saveDialogueSession','restoreDialogueSession','message','$',process.env.SOURCE+';return {sendDialogueCommand,pendingDialogueCommand,recoverDialogueCommand,pendingTaskTurn};')(
 state,headers,sessionStorage,prefix=>prefix+'-id',api,()=>{},()=>{},async()=>{state.dialogueId=turn.session_id;state.dialogueVersion=turn.version},()=>{replies++},()=>({textContent:''}));
const guard=setTimeout(()=>process.exit(9),2000);
(async()=>{const recovered=await funcs.sendDialogueCommand(body,{llm:'original'});assert.equal(recovered.recovered,true);
 assert.equal(posts,1);assert.equal(reads,1);assert.equal(funcs.pendingDialogueCommand().result.session_id,'dlg_one');assert(!saved.has('dsh-fresh-chat'));
 state.pendingTaskTurn=null;await funcs.recoverDialogueCommand();
 assert.equal(posts,1);assert.equal(reads,2);assert.equal(state.dialogueId,'dlg_one');assert.equal(state.dialogueVersion,1);
 assert.equal(funcs.pendingTaskTurn('dialogue:new:token'),null);assert.equal(replies,0);clearTimeout(guard);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for pre-task explicit retry test')
    def test_new_chat_command_waits_for_explicit_retry_and_another_chat_uses_another_scope(self):
        source = (CHAT_JS[CHAT_JS.index('function taskTurnStorageKey('):CHAT_JS.index('async function sendTaskTurn(')] +
                  CHAT_JS[CHAT_JS.index('function dialogueCommandScope('):CHAT_JS.index('async function reviseGuidedExpansion(')])
        script = r"""
const assert=require('node:assert/strict');
const body={session_id:null,expected_version:0,message:'找狗图片',llm_id:'original',phase:'request'};
const state={dialogueId:null,newChatToken:'old'};const saved=new Map(),headers={};
const sessionStorage={getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value),removeItem:key=>saved.delete(key)};
const turn={session_id:'dlg_one',version:1,action:'clarify',stage:'request',reply:'多少张？',is_current:true};
let posts=0,actions=[];
const api=async(path,options)=>{if(options?.method==='POST'){posts++;assert.equal(options.retryFailed,true);assert.equal(options.keyValue,'original-key');assert.deepEqual(options.body,body);return turn}
 return {status:'interrupted'}};
const funcs=new Function('state','headers','sessionStorage','key','api','setDialogueBusy','saveDialogueSession','restoreDialogueSession','message','guidedAction','$',process.env.SOURCE+';return {saveTaskTurn,pendingDialogueCommand,pendingTaskTurn,recoverDialogueCommand};')(
 state,headers,sessionStorage,()=>{throw Error('must reuse key')},api,()=>{},()=>{},async()=>{state.dialogueId=turn.session_id;state.dialogueVersion=1},()=>({remove(){}}),(_bubble,label,action)=>actions.push([label,action]),()=>({textContent:''}));
funcs.saveTaskTurn({taskId:'dialogue:new:old',key:'original-key',body,selection:{llm:'original'}});
const guard=setTimeout(()=>process.exit(9),2000);
(async()=>{state.newChatToken='new';assert.equal(funcs.pendingDialogueCommand(),null);assert(funcs.pendingTaskTurn('dialogue:new:old'));
 state.newChatToken='old';await funcs.recoverDialogueCommand();assert.equal(posts,0);assert.equal(actions[0][0],'重试这条消息');
 await actions[0][1]();assert.equal(posts,1);assert.equal(state.dialogueId,'dlg_one');assert.equal(funcs.pendingTaskTurn('dialogue:new:old'),null);clearTimeout(guard);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for durable command client test')
    def test_task_command_recovers_lost_response_and_reuses_key_after_page_reload(self):
        source = CHAT_JS[CHAT_JS.index('function taskTurnStorageKey('):
                         CHAT_JS.index('async function applyTaskTurnResult(')]
        script = r"""
const assert=require('node:assert/strict');
const state={},saved=new Map(),headers={'X-Tenant-Id':'tenant','X-User-Id':'owner'};
const sessionStorage={getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value),removeItem:key=>saved.delete(key)};
const body={text:'增加无水印',expected_task_version:1},selection={llm:'text',vlm:'vision'};
const result={kind:'cleaning',base_task_version:1};let posts=0,reads=0,offline=false;
const api=async(path,options)=>{if(options){posts++;assert.equal(options.keyValue,'command');assert.deepEqual(options.body,body);
 if(offline)throw Object.assign(Error('offline'),{code:'NETWORK_UNAVAILABLE'});
 if(posts===1)throw Object.assign(Error('response lost'),{code:'NETWORK_UNAVAILABLE'});return result}
 reads++;if(offline)throw Error('offline');return {status:'completed',result}};
const funcs=new Function('state','headers','sessionStorage','api','key',process.env.SOURCE+';return {sendTaskTurn,pendingTaskTurn};')(
 state,headers,sessionStorage,api,()=> 'command');
const guard=setTimeout(()=>process.exit(9),2000);
(async()=>{const recovered=await funcs.sendTaskTurn('task',body,selection);assert.equal(recovered.recovered,true);
 assert.equal(posts,1);assert.equal(reads,1);assert.equal(funcs.pendingTaskTurn('task'),null);
 offline=true;await assert.rejects(funcs.sendTaskTurn('task',body,selection));
 state.pendingTaskTurn=null;const pending=funcs.pendingTaskTurn('task');assert.equal(pending.key,'command');
 assert.deepEqual(pending.body,body);offline=false;await funcs.sendTaskTurn('task',body,selection);
 assert.equal(funcs.pendingTaskTurn('task'),null);clearTimeout(guard);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for explicit command retry test')
    def test_failed_task_command_waits_for_explicit_retry_and_keeps_original_request(self):
        source = CHAT_JS[CHAT_JS.index('function taskTurnStorageKey('):
                         CHAT_JS.index('async function applyTaskTurnResult(')]
        script = r"""
const assert=require('node:assert/strict');
const body={text:'增加无水印',expected_task_version:1};
const state={task:{task_id:'task',task_version:1},pendingTaskTurn:{taskId:'task',key:'old-command',body,selection:{llm:'original'}}};
const saved=new Map(),sessionStorage={getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value),removeItem:key=>saved.delete(key)};
let actions=[],posts=0,applied=0;
const api=async(path,options)=>{if(!options)return {status:'failed',error:{message:'模型暂不可用'}};
 posts++;assert.equal(options.retryFailed,true);assert.equal(options.keyValue,'old-command');assert.deepEqual(options.body,body);
 return {kind:'cleaning',base_task_version:1}};
const funcs=new Function('state','headers','sessionStorage','api','key','setFollowupBusy','message','guidedAction','applyTaskTurnResult','$',process.env.SOURCE+';return {recoverTaskTurn};')(
 state,{},sessionStorage,api,()=>{throw Error('must keep original key')},value=>state.followupBusy=value,
 ()=>({remove(){}}),(_bubble,label,fn)=>actions.push([label,fn]),async()=>{applied++},()=>({textContent:''}));
const guard=setTimeout(()=>process.exit(9),2000);
(async()=>{await funcs.recoverTaskTurn();assert.equal(posts,0);assert.equal(actions[0][0],'重试这条消息');
 await actions[0][1]();assert.equal(posts,1);assert.equal(applied,1);clearTimeout(guard);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for candidate confirmation behavior')
    def test_candidate_confirmation_is_one_click_and_reconciles_disconnects(self):
        source = CHAT_JS[CHAT_JS.index('async function showCandidateManifestReview(){'):CHAT_JS.index('function offerVlmCleaning(')]
        script = r"""
const assert=require('node:assert/strict');
const proposal={kind:'candidate_manifest_proposal',candidate_count:30,proposal_digest:'digest',expected_task_version:2,confirmed:false};
const confirmed={kind:'candidate_input_manifest',candidate_count:30,confirmed:true};
async function scenario(mode){
 const state={task:{task_id:'t',confirmed_revision:1}},lines=[],calls=[],offered=[],actions=[],status={textContent:'old'};
 let release;
 const api=async(path,options={})=>{calls.push(options);assert.equal(path,'/v1/retrieval/tasks/t/first-search/manifest');
  if(mode==='pending'&&calls.length===1)await new Promise(resolve=>release=resolve);
  if(mode==='get-error')throw Object.assign(Error('offline'),{code:'NETWORK_UNAVAILABLE'});
  if(options.method==='POST'){
   assert.equal(options.keyValue,'candidate-manifest:digest');assert.equal(options.body.expected_task_version,2);assert.equal(options.body.action,'confirm');
   if(['post-error','post-unsaved'].includes(mode))throw Object.assign(Error('offline'),{code:'NETWORK_UNAVAILABLE'});
   return confirmed;
  }
  return mode==='already'||mode==='post-error'&&calls.length>1?confirmed:proposal;
 };
 const run=new Function('state','setBusy','message','api','$','offerVlmCleaning','refreshTaskSidebar','guidedAction',process.env.SOURCE+';return showCandidateManifestReview')(
  state,()=>{},(_role,_title,text)=>{const line={textContent:text};lines.push(line);return {querySelector:()=>line}},api,()=>status,
  manifest=>offered.push(manifest),async()=>{},(_,label)=>actions.push(label));
 const first=run();assert.equal(lines[0].textContent,'正在确认候选清单…');
 if(mode==='pending'){await run();assert.equal(calls.length,1);release();}
 await first;assert.equal(state.manifestConfirming,false);
 if(['get-error','post-unsaved'].includes(mode)){assert.match(lines[0].textContent,/连接中断/);assert.deepEqual(actions,['重新确认']);assert.equal(offered.length,0);}
 else{assert.match(lines[0].textContent,/已确认 30 张/);assert.equal(offered.length,1);assert.equal(state.candidateManifest,confirmed);
  assert.equal(calls.filter(call=>call.method==='POST').length,mode==='already'?0:1);}
}
(async()=>{for(const mode of ['normal','pending','already','post-error','post-unsaved','get-error'])await scenario(mode)})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for expansion model replacement')
    def test_expansion_regeneration_uses_current_model_and_suppresses_duplicate_clicks(self):
        source = CHAT_JS[CHAT_JS.index('async function regenerateExpansionJob(job){'):CHAT_JS.index('function watchExpansionJob(job){')]
        script = r"""
const assert=require('node:assert/strict'),state={dialogueId:'dlg_one',dialogueVersion:3};
const fields={'llm-model':{value:'fast'},'vlm-model':{value:'vision'},status:{}};
let calls=0,release,watched;
const api=async(path,options)=>{calls++;assert.equal(options.body.model_selection.llm,'fast');
 assert.equal(options.body.raw_request,'飞天猪20张');assert.equal(options.body.target_count,20);
 assert(!('action' in options.body));assert.equal(options.body.regeneration_key,'fresh');await new Promise(resolve=>release=resolve);return {job_id:'replacement'}};
const regenerate=new Function('state','$','api','expansionJobPath','key','watchExpansionJob',process.env.SOURCE+';return regenerateExpansionJob')(
 state,id=>fields[id],api,()=>'/jobs',()=> 'fresh',job=>watched=job);
(async()=>{const job={dialogue_session_id:'dlg_one',dialogue_version:3,raw_request:'飞天猪20张',target_count:20};
 const first=regenerate(job);await regenerate(job);assert.equal(calls,1);release();await first;
 assert.equal(watched.job_id,'replacement');assert.equal(state.expansionRegenerating,false);
 state.dialogueVersion=4;await regenerate(job);assert.equal(calls,1);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for expansion waiting feedback')
    def test_expansion_waiting_feedback_shows_model_and_real_elapsed_time(self):
        source = CHAT_JS[CHAT_JS.index('function expansionProgress(job){'):CHAT_JS.index('async function regenerateExpansionJob(job){')]
        script = r"""
const assert=require('node:assert/strict');
const describe=new Function('registeredModelProfiles',process.env.SOURCE+';return expansionProgress')([{id:'slow',model:'glm-5.3'}]);
const waiting=describe({status:'running',completed_terms:0,target_terms:null,elapsed_ms:26000,model_selection:{llm:'slow'}});
assert.match(waiting,/glm-5.3/);assert.match(waiting,/26 秒/);assert.match(waiting,/首批/);
assert.doesNotMatch(waiting,/待确定/);assert.match(waiting,/响应较慢/);
assert.match(describe({status:'running',completed_terms:3,target_terms:8,elapsed_ms:5000}),/3\/8/);
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for natural draft reply test')
    def test_natural_draft_reply_is_visible_before_background_expansion(self):
        source = CHAT_JS[CHAT_JS.index('async function prepareTask(){'):
                         CHAT_JS.index('async function reviseGuidedExpansion(')]
        start = source.index('function dialogueCommandScope(')
        end = source.index('async function applyDialogueTurn(')
        source = (source[:start] + "function pendingDialogueCommand(){return null}"
                  "async function sendDialogueCommand(body,selection,signal){return api('/v1/retrieval/projects/demo/dialogue-command',{method:'POST',body,signal})}" + source[end:])
        script = r"""
const assert=require('node:assert/strict');const state={dialogueVersion:0,dialogueStage:'request'};
const fields={'request-text':{value:'想收集一些小狗画面'},'llm-model':{value:'llm'},'dialogue-llm-model':{value:'chat-llm'},'vlm-model':{value:'vlm'},
 'target-count':{value:''},'stop-request':{classList:{add(){},remove(){}}},status:{textContent:''}};
const events=[];const api=async(path,options)=>{assert.equal(options.body.llm_id,'chat-llm');return {session_id:'dlg_x',version:1,stage:'terms',action:'update',
 reply_source:'model',reply:'会保留雪地和无水印这两个要求。',request:{target_count:12,effective_request:'12张雪地小狗图片，无水印'}}};
const prepare=new Function('state','$','api','message','setDialogueBusy','saveDialogueSession','groupInputBody','prepareGuidedExpansion',process.env.SOURCE+';return prepareTask')(
 state,id=>fields[id],api,(role,title,text)=>{events.push(text);return {setAttribute(){},remove(){}}},()=>{},()=>{},()=>({}),async(raw,count,signal,selection)=>{assert.equal(selection.llm,'llm');events.push('expansion')});
(async()=>{await prepare();assert.equal(events.at(-2),'会保留雪地和无水印这两个要求。');assert.equal(events.at(-1),'expansion')})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for connection error behavior test')
    def test_api_distinguishes_disconnect_from_abort_and_never_retries_posts(self):
        source = CHAT_JS[CHAT_JS.index('async function api('):CHAT_JS.index('function stopTaskEvents(')]
        source = 'const window={};' + CHAT_JS[CHAT_JS.index('const pipelineResponseTraces='):CHAT_JS.index('const headers=')] + source
        script = r"""
const assert=require('node:assert/strict');let calls=0,mode='disconnect';
const fetch=async()=>{calls++;if(mode==='disconnect')throw TypeError('Failed to fetch');
 if(mode==='body')return {json:async()=>{throw TypeError('Failed to fetch')}};
 if(mode==='abort'){const e=Error('aborted');e.name='AbortError';throw e}
 return {ok:false,status:409,json:async()=>({code:'DIALOGUE_CONFLICT',message:'reload'})}};
const api=new Function('headers','fetch',process.env.SOURCE+';return api')({},fetch);
(async()=>{await assert.rejects(api('/turn',{method:'POST',body:{message:'fixture'}}),e=>e.code==='NETWORK_UNAVAILABLE');assert.equal(calls,1);
 mode='body';await assert.rejects(api('/turn'),e=>e.code==='NETWORK_UNAVAILABLE');assert.equal(calls,2);
 mode='abort';await assert.rejects(api('/turn'),e=>e.name==='AbortError'&&!e.code);
 mode='conflict';await assert.rejects(api('/turn'),e=>e.code==='DIALOGUE_CONFLICT');assert.equal(calls,4);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for task expansion preview test')
    def test_task_expansion_first_batch_has_no_confirm_and_complete_review_is_compact(self):
        source = CHAT_JS[CHAT_JS.index('function taskExpansionPath('):
                         CHAT_JS.index('async function restoreTaskExpansionJob(')]
        source = CHAT_JS[CHAT_JS.index('function expansionProgress(job){'):CHAT_JS.index('async function regenerateExpansionJob(job){')] + source
        script = r"""
const assert=require('node:assert/strict');
const state={task:{task_id:'a',task_version:1,confirmed_revision:1}};
const bubbles=[],actions=[],details=[];
const message=(role,title,text)=>{const p={textContent:text};const b={p,removed:false,querySelector:s=>s==='p'?p:null,remove(){this.removed=true}};bubbles.push(b);return b};
const watch=new Function('state','$','message','reviewDetails','guidedAction','key','setTimeout','clearTimeout',process.env.SOURCE+';return watchTaskExpansionJob')(
 state,()=>({}),message,(b,label,text)=>details.push([label,text]),(b,label)=>actions.push(label),()=> 'token',()=>1,()=>{});
const job={task_id:'a',task_version:1,revision:1,job_id:'j',target_count:20,status:'running',completed_terms:3,target_terms:10,
 preview_groups:[{primary_label:'猫',auxiliary_terms:['猫在草地','猫在窗边']} ]};
watch(job);assert.match(bubbles[0].p.textContent,/猫在草地/);assert.match(bubbles[0].p.textContent,/暂不可确认/);assert.deepEqual(actions,[]);
const done={...job,status:'completed',changes:{target_before:10,target_after:20,added_terms:1,removed_terms:0},
 result:{plan:{groups:[{primary_label:'猫',auxiliary_terms:['猫在草地','猫在窗边']}]}}};
watch(done);assert.equal(bubbles[0].removed,true);assert.match(bubbles[1].p.textContent,/10 → 20/);assert(!bubbles[1].p.textContent.includes('猫在草地'));
assert.match(details[0][1],/猫在草地/);assert(actions.includes('确认新版词表'));
watch(done);watch({...done,task_version:0});assert.equal(bubbles.length,2);
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for immediate follow-up feedback test')
    def test_followup_echoes_before_response_and_preserves_new_input_on_failure_or_navigation(self):
        source = CHAT_JS[CHAT_JS.index('async function prepareFeedback(){'):
                         CHAT_JS.index('function taskExpansionPath(')]
        source = (source[:source.index('function taskTurnStorageKey(')] +
                  source[source.index('async function applyTaskTurnResult('):])
        source = ("async function sendTaskTurn(taskId,body){return api('/turn',{body})}"
                  "function pendingTaskTurn(){return null}" + source)
        cancel_source = CHAT_JS[CHAT_JS.index('function cancelTaskFollowup('):
                                CHAT_JS.index('function cancelDialogueRequest(')]
        script = r"""
const assert=require('node:assert/strict');
const state={task:{task_id:'a',task_version:1,confirmed_revision:1}};
const fields={'feedback-text':{value:'为什么选这些词'},'llm-model':{value:'llm'},'vlm-model':{value:'vlm'},status:{textContent:''}};
const messages=[];let resolve,reject,removed=0;
const api=()=>new Promise((ok,no)=>{resolve=ok;reject=no});
const run=new Function('state','$','api','message','setFollowupBusy','showPostTaskExpansion','const key=()=>"fixture-command";'+process.env.SOURCE+';return prepareFeedback')(
 state,id=>fields[id],api,(role,title,text)=>{messages.push([role,text]);return {remove(){removed++}}},v=>{state.followupBusy=v},()=>{throw Error('unexpected expansion')});
const cancel=new Function('state','setFollowupBusy',process.env.CANCEL_SOURCE+';return cancelTaskFollowup')(state,v=>{state.followupBusy=v});
(async()=>{let pending=run();assert.deepEqual(messages.map(row=>row[0]),['user','assistant']);assert.equal(fields['feedback-text'].value,'');
 fields['feedback-text'].value='接下来想问的';resolve({kind:'answer',reply:'覆盖了不同场景。'});await pending;
 assert.equal(messages.filter(row=>row[0]==='user').length,1);assert.equal(fields['feedback-text'].value,'接下来想问的');assert.equal(messages.at(-1)[1],'覆盖了不同场景。');
 fields['feedback-text'].value='失败后重试';pending=run();reject(Error('网络失败'));await pending;assert.equal(fields['feedback-text'].value,'失败后重试');
 fields['feedback-text'].value='旧任务消息';pending=run();state.task={task_id:'b',task_version:1,confirmed_revision:1};fields['feedback-text'].value='';reject(Error('旧请求失败'));await pending;
 assert.equal(fields['feedback-text'].value,'');assert.equal(state.followupBusy,false);assert(removed>=3);
 fields['feedback-text'].value='离开前消息';pending=run();const oldReject=reject;cancel();state.historyOpenToken=1;
 fields['feedback-text'].value='新页面消息';const latest=run(),latestResolve=resolve;
 oldReject(Error('旧页面失败'));await pending;assert.equal(state.followupBusy,true);assert.equal(fields['feedback-text'].value,'');
 latestResolve({kind:'answer',reply:'新页面回答'});await latest;assert.equal(state.followupBusy,false);assert.equal(messages.at(-1)[1],'新页面回答');
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source, 'CANCEL_SOURCE': cancel_source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for timing summary test')
    def test_chat_timing_summary_reports_p50_and_p95_without_message_text(self):
        source = CHAT_JS[CHAT_JS.index('function recordChatTiming('):
                         CHAT_JS.index('const headers=')]
        script = r"""
const assert=require('node:assert/strict');const saved=new Map();
const sessionStorage={getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value)};
const window={};
new Function('sessionStorage','window',process.env.SOURCE+';for(let n=1;n<=20;n++)recordChatTiming("understanding",n);return window.dshChatTimingSummary')(sessionStorage,window)();
assert.deepEqual(window.dshChatTimingSummary().understanding,{n:20,p50_ms:10,p95_ms:19});
assert.equal(JSON.stringify([...saved.values()]).includes('用户原话'),false);
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for count-only fast path test')
    def test_image_count_edit_reuses_unallocated_terms_and_requires_group_targets(self):
        source = CHAT_JS[CHAT_JS.index('async function prepareTask(){'):
                         CHAT_JS.index('async function reviseGuidedExpansion(')]
        start = source.index('function dialogueCommandScope(')
        end = source.index('async function applyDialogueTurn(')
        source = (source[:start] + "function pendingDialogueCommand(){return null}"
                  "async function sendDialogueCommand(body,selection,signal){return api('/v1/retrieval/projects/demo/dialogue-command',{method:'POST',body,signal})}" + source[end:])
        script = r"""
const assert=require('node:assert/strict');
const fields={'request-text':{value:'改成20张，其他不变'},'llm-model':{value:'llm'},
 'vlm-model':{value:'vlm'},'target-count':{value:''},'stop-request':{classList:{add(){},remove(){}}},
 status:{textContent:''}};
const $=id=>fields[id];let calls=0,review=null,updates=0;
const state={task:null,requestController:null,dialogueId:'dlg_x',dialogueVersion:1,dialogueStage:'terms',
 targetCountTouched:false,proposedExpansion:{raw:'找10张猫图片',count:10,version:1,
 lines:'猫 | 猫在草地',plan:{groups:[{primary_label:'猫',auxiliary_terms:['猫在草地'],delivery_target:null}]}}};
const api=async()=>{calls++;return {session_id:'dlg_x',version:2,stage:'terms',action:'revise_terms',
 request:{target_count:20,effective_request:'找 20 张猫图片'},count_edits:[],expansion_instruction:null}};
const message=()=>({setAttribute(){},remove(){}});
const prepare=new Function('state','$','api','message','setDialogueBusy','saveDialogueSession','groupInputBody','showTermReview','prepareGuidedExpansion',
 process.env.SOURCE+';return prepareTask')(state,$,api,message,()=>{},()=>{},()=>({groups:[{primary_label:'猫',auxiliary_terms:['猫在草地']}]}),
 (proposal)=>{review=proposal},async(raw,count)=>{updates++;assert.equal(state.dialogueVersion,2);
 assert.equal(state.proposedExpansion.count,10);
 review=state.proposedExpansion={...state.proposedExpansion,raw,count,jobId:'new-job',confirmation:{dialogue_version:2}}});
(async()=>{await prepare();assert.equal(calls,1);assert.equal(review.count,20);
 assert.equal(review.raw,'找 20 张猫图片');assert.equal(state.proposedExpansion.count,20);
 assert.equal(updates,1);assert.equal(review.confirmation.dialogue_version,2);
 state.proposedExpansion.plan.groups[0].delivery_target=10;fields['request-text'].value='改成30张，其他不变';
 await prepare();assert.equal(calls,1);assert.match(fields.status.textContent,/每组的新目标/);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for restored follow-up context test')
    def test_task_followup_context_survives_history_restore(self):
        source = CHAT_JS[CHAT_JS.index('function restoreFollowupContext('):
                         CHAT_JS.index('async function openConversation(')]
        script = r"""
const assert=require('node:assert/strict');
const state={};
const restore=new Function('state',process.env.SOURCE+';return restoreFollowupContext')(state);
const task={task_id:'task-a',task_version:3};
restore(task,[{role:'user',text:'猫少一半'},{role:'assistant',text:'本轮理解：expansion'},
              {role:'user',text:'其他不变'},{role:'assistant',text:'已保留狗组'}]);
assert.deepEqual(state.followupContext.messages,[{role:'user',text:'猫少一半'},
              {role:'user',text:'其他不变'},{role:'assistant',text:'已保留狗组'}]);
restore(task,[]);assert.equal(state.followupContext.messages.length,3);
restore({...task,task_version:4},[]);assert.equal(state.followupContext.messages.length,0);
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_workbench_opens_without_chat(self):
        self.assertIn('href="/production"', CHAT_HTML)
        self.assertIn('id="line-rows"', BOARD_HTML)
        self.assertIn('id="line-detail"', BOARD_HTML)
        self.assertIn('id="line-filter"', BOARD_HTML)
        self.assertIn('function selectLine(id)', BOARD_JS)
        self.assertIn('showDetail(item)', BOARD_JS)
        self.assertIn('id="detail-chat" href="/chat"', BOARD_HTML)
        self.assertIn("$('detail-chat').href='/chat?task='+encodeURIComponent(selected)", BOARD_JS)
        self.assertIn("new URLSearchParams(location.search).get('task')", CHAT_JS)
        self.assertNotIn('openConversation(', BOARD_JS)
        self.assertNotIn('openTask(', BOARD_JS)
        self.assertIn('.detail-nodes', BOARD_CSS)

    def test_complete_new_chat_request_skips_redundant_summary_round(self):
        self.assertIn('generate_terms_when_complete:true', CHAT_JS)
        self.assertIn("if(turn.stage==='terms'&&!proposal&&turn.request.target_count)", CHAT_JS)
        self.assertIn('确认检索词并开始检索', CHAT_JS)

    def test_conversation_and_workflow_use_separate_scroll_regions(self):
        self.assertIn('id="timeline" class="timeline"', CHAT_HTML)
        self.assertIn('id="result-sidebar"', CHAT_HTML)
        self.assertIn('id="result-list"', CHAT_HTML)
        self.assertIn('id="toggle-history"', CHAT_HTML)
        self.assertIn('id="model-controls"', CHAT_HTML)
        self.assertIn('.app-shell{flex:1;min-height:0;grid-template-rows:minmax(0,1fr)}', CHAT_CSS)
        self.assertIn('.timeline{flex:1;min-height:0;overflow:auto', CHAT_CSS)
        self.assertIn('.chat-composer{position:sticky;bottom:8px', CHAT_CSS)
        self.assertIn('id="model-panel-dialog" class="sidebar-dialog"', CHAT_HTML)
        self.assertLess(CHAT_HTML.index('id="history-list"'), CHAT_HTML.index('id="open-model-panel"'))
        self.assertLess(CHAT_HTML.index('id="open-model-panel"'), CHAT_HTML.index('id="model-panel-dialog"'))
        self.assertIn("$('open-model-panel').addEventListener('click'", CHAT_JS)
        self.assertIn("$('open-environment-info').addEventListener('click'", CHAT_JS)
        self.assertIn("resultViewLabels", CHAT_JS)
        self.assertIn("[],list)", CHAT_JS)

    def test_result_workspace_only_appears_for_published_results(self):
        self.assertNotIn('id="result-next-action"', CHAT_HTML)
        self.assertIn('class="rail-card hidden result-workspace"', CHAT_HTML)
        self.assertNotIn('result-purpose', CHAT_HTML)
        self.assertNotIn('开始后你会看到', CHAT_HTML)
        self.assertIn('function resetResultWorkspace()', CHAT_JS)
        self.assertIn("resetResultWorkspace();syncRefillFields()", CHAT_JS)
        self.assertIn("if(!results.available){resetResultWorkspace();return}", CHAT_JS)
        self.assertIn('data-result-view="review"', CHAT_HTML)
        self.assertNotIn('id="result-view-guide"', CHAT_HTML)
        self.assertNotIn('const resultViewGuides=', CHAT_JS)
        self.assertNotIn("card('结果说明'", CHAT_JS)
        self.assertIn('id="result-feedback-dialog"', CHAT_HTML)
        self.assertIn("/manual-reviews", CHAT_JS)
        self.assertIn("人工通过", CHAT_JS)
        self.assertIn("人工淘汰", CHAT_JS)
        self.assertNotIn("window.prompt", CHAT_JS)

    def test_compact_app_width_uses_single_column_and_history_drawer(self):
        self.assertEqual(CHAT_CSS.count('@media(max-width:900px)'), 2)
        self.assertIn('@media(max-width:900px){main{padding:14px', CHAT_CSS)
        self.assertIn('@media(max-width:900px){dialog.sidebar-dialog{left:16px;bottom:16px}main:not(.catalog-page)', CHAT_CSS)
        self.assertIn("matchMedia('(max-width:900px)')", CHAT_JS)

    def test_sidebar_preserves_open_node_across_progress_refresh(self):
        self.assertIn('card.dataset.node=node.node', CHAT_JS)
        self.assertIn('state.nodeOpen?.[key]', CHAT_JS)
        self.assertIn('state.sidebarTaskId===progress.task_id', CHAT_JS)
        self.assertIn("nodes.querySelectorAll('details.node-card[data-node]')", CHAT_JS)

    def test_explicit_term_replacement_has_distinct_confirmation_copy(self):
        self.assertIn("job.result.note?.includes('按用户逐字指定')?'指定词表待确认'", CHAT_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for saved group rendering test')
    def test_saved_groups_do_not_render_internal_model_binding_card(self):
        source = CHAT_JS[CHAT_JS.index('function showSavedGroups('):
                         CHAT_JS.index('function setBusy(')]
        script = r"""
const assert=require('node:assert/strict');
const cards=[];
const show=new Function('card',process.env.SOURCE+';return showSavedGroups')((title,rows)=>cards.push({title,rows}));
show({parent_label:'动物',generator:'llm@1',model_binding:{llm:'internal-llm-id',vlm:'internal-vlm-id',config_digest:'sha256:internal'},groups:[{primary_label:'猫',auxiliary_terms:['布偶猫'],package_name:'cats'}]});
assert.equal(cards.length,1);
assert.equal(cards[0].title,'已确认分组 · 猫');
assert(!JSON.stringify(cards).includes('internal-llm-id'));
assert(!JSON.stringify(cards).includes('sha256:internal'));
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('已确认模型选择', source)

    @unittest.skipUnless(shutil.which('node'), 'Node required for candidate gallery grouping test')
    def test_accepted_gallery_uses_independent_thumbnail_sequence(self):
        source = CHAT_JS[CHAT_JS.index('function candidateResultTab(view)'):
                         CHAT_JS.index('function candidateHint(text)')]
        script = r"""
const assert=require('node:assert/strict');
const [groups,key]=new Function('aggregateLocalCandidates',process.env.SOURCE+';return [candidateGroups,candidateGroupKey]')(
  ()=>{throw new Error('Raw search candidates must not populate accepted results')});
const items=[{candidate_id:'candidate_one',asset_version:'a1',preview_status:'available',sources:[{library_id:'library-a'}]},{candidate_id:'candidate_two',asset_version:'a2',preview_status:'available',sources:[]}];
const accepted=groups({tab:'accepted',items});
assert.deepEqual(accepted.map(group=>key(group)),['candidate_one','candidate_two']);
assert(accepted.every(group=>group.previewItem===group.item));
assert.equal(accepted[0].sources[0].library_id,'library-a');
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('当前结果版本没有${candidateTabLabel(view.tab)}素材', CHAT_JS)
        self.assertIn('/cleaning/candidates/${encodeURIComponent(item.candidate_id)}/preview', CHAT_JS)
        self.assertNotIn("if(data.publication_kind==='preview'){view.items=[]", CHAT_JS)
        self.assertIn('合格结果', CHAT_HTML)
        self.assertIn("search.search?.pagination_stopped)return;candidateLoad", CHAT_JS)
        self.assertIn("searchProgress.initial_window_complete", CHAT_JS)
        self.assertIn("首轮候选已就绪，后续位置留给自动补数", CHAT_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for previewable gallery filter test')
    def test_gallery_defaults_to_previewable_candidates_without_losing_raw_hits(self):
        source = CHAT_JS[CHAT_JS.index('function candidateResultTab(view)'):
                         CHAT_JS.index('function candidateHint(text)')]
        script = r"""
const assert=require('node:assert/strict');
const groups=new Function('aggregateLocalCandidates',process.env.SOURCE+';return candidateGroups')(
  items=>items.map(item=>({item,previewItem:item.preview_status==='available'?item:null})));
const items=[{candidate_id:'unmapped',preview_status:'unavailable'},{candidate_id:'image',preview_status:'available'}];
assert.deepEqual(groups({tab:'candidates',previewOnly:true,items}).map(group=>group.item.candidate_id),['image']);
assert.deepEqual(groups({tab:'candidates',previewOnly:false,items}).map(group=>group.item.candidate_id),['unmapped','image']);
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('id="candidate-preview-filter"', CHAT_HTML)

    def test_candidate_browser_keeps_dialog_open_during_view_reload(self):
        self.assertIn('id="candidate-retry" class="secondary hidden"', CHAT_HTML)
        self.assertIn('id="candidate-reset" class="secondary hidden"', CHAT_HTML)
        self.assertIn("clearInterval(candidateTimer);candidateTimer=null;", CHAT_JS)
        self.assertIn("candidateTimer=setInterval(()=>{if(!candidateCurrent(view))return;", CHAT_JS)
        self.assertNotIn("if(!candidateCurrent(view)){closeCandidateBrowser();return}", CHAT_JS)
        self.assertIn("if(candidateActive?.tab!=='candidates')candidateReset", CHAT_JS)
        self.assertIn("['accepted-tab','accepted']", CHAT_JS)
        self.assertIn("['failed-tab','failed']", CHAT_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for model choice persistence test')
    def test_current_model_choice_is_remembered_for_each_chat(self):
        source = CHAT_JS[CHAT_JS.index('function modelChoiceScope()'):CHAT_JS.index("$('prepare-task').addEventListener")]
        script = r"""
const assert=require('node:assert/strict');
const saved=new Map(),localStorage={getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)};
const sessionStorage={getItem:()=>null};
const fields={'llm-model':{value:'llm-a'},'vlm-model':{value:'vlm-a'}};
const state={task:null,dialogueId:null,serverModelDefaults:{llm:'llm-a',vlm:'vlm-a'}};
const [scope,read,save]=new Function('state','localStorage','sessionStorage','$',process.env.SOURCE+';return [modelChoiceScope,readStoredModelChoice,saveCurrentModelChoice]')(state,localStorage,sessionStorage,id=>fields[id]);
const defaults=state.serverModelDefaults;state.serverModelDefaults=null;assert.equal(read(),null);state.serverModelDefaults=defaults;
assert.equal(scope(),'new');save();assert.deepEqual(read(),{llm:'llm-a',vlm:'vlm-a'});
state.task={task_id:'task-one'};fields['llm-model'].value='llm-b';save();assert.deepEqual(read(),{llm:'llm-b',vlm:'vlm-a'});
state.task={task_id:'task-two'};assert.equal(read(),null);
state.task={task_id:'task-one'};assert.equal(read().llm,'llm-b');
state.serverModelDefaults={llm:'fast-model',vlm:'vlm-a'};
assert.equal(read(),null,'old cache cannot override new server defaults');
fields['llm-model'].value='explicit-model';save();
assert.equal(read().llm,'explicit-model','explicit choice survives with unchanged defaults');
state.serverModelDefaults={llm:'next-default',vlm:'vlm-a'};
assert.equal(read(),null,'changing defaults invalidates earlier cached selection');

"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_model_search_is_inside_each_picker(self):
        for name in ('llm-model', 'vlm-model', 'default-llm-model', 'default-vlm-model'):
            self.assertIn(f'id="{name}-input" list="{name}-list"', CHAT_HTML)
            self.assertIn(f'<datalist id="{name}-list"', CHAT_HTML)
        self.assertNotIn('id="chat-model-search"', CHAT_HTML)
        self.assertNotIn('id="model-catalog-search"', CHAT_HTML)

    @unittest.skipUnless(shutil.which('node'), 'Node required for persisted-connection selection test')
    def test_saved_connection_models_can_be_selected_without_reentering_key(self):
        source = CHAT_JS[CHAT_JS.index('function connectionModelChoices('):CHAT_JS.index('function updateCatalogAvailability(')]
        script = r"""
const assert=require('node:assert/strict');
const models=Array.from({length:203},(_,i)=>'model-'+i);
const connections=[{id:'conn_saved',name:'已保存的服务',models,profiles:[{id:'bound-id',kind:'vlm',model:'model-1'}]}];
const profiles=[{id:'old-static',kind:'llm',model:'old',provider:'static'},
  {id:'bound-id',kind:'vlm',model:'model-1',provider:'registered'}];
const all=new Function('modelConnections','registeredModelProfiles',process.env.SOURCE+';return allModelChoices')(connections,profiles);
const llm=all('llm'),vlm=all('vlm');
assert.equal(llm.length,204);assert.equal(vlm.length,203);
assert.equal(vlm.find(v=>v.label.endsWith('model-1')).value,'bound-id');
assert(llm.some(v=>v.value==='candidate:conn_saved:model-2'));
assert(!vlm.some(v=>v.value==='old-static'));
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for model catalog source test')
    def test_connection_catalog_is_complete_and_never_uses_old_server_models(self):
        source = CHAT_JS[CHAT_JS.index('function connectionModelChoices('):CHAT_JS.index('function updateCatalogAvailability(')]
        script = r"""
const assert=require('node:assert/strict');
const choices=new Function(process.env.CHOICES_SOURCE+';return connectionModelChoices')();
const old=[{id:'old',kind:'llm',provider:'server',model:'old-model'}];
assert.deepEqual(choices(null,old,'llm'),[]);
assert.equal(choices(null,old,'llm',true)[0].value,'old');
const connection={id:'conn_test',models:Array.from({length:204},(_,i)=>'model-'+i),profiles:[{id:'bound',kind:'llm',model:'model-0'}]};
const text=choices(connection,old,'llm'),vision=choices(connection,old,'vlm');
assert.equal(text.length,204);assert.equal(vision.length,204);
assert.equal(text[0].value,'bound');assert(vision[0].value.startsWith('candidate:'));
assert(!text.some(x=>x.label.includes('old-model')));
assert.equal(choices({...connection,models:[],profiles:[]},old,'llm').length,0);
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'CHOICES_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for connection feedback test')
    def test_connection_rejection_is_visible_and_preserves_unsaved_key(self):
        source = CHAT_JS[CHAT_JS.index('async function saveApiConfig('):CHAT_JS.index("$('saved-api-connection').addEventListener")]
        script = r"""
const assert=require('node:assert/strict');
const fields={'api-name':{value:''},'api-provider':{value:'openai-compatible'},'api-base-url':{value:'https://not-approved.example/v1'},'api-key':{value:'synthetic-test-key'}};
let calls=0,fail=true,status='',selected='';
const save=new Function('$','settingsAction','approvedModelUrls','settingsStatus','api','loadModelSettings','discoverConnection','const settingsPath="/settings";'+process.env.SAVE_SOURCE+';return saveApiConfig')(
id=>fields[id],async fn=>{try{await fn()}catch(e){status=e.message}},['https://approved.example/v1'],text=>status=text,
async(path,{body})=>{calls++;assert.equal(body.name,'approved.example');if(fail)throw new Error('明确的拒绝原因');return {connection:{id:'conn_saved'}}},
async id=>{selected=id},async()=>{status='连接已保存，但目录失败，可以重试'});
(async()=>{
await save();assert.equal(calls,0);assert.match(status,/尚未获准/);assert.equal(fields['api-key'].value,'synthetic-test-key');
fields['api-base-url'].value='https://approved.example/v1/';await save();assert.equal(calls,1,status);assert.equal(status,'明确的拒绝原因');assert.equal(fields['api-key'].value,'synthetic-test-key');
fields['api-base-url'].value='https://approved.example';await save();assert.equal(calls,2,status);assert.equal(fields['api-key'].value,'synthetic-test-key');
fail=false;await save();assert.equal(fields['api-key'].value,'');assert.equal(selected,'conn_saved');assert.match(status,/连接已保存/);
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SAVE_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for model defaults behavior test')
    def test_defaults_apply_only_to_new_chat_and_history_keeps_model_ids(self):
        source = CHAT_JS[CHAT_JS.index('async function loadModels('):CHAT_JS.index('async function loadBuildVersion(')]
        restore = CHAT_JS[CHAT_JS.index('function restoreModelChoice('):CHAT_JS.index("$('prepare-task').addEventListener")]
        script = r"""
const assert=require('node:assert/strict');
function select(){return {value:'',options:[],replaceChildren(){this.options=[];this.value=''},append(o){this.options.push(o)}}}
const fields={'llm-model':select(),'vlm-model':select(),'default-llm-model':select(),'default-vlm-model':select(),'model-status':{}};
const state={modelChoiceTouched:false};
let data={items:[{id:'old',kind:'llm',provider:'p',model:'old'},{id:'new',kind:'llm',provider:'p',model:'new'},{id:'vision',kind:'vlm',provider:'p',model:'vision'}],defaults:{llm:'new',vlm:'vision'},runtime:{engine:'dsh'}};
const [load,restore]=new Function('state','$','api','document','updateModelMode',process.env.MODEL_SOURCE+'\n'+process.env.RESTORE_SOURCE+';return [loadModels,restoreModelChoice]')(
state,id=>fields[id],async()=>data,{createElement(){return {}}},()=>{});
(async()=>{
await load();assert.equal(fields['llm-model'].value,'new');
restore({llm:'old',vlm:'vision'});
await load(true);assert.equal(fields['llm-model'].value,'old');assert.equal(fields['default-llm-model'].value,'new');
restore({llm:'removed-history-id',vlm:'vision'});assert.equal(fields['llm-model'].value,'old');
assert(!fields['llm-model'].options.some(o=>o.value==='removed-history-id'));
state.modelChoiceTouched=false;data.defaults={llm:'',vlm:''};await load();assert.equal(fields['vlm-model'].value,'');
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'MODEL_SOURCE': source,
            'RESTORE_SOURCE': restore}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for contextual conversation test')
    def test_current_terms_reach_model_and_questions_do_not_trigger_expansion(self):
        source = CHAT_JS[CHAT_JS.index('async function prepareTask(){'):
                         CHAT_JS.index('async function reviseGuidedExpansion(')]
        start = source.index('function dialogueCommandScope(')
        end = source.index('async function applyDialogueTurn(')
        source = (source[:start] + "function pendingDialogueCommand(){return null}"
                  "async function sendDialogueCommand(body,selection,signal){return api('/v1/retrieval/projects/demo/dialogue-command',{method:'POST',body,signal})}" + source[end:])
        script = r"""
const assert=require('node:assert/strict');
const groups=[{primary_label:'猫',auxiliary_terms:['猫在树下','猫在窗边']}];
const state={dialogueId:'dlg_test',dialogueVersion:1,dialogueStage:'terms',proposedExpansion:{raw:'5张猫图',count:5,lines:'猫 | 猫在树下，猫在窗边'}};
const fields={'request-text':{value:'这些为什么这样写？'},'target-count':{value:'5'},'llm-model':{value:'text'},'vlm-model':{value:'vision'},'prepare-task':{disabled:false},'stop-request':{classList:{add(){},remove(){}}},status:{textContent:''}};
let action='answer',revisions=0;
const replies=[];
const api=async(path,{body})=>{assert.deepEqual(body.current_groups,groups);assert.equal(body.current_target_count,5);if(action==='revise_terms')fields['llm-model'].value='next-model';return {
 session_id:'dlg_test',version:2,stage:'terms',action,reply:'它们覆盖不同的画面场景。',request:{target_count:5},
 count_edits:[{target:'terms',labels:['猫'],operation:'add',value:2}],expansion_instruction:'猫组增加两个场景词'};};
const revise=async(p,e,f,turn,selection)=>{revisions++;assert.equal(turn.count_edits[0].value,2);assert.equal(turn.expansion_instruction,'猫组增加两个场景词');assert.equal(selection.llm,'text')};
const prepare=new Function('state','$','api','message','setDialogueBusy','saveDialogueSession','groupInputBody','reviseGuidedExpansion',process.env.TURN_SOURCE+';return prepareTask')(
 state,id=>fields[id],api,(role,title,text)=>replies.push([role,text]),v=>{fields['prepare-task'].disabled=v;assert.equal(fields['llm-model'].disabled,undefined)},()=>{},()=>({groups}),revise);
(async()=>{await prepare();assert.equal(revisions,0);assert.equal(replies.at(-1)[1],'它们覆盖不同的画面场景。');
action='revise_terms';fields['llm-model'].value='text';fields['request-text'].value='那再补两个';await prepare();assert.equal(revisions,1)})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'TURN_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for reference sentence behavior test')
    def test_reference_sentence_is_optional_and_does_not_overwrite_user_text(self):
        source = CHAT_JS[CHAT_JS.index('function insertRequestExample(){'):
                         CHAT_JS.index('async function restoreDialogueSession(')]
        script = r"""
const assert=require('node:assert/strict');
const input={value:'',focus(){this.focused=true},setSelectionRange(a,b){this.selection=[a,b]}};
const status={textContent:''};
const insert=new Function('$',process.env.INSERT_SOURCE+';return insertRequestExample')(
  id=>({'request-text':input,'status':status})[id]);
insert();
assert.match(input.value,/【内容】.*【数量】.*格式/);
assert.deepEqual(input.selection,[1,5]);
input.value='找3张猫图片';insert();
assert.equal(input.value,'找3张猫图片');
assert.match(status.textContent,/保留你的文字/);
"""
        result = subprocess.run(['node', '-e', script],
                                env={**os.environ, 'INSERT_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_clarification_uses_natural_language_confirmation(self):
        source = CHAT_JS[CHAT_JS.index('async function prepareTask(){'):
                         CHAT_JS.index('async function reviseGuidedExpansion(')]
        self.assertIn("if(turn.action==='confirm'", source)
        self.assertNotIn('isAgreement(', source)

    def test_submit_shows_progress_and_surfaces_timeout_in_conversation(self):
        source = CHAT_JS[CHAT_JS.index('async function prepareTask(){'):
                         CHAT_JS.index('async function reviseGuidedExpansion(')]
        self.assertIn('正在理解需求……', source)
        self.assertIn("onStage('正在生成检索词')", source)
        self.assertIn('正在理解检索词调整要求', source)
        self.assertIn('正在生成检索词', source)
        self.assertIn("if(typeof refreshHistory==='function')refreshHistory()", source)
        self.assertIn("error.code==='DSH_TIMEOUT'?'扩词 Agent 等待超时", CHAT_JS)
        self.assertIn('已等待 ${waitSeconds} 秒', source)
        self.assertIn('clearInterval(waitTimer)', source)
        self.assertIn("e.code==='DSH_TIMEOUT'", source)
        self.assertIn("showDialogueFailure(e,explanation)", source)
        self.assertIn("e.name==='AbortError'", source)
        self.assertIn('controller.signal', source)
        self.assertIn('id="stop-request"', CHAT_HTML)

    def test_history_includes_dialogue_time_and_bounded_delete(self):
        self.assertIn('/v1/retrieval/projects/demo/dialogue-sessions', CHAT_JS)
        self.assertIn("year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'", CHAT_JS)
        self.assertIn("item.confirmation_count>0", CHAT_JS)
        self.assertIn('function visibleDialogueChats(items)', CHAT_JS)
        self.assertIn('item.message_count>0', CHAT_JS)
        self.assertIn("method:'DELETE'", CHAT_JS)
        # Confirmed Chats and legacy tasks both get a force-delete × button.
        self.assertIn('/v1/retrieval/projects/demo/chat-history?', CHAT_JS)
        self.assertIn("path+(force?'?force=true':'')", CHAT_JS)
        start = CHAT_JS[CHAT_JS.index('function startNewChat()'):
                        CHAT_JS.index("const settingsPath=")]
        self.assertNotIn("api('/v1/retrieval/projects/demo/dialogue-sessions'", start)
        self.assertIn("sessionStorage.removeItem('dsh-dialogue')", start)

    @unittest.skipUnless(shutil.which('node'), 'Node required for new-chat behavior test')
    def test_new_chat_does_not_create_empty_history_records(self):
        source = CHAT_JS[CHAT_JS.index('function visibleDialogueChats(items)'):
                         CHAT_JS.index('async function deleteHistoryChat(')]
        start = CHAT_JS[CHAT_JS.index('function startNewChat()'):
                        CHAT_JS.index('const settingsPath=')]
        start += CHAT_JS[CHAT_JS.index('function showHistoryPolicy()'):CHAT_JS.index('function bindHistoryControls()')]
        script = r"""
const assert=require('node:assert/strict');
let reloads=0,focuses=0;
const removed=[];
const state={task:null,dialogueId:null,conversationId:null};
const localStorage={removeItem:key=>removed.push(key)};
const sessionStorage={removeItem:key=>removed.push(key)};
const location={reload:()=>reloads++};
const $=()=>({focus:()=>focuses++});
const [visible,start]=new Function('state','localStorage','sessionStorage','location','$','cancelDialogueRequest',
  'const key=()=>"new-chat";'+process.env.SOURCE+'\n'+process.env.START+';return [visibleDialogueChats,startNewChat]')(
  state,localStorage,sessionStorage,location,$,()=>{});
assert.deepEqual(visible([{message_count:0},{message_count:1}]),[{message_count:1}]);
start();start();
assert.equal(focuses,2);assert.equal(reloads,0);assert.deepEqual(removed,[]);
state.dialogueId='existing-session';start();
assert.equal(reloads,1);assert(removed.includes('dsh-dialogue'));
"""
        result = subprocess.run(['node', '-e', script],
                                env={**os.environ, 'SOURCE': source, 'START': start},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for failed-turn model changes')
    def test_switching_model_after_known_failure_releases_original_turn_before_new_send(self):
        start = CHAT_JS.index('  const unresolved=pendingDialogueCommand();if(unresolved){')
        source = CHAT_JS[start:CHAT_JS.index('  if(state.dialogueModelBinding)', start)]
        script = r"""
(async()=>{
const assert=require('node:assert/strict');let selected='new-model',release=true,retries=[],newSends=0;
const pending={body:{message:'same request',llm_id:'old-model'}};
const run=new Function('pendingDialogueCommand','$','recoverDialogueCommand','prepareTask','text','return async()=>{'+process.env.SOURCE+';return "continue"}')(
 ()=>pending,()=>({value:selected}),async(...args)=>{retries.push(args);return release},()=>newSends++,'same request');
await run();assert.deepEqual(retries,[[false,true]]);assert.equal(newSends,1);
release=false;retries=[];await run();assert.deepEqual(retries,[[false,true]]);assert.equal(newSends,1);
selected='old-model';retries=[];await run();assert.deepEqual(retries,[[true]]);assert.equal(newSends,1);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for dialogue refresh recovery')
    def test_saved_dialogue_refresh_restores_session_instead_of_starting_empty_chat(self):
        source = (CHAT_JS[CHAT_JS.index('function saveDialogueSession()'):CHAT_JS.index('async function restoreDialogueSession(')] +
                  CHAT_JS[CHAT_JS.index('function chatNavigationIntent()'):CHAT_JS.index('function startNewChat()')])
        script = r"""
const assert=require('node:assert/strict');const saved=new Map([['dsh-fresh-chat','token']]);
const sessionStorage={getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)};
const localStorage={getItem:()=>null,removeItem(){}};
const state={dialogueId:'dlg_saved',proposedExpansion:{count:60}};
const [save,intent]=new Function('state','sessionStorage','localStorage','saveCurrentModelChoice','location',process.env.SOURCE+';return [saveDialogueSession,chatNavigationIntent]')(
 state,sessionStorage,localStorage,()=>{},{search:''});
save();assert.deepEqual(intent(),{fresh:false,taskId:null});assert.equal(JSON.parse(saved.get('dsh-dialogue')).id,'dlg_saved');assert.equal(JSON.parse(saved.get('dsh-dialogue')).proposal.count,60);
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for navigation isolation test')
    def test_fresh_chat_ignores_old_storage_and_invalidates_pending_history_reads(self):
        source = CHAT_JS[CHAT_JS.index('function chatNavigationIntent()'):CHAT_JS.index('function syncRefillFields()')]
        script = r"""
const assert=require('node:assert/strict');
const local=new Map([['dsh-task-id','old-task']]),session=new Map([['dsh-dialogue','old-dialogue']]);
const store=m=>({getItem:k=>m.get(k)||null,setItem:(k,v)=>m.set(k,v),removeItem:k=>m.delete(k)});
let reloads=0;const location={search:'',reload(){reloads++}};
const state={task:{task_id:'old-task'},historyOpenToken:7,sidebarReadToken:4};
const [intent,start]=new Function('state','localStorage','sessionStorage','location','$','cancelDialogueRequest','showHistoryPolicy','key',
 process.env.SOURCE+';return [chatNavigationIntent,startNewChat]')(
 state,store(local),store(session),location,()=>({focus(){}}),()=>{},()=>{},()=> 'fresh-token');
assert.equal(intent().taskId,'old-task');start();assert.equal(reloads,1);
assert.equal(state.historyOpenToken,8);assert.equal(state.sidebarReadToken,5);assert.equal(state.task,null);
assert.equal(session.get('dsh-fresh-chat'),'fresh-token');
local.set('dsh-task-id','late-old-task');session.set('dsh-dialogue','late-old-dialogue');
assert.deepEqual(intent(),{fresh:true,taskId:null});assert(!local.has('dsh-task-id'));assert(!session.has('dsh-dialogue'));
const linked='rt_'+'a'.repeat(32);location.search='?task='+linked;
assert.deepEqual(intent(),{fresh:false,taskId:linked});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("if(bootEpoch!==(state.historyOpenToken||0))return", CHAT_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for deficit suggestion test')
    def test_authoritative_gap_offers_new_unconfirmed_term_draft(self):
        stop_label = CHAT_JS[CHAT_JS.index('function refillStopLabel('):
                             CHAT_JS.index('function completedNodeOutput(')]
        source = stop_label + CHAT_JS[CHAT_JS.index('const resultViewLabels='):
                                      CHAT_JS.index('async function loadMoreResults(')]
        source = 'const window={};' + CHAT_JS[CHAT_JS.index('const pipelineResponseTraces='):CHAT_JS.index('const headers=')] + source
        script = r"""
(async()=>{
const assert=require('node:assert/strict');
const state={task:{task_id:'task-one',confirmed_revision:2,run_status:'PARTIAL',pending_draft:null}};
const fields={'result-details':{open:false},'result-compact-counts':{},'result-list':{replaceChildren(){}},'result-sidebar':{classList:{remove(){}}},
  'result-counts':{replaceChildren(){},append(){}},'result-next-action':{},
  'result-next-title':{},'result-next-detail':{},'result-next-button':{}};
const document={querySelectorAll:()=>[],querySelector:()=>null,
  createElement:()=>({append(){},className:'',textContent:''})};
let cards=[],draft=null;
const card=(title,rows,actions)=>{cards.push({title,rows,actions});return {}};
const showPostTaskExpansion=(text,intent,role)=>{draft={text,intent,role}};
let refillPlan={action:'partial_delivery',stop_reason:'SEARCH_STRATEGIES_EXHAUSTED'};
const api=async()=>refillPlan;
const show=new Function('state','$','card','showPostTaskExpansion','api','document',process.env.RESULT_SOURCE+';return showResults')(
  state,id=>fields[id],card,showPostTaskExpansion,api,document);
const results={available:true,view:'accepted',revision:2,run_generation:1,result_set_digest:'digest',
  counts:{accepted:12,reserve:0,duplicate:0,failed:0,pending_review:0,gap:8},
  stop_reason:'exhausted',items:[],next_cursor:null,note:'partial'};
show(results,'task-one');
await new Promise(setImmediate);
const offer=cards.find(row=>row.title==='有效数量不足');
assert(offer);assert.equal(offer.rows[0][1],8);
assert.equal(cards.some(row=>row.title==='补数状态读取失败'),false);
cards=[];show({...results,stop_reason:'INPUT_SCOPE_COMPLETE'},'task-one');
await new Promise(setImmediate);
assert.equal(state.currentResults.stop_reason,'INPUT_SCOPE_COMPLETE');
offer.actions[0][1]();
assert.equal(draft.role,'system');
assert.equal(draft.intent.kind,'expansion');
assert.equal(draft.intent.target_count,null);
assert.match(draft.intent.expansion_instruction,/缺口 8 张/);
cards=[];refillPlan={action:'await_owner',gap:8,revision:2,generation:1,task_version:4,
  next_position:{query:'原确认检索词',library_id:'library-a',position:{cursor:'cursor-2'},position_fingerprint:'sha256:'+'1'.repeat(64)}};
state.task.run_status='AWAITING_INPUT';
show(results,'task-one');await new Promise(setImmediate);
const refill=cards.find(row=>row.title==='继续补数');
assert(refill);assert.equal(refill.rows.find(row=>row[0]==='检索词')[1],'原确认检索词');
assert.match(refill.rows.find(row=>row[0]==='说明')[1],/沿用当前已确认检索词/);
cards=[];state.task.run_status='PLANNING';show(results,'task-one');
assert(!cards.some(row=>row.title==='有效数量不足'));
cards=[];state.task.run_status='PARTIAL';state.task.pending_draft={draft_id:'pending'};show(results,'task-one');
assert(!cards.some(row=>row.title==='有效数量不足'));
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'RESULT_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for refill confirmation test')
    def test_approved_refill_position_is_confirmed_without_resubmitting_terms(self):
        source = CHAT_JS[CHAT_JS.index('async function confirmRefillIteration('):
                         CHAT_JS.index('async function loadMoreResults(')]
        script = r"""
(async()=>{
const assert=require('node:assert/strict');
const state={task:{task_id:'task-one'}};let calls=[],busy=[],messages=[];
const plan={revision:2,generation:3,task_version:4,next_position:{position_fingerprint:'sha256:'+'1'.repeat(64)}};
const api=async(path,options={})=>{calls.push({path,options});if(options.method==='POST')return{status:'committed',replayed:false,iteration:1};if(path.endsWith('/progress'))return{};if(path.includes('/results?'))return{available:true};return{task_id:'task-one'}};
const confirm=new Function('state','api','setBusy','message','showProgress','showResults','refreshTaskSidebar','$',process.env.SOURCE+';return confirmRefillIteration')(
 state,api,value=>busy.push(value),(role,title,text)=>messages.push({role,title,text}),()=>{},()=>{},()=>{},()=>({textContent:''}));
await confirm('task-one',plan);
const request=calls[0];
assert.equal(request.path,'/v1/retrieval/tasks/task-one/refill');
assert.equal(request.options.method,'POST');
assert.deepEqual(Object.keys(request.options.body).sort(),['action','expected_generation','expected_revision','expected_task_version','position_fingerprint']);
assert.equal(request.options.body.position_fingerprint,plan.next_position.position_fingerprint);
assert(!('query' in request.options.body));assert(!('library_id' in request.options.body));
assert.match(request.options.keyValue,/^refill:task-one:sha256:/);
assert.deepEqual(busy,[true,false]);assert.equal(messages[0].title,'补数轮次已启动');
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script],
                                env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for incremental manifest test')
    def test_refill_candidates_require_manifest_confirmation_without_overwriting_results(self):
        source = CHAT_JS[CHAT_JS.index('async function showCandidateManifestReview(){'):
                         CHAT_JS.index('function offerVlmCleaning(manifest){')]
        script = r"""
(async()=>{
const assert=require('node:assert/strict');
const proposal={kind:'candidate_manifest_proposal',confirmed:false,candidate_count:2,
  proposal_digest:'sha256:'+'1'.repeat(64),expected_task_version:7,note:'incremental'};
const manifest={...proposal,kind:'candidate_input_manifest',confirmed:true,manifest_ref:'manifest-new'};
const state={task:{task_id:'task-one',task_version:7},searchSnapshot:{results:{available:true}}};
let calls=[],cards=[],messages=[],vlmOffers=0,refreshes=0;
const api=async(path,options={})=>{calls.push({path,options});return options.method==='POST'?manifest:proposal};
const message=(role,title,text)=>{const line={textContent:text};messages.push(line);return {querySelector:()=>line}};
const review=new Function('state','api','setBusy','message','refreshTaskSidebar','offerVlmCleaning','$',
 process.env.SOURCE+';return showCandidateManifestReview')(
 state,api,()=>{},message,
 async()=>{refreshes++},()=>{vlmOffers++},()=>({textContent:''}));
const originalResults=state.searchSnapshot.results;
await review();
const request=calls.find(item=>item.options.method==='POST');
assert.equal(request.options.keyValue,'candidate-manifest:'+proposal.proposal_digest);
assert.deepEqual(request.options.body,{proposal_digest:proposal.proposal_digest,
  expected_task_version:7,action:'confirm'});
assert.equal(state.candidateManifest.manifest_ref,'manifest-new');
assert.equal(state.searchSnapshot.results,originalResults);
assert.equal(calls.filter(item=>item.options.method==='POST').length,1);
assert.equal(vlmOffers,1);assert.equal(refreshes,1);
assert.match(messages.at(-1).textContent,/已确认本轮新增 2 张/);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script],
                                env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for downstream search status test')
    def test_published_results_hide_expected_stale_first_search_error(self):
        source = CHAT_JS[CHAT_JS.index('async function refreshTaskSidebar(workspaceSeed=null){'):
                         CHAT_JS.index('function showResults(')]
        script = r"""
(async()=>{
const assert=require('node:assert/strict');
const task={task_id:'task-one',confirmed_revision:1,run_generation:1,task_version:7};
const state={task,sidebarReadToken:0};
const workflow={binding:{task_version:7},summary:'清洗已完成，当前入选 1 张。',nodes:[],available_actions:[]};
const progress={task_id:task.task_id,revision:1,run_generation:1,task_version:7,counts:{target:10,accepted:1,returned:5},workflow,nodes:[]};
const results={task_id:task.task_id,revision:1,run_generation:1,task_version:7,available:true,counts:{accepted:1}};
const calls=[];const api=async path=>{calls.push(path);if(path.endsWith('/workspace'))return {...task,progress,workflow,manifest:null,search:null};if(path.includes('/results?'))return results;throw Error('unexpected '+path)};
const fields={'task-sidebar-summary':{textContent:''},'retrieval-live-status':{textContent:''}};let shown;
const refresh=new Function('state','api','$','showProgress',process.env.SOURCE+';return refreshTaskSidebar')(state,api,id=>fields[id],(...args)=>shown=args);
await refresh();assert(shown);assert.equal(shown[1],results);assert(!state.searchSnapshot.error);
assert.equal(calls.length,2);assert(!calls.some(path=>path.includes('/first-search/')));
assert.equal(fields['retrieval-live-status'].textContent,workflow.summary);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script],
                                env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for stale polling test')
    def test_frozen_task_stops_polling_stale_search_until_version_moves(self):
        source = CHAT_JS[CHAT_JS.index('async function refreshTaskSidebar(workspaceSeed=null){'):
                         CHAT_JS.index('function showResults(')]
        script = r"""
(async()=>{
const assert=require('node:assert/strict');const state={task:{task_id:'t',confirmed_revision:1,run_generation:1,task_version:7}};
let version=7;const calls={search:0,manifest:0};
const api=async path=>{
 if(path.includes('/first-search/progress')){calls.search++;throw Error('stale')}
 if(path.includes('/first-search/manifest')){calls.manifest++;throw Error('stale')}
 if(path.endsWith('/workspace')){const workflow={binding:{task_version:version},summary:'清洗已完成',nodes:[],available_actions:[]};return {...state.task,task_version:version,workflow,progress:{task_id:'t',revision:1,run_generation:1,task_version:version,counts:{target:10,accepted:2},nodes:[],workflow},manifest:null,search:null}}
 if(path.includes('/results?'))return {task_id:'t',revision:1,run_generation:1,task_version:7,available:true,counts:{accepted:1}};
 throw Error('unexpected '+path);
};
const fields={'task-sidebar-summary':{textContent:''},'retrieval-live-status':{textContent:''}};
const refresh=new Function('state','api','$','showProgress',process.env.SOURCE+';return refreshTaskSidebar')(state,api,id=>fields[id],()=>{});
await refresh();await refresh();await refresh();assert.deepEqual(calls,{search:0,manifest:0});
assert.equal(state.searchSnapshot.results.counts.accepted,1);
version=8;await refresh();await refresh();await refresh();assert.deepEqual(calls,{search:0,manifest:0});
assert.equal(state.task.task_version,8);assert.equal(state.searchSnapshot.results,undefined,'old result pages cannot cross a version');
assert.doesNotMatch(fields['retrieval-live-status'].textContent,/读取失败/);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script],
                                env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for manifest prompt test')
    def test_ready_auto_window_prompts_candidate_confirmation_once(self):
        source = CHAT_JS[CHAT_JS.index('async function refreshTaskSidebar(workspaceSeed=null){'):
                         CHAT_JS.index('function showResults(')]
        script = r"""
(async()=>{
const assert=require('node:assert/strict');const state={task:{task_id:'t',confirmed_revision:1,run_generation:1,task_version:2}};
let confirmed=false;const api=async path=>{
 if(path.endsWith('/workspace')){const detail=confirmed?'检索已结束，候选清单已确认。':'首轮检索已返回，候选尚未确认。';const workflow={binding:{task_version:2},summary:detail,nodes:[{node:'retrieval',detail}],available_actions:confirmed?[]:[{id:'confirm_candidates'}]};
 return {...state.task,workflow,progress:{task_id:'t',revision:1,run_generation:1,task_version:2,counts:{target:10,returned:60},nodes:workflow.nodes,workflow},search:null,manifest:confirmed?{confirmed:true,candidate_count:60,expected_task_version:2}:null}}
 if(path.includes('/results?'))return {task_id:'t',revision:1,run_generation:1,task_version:2,available:false};
 throw Error('unexpected '+path);
};
const prompts=[],messages=[],fields={'task-sidebar-summary':{textContent:''},'retrieval-live-status':{textContent:''}};
const refresh=new Function('state','api','$','showProgress','message','guidedAction','showCandidateManifestReview',process.env.SOURCE+';return refreshTaskSidebar')(
 state,api,id=>fields[id],()=>{},(kind,title,text)=>{messages.push(title);return {title,text}},(bubble,label)=>prompts.push([bubble.title,label,bubble.text]),()=>{});
await refresh();await refresh();assert.deepEqual(prompts.map(row=>row.slice(0,2)),[['检索结果已返回','确认候选清单']]);
assert.doesNotMatch(prompts[0][2],/已校验/,'raw hits cannot be called verified candidates');
confirmed=true;state.manifestPrompted.clear();await refresh();assert.equal(prompts.length,1);
state.searchSnapshot=null;state.manifestPrompted.clear();await refresh();assert(!messages.includes('检索已启动'));
assert.equal(state.candidateManifest.candidate_count,60);assert.match(fields['retrieval-live-status'].textContent,/检索已结束/);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script],
                                env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_incremental_manifest_does_not_override_completed_retrieval(self):
        self.assertIn("authority?available.some(action=>action.id==='confirm_candidates')", CHAT_JS)
        self.assertIn("emptyManifest&&node.node_status==='succeeded'?'检索已完成", CHAT_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for history Chat navigation test')
    def test_open_dialogue_clears_previous_task_and_loads_in_place(self):
        source = CHAT_JS[CHAT_JS.index('async function openDialogueSession('):
                         CHAT_JS.index('function fillDraftForm(')]
        script = r"""
const assert=require('node:assert/strict');
const values=new Map([['dsh-task-id','task-hello']]);
let restored=false;
const state={task:{task_id:'task-hello'},conversationId:'old',dialogueId:null,draft:{},resumeDraft:{}};
const localStorage={getItem:key=>values.get(key)||null,removeItem:key=>values.delete(key)};
const sessionStorage={setItem:(key,value)=>values.set(key,value),getItem:key=>values.get(key)||null,removeItem:key=>values.delete(key)};
const fields={'llm-model':{value:'llm-id'},'vlm-model':{value:'vlm-id'},'new-request':{classList:{remove(){}}},'feedback-box':{classList:{add(){}}}};
const open=new Function('state','localStorage','sessionStorage','readStoredModelChoice','$','restoreDialogueSession','cancelDialogueRequest',
  process.env.OPEN_SOURCE+';return openDialogueSession')(
  state,localStorage,sessionStorage,()=>null,id=>fields[id],async()=>{restored=true},()=>{});
(async()=>{
  await open('dlg-dogs');
  assert(restored);assert.equal(state.task,null);assert.equal(state.conversationId,null);
  assert.equal(localStorage.getItem('dsh-task-id'),null);
  assert.equal(JSON.parse(sessionStorage.getItem('dsh-dialogue')).id,'dlg-dogs');
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'OPEN_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_five_stage_workflow_is_visible_before_formal_task(self):
        self.assertIn('id="task-sidebar"', CHAT_HTML)
        self.assertNotIn('class="rail-card hidden" id="task-sidebar"', CHAT_HTML)
        self.assertIn('五节点进度：扩检索词、检索、清洗、补数、打包', CHAT_HTML)
        self.assertIn("function renderPlanningWorkflow(stage='request')", CHAT_JS)
        self.assertIn("renderPlanningWorkflow('expansion')", CHAT_JS)
        self.assertIn("renderPlanningWorkflow('request');resetResultWorkspace();syncRefillFields();void loadBuildVersion()", CHAT_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for refill round evidence test')
    def test_refill_rounds_show_returned_new_and_cumulative_valid_counts(self):
        source = CHAT_JS[CHAT_JS.index('function refillStopLabel('):
                         CHAT_JS.index('function completedNodeOutput(')]
        source += CHAT_JS[CHAT_JS.index('async function offerRefillAction('):
                          CHAT_JS.index('async function confirmRefillIteration(')]
        script = r"""
(async()=>{
const assert=require('node:assert/strict');
const state={task:{task_id:'task-one',confirmed_revision:2}};
const execution={issued_rounds:2,completed_rounds:2,stagnant_rounds:1,vlm_calls:9,max_rounds:3,
  rounds:[{iteration:1,baseline_accepted:3,candidate_count:6,vlm_calls:6,outcome:'result',accepted_after:5,new_unique_accepted:2},
          {iteration:2,baseline_accepted:5,candidate_count:3,vlm_calls:3,outcome:'empty',accepted_after:5,new_unique_accepted:0}]};
const plan={action:'partial_delivery',target_count:10,accepted_count:5,gap:5,stop_reason:'NO_PROGRESS',execution};
let cards=[];const card=(title,rows)=>{cards.push({title,rows});return {}};
const api=async path=>{if(path.includes('/nodes/refill/items?'))return {...plan,items:execution.rounds,next_cursor:null};assert.equal(path,'/v1/retrieval/tasks/task-one/refill');return plan};
const nodes=[];const doc={createElement(){const el={children:[],textContent:'',append(...xs){this.children.push(...xs)},replaceChildren(){this.children=[]}};nodes.push(el);return el}};
const build=new Function('state','card','api','document','openDelivery','offerGapExpansion',
  process.env.SOURCE+';return {offerRefillAction,loadRefillRounds,refillRoundRows}');
const ui=build(state,card,api,doc,()=>{},()=>{});
await ui.offerRefillAction({revision:2,counts:{accepted:5,gap:5}},'task-one',{});
const stopped=cards.find(row=>row.title==='自动补数已停止');
assert(stopped);
const round1=stopped.rows.find(row=>row[0]==='第 1 轮 · 已出结果');
assert.equal(round1[1],'返回候选 6 · 新增有效 2 · 累计有效 5 · VLM 调用 6');
assert.equal(stopped.rows.find(row=>row[0]==='第 2 轮 · 无新增候选')[1],'返回候选 3 · 新增有效 0 · 累计有效 5 · VLM 调用 3');
assert.equal(ui.refillRoundRows({rounds:[{iteration:3,baseline_accepted:5,candidate_count:4,vlm_calls:4,outcome:'pending',accepted_after:null,new_unique_accepted:null}]})[0][1],
  '返回候选 4 · 新增有效 待结果 · 累计有效 5 · VLM 调用 4');
const container=doc.createElement(),button={disabled:false};
await ui.loadRefillRounds('task-one',container,button);
const text=container.children.map(el=>el.textContent);
assert.match(text[0],/累计有效 5 · 缺口 5 · 停止原因 连续补数没有新增有效结果 · 已发 2 轮 · 已完成 2 轮 · VLM 调用 9/);
assert(text.includes('第 1 轮 · 已出结果：返回候选 6 · 新增有效 2 · 累计有效 5 · VLM 调用 6'));
assert.equal(button.disabled,false);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_completed_workflow_nodes_are_expandable_and_show_real_output(self):
        self.assertIn("const completed=statusText==='已完成'", CHAT_JS)
        self.assertIn("document.createElement(expandable?'details':'div')", CHAT_JS)
        self.assertIn("function completedNodeOutput(node,progress,results)", CHAT_JS)
        self.assertIn("node.node==='cleaning'&&results?.available", CHAT_JS)
        self.assertIn("node.node==='refill'&&results?.available", CHAT_JS)
        self.assertIn("node.node==='packaging'&&results?.available", CHAT_JS)
        self.assertIn("output.textContent=completedNodeOutput(node,progress,results)", CHAT_JS)
        self.assertIn("node.node==='refill'&&statusText!=='未开始'", CHAT_JS)
        self.assertIn("loadRefillRounds(progress.task_id,output,button)", CHAT_JS)
        self.assertIn("尚未发布独立输出", CHAT_JS)

    def test_restore_replays_saved_assistant_without_duplicate_summary(self):
        restore = CHAT_JS[CHAT_JS.index('async function restoreDialogueSession('):
                          CHAT_JS.index('async function prepareTask()')]
        self.assertIn("!current.messages.some(item=>item.role==='assistant')", restore)
        self.assertNotIn("await prepareGuidedExpansion(current.request.raw_request,current.request.target_count);return", restore)
        self.assertIn("guidedAction(bubble,'重新生成检索词'", restore)
        self.assertIn('if(openToken!==state.historyOpenToken)return', restore)

    @unittest.skipUnless(shutil.which('node'), 'Node required for history restore behavior test')
    def test_history_terms_restore_does_not_wait_for_model(self):
        source = CHAT_JS[CHAT_JS.index('async function restoreDialogueSession('):
                         CHAT_JS.index('async function prepareTask()')]
        script = r"""
const assert=require('node:assert/strict');
const state={historyOpenToken:0,proposedExpansion:null,serverModelDefaults:{llm:'fast',vlm:'vision'}};
const seen=[],actions=[];
let workflow=null;
const sessionStorage={getItem:()=>JSON.stringify({id:'dlg-one',proposal:null,models:{llm:'old-glm',vlm:'vision'}})};
const current={session_id:'dlg-one',version:2,stage:'terms',request:{raw_request:'找5张狗图片',target_count:5},
  messages:[{role:'user',text:'找5张狗图片'},{role:'assistant',text:'请确认数量'}]};
const api=async()=>current;
const timeline={replaceChildren(){seen.length=0}};
const message=(role,title,text)=>{seen.push({role,text});return {}};
const guidedAction=(_,label)=>actions.push(label);
const restore=new Function('state','sessionStorage','resetTimeline','api','restoreModelChoice',
  'readStoredModelChoice','timeline','message','showTermReview','guidedAction','setBusy',
  'prepareGuidedExpansion','$','refreshHistory','renderPlanningWorkflow',
  process.env.SOURCE+';return restoreDialogueSession')(
  state,sessionStorage,()=>{},api,models=>{assert.equal(models.llm,'fast');state.restoredModels=models},()=>null,timeline,message,()=>{},guidedAction,
  ()=>{},()=>{throw Error('model must not run while opening history')},()=>({focus(){}}),async()=>{},
  stage=>{workflow=stage});
(async()=>{
  await restore();
  assert.deepEqual(seen.slice(0,2),[{role:'user',text:'找5张狗图片'},
    {role:'assistant',text:'请确认数量'}]);
  assert(actions.includes('重新生成检索词'));
  assert.equal(state.dialogueId,'dlg-one');
  assert.equal(workflow,'expansion');assert.equal(state.restoredModels.llm,'fast');
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script],
                                env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_topic_replaces_unconfirmed_tiger_request(self):
        source = CHAT_JS[CHAT_JS.index('async function prepareTask(){'):
                         CHAT_JS.index('async function reviseGuidedExpansion(')]
        self.assertIn("if(turn.action==='replace')", source)
        self.assertIn('state.proposedExpansion=null', source)
        self.assertNotIn('isStandaloneNewRequest(text)', source)

    def test_grounding_fallback_is_a_plain_clarification(self):
        source = CHAT_JS[CHAT_JS.index('async function prepareTask(){'):
                         CHAT_JS.index('async function reviseGuidedExpansion(')]
        self.assertIn("message('assistant','',turn.reply)", source)
        self.assertNotIn('MODEL_GROUNDING_FAILED', source)

    def test_safe_clarification_yes_image_and_restatement_stay_unconfirmed(self):
        source = CHAT_JS[CHAT_JS.index('async function prepareTask(){'):
                         CHAT_JS.index('async function reviseGuidedExpansion(')]
        self.assertIn('session_id:state.dialogueId', source)
        self.assertIn('expected_version:state.dialogueVersion', source)
        self.assertIn('phase:state.dialogueStage', source)
        self.assertIn("if(turn.stage==='terms'&&!proposal&&turn.request.target_count)", source)
        self.assertNotIn('state.requestParts.push', source)

    def test_user_messages_show_content_without_a_heading(self):
        self.assertIn("if(title){const h=document.createElement('h2')", CHAT_JS)
        self.assertIn("message('user','',item.text)", CHAT_JS)
        self.assertIn("message('user','',text)", CHAT_JS)
        self.assertNotIn("'你的消息'", CHAT_JS)
        self.assertNotIn("'你的补充'", CHAT_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for confirmation flow test')
    def test_confirmed_expansion_starts_search_and_marks_sidebar(self):
        source = CHAT_JS[CHAT_JS.index('async function decideFeedback('):
                         CHAT_JS.index('async function editFeedback(')]
        script = r"""
const assert=require('node:assert/strict');
const events=[];
const state={task:{task_id:'task-1'},feedback:{draft_id:'draft-1',draft_version:1,proposal_digest:'digest',base_task_version:1}};
const fields={'feedback-text':{value:'change'},'feedback-box':{classList:{remove(){}}},'status':{textContent:''}};
const $=id=>fields[id];
const api=async(path)=>{assert.match(path,/confirmations/);events.push('confirm');return {task:{task_id:'task-1',confirmed_revision:2,task_version:2}}};
const refreshTaskSidebar=async()=>events.push('sidebar');
const refreshHistory=async()=>events.push('history');
const guidedStartSearch=async()=>events.push('search');
const message=(kind,title,body)=>{events.push('message');assert.match(body,/核对全部检索词的索引和编码能力/)};
const decideFeedback=new Function('state','$','api','key','setBusy','refreshTaskSidebar','refreshHistory','guidedStartSearch','message',
  process.env.CONFIRM_SOURCE+';return decideFeedback')(
    state,$,api,()=> 'key',()=>{},refreshTaskSidebar,refreshHistory,guidedStartSearch,message);
(async()=>{await decideFeedback('confirm',true);assert.deepEqual(events,['confirm','sidebar','message','history','search'])})().catch(e=>{console.error(e);process.exit(1)});
"""
        subprocess.run(['node', '-e', script], check=True,
                       env={**os.environ, 'CONFIRM_SOURCE': source})
        self.assertIn("card.classList.add('search-active')", CHAT_JS)
        self.assertIn("card.classList.add('search-unavailable')", CHAT_JS)
        self.assertIn("button.textContent='检索候选'", CHAT_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for draft confirmation behavior test')
    def test_term_confirmation_keeps_manual_gates_and_starts_search(self):
        source = CHAT_JS[CHAT_JS.index('function draftBody('):
                         CHAT_JS.index('function groupPreviewBody(')]
        source += CHAT_JS[CHAT_JS.index('async function confirmGuidedExpansion('):
                          CHAT_JS.index('async function guidedStartSearch(')]
        script = r"""
const assert=require('node:assert/strict');
const events=[];
const messages=[];
let rejectDraft=false;
const proposal={raw:'找5张狗图片',count:5,plan:{groups:[]},lines:'狗'};
const state={proposedExpansion:proposal,task:null,resumeDraft:null};
let composerVisible=false,busy=true;
const fields={'new-request':{classList:{add(){}}},'feedback-box':{classList:{remove(){composerVisible=true}}},'status':{textContent:''}};
const $=id=>fields[id];
const api=async(path,options)=>{
  if(path.endsWith('/drafts')){
    assert.deepEqual(options.body.node_modes,{expansion:'manual',retrieval:'manual',cleaning:'manual',refill:'manual',packaging:'manual'});
    if(rejectDraft){const error=new Error('FEATURE_NOT_SUPPORTED: this slice requires five explicit manual gates');error.code='FEATURE_NOT_SUPPORTED';throw error}
    events.push('draft');return {draft_id:'draft-1',draft_version:1,proposal_digest:'digest',conversation_id:'chat-1'};
  }
  assert.equal(path,'/v1/retrieval/tasks');events.push('task');return {task_id:'task-1'};
};
const sessionStorage={removeItem(){}};
const localStorage={setItem(){}};
const timeline={querySelectorAll(){return []}};
const message=(kind,title,body)=>messages.push([title,body]);
const confirm=new Function('state','$','api','key','setBusy','sessionStorage','localStorage','refreshTaskSidebar','retireDraftActions','message','refreshHistory','guidedStartSearch','timeline',
  process.env.CONFIRM_SOURCE+';return confirmGuidedExpansion')(
  state,$,api,()=> 'key',value=>{busy=value},sessionStorage,localStorage,async()=>{},()=>{},message,async()=>{},async()=>{assert.equal(busy,false);assert.equal(composerVisible,true);events.push('search')},timeline);
(async()=>{
  const manual={enabled:false,libraries:[],limits:{max_rounds:null,max_vlm_calls:null,no_progress_rounds:2}};
  await confirm(proposal,{value:'狗',disabled:false},manual);assert.deepEqual(events,['draft','task','search']);assert.equal(state.task.task_id,'task-1');
  state.task=null;state.resumeDraft=null;state.proposedExpansion=proposal;rejectDraft=true;
  await confirm(proposal,{value:'狗',disabled:false},manual);
  assert.equal(messages.at(-1)[0],'需求尚未保存');
  assert.match(messages.at(-1)[1],/刷新后再次确认检索词/);
  assert.doesNotMatch(messages.at(-1)[1],/FEATURE_NOT_SUPPORTED|manual gates/);
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script],
                                env={**os.environ, 'CONFIRM_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for automatic refill form test')
    def test_auto_refill_form_freezes_scope_limits_and_three_auto_nodes(self):
        source = CHAT_JS[CHAT_JS.index('function optionalPositive('):
                         CHAT_JS.index('function groupPreviewBody(')]
        script = r"""
const assert=require('node:assert/strict');
const fields={
  'auto-refill':{checked:true},
  'refill-library-ids':{selectedOptions:[{value:'library-a'},{value:'library-b'}]},
  'refill-max-rounds':{value:'3'},
  'refill-max-vlm-calls':{value:'40'},
  'refill-no-progress-rounds':{value:'2'},
};
const $=id=>fields[id];
const build=new Function('$',process.env.SOURCE+';return {refillFormValue,draftBody,refillSummary}')($);
const refill=build.refillFormValue();
const body=build.draftBody('找10张狗图片',10,null,refill);
assert.deepEqual(body.query_spec.refill_policy,{mode:'maintain_target',target_count:10,
  approved_library_ids:['library-a','library-b'],progression:'auto'});
assert.deepEqual(body.query_spec.refill_limits,{max_rounds:3,max_vlm_calls:40,no_progress_rounds:2});
assert.deepEqual(body.node_modes,{expansion:'manual',retrieval:'auto',cleaning:'auto',refill:'auto',packaging:'manual'});
assert.match(build.refillSummary(refill),/批准素材库：library-a、library-b/);
fields['auto-refill'].checked=false;
const manual=build.draftBody('找10张狗图片',10,null,build.refillFormValue());
assert.equal(manual.query_spec.refill_policy.mode,'input_only');
assert.deepEqual(manual.node_modes,{expansion:'manual',retrieval:'manual',cleaning:'manual',refill:'manual',packaging:'manual'});
"""
        result = subprocess.run(['node', '-e', script],
                                env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('id="auto-refill"', CHAT_HTML)
        self.assertIn('id="refill-library-ids"', CHAT_HTML)
        self.assertIn("api('/v1/retrieval/projects/demo/catalog-config')", CHAT_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for explicit term approval behavior test')
    def test_terms_only_start_search_after_button_click(self):
        source = CHAT_JS[CHAT_JS.index('function showTermReview('):
                         CHAT_JS.index('async function prepareGuidedExpansion(')]
        script = r"""
const assert=require('node:assert/strict');
const calls=[];
const proposal={lines:'狗 | 柴犬，牧羊犬',count:20,version:1,plan:{groups:[{primary_label:'狗',delivery_target:20}]}};
const state={proposedExpansion:proposal,task:null};
const status={textContent:''};
let click;
const refillFormValue=()=>({enabled:false,libraries:[],limits:{max_rounds:null,max_vlm_calls:null,no_progress_rounds:2}});
const refillSummary=()=> '数量不足时接受减少，不自动检索补数。';
const review=new Function('state','$','message','guidedAction','confirmGuidedExpansion','refillFormValue','refillSummary','reviewDetails',
  process.env.REVIEW_SOURCE+';return showTermReview')(
      state,()=>status,(_kind,_title,body)=>{assert.match(body,/合计需要 20 张/);assert.doesNotMatch(body,/各组目标/);assert.match(body,/不自动补数/);return {}},
  (_bubble,label,handler)=>{if(label==='确认检索词并开始检索')click=handler;
    else assert.equal(label,'重新生成检索词')},
  async()=>calls.push('search'),refillFormValue,refillSummary,(_bubble,_label,body)=>assert.match(body,/不自动检索补数/));
(async()=>{
  review(proposal);assert.deepEqual(calls,[]);
  proposal.version=2;await click();assert.deepEqual(calls,[]);assert.match(status.textContent,/最新词表/);
  review(proposal);await click();assert.deepEqual(calls,['search']);
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script],
                                env={**os.environ, 'REVIEW_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_page_has_explicit_confirmation_without_verbose_local_limit_copy(self):
        self.assertIn('class="chat-composer panel"', CHAT_HTML)
        self.assertIn('id="insert-request-example"', CHAT_HTML)
        self.assertIn("$('insert-request-example').addEventListener('click',insertRequestExample)", CHAT_JS)
        self.assertIn('aria-label="发送消息"', CHAT_HTML)
        self.assertIn("if(turn.action==='confirm_terms'&&proposal)", CHAT_JS)
        self.assertIn("if(turn.action==='confirm_terms'&&proposal){showTermReview(proposal", CHAT_JS)
        self.assertNotIn('来源范围：当前项目已授权的本地测试索引', CHAT_JS)
        self.assertNotIn('多索引歧义会另行要求选择', CHAT_JS)
        self.assertIn('当前文本编码器无法处理', CHAT_JS)
        self.assertNotIn("if(turn.action==='confirm_terms'&&proposal){await confirmGuidedExpansion", CHAT_JS)
        self.assertIn("if(confirmedExecution.candidate_intake){body.node_modes.retrieval='auto';body.node_modes.cleaning='auto'}", CHAT_JS)
        self.assertIn("refreshTaskSidebar();offerExistingSearch(taskId);refreshHistory()", CHAT_JS)
        self.assertIn("error.code==='INVALID_STATE_TRANSITION'&&['AWAITING_INPUT','PARTIAL','COMPLETED','FAILED'].includes(state.task.run_status)", CHAT_JS)
        self.assertIn("'检查检索计划',guidedStartSearch", CHAT_JS)
        self.assertIn('/first-search/manifest', CHAT_JS)
        self.assertIn('确认候选清单', CHAT_JS)
        self.assertIn("manifest.kind!=='candidate_input_manifest'", CHAT_JS)
        self.assertIn('可以继续准备清洗', CHAT_JS)
        self.assertNotIn('当前版本尚未形成可执行计划', CHAT_JS)
        self.assertIn("message('assistant','检索未启动',reason)", CHAT_JS)
        self.assertIn('id="task-sidebar-id"', CHAT_HTML)
        self.assertIn('id="task-sidebar-nodes"', CHAT_HTML)
        self.assertIn("$('task-sidebar-nodes')", CHAT_JS)
        self.assertIn('await startExpansionJob({raw_request:raw', CHAT_JS)
        self.assertIn('await restoreExpansionJob()', CHAT_JS)
        self.assertIn("if(turn.action==='confirm'", CHAT_JS)
        self.assertIn('await prepareGuidedExpansion(turn.request.effective_request||turn.request.raw_request,turn.request.target_count,signal,selection)', CHAT_JS)
        self.assertIn('/conversation-turn', CHAT_JS)
        self.assertIn("job.error?.code==='EXPANSION_UNCHANGED'", CHAT_JS)
        self.assertIn('重新提交这条修改', CHAT_JS)
        self.assertIn("pending?'消息结果待核对':'这条消息未处理'", CHAT_JS)
        self.assertIn("message('assistant','重新扩词未完成'", CHAT_JS)
        self.assertIn("if(intent.kind==='expansion'){await showPostTaskExpansion(text,intent,null,selection);return}", CHAT_JS)
        self.assertIn('requested_terms_per_group:requestedTerms', CHAT_JS)
        self.assertIn('待确认修改', CHAT_JS)
        self.assertIn('新增 ${changes.added_terms} 个词 · 移除 ${changes.removed_terms} 个词', CHAT_JS)
        self.assertIn('确认新版词表', CHAT_JS)
        self.assertNotIn('当前需求没有可调整的已确认扩词分组', CHAT_JS)
        self.assertIn("source.length?source.map", CHAT_JS)
        self.assertIn("watchTaskExpansionJob(job)", CHAT_JS)
        self.assertIn('previous?.requestedTerms', CHAT_JS)
        self.assertIn('const feedbackText=text;', CHAT_JS)
        self.assertIn('async function reviseGuidedExpansion', CHAT_JS)
        self.assertIn('feedback_text:instruction,current_groups:current,count_edits:edits', CHAT_JS)
        self.assertIn("if(turn.action==='revise_terms'&&turn.stage==='terms'&&proposal)", CHAT_JS)
        self.assertIn("体验环境说明", CHAT_HTML)
        self.assertNotIn("确认需求与扩词，开始检索", CHAT_JS)
        self.assertIn("仅保存反馈文本", CHAT_JS)
        self.assertIn("尚未创建或执行清洗管线", CHAT_JS)
        self.assertIn("这条反馈还不能用于清洗", CHAT_JS)
        self.assertNotIn("模型清洗建议 · 未批准", CHAT_JS)
        self.assertNotIn("候选 VLM 指令", CHAT_JS)
        self.assertIn("测试索引检索不等于真实素材授权", CHAT_HTML)
        self.assertIn("feedback-suggestions", CHAT_JS)
        self.assertIn("cleaning/prepare", CHAT_JS)
        self.assertIn("cleaning/confirm", CHAT_JS)
        self.assertIn("cleaning/status?operation_id=", CHAT_JS)
        self.assertIn("if(cleaning?.available){showCleaningHandoff(cleaning);return}", CHAT_JS)
        self.assertIn('实际清洗指令：', CHAT_JS)
        self.assertIn('清洗模型：', CHAT_JS)
        self.assertIn('/cleaning/candidates/', CHAT_JS)
        self.assertIn('id="cleaning-results-dialog"', CHAT_HTML)
        self.assertIn('dialog.showModal()', CHAT_JS)
        self.assertIn('合格预览分类', CHAT_JS)
        self.assertIn('page.result_set_digest!==results.result_set_digest', CHAT_JS)
        self.assertIn('request!==readToken', CHAT_JS)
        self.assertIn('/nodes/cleaning/outputs?', CHAT_JS)
        self.assertIn('/cleaning/steps?summary=true', CHAT_JS)
        self.assertIn('step_execution_id:step.step_execution_id', CHAT_JS)
        self.assertIn("['pending','处理中']", CHAT_JS)
        self.assertIn("['blocked','阻断']", CHAT_JS)
        self.assertIn('加载更多步骤结果', CHAT_JS)
        self.assertNotIn('step.items.filter', CHAT_JS)
        self.assertIn('尚未正式交付', CHAT_JS)
        self.assertIn('检索已完成 · 已固定', CHAT_JS)
        self.assertIn("initialize", CHAT_JS)
        self.assertIn("pending_draft", CHAT_JS)
        self.assertIn("new-task", CHAT_HTML)
        self.assertIn("localStorage.removeItem", CHAT_JS)
        self.assertIn("历史 Chat", CHAT_HTML)
        self.assertIn("草稿消息不会进入业务执行时间线", CHAT_JS)
        self.assertIn("refreshHistory", CHAT_JS)
        self.assertIn("openTask", CHAT_JS)
        self.assertIn("openConversation", CHAT_JS)
        self.assertIn('renderSavedTaskMessages(data.task_messages)', CHAT_JS)
        self.assertNotIn('renderExecutionHistory(', CHAT_JS)
        self.assertNotIn('执行历史 · ', CHAT_JS)
        self.assertNotIn('已关联运行', CHAT_JS)
        self.assertIn('/events?after_sequence=${state.eventSequence}&limit=100', CHAT_JS)
        self.assertIn('/events/stream?after_sequence=${state.eventSequence}', CHAT_JS)
        self.assertIn("Accept:'text/event-stream'", CHAT_JS)
        self.assertIn("if(error.code==='EVENT_HISTORY_EXPIRED')", CHAT_JS)
        self.assertIn('startTaskEvents(task.task_id,progress.as_of_sequence)', CHAT_JS)
        self.assertIn('startTaskEvents(taskId,progress.as_of_sequence)', CHAT_JS)
        self.assertIn('SSE 已恢复到事件序列', CHAT_JS)
        self.assertNotIn('方案已确认 · revision', CHAT_JS)
        self.assertNotIn("['确认时间',item.confirmed_at]", CHAT_JS)
        self.assertIn('renderSavedTaskMessages([...(history?.clarification_messages||[]),...(history?.task_messages||[])])', CHAT_JS)
        self.assertIn('dialogue-sessions/${state.dialogueId}/archive', CHAT_JS)
        self.assertIn('if(clarification.length)renderSavedTaskMessages(clarification)', CHAT_JS)
        self.assertIn("state.proposedExpansion=null;state.targetCountTouched=false;timeline.replaceChildren()", CHAT_JS)
        self.assertIn("不会进入业务执行时间线", CHAT_JS)
        self.assertIn("五节点进度", CHAT_HTML)
        self.assertIn("nodeHeader(1,'诉求澄清'", CHAT_JS)
        self.assertIn("'诉求原文：'+query.raw_request", CHAT_JS)
        self.assertIn("body.className='node-body node-terms'", CHAT_JS)
        self.assertIn("terms.textContent=group.auxiliary_terms?.join('、')", CHAT_JS)
        self.assertNotIn("nodes.append(demandCard(progress))", CHAT_JS)
        self.assertIn("'未开始'", CHAT_JS)
        self.assertIn("'进行中'", CHAT_JS)
        self.assertIn("'已完成'", CHAT_JS)
        self.assertNotIn("'推进门禁'", CHAT_JS)
        self.assertNotIn("'推进模式'", CHAT_JS)
        self.assertIn("progress.counts.accepted===null?'未知'", CHAT_JS)
        self.assertIn("if(!results.available){resetResultWorkspace();return}", CHAT_JS)
        self.assertIn('id="result-sidebar"', CHAT_HTML)
        self.assertIn("renderResultCounts", CHAT_JS)
        self.assertIn("delivery.manifest.eligible_unique_count", CHAT_JS)
        self.assertIn("delivery.manifest.exact_duplicate_count", CHAT_JS)
        self.assertIn("delivery.manifest.packaged_file_count", CHAT_JS)
        self.assertIn("delivery.manifest.members||[]).reduce", CHAT_JS)
        self.assertIn("delivery.manifest.members||[]).length", CHAT_JS)
        self.assertNotIn("人工复核近重复簇", CHAT_JS)
        self.assertNotIn("确认合并，仅保留所选图片", CHAT_JS)
        self.assertIn("仅排除此图", CHAT_JS)
        self.assertIn("排除相似簇", CHAT_JS)
        self.assertIn("升级为规则", CHAT_JS)
        self.assertIn("/result-exclusions", CHAT_JS)
        self.assertIn("item.preview_status==='available'", CHAT_JS)
        self.assertIn("loadMoreResults", CHAT_JS)
        self.assertIn("检索词：", CHAT_JS)
        self.assertIn("检索词", CHAT_JS)
        self.assertIn("确认检索词并开始检索", CHAT_JS)
        self.assertNotIn("回复“是”就开始检索", CHAT_JS)
        self.assertIn("累计原始命中", CHAT_JS)
        self.assertNotIn("完成百分比", CHAT_JS)

    def test_assets_do_not_embed_remote_dependencies_or_dynamic_html(self):
        combined = CHAT_HTML + CHAT_CSS + CHAT_JS
        self.assertNotIn("https://", combined)
        self.assertNotIn("http://", combined)
        self.assertNotIn("innerHTML", CHAT_JS)

    def test_follow_up_uses_plain_chat_composer_copy(self):
        self.assertIn('id="feedback-text" rows="3" placeholder="输入消息"', CHAT_HTML)
        self.assertIn('id="prepare-feedback" aria-label="发送消息"', CHAT_HTML)
        self.assertNotIn('追加反馈', CHAT_HTML)
        self.assertNotIn('反馈或补充条件', CHAT_HTML)
        self.assertNotIn('发送后先判断', CHAT_HTML)
        self.assertNotIn('发送反馈', CHAT_HTML)

    @unittest.skipUnless(shutil.which('node'), 'Node required for feedback routing test')
    def test_cleaning_turn_uses_server_draft_and_preview_in_one_request(self):
        source = CHAT_JS[CHAT_JS.index('async function prepareFeedback(){'):
                         CHAT_JS.index('function taskExpansionPath(')]
        script = r"""
const assert=require('node:assert/strict');
const draft={draft_id:'d',draft_version:1,proposal_digest:'digest'};
const state={task:{task_id:'t',task_version:1,confirmed_revision:1},feedback:draft};
const fields={'feedback-text':{value:'把第二条改成不要水印'},'llm-model':{value:'llm'},'vlm-model':{value:'vlm'},status:{textContent:''}};
const next={...draft,draft_version:2,proposal_digest:'next'};const review={feedback_status:'ready'};
let calls=0,shown;
const api=async(path,options)=>{calls++;assert.equal(path,'/v1/retrieval/tasks/t/conversation-turn');
 assert.deepEqual(options.body.feedback_ref,draft);assert.equal(options.keyValue,'command');
 return {kind:'cleaning',feedback_draft:next,cleaning_review:review}};
const run=new Function('state','$','api','key','message','setFollowupBusy','showFeedbackReview',process.env.SOURCE+';return prepareFeedback')(
 state,id=>fields[id],api,()=> 'command',()=>({remove(){}}),()=>{},value=>shown=value);
(async()=>{await run();assert.equal(calls,1);assert.equal(state.feedback,next);assert.equal(shown,review)})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for feedback routing test')
    def test_expansion_followup_is_not_saved_as_cleaning_feedback(self):
        prepare = CHAT_JS[CHAT_JS.index('async function prepareFeedback(){'):
                          CHAT_JS.index('async function showPostTaskExpansion(')]
        script = r"""
const assert=require('node:assert/strict');
const state={feedback:null,task:{task_id:'task-1',confirmed_revision:1,task_version:1}};
let routed='',posts=0,kind='expansion';
const fields={'feedback-text':{value:'别按情绪搜，多找狗在户外的画面'},'llm-model':{value:'llm'},'vlm-model':{value:'vlm'},'status':{textContent:''}};
const $=id=>fields[id];
const showPostTaskExpansion=async(text,intent,role,selection)=>{routed=text;assert.equal(selection.llm,'llm')};
const api=async path=>{posts++;assert.match(path,/conversation-turn/);fields['llm-model'].value='next';return {kind,target_count:null,requested_terms_per_group:null}};
const setFollowupBusy=v=>{state.followupBusy=v};
const messages=[];const message=(role,title,text)=>{messages.push([role,text]);return {remove(){}}};
const funcs=new Function('state','$','showPostTaskExpansion','api','setFollowupBusy','message',
  'const key=()=>"fixture-command";'+process.env.PREPARE_SOURCE+';return {prepareFeedback,routeTaskFollowup};')(
    state,$,showPostTaskExpansion,api,setFollowupBusy,message);
(async()=>{await funcs.prepareFeedback();assert.match(routed,/多找狗在户外/);assert.equal(posts,1);assert.deepEqual(messages.map(row=>row[0]),['user','assistant']);assert.equal(fields['feedback-text'].value,'')})().catch(e=>{console.error(e);process.exit(1)});
"""
        subprocess.run(['node', '-e', script], check=True,
                       env={**os.environ, 'PREPARE_SOURCE': prepare})

    @unittest.skipUnless(shutil.which('node'), 'Node required for new request routing test')
    def test_new_search_during_retrieval_prompts_for_separate_chat(self):
        source = CHAT_JS[CHAT_JS.index('async function prepareFeedback(){'):
                         CHAT_JS.index('async function showPostTaskExpansion(')]
        script = r"""
const assert=require('node:assert/strict');
const state={feedback:null,task:{task_id:'dog-task',confirmed_revision:1,task_version:1}};
const fields={'feedback-text':{value:'另外找20张兔子图片'},'llm-model':{value:'llm'},'vlm-model':{value:'vlm'},'status':{textContent:''}};
const $=id=>fields[id];
const saved=new Map(),sessionStorage={setItem:(key,value)=>saved.set(key,value)};
let newChat=0,actions=[],messages=[],requests=[];
const api=async(path,options)=>{requests.push(path);return {kind:'new_request',target_count:null,requested_terms_per_group:null,reply:'这是另一项检索需求。'}};
const setFollowupBusy=value=>{state.followupBusy=value};
const message=(kind,title,body)=>{messages.push([kind,title,body]);return {}};
const guidedAction=(bubble,label,action)=>{actions.push([label,action])};
const showPostTaskExpansion=()=>{throw Error('must not revise the running task')};
const startNewChat=()=>{newChat++};
const funcs=new Function('state','$','api','setFollowupBusy','message','guidedAction','showPostTaskExpansion','sessionStorage','startNewChat',
  'const key=()=>"fixture-command";'+process.env.SOURCE+';return {prepareFeedback};')(
  state,$,api,setFollowupBusy,message,guidedAction,showPostTaskExpansion,sessionStorage,startNewChat);
(async()=>{await funcs.prepareFeedback();assert.deepEqual(requests,['/v1/retrieval/tasks/dog-task/conversation-turn']);
  assert.equal(state.task.task_id,'dog-task');assert.equal(fields['feedback-text'].value,'');
  assert.match(messages.at(-1)[2],/新建需求单/);assert.equal(actions[0][0],'新建 Chat 处理');
  assert.equal(newChat,0);actions[0][1]();assert.equal(newChat,1);
  assert.equal(saved.get('dsh-new-request-prefill'),'另外找20张兔子图片');
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for legacy term generation test')
    def test_history_without_confirmed_terms_generates_then_applies_feedback(self):
        source = CHAT_JS[CHAT_JS.index('async function showPostTaskExpansion('):
                         CHAT_JS.index('async function decideFeedback(')]
        script = r"""
const assert=require('node:assert/strict');
const state={task:{task_id:'task-1',confirmed_revision:1,task_version:3},
  expansionPreviewPlan:null,feedback:null,expansionPreviewBubble:null,expansionPreviewToken:null};
const fields={'llm-model':{value:'llm'},'vlm-model':{value:'vlm'},'feedback-text':{value:'词数翻倍',focus(){}},status:{textContent:''}};
const $=id=>fields[id];
const calls=[],actions=[];
const api=async(path,options)=>{
  calls.push([path,options?.body]);
  if(path.endsWith('/revisions'))return {items:[{revision:1,proposal:{dimension_id:'default',query_spec:{raw_request:'找200张猫图片',target_count:200}}}]};
  assert(path.endsWith('/expansion-jobs'));
  assert.equal(options.body.current_groups,undefined);
  assert.deepEqual(options.body.count_edits,[{target:'terms',labels:[],operation:'scale',value:2}]);
  return {job_id:'expjob-1',task_id:'task-1',task_version:3,status:'queued'};
};
const message=()=>({isConnected:true,querySelector(){return null}});
const guidedAction=(bubble,label)=>actions.push(label);
const key=()=> 'token',setBusy=()=>{},setFollowupBusy=v=>{state.followupBusy=v};
const watched=[];
const show=new Function('state','$','api','message','guidedAction','key','setBusy','setFollowupBusy','taskExpansionPath','watchTaskExpansionJob',
  process.env.SHOW_SOURCE+';return showPostTaskExpansion;')(state,$,api,message,guidedAction,key,setBusy,setFollowupBusy,
    id=>'/v1/retrieval/tasks/'+id+'/expansion-jobs',job=>watched.push(job));
(async()=>{
  await show('词数翻倍',{target_count:null,requested_terms_per_group:null,
    count_edits:[{target:'terms',labels:[],operation:'scale',value:2}],expansion_instruction:'每组词数翻倍'});
  assert.equal(fields.status.textContent,'');
  assert.equal(calls.filter(row=>row[0].endsWith('/expansion-jobs')).length,1);
  assert.equal(watched.length,1);
  assert.deepEqual(actions,[]);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        subprocess.run(['node', '-e', script], check=True,
                       env={**os.environ, 'SHOW_SOURCE': source})

    @unittest.skipUnless(shutil.which('node'), 'Node required for expansion preview test')
    def test_latest_two_term_feedback_overrides_previous_hundred_term_preview(self):
        source = CHAT_JS[CHAT_JS.index('async function showPostTaskExpansion('):
                         CHAT_JS.index('async function decideFeedback(')]
        script = r"""
const assert=require('node:assert/strict');
const oldTerms=Array.from({length:100},(_,i)=>'猫场景'+i);
const state={task:{task_id:'task-1',confirmed_revision:1,task_version:3},
  expansionPreviewPlan:{taskId:'task-1',baseVersion:3,count:20,requestedTerms:100,
    feedbackText:'先扩到100个检索词',plan:{groups:[{primary_label:'猫',auxiliary_terms:oldTerms}]}},
  feedback:null,expansionPreviewBubble:null,expansionPreviewToken:null};
const fields={'llm-model':{value:'llm'},'vlm-model':{value:'vlm'},
  'feedback-text':{value:'检索词减少到两个',focus(){}},status:{textContent:''}};
const $=id=>fields[id];let submitted;
const api=async(path,options)=>{
  if(path.endsWith('/revisions'))return {items:[{revision:1,proposal:{dimension_id:'default',
    query_spec:{raw_request:'找20张猫图片',target_count:20,grouped_expansion:{groups:[{primary_label:'猫',auxiliary_terms:['旧词']}]}}}}]};
  submitted=options.body;return {job_id:'new-job',task_id:'task-1',task_version:3,status:'queued'};
};
const message=()=>({isConnected:true,querySelector(){return null}});
const show=new Function('state','$','api','message','guidedAction','key','setBusy','setFollowupBusy','taskExpansionPath','watchTaskExpansionJob',
  process.env.SHOW_SOURCE+';return showPostTaskExpansion;')(state,$,api,message,()=>{},()=> 'key',()=>{},()=>{},
    id=>'/v1/retrieval/tasks/'+id+'/expansion-jobs',()=>{});
(async()=>{await show('检索词减少到两个',{target_count:null,requested_terms_per_group:2,
  count_edits:[],term_count_only:true,expansion_instruction:'将猫组检索词减少到两个'});
  assert.equal(submitted.requested_terms_per_group,2);
  assert.equal(submitted.term_count_only,true);
  assert.equal(submitted.current_groups[0].auxiliary_terms.length,100);
  assert.equal(submitted.confirmation_feedback_text,'检索词减少到两个');
  assert.equal(submitted.feedback_text,'将猫组检索词减少到两个');
})().catch(error=>{console.error(error);process.exit(1)});
"""
        subprocess.run(['node', '-e', script], check=True,
                       env={**os.environ, 'SHOW_SOURCE': source})

    @unittest.skipUnless(shutil.which('node'), 'Node required for expansion preview test')
    def test_unconfirmed_two_term_preview_can_be_revised_to_six(self):
        source = CHAT_JS[CHAT_JS.index('async function showPostTaskExpansion('):
                         CHAT_JS.index('async function decideFeedback(')]
        script = r"""
const assert=require('node:assert/strict');
const terms=Array.from({length:100},(_,i)=>'猫场景'+i);
const state={task:{task_id:'task-1',confirmed_revision:1,task_version:3},
  expansionPreviewPlan:{taskId:'task-1',baseVersion:3,count:20,requestedTerms:2,
    plan:{groups:[{primary_label:'猫',auxiliary_terms:terms.slice(0,2)}]},
    sourceGroups:[{primary_label:'猫',auxiliary_terms:terms}]},
  feedback:null,expansionPreviewBubble:null,expansionPreviewToken:null};
const fields={'llm-model':{value:'llm'},'vlm-model':{value:'vlm'},
  'feedback-text':{value:'改成6个',focus(){}},status:{textContent:''}};
const $=id=>fields[id];let submitted;
const api=async(path,options)=>{
  if(path.endsWith('/revisions'))return {items:[{revision:1,proposal:{dimension_id:'default',
    query_spec:{raw_request:'找20张猫图片',target_count:20,grouped_expansion:{groups:[{primary_label:'猫',auxiliary_terms:terms.slice(0,10)}]}}}}]};
  submitted=options.body;return {job_id:'six-job',task_id:'task-1',task_version:3,status:'queued'};
};
const message=()=>({isConnected:true,querySelector(){return null}});
const show=new Function('state','$','api','message','guidedAction','key','setBusy','setFollowupBusy','taskExpansionPath','watchTaskExpansionJob',
  process.env.SHOW_SOURCE+';return showPostTaskExpansion;')(state,$,api,message,()=>{},()=> 'key',()=>{},()=>{},
    id=>'/v1/retrieval/tasks/'+id+'/expansion-jobs',()=>{});
(async()=>{await show('改成6个',{target_count:null,requested_terms_per_group:6,
  count_edits:[],term_count_only:true,expansion_instruction:'仅把猫组词数改为6个'});
  assert.equal(submitted.requested_terms_per_group,6);
  assert.equal(submitted.term_count_only,true);
  assert.deepEqual(submitted.current_groups[0].auxiliary_terms,terms);
  assert.equal(submitted.confirmation_feedback_text,'改成6个');
})().catch(error=>{console.error(error);process.exit(1)});
"""
        subprocess.run(['node', '-e', script], check=True,
                       env={**os.environ, 'SHOW_SOURCE': source})

    def test_confirmed_search_has_progress_link_without_manual_branch_cards(self):
        self.assertIn('id="search-progress-link"', CHAT_HTML)
        self.assertIn('查看检索进度', CHAT_HTML)
        self.assertIn("progressLink.href='/production?task='+encodeURIComponent(taskId)+'&node=retrieval'", CHAT_JS)
        self.assertNotIn('id="first-search"', CHAT_HTML)
        self.assertNotIn('id="preview-branches"', CHAT_HTML)
        self.assertNotIn('id="preview-routing"', CHAT_HTML)
        self.assertNotIn("card('查询分支 · '", CHAT_JS)
        self.assertNotIn('async function previewFirstSearch(', CHAT_JS)
        self.assertIn("'/first-search/prepare'", CHAT_JS)
        self.assertIn("'/first-search/confirm'", CHAT_JS)
        self.assertIn("action:'confirm'", CHAT_JS)
        self.assertIn("await guidedTick()", CHAT_JS)
        self.assertIn('未清洗', CHAT_JS)
        self.assertIn('无可预览图片', CHAT_JS)
        self.assertNotIn('innerHTML', CHAT_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for guided routing behavior test')
    def test_guided_search_auto_covers_authorized_preconfigured_routes(self):
        source = CHAT_JS[CHAT_JS.index('function searchUnavailableReason('):
                         CHAT_JS.index('async function guidedConfirmAndSearch(){')]
        script = r"""
const assert=require('node:assert/strict');
const state={task:{task_id:'task-1'},firstSearchPreview:null,firstSearchChoices:[]};
let routes=[],started=0,messages=[],actions=[];
const api=async()=>({routing_digest:'digest',routes});
const message=(kind,title,body)=>{const bubble={kind,title,body,children:[],append(item){this.children.push(item)}};messages.push(bubble);return bubble};
const guidedAction=(bubble,label)=>actions.push(label);
const document={createElement:tag=>({tag,append(){},setAttribute(){},addEventListener(){}})};
const guidedConfirmAndSearch=async()=>{started++};
const refreshTaskSidebar=async()=>{};
const $=()=>({classList:{remove(){}}});
const start=new Function('state','api','message','guidedAction','document','guidedConfirmAndSearch','refreshTaskSidebar','$',process.env.GUIDED_SOURCE+';return guidedStartSearch;')(state,api,message,guidedAction,document,guidedConfirmAndSearch,refreshTaskSidebar,$);
const option=(binding_id)=>({binding_id,library_id:binding_id,index_version:'v1',max_batch_size:4});
(async()=>{
  routes=[{branch_id:'dog',query_text:'狗',options:[option('dog-index')]}];
  await start();
  assert.equal(started,1);
  assert.deepEqual(state.firstSearchChoices,[{branch_id:'dog',binding_id:'dog-index',batch_size:2}]);
  assert.equal(messages.length,0);
  routes=[{branch_id:'dog',query_text:'狗',options:[option('dog-index')]},{branch_id:'cat',query_text:'猫',options:[],availability:'query_encoding_unavailable'}];
  await start();
  assert.equal(started,1);
  assert.deepEqual(state.firstSearchChoices,[]);
  assert.match(messages.at(-1).body,/当前文本编码器无法处理：猫/);
  assert.match(messages.at(-1).body,/当前检索环境未能执行这些查询/);
  assert.match(messages.at(-1).body,/不是零命中/);
  assert.match(messages.at(-1).body,/仍需逐图复判/);
  routes=[{branch_id:'dog',query_text:'狗',options:[option('dog-index')]},{branch_id:'cat',query_text:'猫',options:[option('cat-1'),option('cat-2')]}];
  await start();
  assert.equal(started,2);
  assert.deepEqual(state.firstSearchChoices,[
    {branch_id:'dog',binding_id:'dog-index',batch_size:2},
    {branch_id:'cat',binding_id:'cat-1',batch_size:2},
    {branch_id:'cat',binding_id:'cat-2',batch_size:2}
  ]);
  assert.equal(messages.length,1);
  assert.deepEqual(actions,[]);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'GUIDED_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for incremental search behavior test')
    def test_guided_search_polls_sidebar_without_chat_result_bubbles(self):
        source = CHAT_JS[CHAT_JS.index('async function guidedTick(){'):
                         CHAT_JS.index('async function showCandidateManifestReview(){')]
        script = r"""
const assert=require('node:assert/strict');
const state={task:{task_id:'task-1'}};
let updates=0,scheduled=null;
const tick=new Function('state','document','refreshTaskSidebar','setTimeout','clearTimeout',
 'let searchPollTimer=null,searchPollBusy=false;'+process.env.TICK_SOURCE+';return guidedTick;')(
 state,{hidden:false},async()=>{updates++},fn=>{scheduled=fn;return 1},()=>{});
(async()=>{
 await tick();assert.equal(updates,1);assert.equal(typeof scheduled,'function');
 await scheduled();assert.equal(updates,2);
 state.task={task_id:'task-2'};await scheduled();assert.equal(updates,2);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'TICK_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('message(', source)
        self.assertNotIn('appendLocalCandidateGroups', source)

    def test_page_exposes_model_roles_and_accessible_structure(self):
        self.assertIn('id="main-content"', CHAT_HTML)
        self.assertIn('class="skip-link"', CHAT_HTML)
        self.assertIn('role="log"', CHAT_HTML)
        self.assertIn('aria-relevant="additions"', CHAT_HTML)
        self.assertIn('当前 Chat · 扩词模型', CHAT_HTML)
        self.assertIn('dialogue-llm-model-input', CHAT_HTML)
        self.assertIn('清洗语义判定模型', CHAT_HTML)
        self.assertIn('使用 API 与密钥配置', CHAT_HTML)
        self.assertIn('id="api-key" type="password"', CHAT_HTML)
        self.assertIn('密钥不写入浏览器存储', CHAT_HTML)
        self.assertIn('id="new-task"', CHAT_HTML)
        self.assertEqual(CHAT_HTML.count('id="new-task"'), 1)
        self.assertLess(CHAT_HTML.index('id="history-panel"'), CHAT_HTML.index('id="new-task"'))
        self.assertLess(CHAT_HTML.index('id="new-task"'), CHAT_HTML.index('id="history-list"'))
        self.assertNotIn('id="new-chat"', CHAT_HTML)
        self.assertIn('新建 Chat', CHAT_HTML)
        self.assertIn('class="right-rail"', CHAT_HTML)
        self.assertGreater(CHAT_HTML.index('class="right-rail"'), CHAT_HTML.index('class="chat"'))
        self.assertNotIn('LOCAL WORKSPACE · PREVIEW', CHAT_HTML)
        self.assertNotIn('把检索需求整理成', CHAT_HTML)
        self.assertNotIn('先梳理目标、分组和推进方式', CHAT_HTML)
        self.assertNotIn('class="context-bar"', CHAT_HTML)
        self.assertIn('固定体验范围：demo / default', CHAT_HTML)
        self.assertIn('id="open-catalog-settings" href="/catalog"', CHAT_HTML)
        self.assertNotIn('href="/catalog"', CHAT_HTML[:CHAT_HTML.index('id="history-panel"')])
        self.assertNotIn('href="/catalog"', BOARD_HTML)
        self.assertIn("$('new-task').addEventListener('click',startNewChat)", CHAT_JS)
        self.assertIn("function renameChat", CHAT_JS)
        self.assertIn("expected_draft_version=state.resumeDraft.draft_version", CHAT_JS)
        self.assertIn("function saveApiConfig", CHAT_JS)
        self.assertNotIn("localStorage.setItem('dsh-api", CHAT_JS)
        self.assertIn("document.createElement('details')", CHAT_JS)
        self.assertIn("activity.setAttribute('aria-busy'", CHAT_JS)
        self.assertNotIn("处理中…", CHAT_JS)
        self.assertIn(':focus-visible', CHAT_CSS)
        self.assertIn('@media(max-width:560px)', CHAT_CSS)
        self.assertIn('@media(prefers-reduced-motion:reduce)', CHAT_CSS)

    def test_chat_analyzes_each_submitted_utterance_before_draft_write(self):
        self.assertIn('/v1/retrieval/projects/demo/dialogue-command', CHAT_JS)
        self.assertNotIn('DSH 助手 · 正在分析', CHAT_JS)
        self.assertIn('const turn=await sendDialogueCommand(', CHAT_JS)
        self.assertIn('if(turn.action===\'confirm\'', CHAT_JS)
        self.assertIn('async function guidedStartSearch', CHAT_JS)
        self.assertIn('async function guidedTick', CHAT_JS)
        self.assertIn('检索词：', CHAT_JS)
        self.assertIn('每个检索词至少选择一个索引', CHAT_JS)
        self.assertIn('无匹配的已授权索引', CHAT_JS)
        self.assertIn('当前文本编码器无法处理', CHAT_JS)
        self.assertIn("source.query_text", CHAT_JS)
        self.assertIn("setAttribute('aria-label','命中检索词')", CHAT_JS)
        self.assertIn('.search-tag{', CHAT_CSS)
        self.assertIn("event.key==='Enter'&&!event.shiftKey&&!event.isComposing", CHAT_JS)
        self.assertIn("$('feedback-text').addEventListener('keydown'", CHAT_JS)
        self.assertIn("showFeedbackReview(intent.cleaning_review)", CHAT_JS)
        self.assertIn("suggestion.feedback_status==='ready'", CHAT_JS)
        self.assertNotIn('模型的理解建议，不代表已执行', CHAT_JS)
        self.assertNotIn('查看逐条引文与能力边界', CHAT_JS)
        self.assertIn('count_hint:hint', CHAT_JS)
        self.assertIn('state.targetCountTouched', CHAT_JS)
        self.assertIn("llms.length===1)$('llm-model').value", CHAT_JS)
        self.assertIn("vlms.length===1)$('vlm-model').value", CHAT_JS)
        self.assertNotIn('DSH 助手 · 分析未完成', CHAT_JS)
        self.assertIn("$('chat-model-badge').textContent", CHAT_JS)
        self.assertIn("querySelectorAll('button,#llm-model,#vlm-model,#llm-model-input,#vlm-model-input,#dialogue-llm-model,#dialogue-llm-model-input,#target-count,#auto-refill", CHAT_JS)
        self.assertIn('密钥不写入浏览器存储', CHAT_HTML)
        self.assertNotIn('innerHTML', CHAT_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for candidate grouping test')
    def test_local_candidate_grouping_preserves_sources_and_scopes_unmapped_ids(self):
        source = CHAT_JS[CHAT_JS.index('function aggregateLocalCandidates(items){'):
                         CHAT_JS.index('function appendLocalCandidateGroups(')]
        script = r"""
const assert=require('node:assert/strict');
const aggregate=new Function(process.env.GROUP_SOURCE+'; return aggregateLocalCandidates;')();
const base={plan_id:'p1',branch_id:'b1',query_text:'狗',query_role:'primary',source_term:'狗',library_id:'dog',binding_id:'dog-v1',index_version:'v1',vector_id:1,status:'resolved',media_unit_id:'unit-1',asset_version:'v1'};
const rows=[base,{...base,branch_id:'b2',query_text:'金毛',source_term:'金毛'},
  {...base,branch_id:'b2',query_text:'金毛',source_term:'金毛'},
  {...base,library_id:'other',binding_id:'other-v1',vector_id:1,branch_id:'b3',query_text:'宠物狗',source_term:'宠物狗'},
  {...base,asset_version:'v2'},
  {...base,status:'mapping_missing',media_unit_id:null,asset_version:null},
  {...base,status:'mapping_missing',media_unit_id:null,asset_version:null,library_id:'other',binding_id:'other-v1'}];
const groups=aggregate(rows);
assert.equal(groups.length,4);
assert.equal(groups[0].rawHits,4);
assert.deepEqual(groups[0].sources.map(source=>source.query_text),['狗','金毛','宠物狗']);
assert.equal(groups[0].indexes.length,2);
assert.deepEqual(groups[0].vectors,['dog-v1:1','other-v1:1']);
assert.equal(groups[1].item.asset_version,'v2');
assert.equal(groups[2].item.status,'mapping_missing');
assert.notEqual(groups[2].item.binding_id,groups[3].item.binding_id);
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'GROUP_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for cleaning edit behavior test')
    def test_cleaning_edit_opens_visible_feedback_composer(self):
        source = CHAT_JS[CHAT_JS.index('function showCleaningApproval(taskId,proposal){'):
                         CHAT_JS.index('async function confirmVlmCleaning(')]
        script = r"""
const assert=require('node:assert/strict');
let actions,opened=false,focused=false;
const input={value:'',focus(){focused=true}};
const $=id=>{if(id==='feedback-text')return input;if(id==='feedback-box')return {classList:{remove(name){assert.equal(name,'hidden');opened=true}}};throw Error('Unexpected hidden input '+id)};
const state={};const proposal={rules:[],steps:[]};state.cleaningProposal=proposal;
const show=new Function('state','$','key','card','message','confirmVlmCleaning',process.env.EDIT_SOURCE+';return showCleaningApproval;')(state,$,()=> 'key',(_title,_fields,items)=>actions=items,()=>{},()=>{});
show('task',proposal);
actions.find(item=>item[0]==='修改清洗条件')[1]();
assert.equal(opened,true);assert.equal(focused,true);assert.equal(input.value,'增加清洗检测：');
assert.equal(state.cleaningProposal,null);
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'EDIT_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for structured draft restore test')
    def test_structured_feedback_restore_does_not_require_model_selection(self):
        source = CHAT_JS[CHAT_JS.index('async function showCleaningSuggestion(selection){'):
                         CHAT_JS.index('function startNewChat(){')]
        script = r"""
const assert=require('node:assert/strict');
const state={task:{task_id:'task'},feedback:{draft_id:'draft',draft_version:2,proposal_digest:'digest',proposal:{query_spec:{cleaning_conditions:[{}]}}}};
let calls=0,shown;const suggestion={feedback_status:'ready'};
const show=new Function('state','$','api','showFeedbackReview',process.env.SOURCE+';return showCleaningSuggestion')(
 state,()=>({value:''}),async()=>{calls++;return suggestion},value=>shown=value);
(async()=>{await show();assert.equal(calls,1);assert.equal(shown,suggestion)})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for feedback behavior test')
    def test_irrelevant_feedback_has_no_confirmation_action(self):
        source = CHAT_JS[CHAT_JS.index('function showFeedbackReview(suggestion){'):
                         CHAT_JS.index('async function showCleaningSuggestion(selection){')]
        script = r"""
const assert=require('node:assert/strict');
const state={feedbackReviewBubble:null,feedbackManualCard:null};
const message=(kind,title,text)=>({kind,title,text,actions:[],isConnected:true,remove(){this.isConnected=false}});
const guidedAction=(bubble,label)=>bubble.actions.push(label);
const card=()=>({});
const decideFeedback=()=>{};
const editFeedback=()=>{};
const $=()=>({classList:{add(){},remove(){}}});
const show=new Function('state','message','guidedAction','card','decideFeedback','editFeedback','$',process.env.REVIEW_SOURCE+';return showFeedbackReview;')(state,message,guidedAction,card,decideFeedback,editFeedback,$);
show({feedback_status:'unrelated',requirement:'',rationale:'请用户重新描述想保留或排除的画面特征。'});
assert.equal(state.feedbackReviewBubble.title,'这条反馈还不能用于清洗');
assert.deepEqual(state.feedbackReviewBubble.actions,['重新描述','不采用']);
assert.doesNotMatch(state.feedbackReviewBubble.text,/候选 VLM 指令|sha256|找3张狗图片/);
assert.doesNotMatch(state.feedbackReviewBubble.text,/。。/);
assert.match(state.feedbackReviewBubble.text,/请你重新描述/);
show({feedback_status:'needs_clarification',requirement:'',rationale:'请说明要排除什么'});
assert.deepEqual(state.feedbackReviewBubble.actions,['重新描述','不采用']);
show({feedback_status:'ready',requirement:'狗且没有文字',rationale:'增加排除文字条件'});
assert.deepEqual(state.feedbackReviewBubble.actions,['确认更新清洗条件','重新描述','不采用']);
assert.match(state.feedbackReviewBubble.text,/新增条件/);
show({feedback_status:'ready',requirement:'无水印',rationale:'',cleaning_plan_preview:{before_count:1,after_count:2,nodes:[{source:'大象和老虎合影'},{source:'不要水印'}]}});
assert.match(state.feedbackReviewBubble.text,/检测节点 1 → 2/);
assert.match(state.feedbackReviewBubble.text,/大象和老虎合影/);assert.match(state.feedbackReviewBubble.text,/不要水印/);
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'REVIEW_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
