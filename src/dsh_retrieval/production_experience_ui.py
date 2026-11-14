"""Presentation helpers shared by the production overview and image inspectors."""

EXPERIENCE_HTML = '''<dialog id="asset-viewer" aria-labelledby="asset-title"><div class="asset-heading"><h2 id="asset-title">图片详情</h2><button id="asset-close" type="button">关闭图片</button></div><div class="asset-layout"><div id="asset-image"></div><aside id="asset-evidence"></aside></div><div class="output-actions"><button id="asset-prev" type="button">上一张</button><span id="asset-position" role="status"></span><button id="asset-next" type="button">下一张</button></div></dialog>'''

EXPERIENCE_CSS = '''
body.task-page #overview-summary{display:none}
body.task-page .detail-head{align-items:flex-start;gap:12px;flex-wrap:wrap}body.task-page .detail-head>div{flex:1;min-width:220px}body.task-page .detail-head small,body.task-page #detail-id:empty,body.task-page #detail-status:empty{display:none}body.task-page #detail-title{font-size:21px;line-height:1.55;max-width:960px}body.task-page .detail{padding:24px;box-shadow:none}
.task-summary{display:flex;align-items:center;gap:10px 22px;flex-wrap:wrap;margin:14px 0;font-size:13px}.summary-count{font-variant-numeric:tabular-nums;color:#46534a}.task-runtime{font-size:12px;color:var(--muted);margin:0 0 18px}.task-runtime>summary{cursor:pointer;width:fit-content}.runtime-facts{padding:8px 14px;background:#f7f8f6;border-radius:8px;margin-top:8px;overflow-wrap:anywhere}.runtime-facts p{margin:5px 0;line-height:1.7}
.detail-nodes{display:flex;gap:0;overflow-x:auto;border-bottom:1px solid var(--line);margin-top:16px;padding:0;background:transparent}.detail-nodes .detail-node{flex:1 0 120px;display:flex;align-items:center;justify-content:center;gap:12px;border:0;border-bottom:3px solid transparent;border-radius:0;padding:14px 12px;background:transparent;color:#69736b}.detail-nodes .detail-node strong{display:inline;margin:0;font-size:14px}.detail-nodes .detail-node[aria-current=page]{border:0;border-bottom:3px solid #246646;background:#f3f8f3;color:#246646}.node-status{font-size:11px;white-space:nowrap}.node-status:before{content:'';display:inline-block;width:6px;height:6px;background:#afb7b0;border-radius:50%;margin-right:5px}.detail-node[data-status=succeeded] .node-status:before{background:#39764b}.detail-node[data-status=running] .node-status:before{background:#3879bd}.detail-node[data-status=failed] .node-status:before{background:#b64a3c}.node-output{border:0;margin-top:0;padding-top:20px}.node-output h3,.cleaning-heading h3{font-size:16px;margin-top:0}
.output-grid,.cleaning-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px;align-items:stretch}.output-card,.cleaning-image{border:1px solid #e0e5df;border-radius:12px;min-width:0;overflow:hidden;padding:12px;box-shadow:none}.search-thumbnail,.cleaning-thumbnail{display:flex;align-items:center;justify-content:center;width:100%;height:200px;min-height:200px;margin:0 0 12px;padding:0;border:0;border-radius:8px;overflow:hidden;background:#f5f6f3;color:#69736b;font-size:12px}.output-card .search-thumbnail img,.cleaning-image .cleaning-thumbnail img{display:block;width:100%;height:200px;object-fit:contain}.media-caption{min-height:78px;max-height:78px;overflow:hidden}.search-hits,.media-reason{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden;white-space:pre-line;font-size:12px;line-height:1.7;margin:0;color:#46534a}.cleaning-image .media-reason{font-size:12px}.media-caption strong{display:block;font-size:12px;margin:0 0 5px}.media-details{display:block;border:0;background:transparent;color:#246646;font-size:12px;padding:8px 0 0;text-align:left}.output-actions select,.cleaning-filters select,.cleaning-filters input{font:inherit;font-size:12px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:white}.board-cleaning{border-top:0;margin-top:0;padding-top:20px}.cleaning-step-results>.cleaning-step{border:0;padding:0}.cleaning-step>summary{font-size:13px}.cleaning-grid{margin-top:16px}
@media(max-width:760px){body.task-page .detail{padding:16px}body.task-page #detail-title{font-size:18px}.detail-nodes .detail-node{flex-basis:112px;flex-direction:column;gap:6px;padding:12px 8px}.task-summary{gap:10px 14px}.output-grid,.cleaning-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.output-card,.cleaning-image{padding:8px}.search-thumbnail,.cleaning-thumbnail,.output-card .search-thumbnail img,.cleaning-image .cleaning-thumbnail img{height:160px;min-height:160px}}@media(max-width:420px){.output-grid,.cleaning-grid{grid-template-columns:minmax(0,1fr)}}
.overview-summary{display:flex;gap:12px;flex-wrap:wrap;margin:0 0 18px}.overview-summary .stat{flex:1;background:white;border:1px solid var(--line)}
.goal-line{font-weight:700;line-height:1.8}.goal-note,.next-note{font-size:12px;color:var(--muted);white-space:normal;max-width:340px;line-height:1.7}.next-action{display:inline-block;margin:7px 10px 0 0;color:#246646;font-weight:700}.next-action.secondary{font-weight:600;color:#5a6c60}.row-process summary{cursor:pointer;color:#54645a}.row-process .nodes{margin-top:8px}.attention{padding:14px;border:1px solid #e9d59f;background:#fff9eb;border-radius:10px;margin:16px 0}.attention p{margin:0 0 8px}.asset-preview{cursor:zoom-in}.asset-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}
#asset-viewer{width:min(1080px,96vw);max-height:94vh;border:1px solid var(--line);border-radius:14px;padding:20px;color:#202421}#asset-viewer::backdrop{background:#17221c99}.asset-layout{display:grid;grid-template-columns:minmax(0,2fr) minmax(220px,1fr);gap:20px;margin-top:16px}#asset-image img{width:100%;height:65vh;object-fit:contain;background:#f5f6f3}#asset-evidence{overflow-wrap:anywhere;line-height:1.7;max-height:65vh;overflow:auto}#asset-evidence small{display:block;color:var(--muted)}#asset-image p{padding:30px}button:disabled{opacity:.55;cursor:default}@media(max-width:760px){.asset-layout{grid-template-columns:1fr}#asset-image img{height:40vh}#asset-evidence{max-height:23vh}}
'''

