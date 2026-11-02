"""Independent candidate browser, loaded after the chat UI."""

CANDIDATE_CSS = """
#candidate-dialog{width:min(1280px,96vw);height:90dvh;max-width:none;border:1px solid var(--line);border-radius:16px;padding:20px;color:var(--ink)}
#candidate-dialog[open]{display:flex;flex-direction:column;gap:14px}#candidate-dialog header{display:flex;align-items:center;justify-content:space-between;gap:12px}#candidate-dialog h2{margin:0;font-size:18px}.candidate-tools{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.candidate-tools select{width:min(340px,100%)}#candidate-grid{overflow:auto;flex:1;min-height:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px;align-content:start}.candidate-tile{border:1px solid var(--line);border-radius:12px;padding:10px;min-width:0;background:white}.candidate-tile button{display:block;width:100%;padding:0;background:#eef1ed;color:var(--muted);height:160px;overflow:hidden;border-radius:8px}.candidate-tile img{width:100%;height:160px;object-fit:contain}.candidate-tile p{font-size:13px;overflow-wrap:anywhere;margin:8px 0}.candidate-tile small{display:block;color:var(--muted);overflow-wrap:anywhere}#candidate-image-view{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:20px}#candidate-image-stage{min-height:0;display:flex;align-items:center;justify-content:center;background:#eef1ed;border-radius:12px;overflow:hidden}#candidate-image-stage img{max-width:100%;max-height:100%;object-fit:contain}#candidate-image-info{overflow:auto;overflow-wrap:anywhere}#candidate-image-info h3{margin:0 0 10px}#candidate-image-info p{font-size:13px;margin:6px 0;line-height:1.5}#candidate-image-info .detail-label{color:var(--muted);font-weight:600}#candidate-image-info .detail-sub{color:var(--muted);font-weight:400;font-size:12px}#candidate-read-status{font-size:13px;margin:0;white-space:pre-wrap}.candidate-empty{grid-column:1/-1;padding:30px;color:var(--muted)}@media(max-width:680px){#candidate-image-view{grid-template-columns:1fr;grid-template-rows:minmax(220px,45dvh) max-content;overflow:auto}#candidate-image-info{overflow:visible}#candidate-image-stage img{max-height:45dvh}#candidate-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
"""

CANDIDATE_HTML = """<dialog id="candidate-dialog" aria-labelledby="candidate-title"><header><h2 id="candidate-title">检索候选</h2><button id="candidate-close" class="secondary">关闭</button></header><div class="candidate-tools"><button id="candidate-tab" aria-pressed="true">检索候选</button><button id="accepted-tab" class="secondary" aria-pressed="false">合格结果预览</button><label for="candidate-query">检索词</label><select id="candidate-query"><option value="">全部检索词</option></select><button id="candidate-preview-filter" class="secondary" aria-pressed="true">只看可预览图片</button><button id="candidate-retry" class="secondary hidden">重试读取</button><button id="candidate-reset" class="secondary hidden">重新读取结果</button></div><p id="candidate-read-status" role="status"></p><div id="candidate-grid"></div><div id="candidate-image-view" class="hidden"><div id="candidate-image-stage"></div><aside id="candidate-image-info"></aside></div><footer class="candidate-tools"><button id="candidate-more" class="secondary">加载更多</button><button id="candidate-back" class="secondary hidden">返回缩略图</button><button id="candidate-prev" class="secondary hidden">上一张</button><button id="candidate-next" class="secondary hidden">下一张</button><span id="candidate-position"></span></footer></dialog>"""

