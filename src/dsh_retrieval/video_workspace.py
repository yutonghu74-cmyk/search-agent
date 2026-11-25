"""Video execution/review workspace over existing authenticated APIs."""
VIDEO_WORKSPACE_HTML = '''
<section class="rail-card" id="video-workspace">
 <p id="video-capabilities" role="status"></p><button id="video-work-confirm" disabled type="button">确认当前草稿并开始检索</button>
 <div class="field"><label for="video-task-id">视频任务 ID</label><input id="video-task-id" placeholder="可输入已有任务 ID 继续"></div>
 <button id="video-work-refresh" class="secondary" type="button">读取 / 刷新视频任务</button>
 <div class="preview-actions"><button id="video-work-routes" type="button">查看检索来源</button><button id="video-work-search-prepare" type="button" disabled>准备所选检索计划</button><button id="video-work-search-confirm" type="button" disabled>确认计划并开始检索</button></div>
 <div id="video-work-sources"></div><p id="video-search-status" role="status"></p>
 <p id="video-work-status" role="status" aria-live="polite"></p><p id="video-work-counts"></p>
 <pre id="video-work-review" style="white-space:pre-wrap"></pre>
 <div class="preview-actions"><button id="video-work-prepare" type="button">准备验证候选</button><button id="video-work-clean" type="button" disabled>确认并验证上述候选</button><button id="video-work-refill" type="button">查看下一轮补数</button><button id="video-work-refill-confirm" type="button" disabled>确认上述补数</button></div>
 <div class="preview-actions"><button id="video-work-package" type="button">准备交付包</button><button id="video-work-publish" type="button" disabled>确认发布上述交付包</button><button id="video-work-download" type="button" disabled>下载已发布交付包</button></div>
 <div class="field"><label for="video-work-view">查看素材</label><select id="video-work-view"><option value="candidates">检索候选</option><option value="accepted">合格原视频</option><option value="review">待复核</option><option value="failed">淘汰</option></select></div>
 <div id="video-work-items"></div><button id="video-work-more" type="button" class="secondary" disabled>下一页</button>
</section>
'''
VIDEO_WORKSPACE_JS = r'''
const videoWork={id:null,busy:false,epoch:0,cleaning:null,refill:null,delivery:null,result:null,task:null,cursor:null,urls:[],timer:null,caps:null,pageSignature:null,currentCursor:null,search:null,routes:null,routeChoices:[],binding:null,searchProgress:null};
function updateVideoExecution(draft){
  $('video-work-confirm').textContent=draft?.proposal?.node_modes?.retrieval==='auto'?'确认当前草稿并开始检索':'确认当前草稿并创建任务';
  $('video-work-confirm').disabled=!draft?.video_summary?.execution?.available||!!draft.confirmed_task_id||videoWork.busy;
  $('video-capabilities').textContent=draft?.video_summary?.execution?.available?'视频能力已就绪；执行前请确认条件、来源和数量。':'视频能力尚未配置完整，可先保存草稿。';
  if(draft?.confirmed_task_id)$('video-task-id').value=draft.confirmed_task_id;
}
function configureVideoRefill(body){
  const spec=body.query_spec,auto=$('video-auto-refill').checked;
  if(auto){
    const libs=videoWork.caps?.source_library_ids||spec.refill_policy.approved_library_ids;
    if(!libs?.length)throw new Error('自动补数需先配置并验证视频来源；当前可保存固定输入草稿。');
    spec.refill_policy={mode:'maintain_target',target_count:spec.target_count,approved_library_ids:libs,progression:'auto'};
    for(const node of ['retrieval','cleaning','refill'])body.node_modes[node]='auto';
  }else{
    spec.refill_policy={mode:'input_only',target_count:spec.target_count,approved_library_ids:[],progression:'manual'};
    for(const node of ['retrieval','cleaning','refill'])body.node_modes[node]='manual';
  }
  function limit(id){const value=$(id).value;if(!value)return null;const n=Number(value);if(!Number.isSafeInteger(n)||n<1)throw new Error('预算必须为正整数或留空。');return n}
  spec.refill_limits={max_rounds:limit('video-max-rounds'),max_vlm_calls:limit('video-max-calls'),no_progress_rounds:2};
  return body;
}
function videoTaskPath(){if(!/^rt_[a-f0-9]{32}$/.test(videoWork.id||''))throw new Error('请先读取视频任务。');return '/v1/retrieval/tasks/'+videoWork.id}
async function videoWrite(path,body){
  const signature=JSON.stringify({path,body});let pending;
  try{pending=JSON.parse(sessionStorage.getItem('dsh-video-write')||'null')}catch{}
  if(pending?.signature!==signature)pending={signature,key:key('video-action')};
  sessionStorage.setItem('dsh-video-write',JSON.stringify(pending));
  const result=await api(path,{method:'POST',body,keyValue:pending.key});
  sessionStorage.removeItem('dsh-video-write');return result;
}
async function videoAction(action){
  if(videoWork.busy||videoDraftState.busy)return;
  videoWork.busy=true;for(const el of $('video-workspace').querySelectorAll('button,input,select'))el.disabled=true;
  try{await action()}catch(error){clearVideoPlayers();videoWork.pageSignature=null;$('video-work-status').textContent=error.message}
  finally{videoWork.busy=false;
    for(const el of $('video-workspace').querySelectorAll('button,input,select'))el.disabled=false;
    $('video-work-search-prepare').disabled=!videoWork.routes;
    $('video-work-search-confirm').disabled=!videoWork.search;
    $('video-work-clean').disabled=!videoWork.cleaning;$('video-work-refill-confirm').disabled=!videoWork.refill?.next_position;
    $('video-work-download').textContent=videoWork.delivery?.manifest?.delivery_mode==='authorized_originals'?'下载交付清单（原视频逐项下载）':'下载已发布视频 ZIP';
    $('video-work-publish').disabled=videoWork.delivery?.status!=='staged';$('video-work-download').disabled=videoWork.delivery?.status!=='published';
    $('video-work-more').disabled=!videoWork.cursor;$('video-work-package').disabled=!!videoWork.delivery?.available;updateVideoExecution(videoDraftState.draft);
  }
}
function clearVideoPlayers(){for(const url of videoWork.urls)URL.revokeObjectURL(url);videoWork.urls=[];$('video-work-items').replaceChildren()}
function nativeVideoUrl(path){return path.replace('/v1/retrieval/tasks/','/v1/retrieval/projects/'+encodeURIComponent(videoWork.task.project_id)+'/tasks/')}
async function videoBlob(path){
 const response=await fetch(path,{headers});if(!response.ok){const error=await response.json();throw new Error(error.message||'视频读取失败')}
 const blob=await response.blob();const url=URL.createObjectURL(blob);videoWork.urls.push(url);return url;
}
async function showVideoPage(cursor=null){
 const epoch=videoWork.epoch,path=videoTaskPath(),view=$('video-work-view').value;
 const query=new URLSearchParams({limit:'12'});if(cursor)query.set('cursor',cursor);if(view!=='candidates')query.set('view',view);
 const page=await api(path+(view==='candidates'?'/candidates?':'/results?')+query);
 if(epoch!==videoWork.epoch)return;videoWork.cursor=page.next_cursor||null;videoWork.currentCursor=cursor;
 const signature=JSON.stringify([path,view,cursor,page.result_set_digest,(page.items||[]).map(i=>[i.candidate_id,i.asset_version,i.reasons])]);
 if(signature===videoWork.pageSignature)return;videoWork.pageSignature=signature;clearVideoPlayers();const renderEpoch=++videoWork.epoch;
 const seen=new Set();
 for(const item of page.items||[]){if(!item.candidate_id||seen.has(item.candidate_id))continue;seen.add(item.candidate_id);
   const card=document.createElement('article');card.className='message';const label=document.createElement('p');label.textContent=`${item.candidate_id} · ${item.asset_version||''}`;card.append(label);
   const reason=document.createElement('p');reason.textContent=(item.reasons||[]).map(r=>typeof r==='string'?r:JSON.stringify(r)).join('；');card.append(reason);
   const button=document.createElement('button');button.type='button';button.textContent='播放原视频';
   button.onclick=async()=>{button.disabled=true;try{
     const query=item.preview_ref?'?ref='+encodeURIComponent(item.preview_ref):'';
     const mediaPath=path+(view==='candidates'?'/candidates/':'/cleaning/candidates/')+item.candidate_id+'/video'+query;
     const src=window.dshPlatformSession?nativeVideoUrl(mediaPath):await videoBlob(mediaPath);
     if(renderEpoch!==videoWork.epoch){URL.revokeObjectURL(src);return}
     const player=document.createElement('video');player.controls=true;player.preload='metadata';player.style.maxWidth='100%';player.src=src;card.append(player);button.remove();
   }catch(error){reason.textContent=error.message;button.disabled=false}};card.append(button);
   if(view==='review'){const input=document.createElement('input');input.placeholder='人工复核理由';input.maxLength=500;card.append(input);
     for(const [action,text] of [['accept','人工接受'],['reject','人工拒绝']]){const b=document.createElement('button');b.textContent=text;b.type='button';b.onclick=()=>videoAction(async()=>{
       if(!input.value.trim())throw new Error('请填写复核理由。');
       await videoWrite(path+'/manual-reviews',{expected_task_version:videoWork.task.task_version,result_set_digest:page.result_set_digest,
          candidate_id:item.candidate_id,asset_version:item.asset_version,action,reason:input.value.trim()});await refreshVideoTask();});card.append(b)}
   }
   if(view==='accepted'&&videoWork.delivery?.status==='published'&&videoWork.delivery.manifest.schema==='whole-video-delivery@2'){
     const original=document.createElement('a');original.textContent='下载原视频';
     original.href=nativeVideoUrl(path+'/delivery/original/'+item.candidate_id+'?source_digest='+encodeURIComponent(videoWork.delivery.source_digest));
     const ext=videoWork.delivery.manifest.members.find(m=>m.candidate_id===item.candidate_id)?.source.container||'mp4';original.setAttribute('download','original.'+ext);
     if(!window.dshPlatformSession)original.onclick=async event=>{event.preventDefault();try{const url=await videoBlob(path+'/delivery/original/'+item.candidate_id+'?source_digest='+encodeURIComponent(videoWork.delivery.source_digest));const a=document.createElement('a');a.href=url;a.download='original.'+ext;a.click()}catch(error){reason.textContent=error.message}};
     card.append(original);
   }
   $('video-work-items').append(card);
 }
 $('video-work-more').disabled=!videoWork.cursor;
}
async function openVideoWorkspace(taskId){
 stopTaskEvents();state.task=null;localStorage.setItem('dsh-task-id',taskId);
 $('feedback-box').classList.add('hidden');$('new-request').classList.add('hidden');
 $('video-task-id').value=taskId;if(!$('video-draft-dialog').open)$('open-video-draft').click();
 await videoAction(refreshVideoTask);$('status').textContent='视频任务已在整段视频工作区打开。';
}
async function refreshVideoTask(){
 const id=$('video-task-id').value.trim();if(!/^rt_[a-f0-9]{32}$/.test(id))throw new Error('请输入有效的视频任务 ID。');
 if(videoWork.id!==id){videoWork.epoch++;clearVideoPlayers();videoWork.pageSignature=null;videoWork.currentCursor=null;videoWork.cleaning=null;videoWork.refill=null;videoWork.delivery=null;videoWork.search=null;videoWork.routes=null;videoWork.routeChoices=[];$('video-work-sources').replaceChildren()}
 videoWork.id=id;const path=videoTaskPath(),binding=await api(path+'/read-binding');
 if(binding.proposal.query_spec.media_type!=='video')throw new Error('此任务不是整段视频需求。');
 videoWork.binding=binding;videoWork.searchProgress=await api(path+'/first-search/progress');
 $('video-search-status').textContent=videoWork.searchProgress.status==='unapproved'?'尚未确认检索计划；请查看来源并确认。':videoWork.searchProgress.initial_window_complete?'首批候选检索已完成，可准备验证。':'检索计划已确认，正在读取首批候选。';
 videoWork.task=await api(path);sessionStorage.setItem('dsh-video-task',id);
 videoWork.result=await api(path+'/results?limit=1');videoWork.delivery=await api(path+'/delivery');
 const counts=videoWork.result.counts||{};
 $('video-work-status').textContent=`${videoWork.task.stage} · ${videoWork.task.run_status}`+(videoWork.task.video_intake?.last_error?' · '+videoWork.task.video_intake.last_error:'')+(videoWork.task.video_intake?.reason_code?' · '+videoWork.task.video_intake.reason_code:'');
 $('video-work-counts').textContent=`目标 ${binding.proposal.query_spec.target_count} 个原视频；有效 ${counts.accepted??'待计算'}；待复核 ${counts.pending_review??'待计算'}；缺口 ${counts.gap??'待计算'}`;
 if(videoWork.searchProgress.status==='unapproved'&&$('video-work-view').value==='candidates'){
   clearVideoPlayers();videoWork.cursor=null;videoWork.currentCursor=null;videoWork.pageSignature=null;
 }else await showVideoPage(videoWork.currentCursor);
}
async function confirmVideoDraft(){
 const draft=videoDraftState.draft;if(!draft)throw new Error('请先保存视频草稿。');
 const fresh=await api('/v1/retrieval/projects/demo/drafts/'+draft.draft_id);
 if(!fresh.video_summary?.execution?.available)throw new Error('视频能力未就绪，不能执行。');
 const lines=fresh.proposal.query_spec.video_conditions.clauses.map(c=>c.predicate).join('\n');
 if(lines!==$('video-draft-query').value.trim()||fresh.proposal.query_spec.target_count!==Number($('video-draft-count').value)
    ||fresh.proposal.query_spec.video_conditions.relation.operator!==$('video-condition-relation').value
    ||(fresh.proposal.query_spec.refill_policy.mode==='maintain_target')!==$('video-auto-refill').checked
    ||(fresh.proposal.query_spec.refill_limits?.max_rounds??'')!==($('video-max-rounds').value?Number($('video-max-rounds').value):'')
    ||(fresh.proposal.query_spec.refill_limits?.max_vlm_calls??'')!==($('video-max-calls').value?Number($('video-max-calls').value):''))throw new Error('请先保存当前修改，再确认执行。');
 const task=await videoWrite('/v1/retrieval/tasks',{draft_id:fresh.draft_id,expected_draft_version:fresh.draft_version,proposal_digest:fresh.proposal_digest,action:'confirm'});
 $('video-task-id').value=task.task_id;videoDraftState.draft={...fresh,confirmed_task_id:task.task_id};await refreshVideoTask();
}
async function loadVideoRoutes(){
 await refreshVideoTask();
 if(videoWork.searchProgress.status!=='unapproved')throw new Error('已有已确认的检索计划，请继续查看候选或准备验证。');
 const preview=await api(videoTaskPath()+'/first-search');
 videoWork.routes=preview;videoWork.search=null;videoWork.routeChoices=[];$('video-work-sources').replaceChildren();
 for(const route of preview.routes){
   const section=document.createElement('section'),label=document.createElement('p');label.textContent=route.query_text;section.append(label);
   if(!route.options.length){const note=document.createElement('p');note.textContent='此条件暂无可用的整段视频索引。';section.append(note)}
   for(const option of route.options){
     const row=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=true;
     const choice={branch_id:route.branch_id,binding_id:option.binding_id,max_batch_size:option.max_batch_size,selected:true};videoWork.routeChoices.push(choice);
     input.onchange=()=>{choice.selected=input.checked;videoWork.search=null;$('video-work-search-confirm').disabled=true;$('video-work-review').textContent='检索来源已修改，请重新准备计划后确认。'};
     row.append(input,document.createTextNode(`${option.library_id||option.binding_id} · ${option.binding_id}`));section.append(row);
   }
   $('video-work-sources').append(section);
 }
 $('video-work-review').textContent='请核对检索来源；每条查询至少选择一个索引。准备计划后还需确认，才会开始检索。';
}
async function prepareVideoSearch(){
 const preview=videoWork.routes;if(!preview)throw new Error('请先查看检索来源。');
 const selected=videoWork.routeChoices.filter(c=>c.selected);
 if(!preview.routes.length||preview.routes.some(r=>!selected.some(c=>c.branch_id===r.branch_id)))throw new Error('每条查询至少选择一个可用索引。');
 if(selected.length>64)throw new Error('所选来源过多，请减少索引。');
 const spec=videoWork.binding.proposal.query_spec,limit=spec.refill_limits?.max_vlm_calls,unit=spec.video_conditions.clauses.length;
 let cap=Math.min(8,Math.max(1,spec.target_count*2),Math.floor(100/selected.length));
 if(limit!=null)cap=Math.min(cap,Math.floor(limit/(selected.length*unit)));
 if(cap<1)throw new Error('当前预算无法覆盖所选索引的首批验证，请减少来源或调整需求预算。');
 const choices=selected.map(c=>({branch_id:c.branch_id,binding_id:c.binding_id,batch_size:Math.min(cap,c.max_batch_size)}));
 videoWork.search=await videoWrite(videoTaskPath()+'/first-search/prepare',{routing_digest:preview.routing_digest,choices});
 $('video-work-review').textContent='待确认的检索计划：\n'+JSON.stringify(videoWork.search.members,null,2)+'\n确认后仅检索候选，不代表合格数量。';
}
async function confirmVideoSearch(){
 const p=videoWork.search;if(!p)throw new Error('请先准备并审视检索计划。');
 await videoWrite(videoTaskPath()+'/first-search/confirm',{proposal_digest:p.proposal_digest,expected_task_version:p.expected_task_version,action:'confirm'});
 videoWork.search=null;videoWork.routes=null;videoWork.routeChoices=[];$('video-work-sources').replaceChildren();await refreshVideoTask();
}
async function prepareVideoCleaning(){
 const path=videoTaskPath();let manifest=await api(path+'/first-search/manifest');
 if(!manifest.candidate_count)throw new Error('当前没有可验证候选。');
 if(!manifest.confirmed)manifest=await videoWrite(path+'/first-search/manifest',{proposal_digest:manifest.proposal_digest,expected_task_version:manifest.expected_task_version,action:'confirm'});
 videoWork.cleaning=await videoWrite(path+'/cleaning/prepare',{manifest_ref:manifest.manifest_ref});
 const binding=await api(path+'/read-binding');$('video-work-review').textContent=`待验证 ${videoWork.cleaning.candidate_count} 个候选\n`+JSON.stringify(binding.proposal.query_spec.video_conditions,null,2);
}
async function confirmVideoCleaning(){const p=videoWork.cleaning;if(!p)throw new Error('请先准备验证。');
 await videoWrite(videoTaskPath()+'/cleaning/confirm',{proposal_digest:p.proposal_digest,expected_task_version:p.expected_task_version,action:'confirm'});videoWork.cleaning=null;await refreshVideoTask()}
async function prepareVideoRefill(){videoWork.refill=await api(videoTaskPath()+'/refill');$('video-work-review').textContent='补数计划：\n'+JSON.stringify(videoWork.refill,null,2)}
async function confirmVideoRefill(){const p=videoWork.refill;if(!p?.next_position)throw new Error('没有可执行的补数位置。');
 await videoWrite(videoTaskPath()+'/refill',{expected_revision:p.revision,expected_generation:p.generation,expected_task_version:p.task_version,position_fingerprint:p.next_position.position_fingerprint,action:'confirm'});videoWork.refill=null;await refreshVideoTask()}
async function prepareVideoPackage(){await refreshVideoTask();const p=videoWork.result;
 videoWork.delivery=await videoWrite(videoTaskPath()+'/delivery/prepare',{expected_task_version:videoWork.task.task_version,result_set_digest:p.result_set_digest});
 $('video-work-review').textContent='待发布交付：\n'+JSON.stringify(videoWork.delivery.manifest,null,2)}
async function publishVideoPackage(){const p=videoWork.delivery;if(p?.status!=='staged')throw new Error('请先准备交付。');
 videoWork.delivery=await videoWrite(videoTaskPath()+'/delivery/publish',{package_id:p.package_id,manifest_digest:p.manifest_digest,expected_task_version:p.current_version,action:'confirm'});await refreshVideoTask()}
async function downloadVideoPackage(){const p=videoWork.delivery;if(p?.status!=='published')throw new Error('交付尚未发布。');
 const url=await videoBlob(videoTaskPath()+'/delivery/'+p.package_id+'/download'),a=document.createElement('a');a.href=url;a.download=p.package_id+'.zip';a.click()}
for(const [id,action] of [['video-work-routes',loadVideoRoutes],['video-work-search-prepare',prepareVideoSearch],['video-work-search-confirm',confirmVideoSearch],['video-work-confirm',confirmVideoDraft],['video-work-refresh',refreshVideoTask],['video-work-prepare',prepareVideoCleaning],['video-work-clean',confirmVideoCleaning],['video-work-refill',prepareVideoRefill],['video-work-refill-confirm',confirmVideoRefill],['video-work-package',prepareVideoPackage],['video-work-publish',publishVideoPackage],['video-work-download',downloadVideoPackage]])$(id).addEventListener('click',()=>void videoAction(action));
$('video-work-view').addEventListener('change',()=>void videoAction(()=>showVideoPage()));$('video-work-more').addEventListener('click',()=>void videoAction(()=>showVideoPage(videoWork.cursor)));
$('open-video-draft').addEventListener('click',async()=>{try{videoWork.caps=await api('/v1/retrieval/projects/demo/video-capabilities');$('video-capabilities').textContent=videoWork.caps.available?'视频能力已配置。':'视频能力尚未就绪，可先保存草稿。'}catch(error){$('video-capabilities').textContent=error.message}
 const saved=sessionStorage.getItem('dsh-video-task');if(saved&&!$('video-task-id').value)$('video-task-id').value=saved;
 clearTimeout(videoWork.timer);const poll=async()=>{if(!$('video-draft-dialog').open)return;if(videoWork.id&&!videoWork.busy)await videoAction(refreshVideoTask);videoWork.timer=setTimeout(poll,3000)};videoWork.timer=setTimeout(poll,3000);
});
$('video-draft-dialog').addEventListener('close',()=>{clearTimeout(videoWork.timer);videoWork.epoch++;clearVideoPlayers()});
'''
