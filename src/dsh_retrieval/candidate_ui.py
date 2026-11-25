"""Independent candidate browser, loaded after the chat UI."""

CANDIDATE_CSS = """
#candidate-dialog{width:min(1280px,96vw);height:90dvh;max-width:none;border:1px solid var(--line);border-radius:16px;padding:20px;color:var(--ink)}
#candidate-dialog[open]{display:flex;flex-direction:column;gap:14px}#candidate-dialog header{display:flex;align-items:center;justify-content:space-between;gap:12px}#candidate-dialog h2{margin:0;font-size:18px}.candidate-tools{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.candidate-tools select{width:min(340px,100%)}#candidate-grid{overflow:auto;flex:1;min-height:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px;align-content:start}.candidate-tile{border:1px solid var(--line);border-radius:12px;padding:10px;min-width:0;background:white}.candidate-tile button{display:block;width:100%;padding:0;background:#eef1ed;color:var(--muted);height:160px;overflow:hidden;border-radius:8px}.candidate-tile img{width:100%;height:160px;object-fit:contain}.candidate-tile p{font-size:13px;overflow-wrap:anywhere;margin:8px 0}.candidate-tile small{display:block;color:var(--muted);overflow-wrap:anywhere}#candidate-image-view{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:20px}#candidate-image-stage{min-height:0;display:flex;align-items:center;justify-content:center;background:#eef1ed;border-radius:12px;overflow:hidden}#candidate-image-stage img{max-width:100%;max-height:100%;object-fit:contain}#candidate-image-info{overflow:auto;overflow-wrap:anywhere}#candidate-image-info h3{margin:0 0 10px}#candidate-image-info p{font-size:13px;margin:6px 0;line-height:1.5}#candidate-image-info .detail-label{color:var(--muted);font-weight:600}#candidate-image-info .detail-sub{color:var(--muted);font-weight:400;font-size:12px}.candidate-reasons{padding-left:20px;font-size:13px;line-height:1.5}.candidate-detail-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.candidate-detail-actions button{font-size:12px;padding:7px 9px}#candidate-read-status{font-size:13px;margin:0;white-space:pre-wrap}.candidate-empty{grid-column:1/-1;padding:30px;color:var(--muted)}@media(max-width:680px){#candidate-image-view{grid-template-columns:1fr;grid-template-rows:minmax(220px,45dvh) max-content;overflow:auto}#candidate-image-info{overflow:visible}#candidate-image-stage img{max-height:45dvh}#candidate-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
"""

CANDIDATE_HTML = """<dialog id="candidate-dialog" aria-labelledby="candidate-title"><header><h2 id="candidate-title">素材与结果</h2><button id="candidate-close" class="secondary">关闭</button></header><div class="candidate-tools"><button id="candidate-tab" aria-pressed="true">检索候选</button><button id="accepted-tab" class="secondary" aria-pressed="false">合格结果</button><button id="failed-tab" class="secondary" aria-pressed="false">规则淘汰</button><button id="review-tab" class="secondary" aria-pressed="false">待复核</button><button id="excluded-tab" class="secondary" aria-pressed="false">用户排除</button><label for="candidate-query">检索词</label><select id="candidate-query"><option value="">全部检索词</option></select><button id="candidate-preview-filter" class="secondary" aria-pressed="true">只看可预览图片</button><button id="candidate-retry" class="secondary hidden">重试读取</button><button id="candidate-reset" class="secondary hidden">重新读取结果</button></div><p id="candidate-read-status" role="status"></p><div id="candidate-grid"></div><div id="candidate-image-view" class="hidden"><div id="candidate-image-stage"></div><aside id="candidate-image-info"></aside></div><footer class="candidate-tools"><button id="candidate-page-prev" class="secondary">上一页</button><span id="candidate-page-position" role="status"></span><button id="candidate-page-next" class="secondary">下一页</button><button id="candidate-more" class="secondary">加载更多</button><button id="candidate-back" class="secondary hidden">返回缩略图</button><button id="candidate-prev" class="secondary hidden">上一张</button><button id="candidate-next" class="secondary hidden">下一张</button><span id="candidate-position"></span></footer></dialog>"""

