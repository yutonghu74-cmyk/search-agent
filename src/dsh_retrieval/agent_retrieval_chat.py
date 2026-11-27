"""Small opt-in branch within the existing Chat and candidate windows."""

AGENT_RETRIEVAL_CHAT_JS = r"""
async function loadAgentWindowProfile(){
 state.agentWindowLoading=true;state.agentWindowError=null;
 try{
  const profile=await api('/v1/retrieval/projects/demo/agent-retrieval');state.agentWindowProfile=profile;
  if(profile.enabled){
   if(!profile.available)state.agentWindowError='暂时无法查找图片，请稍后再试。';
  }
 }catch(error){
  if(error.code==='NOT_FOUND')state.agentWindowProfile={enabled:false};
  else state.agentWindowError='暂时无法连接图片服务，请稍后刷新。';
 }finally{state.agentWindowLoading=false}
}
function agentRequestCount(text,profile){
 const matches=[...text.matchAll(/([1-9]\d*)\s*张/g)];
 if(matches.length>1)throw new Error('本轮只支持一个检索主题和数量，分组数量请使用其他已支持的流程。');
 const raw=$('target-count').value.trim(),hint=state.targetCountTouched&&raw?Number(raw):null;
 if(hint!==null&&matches.length&&hint!==Number(matches[0][1]))throw new Error('消息和数量设置不一致，请先修改后再确认。');
 const count=hint??(matches.length?Number(matches[0][1]):null);
 if(!Number.isInteger(count)||count<1||count>profile.max_candidates)throw new Error(`请写明 1 到 ${profile.max_candidates} 张，例如“找 5 张猫图片”。本轮不会自动补数。`);
 return count;
}
function agentDraftStorageKey(){return 'dsh-agent-draft:'+state.agentWindowProfile.scope_ref}
function agentDraftSave(record){state.agentDraftRecord=record;try{sessionStorage.setItem(agentDraftStorageKey(),JSON.stringify(record))}catch{}}
function appendAgentDetails(target,rows){
 if(!rows.length)return;
 const details=document.createElement('details'),summary=document.createElement('summary'),list=document.createElement('dl');
 details.className='agent-details';summary.textContent='查看详情';details.append(summary);
 for(const [name,value] of rows){const term=document.createElement('dt'),description=document.createElement('dd');term.textContent=name;description.textContent=String(value);list.append(term,description)}
 details.append(list);target.append(details);
}
function showConfirmedTermsAgentStatus(job){
 if(!['failed','unknown'].includes(job.status))return;
 const identity=JSON.stringify([job.job_ref,job.status,job.code]);
 if(state.confirmedTermsStatusIdentity===identity)return;state.confirmedTermsStatusIdentity=identity;
 const intake=job.artifacts_ready&&job.execution_scope?.candidate_intake;
 const title=intake?'自动清洗交接未完成':job.status==='unknown'?'检索结果需要核对':'这次检索未完成';
 const text=intake?'检索候选已保留，自动清洗尚未获准进入执行。请查看状态原因，系统不会跳过预算、规则或授权校验。':job.status==='unknown'?'部分检索请求尚未收到确定结果，已暂停，不会自动重发。已确认的词表仍保留。':
  job.code==='SEARCH_BUDGET_EXHAUSTED'?'已达到确认的检索预算，停止继续查找。已有候选仍可查看。':
  job.code==='AGENT_SOURCE_UNAVAILABLE'?'当前来源不能执行完整词表，请调整范围后重新确认。':
  '检索暂时未能完成。已确认的词表和已有结果仍保留。';
 const bubble=message('assistant',title,text);appendAgentDetails(bubble,[['状态代码',job.code||job.status]]);
}
function agentWindowPresentation(data){
 const job=data.agent_job,c=job?.confirmation;
 const progress=job?.planning_progress;
 if(!job?.query_terms?.length&&['unknown','failed'].includes(job?.status)&&progress?.deadline_reached)return {title:'生成检索词超时',text:`未能在 ${progress.max_seconds} 秒内收到完整方案，图片检索尚未开始。这次请求已保留，不会自动重试。`};
 if(job?.status==='unknown'||job?.code==='AGENT_PLANNING_UNKNOWN')return {title:'这次查找已暂停',text:'准备查找时没有收到完整结果。请求已保留，可以稍后查看状态。'};
 if(job?.status==='failed')return {title:'这次查找未完成',text:'这次没有得到完整结果。你可以查看状态，或新建对话重新说明需求。'};
 if(job?.status==='cancelled')return {title:'这次查找已结束',text:'可以新建对话，告诉我接下来想找什么。'};
 if(job?.status==='completed')return {title:'图片已准备好',text:'可以查看这些图片；它们还没有经过清洗。'};
 if(c?.checkpoint==='planning'||job?.status==='ready')return {title:'准备生成检索词',text:'确认后，我会根据你的需求整理检索词，供你查看。'};
 if(c?.checkpoint==='retrieval')return {title:'检索词已准备好',text:'请查看下面的检索词，确认后开始找图片。'};
 if(c?.checkpoint==='candidates')return {title:'请查看找到的图片',text:`已准备 ${c.candidate_count} 张候选图片，请查看后确认。这些图片还没有经过清洗。`};
 if(job?.phase==='planning')return {title:'正在生成检索词',text:progress?`正在等待模型返回方案，已等待 ${progress.elapsed_seconds} 秒，最长 ${progress.max_seconds} 秒。检索词生成后会在这里显示。`:'正在等待模型返回检索词，图片检索尚未开始。'};
 return {title:job?.phase==='retrieval'?'正在查找图片':'正在准备查找',text:'结果就绪后会在这里显示。'};
}
async function prepareAgentCandidateRequest(text){
 const profile=state.agentWindowProfile;if(!profile?.enabled)return;
 const epoch=state.historyOpenToken,current=()=>epoch===state.historyOpenToken&&!state.task;
 setBusy(true);
 try{
  if(!profile.available)throw new Error('检索后台尚未就绪，未创建任务。');
  if(state.queryImage||$('group-lines').value.trim()||$('auto-refill').checked||state.historyPolicy?.scope&&state.historyPolicy.scope!=='none'||/视频|片段/.test(text))
   throw new Error('本轮只支持图片文本检索，不支持参考图、分组、历史去重或自动补数。原设置已保留。');
  const count=agentRequestCount(text,profile);
  const signature=JSON.stringify([text,count]);
  let record=state.agentDraftRecord;
  if(!record){try{record=JSON.parse(sessionStorage.getItem(agentDraftStorageKey())||'null')}catch{}}
  if(record?.signature===signature&&record?.draft){
   const saved=await api('/v1/retrieval/projects/demo/drafts/'+record.draft.draft_id);
   if(!current())return;
   if(saved.confirmed_task_id){setBusy(false);await openTask(saved.confirmed_task_id);return}
  }
  if(record?.signature!==signature){
   const body={dimension_id:'default',query_spec:{schema_version:'query-spec@2',raw_request:text,
    media_type:'image',target_count:count,business_domain:'candidate_retrieval',
    intra_batch_dedup:{policy_ref:'exact-content',config_version:'1'},
    cross_batch_dedup:{scope:'none',references:[],comparison_methods:[],reference_digest:null},
    refill_policy:{mode:'input_only',target_count:count,approved_library_ids:[],progression:'manual'},
    agent_requirements:{schema_version:'agent-requirements@2',processing:[],autonomy:{
     actions:profile.actions,methods:profile.methods,library_ids:profile.library_ids,
     max_rounds:1,max_no_progress_rounds:1,max_calls:profile.max_model_calls,max_seconds:profile.max_seconds}}},
    node_modes:{expansion:'manual',retrieval:'manual',cleaning:'manual',refill:'manual',packaging:'manual'}};
   record={signature,body,key:key('agent-draft'),draft:null};agentDraftSave(record);
  }
  const draft=await api('/v1/retrieval/projects/demo/drafts',{method:'POST',keyValue:record.key,body:record.body});
  if(!current())return;
  record={...record,draft};agentDraftSave(record);state.draft=draft;
  state.agentDraftCard?.remove?.();
  message('user','',text);$('request-text').value='';
  state.agentDraftCard=card('确认一下你的需求',[
   ['想找',draft.proposal.query_spec.raw_request],['数量',`${count} 张`],
   ['说明','这次先找图片，找到后再由你查看确认。']
  ],[['确认需求',()=>confirmAgentCandidateTask(record),false]]);
  appendAgentDetails(state.agentDraftCard,[
   ['范围',profile.max_pages>1?`最多 ${profile.max_pages} 页，每页最多 ${Math.min(profile.page_size,count)} 条。末页可能超出期望数量。`:`最多 ${count} 个图片候选`],
   ['来源',draft.proposal.query_spec.agent_requirements.autonomy.library_ids.join('、')],
   ['规划上限',`${profile.max_model_calls} 次模型调用，最多 ${profile.max_seconds} 秒`],
   ['交付说明','候选未经清洗，不保证筛选条件通过；本轮不打标、不补数、不计算有效数量。']
  ]);
 }catch(error){if(current()){$('status').textContent=error.detail||error.message;message('assistant','需求尚未执行',error.detail||error.message)}}
 finally{if(current())setBusy(false)}
}
async function confirmAgentCandidateTask(record){
 if(state.agentActionBusy)return;state.agentActionBusy=true;setBusy(true);
 const epoch=state.historyOpenToken,current=()=>epoch===state.historyOpenToken;
 try{
  const draft=record.draft;
  const result=await api('/v1/retrieval/tasks',{method:'POST',keyValue:'agent-task:'+draft.draft_id+':'+draft.draft_version,
   body:{draft_id:draft.draft_id,expected_draft_version:draft.draft_version,proposal_digest:draft.proposal_digest,action:'confirm'}});
  if(!current()||state.task&&state.task.task_id!==result.task_id)return;
  sessionStorage.removeItem(agentDraftStorageKey());state.agentDraftRecord=null;state.draft=null;
  state.agentDraftCard?.querySelector('.actions')?.remove();state.agentActionBusy=false;setBusy(false);await openTask(result.task_id);
 }catch(error){
  if(!current())return;
  $('status').textContent='确认结果需核对：'+(error.detail||error.message)+'。可以重试同一确认，不会重复提单。';
  // A lost response is resolved by reading the original owner-bound draft.
  try{const saved=await api('/v1/retrieval/projects/demo/drafts/'+record.draft.draft_id);if(current()&&saved.confirmed_task_id){state.agentActionBusy=false;setBusy(false);await openTask(saved.confirmed_task_id)}}catch{}
 }finally{if(current()){state.agentActionBusy=false;setBusy(false);if(state.task?.agent_retrieval){await refreshTaskSidebar();scheduleAgentWindowPoll()}}}
}
function stopAgentWindowPolling(){clearTimeout(state.agentWindowTimer);state.agentWindowTimer=null;state.agentWindowPollToken=(state.agentWindowPollToken||0)+1}
async function openAgentWindowWorkspace(data){
 stopTaskEvents();stopAgentWindowPolling();state.task=data;state.conversationId=data.conversation_id||null;
 localStorage.setItem('dsh-task-id',data.task_id);state.agentCardIdentity=null;
 $('new-request').classList.add('hidden');$('feedback-box').classList.remove('hidden');
 $('status').textContent='';
 if(typeof updateChatHeader==='function')updateChatHeader(data.raw_request||'图片查找',agentWindowPresentation(data).title);
 renderAgentWindowWorkspace(data);scheduleAgentWindowPoll();return true;
}
function scheduleAgentWindowPoll(){
 clearTimeout(state.agentWindowTimer);const taskId=state.task?.task_id,token=state.agentWindowPollToken;
 if(!taskId||!state.task.agent_retrieval||['completed','failed','unknown','cancelled'].includes(state.task.agent_job?.status))return;
 state.agentWindowTimer=setTimeout(async()=>{
  if(state.task?.task_id!==taskId||state.agentWindowPollToken!==token)return;
  try{if(!state.agentActionBusy&&!document.hidden){const value=await api(`/v1/retrieval/tasks/${taskId}/workspace`);if(state.task?.task_id===taskId&&state.agentWindowPollToken===token)renderAgentWindowWorkspace(value)}}
  catch(error){$('retrieval-live-status').textContent='状态读取失败，保留上次结果：'+(error.detail||error.message)}
  if(state.task?.task_id===taskId&&state.agentWindowPollToken===token)scheduleAgentWindowPoll();
 },1500);
}
function renderAgentWindowWorkspace(data){
 if(!data.agent_retrieval||state.task?.task_id!==data.task_id)return;
 state.task={...state.task,...data};const job=data.agent_job;
 state.searchSnapshot={taskId:data.task_id,revision:data.confirmed_revision,generation:data.run_generation,
  progress:data.progress,workflow:data.workflow,search:{pagination_stopped:['completed','cancelled','failed','unknown'].includes(job?.status)},candidates:{raw_hit_count:data.candidate_count}};
 resetResultWorkspace();$('task-sidebar-identity').classList.remove('hidden');if(typeof setTaskIdentity==='function')setTaskIdentity(data);else $('task-sidebar-id').textContent=data.request_number||data.task_id;
 const presentation=agentWindowPresentation(data),count=data.target_count??data.progress.counts.requested;
 if(typeof updateChatHeader==='function')updateChatHeader(data.raw_request||'图片查找',presentation.title);
 $('task-sidebar-summary').textContent=`想找 ${count} 张图片 · 找到的图片还需筛选`;
 $('retrieval-live-status').textContent=presentation.title;
 const nodes=$('task-sidebar-nodes');nodes.replaceChildren();
 data.progress.nodes.forEach((node,index)=>{const box=document.createElement('div');box.className='node-card';box.append(nodeHeader(index+1,node.label,node.display_status,'div'));nodes.append(box)});
 if(state.agentActionBusy)return;
 const confirmation=job?.confirmation;
 const identity=job?JSON.stringify([job.job_ref,job.status,job.phase,confirmation?.checkpoint,confirmation?.proposal_digest,job.retrieval_progress,job.query_terms,Math.floor((job.planning_progress?.elapsed_seconds||0)/5)]):'missing:'+data.task_id;
 if(state.agentCardIdentity===identity)return;state.agentCardIdentity=identity;
 state.agentJobCard?.remove?.();
 if(!job){
  state.agentJobCard=card('需求已确认',[['状态','尚未准备检索规划；查询不会创建运行。']],
   [['准备检索',()=>prepareAgentWindowJob(data.task_id),false]]);return;
 }
 const actions=[];
 if(confirmation)actions.push([confirmation.checkpoint==='planning'?'生成检索词':confirmation.checkpoint==='retrieval'?'开始查找':'确认这些图片',()=>confirmAgentWindowCheckpoint(data.task_id,job),false]);
 else if(job.status==='ready')actions.push(['生成检索词',()=>startAgentWindowJob(data.task_id,job),false]);
 if(data.candidate_count>0)actions.push(['查看图片',openCandidateBrowser,true]);
 if(job.status==='completed')actions.push(['查看已确认图片',()=>showAgentWindowArtifacts(data.task_id,job.job_ref),true]);
 if(['failed','unknown','cancelled'].includes(job.status))actions.push(['新建对话',()=>startNewChat(),false]);
 if(!['completed','cancelled'].includes(job.status))actions.push([['failed','unknown'].includes(job.status)?'结束这次请求':'停止查找',()=>cancelAgentWindowJob(data.task_id,job),true]);
 actions.push(['查看最新状态',async()=>{await refreshTaskSidebar();scheduleAgentWindowPoll()},true]);
 const rows=[];
 if(confirmation?.checkpoint==='planning')rows.push(['规划模型',confirmation.model_profile_ref],['资源上限',`${confirmation.model_calls} 次调用，${confirmation.max_seconds} 秒`]);
 if(confirmation?.checkpoint==='retrieval')rows.push(['数据来源',confirmation.library_id],['数量上限',confirmation.batch_size]);
 if(confirmation?.checkpoint==='retrieval'&&confirmation.max_pages>1)rows.push(['翻页范围',`最多 ${confirmation.max_pages} 页、${confirmation.max_raw_hits} 条原始命中；期望 ${confirmation.target_count} 个候选，按整页检索，达到目标后停止。`]);
 if(confirmation?.checkpoint==='candidates')rows.push(['候选数量',confirmation.candidate_count]);
 if(job.retrieval_progress)rows.push(['累计检索',`${job.retrieval_progress.page_count} 页 · ${job.retrieval_progress.raw_hit_count} 条原始命中 · ${job.retrieval_progress.candidate_count} 个唯一候选`]);
 if(job.retrieval_progress?.stop_reason)rows.push(['停止原因',({candidate_target:'候选目标已达到',page_limit:'页数上限已达到',exhausted:'当前来源已耗尽',depth_limit:'来源检索深度已达上限'})[job.retrieval_progress.stop_reason]||job.retrieval_progress.stop_reason]);
 if(job.code)rows.push(['状态代码',job.code]);
 const mainRows=[['说明',presentation.text]];
 if(job.query_terms?.length)mainRows.push(['检索词',job.query_terms.join('；')]);
 state.agentJobCard=card(presentation.title,mainRows,actions);
 appendAgentDetails(state.agentJobCard,rows);
}
async function prepareAgentWindowJob(taskId){
 if(state.agentActionBusy||state.task?.task_id!==taskId)return;state.agentActionBusy=true;setBusy(true);
 try{await api(`/v1/retrieval/tasks/${taskId}/agent-retrieval/prepare`,{method:'POST',keyValue:'agent-prepare:'+taskId,body:{}})}
 catch(error){$('status').textContent=error.detail||error.message}
 finally{state.agentActionBusy=false;setBusy(false);if(state.task?.task_id===taskId){await refreshTaskSidebar();scheduleAgentWindowPoll()}}
}
async function confirmAgentWindowCheckpoint(taskId,job){
 if(state.agentActionBusy||state.task?.task_id!==taskId)return;state.agentActionBusy=true;setBusy(true);
 try{
  const c=job.confirmation;if(!c)return;
  const value=await api(`/v1/retrieval/tasks/${taskId}/agent-jobs/${job.job_ref}/confirm`,{method:'POST',
   keyValue:`agent-confirm:${job.job_ref}:${c.checkpoint}:${c.proposal_digest}`,
   body:{action:'confirm',checkpoint:c.checkpoint,proposal_digest:c.proposal_digest,expected_task_version:c.expected_task_version}});
  if(c.checkpoint==='planning'&&value.status==='ready')await postAgentWindowStart(taskId,value);
 }catch(error){$('status').textContent='确认未完成，请核对状态：'+(error.detail||error.message)}
 finally{state.agentActionBusy=false;setBusy(false);if(state.task?.task_id===taskId){state.agentCardIdentity=null;await refreshTaskSidebar();scheduleAgentWindowPoll()}}
}
async function postAgentWindowStart(taskId,job){
 return api(`/v1/retrieval/tasks/${taskId}/agent-jobs/${job.job_ref}/start`,{method:'POST',
  keyValue:'agent-start:'+job.job_ref,body:{action:'start',...job.planning_review}});
}
async function startAgentWindowJob(taskId,job){
 if(state.agentActionBusy||state.task?.task_id!==taskId)return;state.agentActionBusy=true;setBusy(true);
 try{await postAgentWindowStart(taskId,job)}catch(error){$('status').textContent=error.detail||error.message}
 finally{state.agentActionBusy=false;setBusy(false);if(state.task?.task_id===taskId){state.agentCardIdentity=null;await refreshTaskSidebar();scheduleAgentWindowPoll()}}
}
async function cancelAgentWindowJob(taskId,job){
 if(state.agentActionBusy||state.task?.task_id!==taskId)return;state.agentActionBusy=true;setBusy(true);
 try{await api(`/v1/retrieval/tasks/${taskId}/agent-jobs/${job.job_ref}/cancel`,{method:'POST',
  keyValue:'agent-cancel:'+job.job_ref,body:{action:'cancel',expected_task_version:job.task_version}})}
 catch(error){$('status').textContent=error.detail||error.message}
 finally{state.agentActionBusy=false;setBusy(false);if(state.task?.task_id===taskId){state.agentCardIdentity=null;await refreshTaskSidebar();scheduleAgentWindowPoll()}}
}
async function showAgentWindowArtifacts(taskId,jobRef){
 try{const value=await api(`/v1/retrieval/tasks/${taskId}/agent-jobs/${jobRef}/artifacts`);if(state.task?.task_id!==taskId)return;
  if(value.page_count>1)card('候选清单汇总',[['来源页面',value.page_count],['唯一候选',value.candidate_count],['说明','页面间重复候选只计一次；各页保留原始清单引用，未经清洗。']]);
  for(const item of value.items)card('已发布候选清单',[['候选数量',item.candidate_count],['清单引用',item.manifest_ref],['说明','原始候选，未验证清洗规则；有效数量未计算。']]);
 }catch(error){$('status').textContent=error.detail||error.message}
}
"""
