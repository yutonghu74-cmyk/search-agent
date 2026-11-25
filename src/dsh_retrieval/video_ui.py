"""Whole-video draft editor; uses existing draft APIs and never dispatches work."""
VIDEO_DRAFT_HTML = '''
<dialog id="video-draft-dialog" class="sidebar-dialog" aria-labelledby="video-draft-heading">
  <section class="rail-card"><div class="rail-heading"><h2 id="video-draft-heading">文字找整段视频</h2><button id="video-draft-close" type="button" class="secondary">关闭</button></div>
  <p class="helper">保存需求草稿，按不同原视频计数。真实检索与交付接入后才能执行。</p>
  <form id="video-draft-form">
    <div class="field"><label for="video-draft-query">需要的画面和要求（每行一项条件）</label><textarea id="video-draft-query" rows="4" maxlength="5120" required placeholder="例如：有人在室外喝水的视频，不要水印"></textarea></div>
    <div class="field"><label for="video-draft-count">原视频数量（个）</label><input id="video-draft-count" type="number" min="1" step="1" required></div>
    <p class="helper">当前不提供片段定位、字幕检索或裁剪；相似命中不代表动作先后顺序或全程条件验证通过。</p>
    <div class="field"><label for="video-condition-relation">条件关系</label><select id="video-condition-relation"><option value="all">全部满足</option><option value="any">满足任意一项</option></select></div>
    <label><input id="video-auto-refill" type="checkbox">自动检索、验证并补足目标数量</label>
    <div class="field"><label for="video-max-rounds">最多补数轮次（留空不限）</label><input id="video-max-rounds" type="number" min="1" step="1"></div>
    <div class="field"><label for="video-max-calls">最多验证调用（含首批，留空不限）</label><input id="video-max-calls" type="number" min="1" step="1"></div>
    <button id="video-draft-save" type="submit">保存视频草稿</button>
  </form>
  <p id="video-draft-status" role="status" aria-live="polite"></p>
  <div class="field"><label for="video-draft-id">草稿 ID</label><input id="video-draft-id" placeholder="保存后显示；也可输入已有草稿 ID"></div>
  <div class="preview-actions"><button id="video-draft-load" type="button" class="secondary">读取草稿</button><button id="video-draft-new" type="button" class="secondary">新建草稿</button></div>
  <p id="video-draft-summary" class="helper"></p>
  </section>
</dialog>
'''