CANDIDATE_JS = r"""
const candidateViews=new Map();
const candidatePageSize=60;
const candidateCacheLimit=180;
let candidateActive=null,candidateTimer=null,searchPollTimer=null,searchPollBusy=false;
let candidateImageActive=0;const candidateImageQueue=[];
function candidateImagePermit(signal){return new Promise((resolve,reject)=>{const start=()=>{if(signal.aborted){reject(new DOMException('cancelled','AbortError'));return}candidateImageActive++;resolve()};if(candidateImageActive<4){start();return}const queued={signal,start,reject};candidateImageQueue.push(queued);signal.addEventListener('abort',()=>{const index=candidateImageQueue.indexOf(queued);if(index>=0){candidateImageQueue.splice(index,1);reject(new DOMException('cancelled','AbortError'))}},{once:true})})}
function candidateImageRelease(){candidateImageActive--;while(candidateImageQueue.length){const next=candidateImageQueue.shift();if(next.signal.aborted){next.reject(new DOMException('cancelled','AbortError'));continue}next.start();break}}
const candidateDialog=$('candidate-dialog');
$('open-candidate-window').onclick=openCandidateBrowser;

function candidateScope(){return state.task?`${state.task.project_id||'demo'}:${state.conversationId||''}:${state.task.task_id}:${state.task.confirmed_revision}:${state.task.run_generation}`:null}
function candidateCurrent(view){return candidateActive===view&&candidateDialog.open&&view.scope===candidateScope()}
function candidateResultTab(view){return view.tab!=='candidates'}
function candidateTabLabel(tab){return({accepted:'合格结果',failed:'规则淘汰',review:'待复核',user_excluded:'用户排除'})[tab]||'检索候选'}
function candidateSave(view){if(!$('candidate-grid').classList.contains('hidden'))view.scroll=$('candidate-grid').scrollTop;try{sessionStorage.setItem('dsh-candidate-view:'+view.scope,JSON.stringify({query:view.query,tab:view.tab,previewOnly:view.previewOnly,scroll:view.scroll,selected:view.selected,resumeCursor:candidateResumeCursor(view),version:view.version}))}catch{}}
function candidateAllGroups(view){return candidateResultTab(view)?view.items.map(item=>({item,sources:item.sources||[],indexes:[candidateTabLabel(view.tab)],previewItem:item.preview_status==='available'?item:null})):aggregateLocalCandidates(view.items)}
function candidateGroups(view){const groups=candidateAllGroups(view);return view.tab==='candidates'&&view.previewOnly!==false?groups.filter(group=>group.previewItem):groups}
function candidateCanRead(view){return Boolean(view.next||(!candidateResultTab(view)&&view.changes&&!(state.searchSnapshot?.taskId===view.taskId&&state.searchSnapshot.search?.pagination_stopped)))}
function candidateWindow(view,groups){
  const pages=Math.max(1,Math.ceil(groups.length/candidatePageSize));
  view.page=Math.max(0,Math.min(Number.isInteger(view.page)?view.page:0,pages-1));
  return groups.slice(view.page*candidatePageSize,(view.page+1)*candidatePageSize);
}
function candidateResumeCursor(view){
  const groups=candidateGroups(view),group=groups.find(g=>candidateGroupKey(g)===view.selected)||groups[(view.page||0)*candidatePageSize];
  const key=group&&(candidateResultTab(view)?group.item.candidate_id:group.item.hit_sequence);
  return view.itemCursors?.has(key)?view.itemCursors.get(key):(view.lastPageCursor||null);
}
function candidateTrimCache(view){
  if(view.items.length<=candidateCacheLimit)return;
  const remove=view.items.length-candidateCacheLimit,first=view.items[0];
  const key=candidateResultTab(view)?first.candidate_id:first.hit_sequence;
  (view.previousCursors??=[]).push(view.itemCursors?.get(key)||null);
  view.items.splice(0,remove);view.page=Math.max(0,(view.page||0)-Math.ceil(remove/candidatePageSize));
  const retained=new Set(view.items.map(item=>candidateResultTab(view)?item.candidate_id:item.hit_sequence));
  for(const key of view.itemCursors?.keys()||[])if(!retained.has(key))view.itemCursors.delete(key);
}
function candidateRememberPage(view,items,cursor){
  if(!view.itemCursors)view.itemCursors=new Map();
  for(const item of items){const key=candidateResultTab(view)?item.candidate_id:item.hit_sequence;if(!view.itemCursors.has(key))view.itemCursors.set(key,cursor||null)}
}
async function candidatePageMove(delta){
  const view=candidateActive;if(!view||view.busy||view.invalid)return;
  const page=view.page||0;
  if(delta<0&&page===0&&view.previousCursors?.length){
    const cursor=view.previousCursors[view.previousCursors.length-1];
    await candidateLoad(view,false,{cursor});if(!view.readError&&!view.invalid)view.previousCursors.pop();candidateRender(view);return;
  }
  if(delta>0&&(page+1)*candidatePageSize>=candidateGroups(view).length&&candidateCanRead(view))await candidateLoad(view);
  if(!candidateCurrent(view))return;
  view.page=page+delta;candidateRender(view);$('candidate-grid').scrollTop=0;candidateSave(view);
}
function candidateGroupKey(group){const i=group.item;return i.candidate_id||JSON.stringify([i.library_id,i.binding_id,i.vector_id])}
function candidateHint(text){$('candidate-read-status').textContent=text}
function candidateRecovery(view){$('candidate-reset').classList.toggle('hidden',!view.invalid);$('candidate-retry').classList.toggle('hidden',!view.readError||Boolean(view.invalid))}
function candidateFilterLabel(view){const button=$('candidate-preview-filter');button.classList.toggle('hidden',candidateResultTab(view));button.setAttribute('aria-pressed',String(view.previewOnly!==false));button.textContent='只看可预览图片'}
function candidateCleanupImages(){for(const img of candidateDialog.querySelectorAll('img')){img._observer?.disconnect();img._abort?.abort();if(img._url)URL.revokeObjectURL(img._url)}}
async function candidateImage(img,item,view){
  const controller=new AbortController();img._abort=controller;let permitted=false;
  try{
    await candidateImagePermit(controller.signal);permitted=true;
    const path=candidateResultTab(view)?`/v1/retrieval/tasks/${encodeURIComponent(view.taskId)}/cleaning/candidates/${encodeURIComponent(item.candidate_id)}/preview`:`/v1/retrieval/tasks/${encodeURIComponent(view.taskId)}/candidates/${encodeURIComponent(item.candidate_id)}/preview?ref=${encodeURIComponent(item.preview_ref||'')}`;
    const response=await fetch(path,{headers,signal:controller.signal});if(!response.ok)throw new Error('图片不可读取，请重试');
    const blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('预览格式不可用');
    if(!candidateCurrent(view)||!img.isConnected)return;
    img._url=URL.createObjectURL(blob);img.src=img._url;
  }catch(error){if(error.name==='AbortError'||!candidateCurrent(view)||!img.isConnected)return;img.alt='预览失败：'+error.message;img.title='点击图片重试';img.onclick=event=>{event.stopPropagation();candidateImage(img,item,view)}}
  finally{if(permitted)candidateImageRelease()}
}
function candidateRender(view){
  if(!candidateCurrent(view))return;
  return typeof measureChatRender==='function'?measureChatRender('candidate_gallery_render',()=>candidateRenderContent(view),view):candidateRenderContent(view);
}
function candidateRenderContent(view){
  const grid=$('candidate-grid'),allGroups=candidateGroups(view),groups=candidateWindow(view,allGroups),existing=new Map([...grid.children].map(el=>[el.dataset.key,el]));
  grid.querySelector('.candidate-empty')?.remove();
  const visible=new Set(groups.map(candidateGroupKey));for(const [id,tile] of existing)if(!visible.has(id)){for(const img of tile.querySelectorAll('img')){img._observer?.disconnect();img._abort?.abort();if(img._url)URL.revokeObjectURL(img._url)}tile.remove();existing.delete(id)}
  for(const group of groups){const id=candidateGroupKey(group);let tile=existing.get(id);
    if(!tile){tile=document.createElement('article');tile.className='candidate-tile';tile.dataset.key=id;const button=document.createElement('button');button.setAttribute('aria-label',group.previewItem?'查看大图':'查看候选详情');button.textContent='无授权图片 · 查看详情';
      if(group.previewItem){const img=document.createElement('img');img.alt='正在加载候选图片';img.loading='lazy';button.replaceChildren(img);tile._image=img}
      button.onclick=()=>{view.selected=id;candidateShowImage(view)};tile.append(button,document.createElement('p'),document.createElement('small'));grid.append(tile);if(tile._image){const img=tile._image;const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){observer.disconnect();if(candidateCurrent(view))candidateImage(img,group.previewItem,view)}},{root:grid,rootMargin:'200px'});img._observer=observer;observer.observe(img)}
    }
    if(group.previewItem&&!tile._image){const img=document.createElement('img');img.alt='正在加载候选图片';img.loading='lazy';tile.querySelector('button').replaceChildren(img);tile.querySelector('button').setAttribute('aria-label','查看大图');tile._image=img;candidateImage(img,group.previewItem,view)}
    tile.querySelector('p').textContent=candidateResultTab(view)?group.item.candidate_id:group.sources.map(s=>s.query_text).join(' · ');
    tile.querySelector('small').textContent=candidateResultTab(view)?`${candidateTabLabel(view.tab)} · ${group.previewItem?'可授权预览':'预览不可用'}${view.tab==='accepted'?` · 融合来源 ${group.item.source_count??0} · 近重复待复核 ${(group.item.near_duplicate_matches||[]).length}`:''}`:group.indexes.join('、')+' · '+(group.previewItem?'未清洗':'无授权图片 · 未清洗');
  }
  if(!groups.length){const empty=document.createElement('p');empty.className='candidate-empty';empty.textContent=candidateResultTab(view)?`当前结果版本没有${candidateTabLabel(view.tab)}素材。`:view.items.length&&view.previewOnly!==false?'当前已读取的候选没有可预览图片；可加载更多，或显示全部候选查看原因。':'暂无候选，检索过程中会自动追加。';grid.append(empty)}
  $('candidate-page-prev').disabled=view.busy||view.invalid||(view.page===0&&!view.previousCursors?.length);
  $('candidate-page-next').disabled=view.busy||view.invalid||((view.page+1)*candidatePageSize>=allGroups.length&&!candidateCanRead(view));
  $('candidate-page-position').textContent=`第 ${view.page+1} / ${Math.max(1,Math.ceil(allGroups.length/candidatePageSize))} 页（已读取）`;
  $('candidate-more').disabled=!candidateCanRead(view);$('candidate-more').textContent=view.next?'加载更多':candidateCanRead(view)?'检查新增候选':'已加载当前水位';candidateRecovery(view);
  if(view.selected)candidateShowImage(view);
}
function candidateShowImage(view){
  const groups=candidateGroups(view),index=groups.findIndex(g=>candidateGroupKey(g)===view.selected);if(index<0)return;
  view.page=Math.floor(index/candidatePageSize);
  const group=groups[index];$('candidate-grid').classList.add('hidden');$('candidate-image-view').classList.remove('hidden');
  for(const id of ['candidate-page-prev','candidate-page-next','candidate-page-position'])$(id).classList.add('hidden');
  for(const id of ['candidate-back','candidate-prev','candidate-next'])$(id).classList.remove('hidden');$('candidate-more').classList.add('hidden');
  $('candidate-position').textContent=`${index+1} / ${groups.length} 张已加载`+(!candidateResultTab(view)&&view.rawHitCount!=null?`（原始命中 ${view.rawHitCount} 条）`:'');
  const stage=$('candidate-image-stage');if(stage.dataset.key!==view.selected){for(const img of stage.querySelectorAll('img')){img._abort?.abort();if(img._url)URL.revokeObjectURL(img._url)}stage.replaceChildren();stage.dataset.key=view.selected;if(group.previewItem){const img=document.createElement('img');img.alt='正在加载大图';stage.append(img);candidateImage(img,group.previewItem,view)}else stage.textContent=candidateResultTab(view)?'当前结果素材没有可授权读取的图片。':'此候选来源没有可授权读取的图片；可返回并只看可预览图片。'}
  const info=$('candidate-image-info');info.replaceChildren();const heading=document.createElement('h3');heading.textContent=candidateResultTab(view)?candidateTabLabel(view.tab)+'详情':'命中检索词';info.append(heading);
  if(candidateResultTab(view)){const libraries=[...new Set((group.item.sources||[]).map(source=>source.library_id).filter(Boolean))],near=(group.item.near_duplicate_matches||[]).map(match=>`${match.candidate_id}（pHash 距离 ${match.hamming_distance}/${match.review_threshold}）`).join('、'),status=({pass:'全部已确认硬规则通过',fail:'至少一条硬规则未通过',unknown:'证据不足，未计入有效数',user_excluded:'用户从当前结果中排除'})[group.item.cleaning_status]||candidateTabLabel(view.tab);for(const text of ['素材标识：'+group.item.candidate_id,'素材版本：'+group.item.asset_version,'状态：'+status,...(view.tab==='accepted'?['发布状态：'+(view.publicationKind==='delivery'?'已正式交付':'合格预览，尚未正式交付'),'融合来源：'+(group.item.source_count?`${group.item.source_count} 条命中 · ${libraries.join('、')}`:'旧结果未记录来源'),'精确重复副本：'+(group.item.duplicate_candidate_count??0),'疑似近重复：'+(near?near+'；仅标记，未自动扣减有效数量':'无')]:[]),...(group.item.user_exclusion?[`用户排除范围：${group.item.user_exclusion.scope==='candidate'?'仅此图':'相似簇'}`,`用户排除原因：${group.item.user_exclusion.reason}`]:[]),'预览：每次读取均复验当前结果血缘、授权和素材摘要']){const p=document.createElement('p');p.textContent=text;info.append(p)}if(group.item.reasons?.length){const title=document.createElement('strong');title.textContent='规则判定';info.append(title);const reasons=document.createElement('ul');reasons.className='candidate-reasons';for(const reason of group.item.reasons){const li=document.createElement('li');li.textContent=`${reason.rule_id}：${reason.verdict==='pass'?'通过':reason.verdict==='fail'?'未通过':reason.verdict==='not_applicable'?'不适用':'待复核'} · ${cleaningReasonLabel(reason.reason_code)}${reason.reason_summary?' · '+reason.reason_summary:''}`;reasons.append(li)}info.append(reasons)}if(['accepted','review'].includes(view.tab)&&view.publicationKind!=='delivery'){const actions=document.createElement('div');actions.className='candidate-detail-actions';const add=(label,fn)=>{const button=document.createElement('button');button.className='secondary';button.textContent=label;button.onclick=fn;actions.append(button)};const binding={task_version:view.taskVersion,result_set_digest:view.version,view:view.tab};if(view.tab==='review'){add('人工通过',()=>openResultFeedback('manual_accept',view.taskId,binding,group.item));add('人工淘汰',()=>openResultFeedback('manual_reject',view.taskId,binding,group.item))}else{add('仅排除此图',()=>openResultFeedback('exclude',view.taskId,binding,group.item,'candidate'));if(group.item.near_duplicate_matches?.length)add('排除相似簇',()=>openResultFeedback('exclude',view.taskId,binding,group.item,'near_duplicate_cluster'));add('升级为规则',()=>openResultFeedback('rule',view.taskId,binding,group.item))}info.append(actions)}}
  else{const roleLabel=role=>role?({'primary':'主标签','auxiliary':'辅助词'})[role]||role:'';
    for(const source of group.sources){const p=document.createElement('p');const tag=document.createElement('strong');tag.textContent=source.query_text;p.append(tag);if(source.query_role||source.source_term||source.branch_id){const span=document.createElement('span');span.className='detail-sub';span.textContent='（'+[roleLabel(source.query_role),source.source_term,source.branch_id?'分支 '+source.branch_id:null].filter(Boolean).join(' · ')+'）';p.append(span)}info.append(p)}
    const rep=group.item,rows=[];
    if(group.scores.length){const vals=group.scores.map(s=>s.score);const range=vals.length>1?Math.min(...vals).toFixed(4)+' ~ '+Math.max(...vals).toFixed(4):vals[0].toFixed(4);rows.push(['检索相关性',range+'（'+[...group.metrics].join('/')+'；未清洗，相关性不代表合格）'])}
    if(rep.status==='resolved'){rows.push(['素材标识',rep.media_unit_id]);rows.push(['素材版本',rep.asset_version]);if(rep.content_digest)rows.push(['内容指纹',rep.content_digest])}else{rows.push(['映射状态',rep.status||'未解析为素材'])}
    rows.push(['原始命中',group.rawHits+' 条']);
    if(group.hitSequences.length)rows.push(['命中序号',group.hitSequences.join('、')]);
    if(rep.library_id)rows.push(['物理索引',rep.library_id+' / '+rep.binding_id+'（'+rep.index_version+'）']);
    rows.push(['命中向量',group.vectors.join('、')]);
    rows.push(['预览状态',group.previewItem?'可预览（未清洗）':'无授权图片 · 不可读取']);
    for(const [label,value] of rows){const p=document.createElement('p');const k=document.createElement('strong');k.className='detail-label';k.textContent=label+'：';p.append(k,value);info.append(p)}
  }
  $('candidate-prev').disabled=index===0;$('candidate-next').disabled=index===groups.length-1&&!candidateCanRead(view);candidateSave(view);
}
function candidateBack(){if(!candidateActive)return;candidateActive.selected=null;const stage=$('candidate-image-stage');for(const img of stage.querySelectorAll('img')){img._abort?.abort();if(img._url)URL.revokeObjectURL(img._url)}stage.replaceChildren();stage.dataset.key='';$('candidate-image-view').classList.add('hidden');$('candidate-grid').classList.remove('hidden');for(const id of ['candidate-back','candidate-prev','candidate-next'])$(id).classList.add('hidden');$('candidate-more').classList.remove('hidden');$('candidate-position').textContent='';for(const id of ['candidate-page-prev','candidate-page-next','candidate-page-position'])$(id).classList.remove('hidden');candidateRender(candidateActive);candidateSave(candidateActive)}
async function candidateMove(delta){const view=candidateActive;if(!view||view.busy)return;let groups=candidateGroups(view),index=groups.findIndex(g=>candidateGroupKey(g)===view.selected);if(delta>0&&index===groups.length-1&&candidateCanRead(view)){await candidateLoad(view);if(!candidateCurrent(view))return;groups=candidateGroups(view);index=groups.findIndex(g=>candidateGroupKey(g)===view.selected)}const next=groups[index+delta];if(next){view.selected=candidateGroupKey(next);candidateShowImage(view)}}
async function candidateLoad(view,incremental=false,replacement=null){
  if(!candidateCurrent(view)||view.busy)return;view.busy=true;const epoch=view.epoch;const current=()=>candidateCurrent(view)&&view.epoch===epoch;
  try{
    if(candidateResultTab(view)){const pageCursor=replacement?replacement.cursor:view.next;const params=new URLSearchParams({view:view.tab,limit:'60'});if(pageCursor)params.set('cursor',pageCursor);const data=await api(`/v1/retrieval/tasks/${view.taskId}/results?${params}`);if(!current())return;if(typeof linkPipelineRender==='function')linkPipelineRender(view,data);if(view.version&&data.result_set_digest&&view.version!==data.result_set_digest)throw new Error('结果版本已变化，请重新加载当前版本');if(!data.available)view.items=[];view.version=data.result_set_digest;view.taskVersion=data.task_version;view.publicationKind=data.publication_kind;if(replacement){view.items=[];view.itemCursors=new Map();view.page=0}view.lastPageCursor=pageCursor;candidateRememberPage(view,data.items||[],pageCursor);view.next=data.next_cursor;const seen=new Set(view.items.map(item=>item.candidate_id));for(const item of data.items||[])if(!seen.has(item.candidate_id)){view.items.push(item);seen.add(item.candidate_id)}candidateTrimCache(view);view.readError=false;candidateHint(data.available?`${candidateTabLabel(view.tab)} · 当前结果版本 ${data.result_set_digest} · 当前窗口 ${view.items.length} 张`:(data.note||'尚无当前版本结果'));candidateRender(view);candidateSave(view);return}
    const cursor=replacement?replacement.cursor:(incremental?view.changes:(view.next||view.changes));
    if(incremental&&!cursor)return;
    const params=new URLSearchParams({limit:'60',query:view.query});if(cursor)params.set('cursor',cursor);
    const data=await api(`/v1/retrieval/tasks/${view.taskId}/candidates${cursor?'/changes':''}?${params}`);
    if(!current())return;
    if(typeof linkPipelineRender==='function')linkPipelineRender(view,data);
    if(view.version&&view.version!==data.search_version)throw new Error('检索版本已变化，请重新加载当前版本');
    if(replacement){view.items=[];view.itemCursors=new Map();view.page=0}
    view.version=data.search_version;if(!incremental)view.lastPageCursor=cursor;view.next=data.next_cursor;view.changes=data.changes_cursor;view.rawHitCount=data.raw_hit_count;
    candidateRememberPage(view,data.items,cursor);
    const seen=new Set(view.items.map(i=>i.hit_sequence));for(const item of data.items)if(!seen.has(item.hit_sequence)){view.items.push(item);seen.add(item.hit_sequence)}
    candidateTrimCache(view);
    const select=$('candidate-query');for(const term of data.query_terms||[])if(![...select.options].some(o=>o.value===term)){const option=document.createElement('option');option.value=term;option.textContent=term;select.append(option)}select.value=view.query;
    const all=candidateAllGroups(view),previewable=all.filter(group=>group.previewItem).length;view.readError=false;candidateHint(`原始命中 ${data.raw_hit_count} 条 · 当前窗口 ${all.length} 个候选，其中 ${previewable} 张可预览、${all.length-previewable} 条无授权图片 · 命中来源为当前窗口 · 未清洗 · 更新于 ${new Date().toLocaleTimeString()}`);candidateRender(view);candidateSave(view);
  }catch(error){if(current()){const invalid=error.message.includes('版本已变化')||['CURSOR_EXPIRED','CANDIDATE_VERSION_CHANGED','REVISION_CONFLICT','ACCESS_DENIED','PROJECT_ACCESS_DENIED','NOT_FOUND'].includes(error.code);view.readError=!invalid;candidateHint('结果读取失败：'+error.message+(invalid?'。请在当前窗口重新读取结果。':'。已加载内容保留；可重试读取。'));if(invalid){candidateCleanupImages();$('candidate-grid').replaceChildren();$('candidate-image-stage').replaceChildren();view.items=[];view.selected=null;view.invalid=true;candidateBack();candidateRender(view)}candidateRecovery(view)}}
  finally{if(view.epoch===epoch){view.busy=false;if(current())candidateRender(view)}}
}
async function openCandidateBrowser(){
  if(!state.task)return;const scope=candidateScope();let view=candidateViews.get(scope);
  clearInterval(candidateTimer);candidateTimer=null;
  if(!view){let saved={};try{saved=JSON.parse(sessionStorage.getItem('dsh-candidate-view:'+scope)||'{}')}catch{}const tab=['candidates','accepted','failed','review','user_excluded'].includes(saved.tab)?saved.tab:'candidates',resumeCursor=typeof saved.resumeCursor==='string'?saved.resumeCursor:null;view={scope,taskId:state.task.task_id,query:saved.query||'',tab,previewOnly:saved.previewOnly!==false,items:[],next:resumeCursor,lastPageCursor:resumeCursor,changes:null,selected:saved.selected||null,scroll:saved.scroll||0,restoreScroll:saved.scroll||0,version:saved.version};candidateViews.set(scope,view);while(candidateViews.size>3){const key=candidateViews.keys().next().value;if(key===scope)break;candidateViews.delete(key)}}
  const restoreScroll=view.scroll;view.epoch=(view.epoch||0)+1;view.busy=false;candidateActive=view;candidateCleanupImages();$('candidate-grid').replaceChildren();$('candidate-image-stage').replaceChildren();$('candidate-image-stage').dataset.key='';if(!candidateDialog.open)candidateDialog.showModal();candidateHint('正在读取'+(candidateResultTab(view)?candidateTabLabel(view.tab):'检索候选')+'…');
  $('candidate-query').replaceChildren(new Option('全部检索词',''));if(view.query)$('candidate-query').append(new Option(view.query,view.query));$('candidate-query').value=view.query;
  $('candidate-query').disabled=candidateResultTab(view);candidateFilterLabel(view);candidateRecovery(view);for(const [id,tab] of [['candidate-tab','candidates'],['accepted-tab','accepted'],['failed-tab','failed'],['review-tab','review'],['excluded-tab','user_excluded']]){$(id).setAttribute('aria-pressed',String(view.tab===tab));$(id).classList.toggle('secondary',view.tab!==tab)}
  const selected=view.selected;candidateBack();view.scroll=restoreScroll;view.selected=selected;candidateRender(view);
  if(!view.items.length&&!view.invalid)await candidateLoad(view);
  if(candidateCurrent(view))$('candidate-grid').scrollTop=restoreScroll;
  candidateTimer=setInterval(()=>{if(!candidateCurrent(view))return;if(document.hidden||candidateResultTab(view)||view.invalid||view.items.length>candidateCacheLimit-candidatePageSize)return;const search=state.searchSnapshot;if(search?.taskId===view.taskId&&search.search?.pagination_stopped)return;candidateLoad(view,Boolean(!view.next))},2500);
}
function closeCandidateBrowser(){if(candidateActive)candidateSave(candidateActive);clearInterval(candidateTimer);candidateCleanupImages();candidateDialog.close();candidateActive=null}
$('candidate-close').onclick=closeCandidateBrowser;candidateDialog.addEventListener('cancel',event=>{event.preventDefault();if(candidateActive?.selected)candidateBack();else closeCandidateBrowser()});
$('candidate-back').onclick=candidateBack;$('candidate-prev').onclick=()=>candidateMove(-1);$('candidate-next').onclick=()=>candidateMove(1);
candidateDialog.addEventListener('keydown',event=>{if(event.target.matches('select,input'))return;if(candidateActive?.selected&&['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();candidateMove(event.key==='ArrowLeft'?-1:1)}});
$('candidate-page-prev').onclick=()=>candidatePageMove(-1);$('candidate-page-next').onclick=()=>candidatePageMove(1);
$('candidate-more').onclick=()=>candidateLoad(candidateActive);$('candidate-retry').onclick=()=>{const v=candidateActive;if(v&&!v.invalid)candidateLoad(v,v.tab==='candidates'&&!v.next&&Boolean(v.changes))};
function candidateReset(tab,query){const view=candidateActive;if(!view)return;candidateSave(view);candidateCleanupImages();candidateViews.delete(view.scope);sessionStorage.removeItem('dsh-candidate-view:'+view.scope);candidateViews.set(view.scope,{scope:view.scope,taskId:view.taskId,tab,query,previewOnly:view.previewOnly!==false,items:[],next:null,changes:null,selected:null,scroll:0});openCandidateBrowser()}
$('candidate-query').onchange=()=>candidateReset('candidates',$('candidate-query').value);$('candidate-reset').onclick=()=>{if(candidateActive?.invalid)candidateReset(candidateActive.tab,candidateActive.query)};
$('candidate-tab').onclick=()=>{if(candidateActive?.tab!=='candidates')candidateReset('candidates','')};for(const [id,tab] of [['accepted-tab','accepted'],['failed-tab','failed'],['review-tab','review'],['excluded-tab','user_excluded']])$(id).onclick=()=>{if(candidateActive?.tab!==tab)candidateReset(tab,'')};
$('candidate-preview-filter').onclick=()=>{const view=candidateActive;if(!view||view.tab!=='candidates')return;candidateBack();view.previewOnly=view.previewOnly===false;view.page=0;candidateFilterLabel(view);candidateRender(view);candidateSave(view)};
"""
