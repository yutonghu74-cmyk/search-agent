import os
import shutil
import subprocess
import unittest

from dsh_retrieval.chat_ui import CHAT_CSS, CHAT_HTML, CHAT_JS


class ChatUiTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for contextual conversation test')
    def test_current_terms_reach_model_and_questions_do_not_trigger_expansion(self):
        source = CHAT_JS[CHAT_JS.index('async function prepareTask(){'):
                         CHAT_JS.index('async function reviseGuidedExpansion(')]
        script = r"""
const assert=require('node:assert/strict');
const groups=[{primary_label:'猫',auxiliary_terms:['猫在树下','猫在窗边']}];
const state={dialogueId:'dlg_test',dialogueVersion:1,dialogueStage:'terms',proposedExpansion:{raw:'5张猫图',count:5,lines:'猫 | 猫在树下，猫在窗边'}};
const fields={'request-text':{value:'这些为什么这样写？'},'llm-model':{value:'text'},status:{textContent:''}};
let action='answer',revisions=0;
const replies=[];
const api=async(path,{body})=>{assert.deepEqual(body.current_groups,groups);assert.equal(body.current_target_count,5);return {
 session_id:'dlg_test',version:2,stage:'terms',action,reply:'它们覆盖不同的画面场景。',request:{target_count:5},
 count_edits:[{target:'terms',labels:['猫'],operation:'add',value:2}],expansion_instruction:'猫组增加两个场景词'};};
const revise=async(p,e,f,turn)=>{revisions++;assert.equal(turn.count_edits[0].value,2);assert.equal(turn.expansion_instruction,'猫组增加两个场景词')};
const prepare=new Function('state','$','api','message','setBusy','saveDialogueSession','groupInputBody','reviseGuidedExpansion',process.env.TURN_SOURCE+';return prepareTask')(
 state,id=>fields[id],api,(role,title,text)=>replies.push([role,text]),()=>{},()=>{},()=>({groups}),revise);
(async()=>{await prepare();assert.equal(revisions,0);assert.equal(replies.at(-1)[1],'它们覆盖不同的画面场景。');
action='revise_terms';fields['request-text'].value='那再补两个';await prepare();assert.equal(revisions,1)})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'TURN_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for reference sentence behavior test')
    def test_reference_sentence_is_optional_and_does_not_overwrite_user_text(self):
        source = CHAT_JS[CHAT_JS.index('function insertRequestExample(){'):
                         CHAT_JS.index('async function restoreDialogueSession(){')]
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
const message=(kind,title,body)=>{events.push('message');assert.match(body,/正在检查索引并逐词检索/)};
const decideFeedback=new Function('state','$','api','key','setBusy','refreshTaskSidebar','refreshHistory','guidedStartSearch','message',
  process.env.CONFIRM_SOURCE+';return decideFeedback')(
    state,$,api,()=> 'key',()=>{},refreshTaskSidebar,refreshHistory,guidedStartSearch,message);
(async()=>{await decideFeedback('confirm',true);assert.deepEqual(events,['confirm','sidebar','message','history','search'])})().catch(e=>{console.error(e);process.exit(1)});
"""
        subprocess.run(['node', '-e', script], check=True,
                       env={**os.environ, 'CONFIRM_SOURCE': source})
        self.assertIn("card.classList.add('search-active')", CHAT_JS)
        self.assertIn("card.classList.add('search-unavailable')", CHAT_JS)
        self.assertIn("setAttribute('aria-label','逐词检索结果')", CHAT_JS)

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
const fields={'new-request':{classList:{add(){}}},'feedback-box':{classList:{add(){}}},'status':{textContent:''}};
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
  state,$,api,()=> 'key',()=>{},sessionStorage,localStorage,async()=>{},()=>{},message,async()=>{},async()=>events.push('search'),timeline);