EXPERIENCE_JS = r'''
function knownCount(value){return Number.isInteger(value)&&value>=0?String(value):'待计算'}
function runStatusLabel(item){
 const p=item.progress,state=p?.run_status||item.run_status;
 const stoppedRefill=p?.nodes?.find(node=>node.node==='refill'&&node.detail?.includes('自动补数已停止'));
 if(stoppedRefill)return '补数已停止';
 if((p?.counts?.pending_review||0)>0)return '清洗待复核';
 const gate=p?.nodes?.find(node=>node.gate_status==='awaiting_approval');
 if(gate)return ({expansion:'扩词待确认',retrieval:'检索待确认',cleaning:'清洗待确认',refill:'补数待确认',packaging:'打包待确认'})[gate.node]||'待确认';
 if(state==='RUNNING'){
  const active=p?.nodes?.find(node=>node.node_status==='running');
  if(active)return ({expansion:'扩词中',retrieval:'检索中',cleaning:'清洗中',refill:'补数中',packaging:'打包中'})[active.node]||`${active.label||active.node}中`;
  return '后台运行中';
 }
 return ({CREATED:'已创建',PARSING:'需求解析中',PLANNING:'规划中',AWAITING_INPUT:'待操作',PAUSED:'已暂停',PARTIAL:'部分完成',COMPLETED:'已完成',FAILED:'失败',CANCELLED:'已取消'})[state]||state||'状态未知';
}
function nextAction(item){
 const p=item.progress,chat='/chat?task='+encodeURIComponent(item.task_id);
 if(item.error)return {kind:'attention',label:'重试读取',message:'状态读取失败；保留上次读取结果，不代表任务执行失败。',retry:true};
 if(!p)return {kind:'loading',label:'查看详情',message:'正在读取当前进度',href:nodeURL(item.task_id)};
 const state=p.run_status||item.run_status;
 if(state==='CANCELLED')return {kind:'stopped',label:'查看记录',message:'任务已取消',href:nodeURL(item.task_id)};
 const failed=p.nodes.find(n=>n.node_status==='failed');
 if(failed||state==='FAILED')return {kind:'attention',label:'查看失败原因',message:failed?.detail||'执行失败，请查看运行记录后处理',href:nodeURL(item.task_id,failed?.node||'retrieval')};
 if(state==='PAUSED')return {kind:'attention',label:'继续此 Chat',message:'任务已暂停，请在对话中查看可用处理操作',href:chat};
 if(p.counts.pending_review>0)return {kind:'attention',label:'查看待复核',message:`${p.counts.pending_review} 张待复核，未计入有效素材`,href:nodeURL(item.task_id,'cleaning')};
 const stoppedRefill=p.nodes.find(n=>n.node==='refill'&&n.detail?.includes('自动补数已停止'));
 if(stoppedRefill){
  const accepted=knownCount(p.counts.accepted),gap=knownCount(p.counts.gap);
  return {kind:'attention',label:'调整方案',message:`补数没有新增有效素材，已自动停止；当前有效 ${accepted}、缺口 ${gap}${p.counts.accepted===0?'，无法打包':''}。`,href:chat,secondary:{label:'查看补数明细',href:nodeURL(item.task_id,'refill')}};
 }
 const gate=p.nodes.find(n=>n.gate_status==='awaiting_approval');
 if(gate||state==='AWAITING_INPUT')return {kind:'attention',label:'前往处理',message:gate?.detail||'等待需求发起人处理',href:chat};
 if(['COMPLETED','PARTIAL'].includes(state))return {kind:'finished',label:'查看交付',message:state==='PARTIAL'?'部分交付，缺口与停止原因见交付清单':'查看当前交付状态与成员清单',href:nodeURL(item.task_id,'packaging')};
 const active=p.nodes.find(n=>n.node_status==='running'),running=state==='RUNNING';
 return {kind:running?'running':'pending',label:running?'查看进展':'查看详情',message:running?(active?`${active.label}进行中`:'后台执行中'):(state==='PLANNING'?'方案规划中，尚未开始生产':'等待后续节点推进'),href:nodeURL(item.task_id,active?.node||'expansion')};
}
function goalSummary(item){const c=item.progress?.counts;return c?`目标 ${knownCount(c.target)} · 有效 ${knownCount(c.accepted)} · 缺口 ${knownCount(c.gap)}${(c.groups||[]).map(row=>` · ${row.label} ${knownCount(row.accepted_count)}/${row.target_count}${row.gap?`（缺 ${row.gap}）`:''}`).join('')}`:'目标 / 有效 / 缺口：待计算'}
function renderNextAction(parent,item){const action=nextAction(item);const message=document.createElement('p');message.className='next-note';message.textContent=action.message;parent.append(message);const link=document.createElement(action.retry?'button':'a');link.className='next-action';link.textContent=action.label;if(action.retry)link.onclick=()=>selected?loadDetailPage():load();else link.href=action.href;parent.append(link);if(action.secondary){const secondary=document.createElement('a');secondary.className='next-action secondary';secondary.textContent=action.secondary.label;secondary.href=action.secondary.href;parent.append(secondary)}}
function updateOverviewSummary(){const box=$('overview-summary');if(!box)return;box.replaceChildren();const kinds=rows.map(nextAction);for(const [label,value] of [['本页需求',rows.length],['本页运行中',kinds.filter(x=>x.kind==='running').length],['本页待处理 / 读取异常',kinds.filter(x=>x.kind==='attention').length],['本页已结束（含部分交付）',kinds.filter(x=>x.kind==='finished').length]])stat(box,label,value);const unknown=kinds.filter(x=>x.kind==='loading').length;if(unknown){const note=document.createElement('p');note.textContent=`${unknown} 条进度读取中，统计逐条更新`;box.append(note)}}
const assetViewer={cards:[],index:0,token:0,abort:null,url:null,focus:null};
function clearAssetImage(){assetViewer.token++;assetViewer.abort?.abort();if(assetViewer.url)URL.revokeObjectURL(assetViewer.url);assetViewer.url=null;$('asset-image').replaceChildren()}
function closeAssetViewer(){clearAssetImage();assetViewer.cards=[];$('asset-evidence').replaceChildren();assetViewer.focus?.focus?.({preventScroll:true})}
async function showAsset(index){
 clearAssetImage();assetViewer.index=index;const card=assetViewer.cards[index],asset=card?._asset;if(!asset)return;
 const evidence=$('asset-evidence');evidence.replaceChildren();const title=document.createElement('h3');title.textContent=asset.title;evidence.append(title);
 for(const line of asset.lines()){const p=document.createElement('p');p.textContent=line;evidence.append(p)}
 $('asset-title').textContent=asset.title;$('asset-position').textContent=`${index+1} / ${assetViewer.cards.length}（当前已加载）`;$('asset-prev').disabled=index===0;$('asset-next').disabled=index===assetViewer.cards.length-1;
 const box=$('asset-image'),status=document.createElement('p');status.textContent=asset.url?'正在授权读取图片…':'此素材没有可用的授权预览';box.append(status);if(!asset.url)return;
 const token=assetViewer.token;assetViewer.abort=new AbortController();try{const response=await fetch(asset.url,{headers,cache:'no-store',signal:assetViewer.abort.signal});if(!response.ok)throw new Error('图片不可读取或授权已失效');const blob=await response.blob();if(!['image/jpeg','image/png'].includes(blob.type)||blob.size>10*1024*1024)throw new Error('图片格式或大小无效');if(token!==assetViewer.token||!$('asset-viewer').open)return;assetViewer.url=URL.createObjectURL(blob);const img=document.createElement('img');img.alt=asset.title;img.src=assetViewer.url;box.replaceChildren(img)}catch(error){if(token!==assetViewer.token||error.name==='AbortError')return;status.textContent=error.message;const retry=document.createElement('button');retry.textContent='重试图片';retry.onclick=()=>showAsset(index);box.append(retry)}
}
function openAssetViewer(card){const dialog=$('asset-viewer');assetViewer.focus=document.activeElement;assetViewer.cards=[...card.parentElement.children].filter(el=>el._asset);const index=assetViewer.cards.indexOf(card);if(index<0)return;if(!dialog.open)dialog.showModal();showAsset(index)}
function initializeAssetViewer(){const dialog=$('asset-viewer');$('asset-close').onclick=()=>dialog.close();dialog.addEventListener('close',closeAssetViewer);$('asset-prev').onclick=()=>showAsset(assetViewer.index-1);$('asset-next').onclick=()=>showAsset(assetViewer.index+1);dialog.addEventListener('keydown',event=>{const delta=event.key==='ArrowLeft'?-1:event.key==='ArrowRight'?1:0;if(!delta||/INPUT|SELECT|TEXTAREA/.test(event.target.tagName))return;const index=assetViewer.index+delta;if(index>=0&&index<assetViewer.cards.length){event.preventDefault();showAsset(index)}});window.addEventListener('pagehide',closeAssetViewer)}
'''