VIDEO_DRAFT_JS = r'''
function videoDraftBody(text,count,relation='all'){
  if(typeof text!=='string'||!text.trim()||text.length>5120||!Number.isSafeInteger(count)||count<1)throw new Error('请填写画面要求和正整数原视频数量。');
  const clauses=text.split(/\n+/).map(s=>s.trim()).filter(Boolean);
  if(!clauses.length||clauses.length>8||clauses.some(s=>s.length>640)||!['all','any'].includes(relation))throw new Error('请填写 1–8 项条件，每项最多 640 字。');
  return {dimension_id:'default',query_spec:{schema_version:'query-spec@3',raw_request:text,
    media_type:'video',count_unit:'distinct_video',target_count:count,business_domain:'video',
    query_input:{mode:'text',text},video_conditions:{clauses:clauses.map((predicate,i)=>({id:'visual_'+(i+1),channel:'visual',predicate,verification_scope:'video_content'})),relation:{operator:relation,scope:'same_video'}},
    intra_batch_dedup:{policy_ref:'exact-content',config_version:'1'},
    cross_batch_dedup:{scope:'none',references:[],comparison_methods:[],reference_digest:null},
    refill_policy:{mode:'input_only',target_count:count,approved_library_ids:[],progression:'manual'}},
    node_modes:{expansion:'manual',retrieval:'manual',cleaning:'manual',refill:'manual',packaging:'manual'}};
}
const videoDraftState={draft:null,busy:false,pending:null};
function showVideoDraft(draft){
  const spec=draft.proposal?.query_spec,summary=draft.video_summary;
  if(spec?.media_type!=='video'||!summary)throw new Error('该草稿不是视频需求。');
  videoDraftState.draft=draft;
  $('video-draft-id').value=draft.draft_id;
  $('video-draft-query').value=spec.video_conditions?.clauses?.map(c=>c.predicate).join('\n')||spec.query_input?.text||spec.raw_request;
  if($('video-condition-relation'))$('video-condition-relation').value=spec.video_conditions.relation.operator;
  if($('video-auto-refill'))$('video-auto-refill').checked=spec.refill_policy.mode==='maintain_target';
  if($('video-max-rounds'))$('video-max-rounds').value=spec.refill_limits?.max_rounds??'';
  if($('video-max-calls'))$('video-max-calls').value=spec.refill_limits?.max_vlm_calls??'';
  $('video-draft-count').value=spec.target_count;
  const outdated=summary.requires_scope_update;
  $('video-draft-summary').textContent=outdated?'这是旧范围草稿，原内容保留。请新建整段视频草稿；旧的片段或字幕条件不会被自动删除。':`目标：${spec.target_count} 个不同原视频；条件：${spec.video_conditions.clauses.map(c=>c.predicate).join('；')}；关系：${spec.video_conditions.relation.operator}；补数来源：${spec.refill_policy.approved_library_ids.join('、')||'固定输入'}。`;
  $('video-draft-save').disabled=!!outdated||videoDraftState.busy||!!draft.confirmed_task_id;
  if(typeof updateVideoExecution==='function')updateVideoExecution(draft);
}
function setVideoDraftBusy(busy){
  videoDraftState.busy=busy;
  for(const id of ['video-draft-query','video-draft-count','video-draft-id','video-draft-load','video-draft-new','video-condition-relation','video-auto-refill','video-max-rounds','video-max-calls'])$(id).disabled=busy;
  $('video-draft-save').disabled=busy||!!videoDraftState.draft?.video_summary?.requires_scope_update||!!videoDraftState.draft?.confirmed_task_id;
}
async function saveVideoDraft(){
  if(videoDraftState.busy)return;
  const draft=videoDraftState.draft;
  // Loaded plans with multiple predicates must be edited explicitly, not flattened.
  if(draft&&(draft.video_summary?.requires_scope_update||draft.confirmed_task_id)){
    $('video-draft-status').textContent='此草稿不支持在简版编辑器中修改；请新建草稿，原条件保留。';return;
  }
  let body;
  try{body=videoDraftBody($('video-draft-query').value,Number($('video-draft-count').value),$('video-condition-relation')?.value||'all')}
  catch(error){$('video-draft-status').textContent=error.message;return}
  if(draft){
    // Preserve authorized source/dedup/refill policy and all other stored fields.
    const spec=JSON.parse(JSON.stringify(draft.proposal.query_spec));
    spec.raw_request=body.query_spec.raw_request;spec.query_input=body.query_spec.query_input;
    spec.target_count=body.query_spec.target_count;spec.refill_policy.target_count=spec.target_count;
    spec.video_conditions=body.query_spec.video_conditions;
    body={...draft.proposal,query_spec:spec,expected_draft_version:draft.draft_version};
  }
  if(typeof configureVideoRefill==='function'){try{body=configureVideoRefill(body)}catch(error){$('video-draft-status').textContent=error.message;return}}
  const path='/v1/retrieval/projects/demo/drafts'+(draft?'/'+encodeURIComponent(draft.draft_id):'');
  const signature=JSON.stringify({path,body});
  if(videoDraftState.pending?.signature!==signature)videoDraftState.pending={signature,key:key('video-draft')};
  setVideoDraftBusy(true);$('video-draft-status').textContent='正在保存…';
  try{
    const saved=await api(path,{method:'POST',body,keyValue:videoDraftState.pending.key});
    showVideoDraft(saved);videoDraftState.pending=null;
    $('video-draft-status').textContent='视频草稿已保存，未创建正式任务或启动检索。';
  }catch(error){$('video-draft-status').textContent=error.message}
  finally{setVideoDraftBusy(false)}
}
async function loadVideoDraft(){
  if(videoDraftState.busy)return;
  const id=$('video-draft-id').value.trim();
  if(!/^draft_[a-f0-9]{32}$/.test(id)){$('video-draft-status').textContent='请输入有效的视频草稿 ID。';return}
  setVideoDraftBusy(true);
  try{showVideoDraft(await api('/v1/retrieval/projects/demo/drafts/'+encodeURIComponent(id)));videoDraftState.pending=null;$('video-draft-status').textContent='已读取草稿，尚未执行。'}
  catch(error){$('video-draft-status').textContent=error.message}
  finally{setVideoDraftBusy(false)}
}
function newVideoDraft(){
  if(videoDraftState.busy)return;
  videoDraftState.draft=null;videoDraftState.pending=null;
  for(const id of ['video-draft-query','video-draft-count','video-draft-id'])$(id).value='';
  if($('video-auto-refill'))$('video-auto-refill').checked=false;
  $('video-draft-summary').textContent='';$('video-draft-status').textContent='';$('video-draft-save').disabled=false;
}
$('open-video-draft').addEventListener('click',()=>$('video-draft-dialog').showModal());
$('video-draft-close').addEventListener('click',()=>$('video-draft-dialog').close());
$('video-draft-form').addEventListener('submit',event=>{event.preventDefault();void saveVideoDraft()});
$('video-draft-load').addEventListener('click',()=>void loadVideoDraft());
$('video-draft-new').addEventListener('click',newVideoDraft);
'''