(async()=>{
  await confirm(proposal,{value:'狗',disabled:false});assert.deepEqual(events,['draft','task','search']);assert.equal(state.task.task_id,'task-1');
  state.task=null;state.resumeDraft=null;state.proposedExpansion=proposal;rejectDraft=true;
  await confirm(proposal,{value:'狗',disabled:false});
  assert.equal(messages.at(-1)[0],'检索还没开始');
  assert.match(messages.at(-1)[1],/刷新后再次确认检索词/);
  assert.doesNotMatch(messages.at(-1)[1],/FEATURE_NOT_SUPPORTED|manual gates/);
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script],
                                env={**os.environ, 'CONFIRM_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for explicit term approval behavior test')
    def test_terms_only_start_search_after_button_click(self):
        source = CHAT_JS[CHAT_JS.index('function showTermReview('):
                         CHAT_JS.index('async function prepareGuidedExpansion(')]
        script = r"""
const assert=require('node:assert/strict');
const calls=[];
const proposal={lines:'狗 | 柴犬，牧羊犬',version:1};
const state={proposedExpansion:proposal,task:null};
const status={textContent:''};
let click;
const review=new Function('state','$','message','guidedAction','confirmGuidedExpansion',
  process.env.REVIEW_SOURCE+';return showTermReview')(
  state,()=>status,(_kind,_title,body)=>{assert.match(body,/只有点击下方按钮/);return {}},
  (_bubble,label,handler)=>{assert.equal(label,'确认检索词并开始检索');click=handler},
  async()=>calls.push('search'));
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

    def test_page_has_explicit_confirmation_and_local_limit_copy(self):
        self.assertIn('class="chat-composer panel"', CHAT_HTML)
        self.assertIn('id="insert-request-example"', CHAT_HTML)
        self.assertIn("$('insert-request-example').addEventListener('click',insertRequestExample)", CHAT_JS)
        self.assertIn('aria-label="发送消息"', CHAT_HTML)
        self.assertIn("if(turn.action==='confirm_terms'&&proposal)", CHAT_JS)
        self.assertIn("if(turn.action==='confirm_terms'&&proposal){showTermReview(proposal", CHAT_JS)
        self.assertNotIn("if(turn.action==='confirm_terms'&&proposal){await confirmGuidedExpansion", CHAT_JS)
        self.assertNotIn("body.node_modes.retrieval='auto'", CHAT_JS)
        self.assertIn("await offerExistingSearch(taskId)", CHAT_JS)
        self.assertIn("'开始逐词检索',guidedStartSearch", CHAT_JS)
        self.assertIn('id="task-sidebar-id"', CHAT_HTML)
        self.assertIn('id="task-sidebar-nodes"', CHAT_HTML)
        self.assertIn("$('task-sidebar-nodes')", CHAT_JS)
        self.assertIn('groupPreviewBody(raw,count)', CHAT_JS)
        self.assertIn("if(turn.action==='confirm'", CHAT_JS)
        self.assertIn('await prepareGuidedExpansion(turn.request.raw_request,turn.request.target_count)', CHAT_JS)
        self.assertIn('/followup-intent', CHAT_JS)
        self.assertIn("if(intent.kind==='expansion'){await showPostTaskExpansion(text,intent);return}", CHAT_JS)
        self.assertIn('requested_terms_per_group:requestedTerms', CHAT_JS)
        self.assertIn('重新扩词结果', CHAT_JS)
        self.assertIn('确认新版词表', CHAT_JS)
        self.assertIn('previous?.requestedTerms', CHAT_JS)
        self.assertIn("previous.feedbackText+'\\n后续调整：'+text", CHAT_JS)
        self.assertIn('async function reviseGuidedExpansion', CHAT_JS)
        self.assertIn('feedback_text:instruction,current_groups:current,count_edits:edits', CHAT_JS)
        self.assertIn("if(turn.action==='revise_terms'&&proposal)", CHAT_JS)
        self.assertIn("体验环境说明", CHAT_HTML)
        self.assertNotIn("确认需求与扩词，开始检索", CHAT_JS)
        self.assertIn("仅保存反馈文本", CHAT_JS)
        self.assertIn("尚未创建或执行清洗管线", CHAT_JS)
        self.assertIn("这条反馈还不能用于清洗", CHAT_JS)
        self.assertNotIn("模型清洗建议 · 未批准", CHAT_JS)
        self.assertNotIn("候选 VLM 指令", CHAT_JS)
        self.assertIn("测试索引检索不等于真实素材授权", CHAT_HTML)
        self.assertIn("feedback-suggestions", CHAT_JS)
        self.assertIn("initialize", CHAT_JS)
        self.assertIn("pending_draft", CHAT_JS)
        self.assertIn("new-task", CHAT_HTML)
        self.assertIn("localStorage.removeItem", CHAT_JS)
        self.assertIn("历史 Chat", CHAT_HTML)
        self.assertIn("草稿消息不会进入业务执行时间线", CHAT_JS)
        self.assertIn("refreshHistory", CHAT_JS)
        self.assertIn("openTask", CHAT_JS)
        self.assertIn("openConversation", CHAT_JS)
        self.assertIn("state.proposedExpansion=null;state.targetCountTouched=false;timeline.replaceChildren()", CHAT_JS)
        self.assertIn("不会进入业务执行时间线", CHAT_JS)
        self.assertIn("六节点进度", CHAT_HTML)
        self.assertIn("nodeHeader(1,'诉求确认'", CHAT_JS)
        self.assertIn("'诉求原文：'+query.raw_request", CHAT_JS)
        self.assertIn("body.className='node-body node-terms'", CHAT_JS)
        self.assertIn("terms.textContent=group.auxiliary_terms?.join('、')", CHAT_JS)
        self.assertIn("nodes.append(demandCard(progress))", CHAT_JS)
        self.assertIn("'未开始'", CHAT_JS)
        self.assertIn("'进行中'", CHAT_JS)
        self.assertIn("'已完成'", CHAT_JS)
        self.assertNotIn("'推进门禁'", CHAT_JS)
        self.assertNotIn("'推进模式'", CHAT_JS)
        self.assertIn("progress.counts.accepted===null?'未知'", CHAT_JS)
        self.assertIn("结果尚未发布", CHAT_JS)
        self.assertIn("权威结果统计", CHAT_JS)
        self.assertIn("preview_status==='unavailable'", CHAT_JS)
        self.assertIn("loadMoreResults", CHAT_JS)
        self.assertIn("检索词：", CHAT_JS)
        self.assertIn("检索词", CHAT_JS)
        self.assertIn("确认检索词并开始检索", CHAT_JS)
        self.assertNotIn("回复“是”就开始检索", CHAT_JS)
        self.assertIn("它们尚未清洗", CHAT_JS)
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
    def test_expansion_followup_is_not_saved_as_cleaning_feedback(self):
        prepare = CHAT_JS[CHAT_JS.index('async function prepareFeedback(){'):
                          CHAT_JS.index('async function showPostTaskExpansion(')]
        script = r"""
const assert=require('node:assert/strict');
const state={feedback:null,task:{task_id:'task-1',confirmed_revision:1,task_version:1}};
let routed='',posts=0,kind='expansion';
const fields={'feedback-text':{value:'别按情绪搜，多找狗在户外的画面'},'llm-model':{value:'llm'},'status':{textContent:''}};
const $=id=>fields[id];
const showPostTaskExpansion=async text=>{routed=text};
const api=async path=>{posts++;assert.match(path,/followup-intent/);return {kind,target_count:null,requested_terms_per_group:null}};
const setBusy=()=>{};
const message=()=>{throw Error('no clarification or cleaning card expected')};
const funcs=new Function('state','$','showPostTaskExpansion','api','setBusy','message',
  process.env.PREPARE_SOURCE+';return {prepareFeedback,routeTaskFollowup};')(
    state,$,showPostTaskExpansion,api,setBusy,message);
(async()=>{await funcs.prepareFeedback();assert.match(routed,/多找狗在户外/);assert.equal(posts,1)})().catch(e=>{console.error(e);process.exit(1)});
"""
        subprocess.run(['node', '-e', script], check=True,
                       env={**os.environ, 'PREPARE_SOURCE': prepare})

    def test_local_first_search_requires_separate_choice_confirmation_and_tick(self):
        self.assertIn('id="first-search"', CHAT_HTML)
        self.assertIn('id="first-search-candidates"', CHAT_HTML)
        self.assertIn('本地首次检索（需确认）', CHAT_HTML)
        self.assertIn('type=\'checkbox\'', CHAT_JS)
        self.assertIn('生成检索确认卡', CHAT_JS)
        self.assertIn('确认整组检索方案', CHAT_JS)
        self.assertIn('执行下一批', CHAT_JS)
        self.assertIn('非合格结果', CHAT_JS)
        self.assertIn('未接授权媒体网关', CHAT_JS)
        self.assertNotIn('innerHTML', CHAT_JS)

    @unittest.skipUnless(shutil.which('node'), 'Node required for guided routing behavior test')
    def test_guided_search_auto_starts_only_with_unique_authorized_routes(self):
        source = CHAT_JS[CHAT_JS.index('async function guidedStartSearch(){'):
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
  assert.match(messages.at(-1).body,/测试索引无法编码：猫/);
  routes=[{branch_id:'dog',query_text:'狗',options:[option('dog-index')]},{branch_id:'cat',query_text:'猫',options:[option('cat-1'),option('cat-2')]}];
  await start();
  assert.equal(started,1);
  assert.deepEqual(state.firstSearchChoices,[{branch_id:'dog',binding_id:'dog-index',batch_size:2}]);
  assert.equal(messages.at(-1).title,'请选择有歧义的索引');
  assert.equal(actions.at(-1),'按所选索引开始检索');
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'GUIDED_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for incremental search behavior test')
    def test_guided_search_runs_batches_and_updates_visible_candidates_until_done(self):
        source = CHAT_JS[CHAT_JS.index('async function guidedTick(){'):
                         CHAT_JS.index('async function confirmTask(){')]
        script = r"""
const assert=require('node:assert/strict');
const state={task:{task_id:'task-1'},firstSearchChoices:[{branch_id:'dog',binding_id:'dog'}],guidedResultBubble:null};
let ticks=0,candidates=0,actions=[],messages=[];
const api=async(path)=>{
  if(path.endsWith('/tick')){ticks++;return {network_attempts:1,pagination_stopped:ticks===2,outcomes:[],members:[{stop_reason:ticks===2?'exhausted':null}]}}
  candidates++;return {raw_hit_count:candidates,items:[],truncated:false};
};
const message=(kind,title,text)=>{const bubble={title,text,isConnected:true,append(){},remove(){this.isConnected=false}};messages.push(bubble);return bubble};
const aggregateLocalCandidates=()=>[];
const appendLocalCandidateGroups=()=>0;
const document={createElement:()=>({textContent:''})};
const live={textContent:'',classList:{remove(){}}};
const fields={'retrieval-live-status':live,'feedback-heading':{textContent:''},'feedback-box':{classList:{remove(){}}}};
const $=id=>fields[id];
const guidedAction=(bubble,label)=>actions.push(label);
const setBusy=()=>{};
const refreshTaskSidebar=async()=>{};
const tick=new Function('state','api','message','aggregateLocalCandidates','appendLocalCandidateGroups','document','$','guidedAction','setBusy','refreshTaskSidebar',process.env.TICK_SOURCE+';return guidedTick;')(state,api,message,aggregateLocalCandidates,appendLocalCandidateGroups,document,$,guidedAction,setBusy,refreshTaskSidebar);
(async()=>{
  await tick();
  assert.equal(ticks,2);
  assert.equal(candidates,2);
  assert.equal(state.guidedResultBubble.title,'检索完成 · 本地测试');
  assert.match(live.textContent,/本地检索完成/);
  assert.deepEqual(actions,[]);
  assert.equal(messages[0].isConnected,false);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'TICK_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_page_exposes_model_roles_and_accessible_structure(self):
        self.assertIn('id="main-content"', CHAT_HTML)
        self.assertIn('class="skip-link"', CHAT_HTML)
        self.assertIn('role="log"', CHAT_HTML)
        self.assertIn('aria-relevant="additions"', CHAT_HTML)
        self.assertIn('DSH 需求理解 / 扩词模型', CHAT_HTML)
        self.assertIn('清洗语义判定模型', CHAT_HTML)
        self.assertIn('使用 API 与密钥配置', CHAT_HTML)
        self.assertIn('id="api-key" type="password"', CHAT_HTML)
        self.assertIn('密钥不写入浏览器存储', CHAT_HTML)
        self.assertIn('id="new-chat"', CHAT_HTML)
        self.assertIn('新建 Chat', CHAT_HTML)
        self.assertIn('class="right-rail"', CHAT_HTML)
        self.assertGreater(CHAT_HTML.index('class="right-rail"'), CHAT_HTML.index('class="chat"'))
        self.assertNotIn('LOCAL WORKSPACE · PREVIEW', CHAT_HTML)
        self.assertNotIn('把检索需求整理成', CHAT_HTML)
        self.assertNotIn('先梳理目标、分组和推进方式', CHAT_HTML)
        self.assertIn('class="context-bar"', CHAT_HTML)
        self.assertIn("$('new-chat').addEventListener('click',startNewChat)", CHAT_JS)
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
        self.assertIn('/v1/retrieval/projects/demo/dialogue-turn', CHAT_JS)
        self.assertNotIn('DSH 助手 · 正在分析', CHAT_JS)
        self.assertIn('const turn=await api(', CHAT_JS)
        self.assertIn('if(turn.action===\'confirm\'', CHAT_JS)
        self.assertIn('async function guidedStartSearch', CHAT_JS)
        self.assertIn('async function guidedTick', CHAT_JS)
        self.assertIn('检索词：', CHAT_JS)
        self.assertIn('每个检索词至少选择一个索引', CHAT_JS)
        self.assertIn('无匹配的已授权索引', CHAT_JS)
        self.assertIn('测试索引无法编码', CHAT_JS)
        self.assertIn("source.query_text", CHAT_JS)
        self.assertIn("setAttribute('aria-label','命中检索词')", CHAT_JS)
        self.assertIn('.search-tag{', CHAT_CSS)
        self.assertIn("event.key==='Enter'&&!event.shiftKey&&!event.isComposing", CHAT_JS)
        self.assertIn("$('feedback-text').addEventListener('keydown'", CHAT_JS)
        self.assertIn("await showCleaningSuggestion()", CHAT_JS)
        self.assertIn("suggestion.feedback_status==='ready'", CHAT_JS)
        self.assertNotIn('模型的理解建议，不代表已执行', CHAT_JS)
        self.assertNotIn('查看逐条引文与能力边界', CHAT_JS)
        self.assertIn('count_hint:hint', CHAT_JS)
        self.assertIn('state.targetCountTouched', CHAT_JS)
        self.assertIn("llms.length===1)$('llm-model').value", CHAT_JS)
        self.assertIn("vlms.length===1)$('vlm-model').value", CHAT_JS)
        self.assertNotIn('DSH 助手 · 分析未完成', CHAT_JS)
        self.assertIn("$('chat-model-badge').textContent", CHAT_JS)
        self.assertIn("querySelectorAll('button,#llm-model,#vlm-model,#target-count')", CHAT_JS)
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

    @unittest.skipUnless(shutil.which('node'), 'Node required for feedback behavior test')
    def test_irrelevant_feedback_has_no_confirmation_action(self):
        source = CHAT_JS[CHAT_JS.index('function showFeedbackReview(suggestion){'):
                         CHAT_JS.index('async function showCleaningSuggestion(){')]
        script = r"""
const assert=require('node:assert/strict');
const state={feedbackReviewBubble:null,feedbackManualCard:null};
const message=(kind,title,text)=>({kind,title,text,actions:[],isConnected:true,remove(){this.isConnected=false}});
const guidedAction=(bubble,label)=>bubble.actions.push(label);
const card=()=>({});
const decideFeedback=()=>{};
const editFeedback=()=>{};
const $=()=>({classList:{add(){}}});
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
assert.deepEqual(state.feedbackReviewBubble.actions,['仅保存反馈文本','重新描述','不采用']);
assert.match(state.feedbackReviewBubble.text,/模型建议尚未写入清洗规则/);
"""
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'REVIEW_SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