CANDIDATE_JS = r"""
const candidateViews=new Map();
let candidateActive=null,candidateTimer=null,searchPollTimer=null,searchPollBusy=false;
const candidateDialog=$('candidate-dialog');
$('open-candidate-window').onclick=openCandidateBrowser;

function candidateScope(){return state.task?`${state.task.project_id||'demo'}:${state.conversationId||''}:${state.task.task_id}:${state.task.confirmed_revision}:${state.task.run_generation}`:null}
function candidateCurrent(view){return candidateActive===view&&candidateDialog.open&&view.scope===candidateScope()}
function candidateSave(view){if(!$('candidate-grid').classList.contains('hidden'))view.scroll=$('candidate-grid').scrollTop;try{sessionStorage.setItem('dsh-candidate-view:'+view.scope,JSON.stringify({query:view.query,tab:view.tab,previewOnly:view.previewOnly,scroll:view.scroll,selected:view.selected,loaded:view.items.length,version:view.version}))}catch{}}
function candidateAllGroups(view){return view.tab==='accepted'?view.items.map(item=>({item,sources:item.sources||[],indexes:['合格结果预览'],previewItem:item})):aggregateLocalCandidates(view.items)}
function candidateGroups(view){const groups=candidateAllGroups(view);return view.tab==='candidates'&&view.previewOnly!==false?groups.filter(group=>group.previewItem):groups}
function candidateGroupKey(group){const i=group.item;return i.candidate_id||JSON.stringify([i.library_id,i.binding_id,i.vector_id])}
function candidateHint(text){$('candidate-read-status').textContent=text}
function candidateRecovery(view){$('candidate-reset').classList.toggle('hidden',!view.invalid);$('candidate-retry').classList.toggle('hidden',!view.readError||Boolean(view.invalid))}
function candidateFilterLabel(view){const button=$('candidate-preview-filter');button.classList.toggle('hidden',view.tab==='accepted');button.setAttribute('aria-pressed',String(view.previewOnly!==false));button.textContent='只看可预览图片'}
function candidateCleanupImages(){for(const img of candidateDialog.querySelectorAll('img')){img._observer?.disconnect();img._abort?.abort();if(img._url)URL.revokeObjectURL(img._url)}}
async function candidateImage(img,item,view){
  const controller=new AbortController();img._abort=controller;
  try{
    const path=view.tab==='accepted'?`/v1/retrieval/tasks/${encodeURIComponent(view.taskId)}/cleaning/candidates/${encodeURIComponent(item.candidate_id)}/preview`:`/v1/retrieval/tasks/${encodeURIComponent(view.taskId)}/candidates/${encodeURIComponent(item.candidate_id)}/preview?ref=${encodeURIComponent(item.preview_ref||'')}`;
    const response=await fetch(path,{headers,signal:controller.signal});if(!response.ok)throw new Error('图片不可读取，请重试');
    const blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('预览格式不可用');
    if(!candidateCurrent(view)||!img.isConnected)return;
    img._url=URL.createObjectURL(blob);img.src=img._url;
  }catch(error){if(error.name==='AbortError'||!candidateCurrent(view)||!img.isConnected)return;img.alt='预览失败：'+error.message;img.title='点击图片重试';img.onclick=event=>{event.stopPropagation();candidateImage(img,item,view)}}
}
function candidateRender(view){
  if(!candidateCurrent(view))return;
  const grid=$('candidate-grid'),groups=candidateGroups(view),existing=new Map([...grid.children].map(el=>[el.dataset.key,el]));
  grid.querySelector('.candidate-empty')?.remove();
  const visible=new Set(groups.map(candidateGroupKey));for(const [id,tile] of existing)if(!visible.has(id)){for(const img of tile.querySelectorAll('img')){img._observer?.disconnect();img._abort?.abort();if(img._url)URL.revokeObjectURL(img._url)}tile.remove();existing.delete(id)}
  for(const group of groups){const id=candidateGroupKey(group);let tile=existing.get(id);
    if(!tile){tile=document.createElement('article');tile.className='candidate-tile';tile.dataset.key=id;const button=document.createElement('button');button.setAttribute('aria-label',group.previewItem?'查看大图':'查看候选详情');button.textContent='无授权图片 · 查看详情';
      if(group.previewItem){const img=document.createElement('img');img.alt='正在加载候选图片';img.loading='lazy';button.replaceChildren(img);tile._image=img}
      button.onclick=()=>{view.selected=id;candidateShowImage(view)};tile.append(button,document.createElement('p'),document.createElement('small'));grid.append(tile);if(tile._image){const img=tile._image;const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){observer.disconnect();if(candidateCurrent(view))candidateImage(img,group.previewItem,view)}},{root:grid,rootMargin:'200px'});img._observer=observer;observer.observe(img)}
    }
    if(group.previewItem&&!tile._image){const img=document.createElement('img');img.alt='正在加载候选图片';img.loading='lazy';tile.querySelector('button').replaceChildren(img);tile.querySelector('button').setAttribute('aria-label','查看大图');tile._image=img;candidateImage(img,group.previewItem,view)}
    tile.querySelector('p').textContent=view.tab==='accepted'?group.item.candidate_id:group.sources.map(s=>s.query_text).join(' · ');
    tile.querySelector('small').textContent=view.tab==='accepted'?`${view.publicationKind==='delivery'?'已正式交付':'合格预览 · 尚未正式交付'} · 融合来源 ${group.item.source_count??0} · 近重复待复核 ${(group.item.near_duplicate_matches||[]).length}`:group.indexes.join('、')+' · '+(group.previewItem?'未清洗':'无授权图片 · 未清洗');
  }
  if(!groups.length){const empty=document.createElement('p');empty.className='candidate-empty';empty.textContent=view.tab==='accepted'?'尚无合格结果预览，或当前结果集为空。':view.items.length&&view.previewOnly!==false?'当前已读取的候选没有可预览图片；可加载更多，或显示全部候选查看原因。':'暂无候选，检索过程中会自动追加。';grid.append(empty)}
  $('candidate-more').disabled=!view.next;$('candidate-more').textContent=view.next?'加载更多':'已加载当前水位';candidateRecovery(view);
  if(view.selected)candidateShowImage(view);
}
function candidateShowImage(view){
  const groups=candidateGroups(view),index=groups.findIndex(g=>candidateGroupKey(g)===view.selected);if(index<0)return;
  const group=groups[index];$('candidate-grid').classList.add('hidden');$('candidate-image-view').classList.remove('hidden');
  for(const id of ['candidate-back','candidate-prev','candidate-next'])$(id).classList.remove('hidden');$('candidate-more').classList.add('hidden');
  $('candidate-position').textContent=view.tab==='accepted'?`${index+1} / ${groups.length} 张已加载`:`${index+1} / ${groups.length} 张已加载`+(view.rawHitCount!=null?`（原始命中 ${view.rawHitCount} 条）`:'');
  const stage=$('candidate-image-stage');if(stage.dataset.key!==view.selected){for(const img of stage.querySelectorAll('img')){img._abort?.abort();if(img._url)URL.revokeObjectURL(img._url)}stage.replaceChildren();stage.dataset.key=view.selected;if(group.previewItem){const img=document.createElement('img');img.alt='正在加载大图';stage.append(img);candidateImage(img,group.previewItem,view)}else stage.textContent=view.tab==='accepted'?'当前合格结果没有可授权读取的图片。':'此候选来源没有可授权读取的图片；可返回并只看可预览图片。'}
  const info=$('candidate-image-info');info.replaceChildren();const heading=document.createElement('h3');heading.textContent=view.tab==='accepted'?'合格结果信息':'命中检索词';info.append(heading);
  if(view.tab==='accepted'){const libraries=[...new Set((group.item.sources||[]).map(source=>source.library_id).filter(Boolean))],near=(group.item.near_duplicate_matches||[]).map(match=>`${match.candidate_id}（pHash 距离 ${match.hamming_distance}/${match.review_threshold}）`).join('、');for(const text of ['素材标识：'+group.item.candidate_id,'素材版本：'+group.item.asset_version,'状态：'+(view.publicationKind==='delivery'?'已正式交付':'合格预览，尚未正式交付'),'融合来源：'+(group.item.source_count?`${group.item.source_count} 条命中 · ${libraries.join('、')}`:'旧结果未记录来源'),'精确重复副本：'+(group.item.duplicate_candidate_count??0),'疑似近重复：'+(near?near+'；仅标记，未自动扣减有效数量':'无'),'预览：通过当前结果血缘和授权复验后读取']){const p=document.createElement('p');p.textContent=text;info.append(p)}}
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
  $('candidate-prev').disabled=index===0;$('candidate-next').disabled=index===groups.length-1&&!view.next;candidateSave(view);
}
function candidateBack(){if(!candidateActive)return;candidateActive.selected=null;$('candidate-image-view').classList.add('hidden');$('candidate-grid').classList.remove('hidden');for(const id of ['candidate-back','candidate-prev','candidate-next'])$(id).classList.add('hidden');$('candidate-more').classList.remove('hidden');$('candidate-position').textContent='';candidateSave(candidateActive)}
async function candidateMove(delta){const view=candidateActive;if(!view||view.busy)return;let groups=candidateGroups(view),index=groups.findIndex(g=>candidateGroupKey(g)===view.selected);if(delta>0&&index===groups.length-1&&view.next){await candidateLoad(view);if(!candidateCurrent(view))return;groups=candidateGroups(view)}const next=groups[index+delta];if(next){view.selected=candidateGroupKey(next);candidateShowImage(view)}}
async function candidateLoad(view,incremental=false){
  if(!candidateCurrent(view)||view.busy)return;view.busy=true;const epoch=view.epoch;const current=()=>candidateCurrent(view)&&view.epoch===epoch;
  try{
    if(view.tab==='accepted'){const params=new URLSearchParams({view:'accepted',limit:'60'});if(view.next)params.set('cursor',view.next);const data=await api(`/v1/retrieval/tasks/${view.taskId}/results?${params}`);if(!current())return;if(view.version&&data.result_set_digest&&view.version!==data.result_set_digest)throw new Error('合格结果版本已变化，请重新加载当前版本');if(!data.available)view.items=[];view.version=data.result_set_digest;view.publicationKind=data.publication_kind;view.next=data.next_cursor;const seen=new Set(view.items.map(item=>item.candidate_id));for(const item of data.items||[])if(!seen.has(item.candidate_id)){view.items.push(item);seen.add(item.candidate_id)}view.readError=false;candidateHint(data.available?(data.publication_kind==='delivery'?'已正式交付 · ':'合格预览 · 尚未正式交付 · ')+data.result_set_digest:(data.note||'尚无合格结果预览'));candidateRender(view);candidateSave(view);return}
    const cursor=incremental?view.changes:view.next;
    if(incremental&&!cursor)return;
    const params=new URLSearchParams({limit:'60',query:view.query});if(cursor)params.set('cursor',cursor);
    const data=await api(`/v1/retrieval/tasks/${view.taskId}/candidates${incremental?'/changes':''}?${params}`);
    if(!current())return;
    if(view.version&&view.version!==data.search_version)throw new Error('检索版本已变化，请重新加载当前版本');
    view.version=data.search_version;view.next=data.next_cursor;view.changes=data.changes_cursor;view.rawHitCount=data.raw_hit_count;
    const seen=new Set(view.items.map(i=>i.hit_sequence));for(const item of data.items)if(!seen.has(item.hit_sequence)){view.items.push(item);seen.add(item.hit_sequence)}
    const select=$('candidate-query');for(const term of data.query_terms||[])if(![...select.options].some(o=>o.value===term)){const option=document.createElement('option');option.value=term;option.textContent=term;select.append(option)}select.value=view.query;
    const all=candidateAllGroups(view),previewable=all.filter(group=>group.previewItem).length;view.readError=false;candidateHint(`原始命中 ${data.raw_hit_count} 条 · 已读取 ${all.length} 个候选，其中 ${previewable} 张可预览、${all.length-previewable} 条无授权图片 · 未清洗 · 更新于 ${new Date().toLocaleTimeString()}`);candidateRender(view);candidateSave(view);
  }catch(error){if(current()){const invalid=error.message.includes('版本已变化')||['CURSOR_EXPIRED','CANDIDATE_VERSION_CHANGED','REVISION_CONFLICT','ACCESS_DENIED','PROJECT_ACCESS_DENIED','NOT_FOUND'].includes(error.code);view.readError=!invalid;candidateHint('结果读取失败：'+error.message+(invalid?'。请在当前窗口重新读取结果。':'。已加载内容保留；可重试读取。'));if(invalid){candidateCleanupImages();$('candidate-grid').replaceChildren();$('candidate-image-stage').replaceChildren();view.items=[];view.selected=null;view.invalid=true;candidateBack();candidateRender(view)}candidateRecovery(view)}}
  finally{if(view.epoch===epoch)view.busy=false}
}
async function openCandidateBrowser(){
  if(!state.task)return;const scope=candidateScope();let view=candidateViews.get(scope);
  clearInterval(candidateTimer);candidateTimer=null;
  if(!view){let saved={};try{saved=JSON.parse(sessionStorage.getItem('dsh-candidate-view:'+scope)||'{}')}catch{}view={scope,taskId:state.task.task_id,query:saved.query||'',tab:saved.tab||'candidates',previewOnly:saved.previewOnly!==false,items:[],next:null,changes:null,selected:saved.selected||null,scroll:saved.scroll||0,restoreCount:Math.min(saved.loaded||0,10000),restoreScroll:saved.scroll||0,version:saved.version};candidateViews.set(scope,view)}
  const restoreScroll=view.scroll;view.epoch=(view.epoch||0)+1;view.busy=false;candidateActive=view;candidateCleanupImages();$('candidate-grid').replaceChildren();$('candidate-image-stage').replaceChildren();$('candidate-image-stage').dataset.key='';if(!candidateDialog.open)candidateDialog.showModal();candidateHint('正在读取'+(view.tab==='accepted'?'合格结果预览':'检索候选')+'…');
  $('candidate-query').replaceChildren(new Option('全部检索词',''));if(view.query)$('candidate-query').append(new Option(view.query,view.query));$('candidate-query').value=view.query;
  $('candidate-query').disabled=view.tab==='accepted';candidateFilterLabel(view);candidateRecovery(view);$('candidate-tab').setAttribute('aria-pressed',String(view.tab==='candidates'));$('accepted-tab').setAttribute('aria-pressed',String(view.tab==='accepted'));
  const selected=view.selected;candidateBack();view.scroll=restoreScroll;view.selected=selected;candidateRender(view);
  if(view.items.length&&!view.invalid)await candidateLoad(view,Boolean(!view.next));else if(!view.invalid){view.next=null;await candidateLoad(view)}
  while(candidateCurrent(view)&&view.next&&view.items.length<view.restoreCount&&!view.invalid){const before=view.items.length;await candidateLoad(view);if(view.items.length===before)break}
  if(candidateCurrent(view))$('candidate-grid').scrollTop=restoreScroll;
  candidateTimer=setInterval(()=>{if(!candidateCurrent(view))return;if(document.hidden||view.tab!=='candidates'||view.invalid)return;const search=state.searchSnapshot;if(search?.taskId===view.taskId&&search.search?.pagination_stopped)return;candidateLoad(view,Boolean(!view.next))},2500);
}
function closeCandidateBrowser(){if(candidateActive)candidateSave(candidateActive);clearInterval(candidateTimer);candidateCleanupImages();candidateDialog.close();candidateActive=null}
$('candidate-close').onclick=closeCandidateBrowser;candidateDialog.addEventListener('cancel',event=>{event.preventDefault();if(candidateActive?.selected)candidateBack();else closeCandidateBrowser()});
$('candidate-back').onclick=candidateBack;$('candidate-prev').onclick=()=>candidateMove(-1);$('candidate-next').onclick=()=>candidateMove(1);
candidateDialog.addEventListener('keydown',event=>{if(event.target.matches('select,input'))return;if(candidateActive?.selected&&['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();candidateMove(event.key==='ArrowLeft'?-1:1)}});
$('candidate-more').onclick=()=>candidateLoad(candidateActive);$('candidate-retry').onclick=()=>{const v=candidateActive;if(v&&!v.invalid)candidateLoad(v,v.tab==='candidates'&&!v.next&&Boolean(v.changes))};
function candidateReset(tab,query){const view=candidateActive;if(!view)return;candidateSave(view);candidateCleanupImages();candidateViews.delete(view.scope);sessionStorage.removeItem('dsh-candidate-view:'+view.scope);candidateViews.set(view.scope,{scope:view.scope,taskId:view.taskId,tab,query,previewOnly:view.previewOnly!==false,items:[],next:null,changes:null,selected:null,scroll:0});openCandidateBrowser()}
$('candidate-query').onchange=()=>candidateReset('candidates',$('candidate-query').value);$('candidate-reset').onclick=()=>{if(candidateActive?.invalid)candidateReset(candidateActive.tab,candidateActive.query)};
$('candidate-tab').onclick=()=>{if(candidateActive?.tab!=='candidates')candidateReset('candidates','')};$('accepted-tab').onclick=()=>{if(candidateActive?.tab!=='accepted')candidateReset('accepted','')};
$('candidate-preview-filter').onclick=()=>{const view=candidateActive;if(!view||view.tab!=='candidates')return;candidateBack();view.previewOnly=view.previewOnly===false;candidateFilterLabel(view);candidateRender(view);candidateSave(view)};
"""
