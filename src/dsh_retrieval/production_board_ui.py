"""Standalone read-only production workbench, independent from Chat state."""

BOARD_HTML = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>数据生产线路</title><link rel="stylesheet" href="/production.css"></head><body><a class="skip" href="#main">跳到线路总览</a><header><a class="brand" href="/">DSH Retrieval</a><nav><a aria-current="page" href="/">生产线路</a><a href="/chat">新建需求 / 历史 Chat</a></nav></header><main id="main"><div id="overview-summary" class="overview-summary" aria-label="需求概览"></div><section class="panel"><div class="list-actions"><a class="primary" href="/chat">＋ 新建需求</a></div><p id="line-status" role="status" aria-live="polite">正在读取线路清单…</p><div class="table-wrap"><table><thead><tr><th scope="col"><span>需求编号</span></th><th scope="col"><span>需求内容</span><input id="line-search" type="search" aria-label="筛选需求内容、编号或完整 ID" placeholder="搜索内容或编号"></th><th scope="col"><span>运行状态</span><select id="line-filter" aria-label="筛选运行状态"><option value="">全部状态</option><option value="RUNNING">运行中</option><option value="AWAITING_INPUT">待操作</option><option value="COMPLETED">已完成</option><option value="FAILED">失败</option><option value="PAUSED">已暂停</option><option value="PARTIAL">部分完成</option><option value="CREATED">已创建</option><option value="PLANNING">规划中</option><option value="PARSING">需求解析中</option><option value="CANCELLED">已取消</option></select></th><th scope="col"><span>生产过程</span><select id="line-node-filter" aria-label="筛选进行中的生产节点"><option value="">全部节点</option><option value="expansion">扩词中</option><option value="retrieval">检索中</option><option value="cleaning">清洗中</option><option value="refill">补数中</option><option value="packaging">打包中</option></select></th><th scope="col"><span>目标数</span><input id="line-target-min" type="number" min="0" aria-label="筛选目标数不少于" placeholder="不少于"></th><th scope="col"><span>缺口数</span><select id="line-gap-filter" aria-label="筛选缺口数"><option value="">全部缺口</option><option value="positive">有缺口</option><option value="zero">无缺口</option><option value="unknown">待计算</option></select></th><th scope="col">创建时间</th><th scope="col">下一步</th></tr></thead><tbody id="line-rows"></tbody></table></div><nav class="page-actions task-pagination" aria-label="线路分页"><button id="line-prev" type="button" disabled>上一页</button><span id="line-page" role="status">第 1 页 · 每页 20 条</span><button id="line-next" type="button" disabled>下一页</button></nav></section><section id="line-detail" class="panel detail hidden" aria-labelledby="detail-title"><div class="detail-head"><div><small>LINE DETAIL</small><h2 id="detail-title">线路详情</h2><p id="detail-id"></p></div><button id="detail-close" type="button">关闭详情</button></div><p id="detail-status" role="status"></p><div id="detail-body"></div></section></main><script src="/production.js"></script></body></html>'''

BOARD_CSS = '''
:root{font-family:Inter,"PingFang SC",system-ui,sans-serif;color:#202421;background:#f7f7f5;--line:#dedfdb;--muted:#69736b}*{box-sizing:border-box}body{margin:0}.skip{position:absolute;top:-100px}.skip:focus{top:4px}header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px max(24px,calc((100vw - 1450px)/2));background:white;border-bottom:1px solid var(--line)}.brand{font-weight:800;color:#202421;text-decoration:none}nav{display:flex;gap:8px;flex-wrap:wrap}nav a{color:#3c5142;text-decoration:none;font-size:13px;padding:8px 12px;border-radius:8px}nav a[aria-current]{background:#e5f0e8;font-weight:700}main{max-width:1500px;margin:auto;padding:26px 24px 70px}.heading,.detail-head{display:flex;justify-content:space-between;align-items:center;gap:18px}.heading{margin-bottom:22px}.heading small,.detail-head small{font-size:11px;font-weight:800;letter-spacing:.14em;color:#5b8066}h1{font-size:29px;margin:4px 0}h2{font-size:18px;margin:0}.heading p,.detail-head p{color:var(--muted);font-size:13px;margin:5px 0}.primary{background:#246646;color:white;text-decoration:none;border-radius:9px;padding:10px 16px;white-space:nowrap;font-size:13px;font-weight:700}.panel{background:white;border:1px solid var(--line);border-radius:14px;box-shadow:0 10px 32px #1c2c2210}button,input,select{font:inherit}button{border:1px solid var(--line);border-radius:8px;background:white;padding:8px 12px;cursor:pointer;color:#2f4936}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #246646;outline-offset:2px}.toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:18px}.toolbar h2{margin-right:auto}.toolbar label{font-size:12px;font-weight:700}.toolbar input,.toolbar select{border:1px solid var(--line);border-radius:8px;padding:8px;background:white}.toolbar input{width:min(290px,100%)}#line-status{font-size:12px;color:var(--muted);margin:0;padding:0 18px 12px}.table-wrap{overflow:auto;max-height:calc(100vh - 290px);min-height:250px;border-top:1px solid var(--line)}table{border-collapse:collapse;width:100%;min-width:950px;font-size:12px}td,th{border-bottom:1px solid #e8ebe7;text-align:left;padding:11px 12px;vertical-align:middle}th{position:sticky;top:0;z-index:2;background:#f3f6f2;font-size:11px;color:#48534b}td:last-child,th:last-child{position:sticky;right:0;z-index:3;background:white;box-shadow:-8px 0 14px #17221c0d}td:last-child button{white-space:nowrap}th:last-child{z-index:4;background:#f3f6f2}tr:hover td,tr.selected td{background:#f8faf7}.name{display:block;max-width:340px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px;font-weight:700}.id{display:block;color:var(--muted);font-size:10px;margin-top:3px}.state{display:inline-block;padding:4px 8px;border-radius:999px;background:#edf1ed;white-space:nowrap;font-weight:700}.state[data-state=FAILED],.node[data-status=failed]{background:#fff0ee;color:#9f2525}.state[data-state=AWAITING_INPUT]{background:#fff5dd;color:#805c19}.nodes{display:flex;gap:4px;min-width:330px}.node{display:inline-flex;align-items:center;justify-content:center;min-width:55px;padding:5px 4px;border-radius:6px;background:#eff1ee;color:#737b75;font-size:10px;white-space:nowrap}.node[data-status=succeeded]{background:#e2f1e6;color:#246646}.node[data-status=running]{background:#dbe9fb;color:#235b9a}.count{white-space:nowrap}.hidden{display:none}.page-actions{padding:12px 18px}.detail{margin-top:18px;padding:20px}.detail-head p{overflow-wrap:anywhere}#detail-status{font-size:12px;color:var(--muted)}.stats{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0}.stat{background:#f5f7f4;border-radius:10px;padding:12px;min-width:125px}.stat b{display:block;font-size:17px}.stat span{font-size:11px;color:var(--muted)}.detail-nodes{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:9px}.detail-node{border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0}.detail-node strong{display:block;margin:8px 0 5px}.detail-node p{font-size:11px;color:var(--muted);margin:0;overflow-wrap:anywhere}@media(max-width:760px){header,.heading{flex-direction:column;align-items:start}header{padding:14px 16px}main{padding:18px 12px}.toolbar h2{width:100%}.toolbar input{flex:1}.detail-nodes{grid-template-columns:repeat(2,minmax(0,1fr))}.table-wrap{max-height:50vh}}
.created-inline{display:none}.list-actions{display:flex;justify-content:flex-end;padding:14px 18px}.table-wrap{max-height:calc(100vh - 220px)}table{min-width:1280px}th{vertical-align:top}th span{display:block;margin-bottom:6px}th input,th select{display:block;width:100%;min-width:0;border:1px solid #d6ddd6;border-radius:7px;background:white;padding:6px 8px;color:#35473b;font-size:11px}th:nth-child(1){min-width:180px}th:nth-child(2){min-width:240px}th:nth-child(3){min-width:125px}th:nth-child(4){min-width:350px}th:nth-child(5),th:nth-child(6){min-width:94px}th:nth-child(7){min-width:125px}th:nth-child(8){min-width:110px}td:first-child .id{max-width:210px;white-space:normal;overflow-wrap:anywhere;margin:0}.name{white-space:normal;overflow-wrap:anywhere}.nodes{min-width:0}.nodes a{text-decoration:none}.target-count,.gap-count{font-size:16px;font-weight:750;font-variant-numeric:tabular-nums;white-space:nowrap}.gap-count[data-gap=positive]{color:#a35a20}#line-status:empty{display:none}@media(max-width:760px){.table-wrap{max-height:65vh}}
@media(min-width:761px) and (max-width:1100px){table{min-width:1080px;table-layout:fixed}th,td{min-width:0!important;padding:10px 8px}th:nth-child(1){width:16%}th:nth-child(2){width:20%}th:nth-child(3){width:12%}th:nth-child(4){width:22%}th:nth-child(5){width:8%}th:nth-child(6){width:10%}th:nth-child(5) input{padding-inline:4px}th:nth-child(8){width:12%}th:nth-child(7),td:nth-child(7){display:none}.node{min-width:0;flex:1;padding:5px 2px}.id{white-space:normal;overflow-wrap:anywhere}.row-action .next-note{display:none}.row-action .next-action{margin:0;white-space:normal}.created-inline{display:block;margin-top:3px;color:var(--muted);font-size:10px}}
'''

BOARD_JS = r'''
const $=id=>document.getElementById(id),headers={'X-Tenant-Id':'tenant-a','X-User-Id':'user-a'};
const nodeNames={expansion:'扩词',retrieval:'检索',cleaning:'清洗',refill:'补数',packaging:'打包'};
const stateNames={CREATED:'已创建',PARSING:'需求解析中',PLANNING:'规划中',RUNNING:'运行中',AWAITING_INPUT:'待操作',PAUSED:'已暂停',PARTIAL:'部分完成',COMPLETED:'已完成',FAILED:'失败',CANCELLED:'已取消'};
let rows=[],rowEls=new Map(),listEpoch=0,detailEpoch=0,nextTaskCursor=null,selected=new URLSearchParams(location.search).get('task');
const taskPaging={index:0,cursors:[null],busy:false};
function updateTaskPager(){const prev=$('line-prev'),next=$('line-next');prev.disabled=taskPaging.busy||taskPaging.index===0;next.disabled=taskPaging.busy||!nextTaskCursor;$('line-page').textContent=`第 ${taskPaging.index+1} 页 · 每页 20 条 · 本页 ${rows.length} 条${rows.filter(visible).length!==rows.length?' · 匹配 '+rows.filter(visible).length+' 条':''}`}
async function api(path){const response=await fetch(path,{headers,cache:'no-store'}),data=await response.json();if(!response.ok)throw new Error(`${data.code||response.status}: ${data.message||'读取失败'}`);return data}
function cell(row,value){const el=document.createElement('td');if(value!==undefined)el.textContent=value;row.append(el);return el}
function date(value){if(!value)return'时间未知';const d=new Date(value);return Number.isNaN(d.getTime())?'时间未知':new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d)}
function requestNumber(item){
 const id=String(item.task_id||''),match=/^rt_([a-f0-9]{32})$/i.exec(id);if(!match)return id||'编号未知';
 const code=match[1].slice(-8).toUpperCase(),created=item.created_at?new Date(item.created_at):null;
 if(!created||Number.isNaN(created.getTime()))return '时间未知-'+code;
 const parts=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(created).map(part=>[part.type,part.value]));
 return `${parts.year}${parts.month}${parts.day}-${parts.hour}${parts.minute}-${code}`;
}
function visible(item){
 const q=$('line-search').value.trim().toLowerCase(),status=$('line-filter').value,node=$('line-node-filter').value;
 const minimum=$('line-target-min').value.trim(),gap=$('line-gap-filter').value,counts=item.progress?.counts;
 if(status&&(item.progress?.run_status||item.run_status)!==status)return false;
 if(q&&!(item.title+' '+item.task_id+' '+requestNumber(item)).toLowerCase().includes(q))return false;
 if(node&&!item.progress?.nodes?.some(part=>part.node===node&&part.node_status==='running'))return false;
 if(minimum&&(!Number.isInteger(counts?.target)||counts.target<Number(minimum)))return false;
 if(gap==='positive'&&!(Number.isInteger(counts?.gap)&&counts.gap>0))return false;
 if(gap==='zero'&&counts?.gap!==0)return false;
 if(gap==='unknown'&&Number.isInteger(counts?.gap))return false;
 return true;
}
function updateRow(item){
 updateOverviewSummary();const row=rowEls.get(item.task_id);
 if(!row){if(visible(item))render();return}if(!visible(item)){render();return}
 for(const node of row.querySelectorAll('.node')){const detail=item.progress?.nodes?.find(n=>n.node===node.dataset.node);node.dataset.status=detail?.node_status||'pending';node.title=detail?`${detail.label}：${detail.detail}`:'状态暂不可读'}
 const target=row.querySelector('.target-count'),gap=row.querySelector('.gap-count'),counts=item.progress?.counts;
 target.textContent=knownCount(counts?.target);target.dataset.known=String(Number.isInteger(counts?.target)&&counts.target>=0);target.title=goalSummary(item);
 gap.textContent=knownCount(counts?.gap);gap.dataset.known=String(Number.isInteger(counts?.gap)&&counts.gap>=0);gap.dataset.gap=Number.isInteger(counts?.gap)&&counts.gap>0?'positive':'other';gap.title=item.error?'状态读取失败，显示上次结果':`有效 ${knownCount(counts?.accepted)} · 待复核 ${knownCount(counts?.pending_review)}`;
 const badge=row.querySelector('.state');badge.dataset.state=item.progress?.run_status||item.run_status;badge.textContent=runStatusLabel(item);
 const action=row.querySelector('.row-action');action.replaceChildren();renderNextAction(action,item);
}
function render(){
 updateTaskPager();
 const body=$('line-rows');body.replaceChildren();rowEls=new Map();updateOverviewSummary();let shown=0;
 for(const item of rows){if(!visible(item))continue;shown++;const row=document.createElement('tr');row.classList.toggle('selected',item.task_id===selected);rowEls.set(item.task_id,row);
 const idCell=cell(row),id=document.createElement('span');id.className='id request-number';id.textContent=requestNumber(item);id.title='完整 ID：'+item.task_id;idCell.append(id);
 const nameCell=cell(row),name=document.createElement('span');name.className='name';name.textContent=item.title;name.title=item.title;const created=document.createElement('span');created.className='created-inline';created.textContent=date(item.created_at);nameCell.append(name,created);
 const stateCell=cell(row),badge=document.createElement('span');badge.className='state';badge.dataset.state=item.run_status;badge.textContent=runStatusLabel(item);stateCell.append(badge);
 const nodesCell=cell(row),strip=document.createElement('div');strip.className='nodes';strip.setAttribute('aria-label','生产节点');for(const key of Object.keys(nodeNames)){const node=document.createElement('a');node.href=nodeURL(item.task_id,key);node.className='node';node.dataset.node=key;node.textContent=nodeNames[key];strip.append(node)}nodesCell.append(strip);
 cell(row).className='target-count';cell(row).className='gap-count';cell(row,date(item.created_at));const action=cell(row);action.className='row-action';body.append(row);updateRow(item)
 }
 if(!shown){const row=document.createElement('tr'),empty=cell(row,rows.length?'没有符合条件的线路':'暂无已确认线路');empty.colSpan=8;body.append(row)}
}
function stat(box,label,value){const el=document.createElement('div'),number=document.createElement('b'),caption=document.createElement('span');el.className='stat';number.textContent=value??'未知';caption.textContent=label;el.append(number,caption);box.append(el)}
function showDetail(item){
 if(item.task_id!==selected)return;
 const progress=item.progress,body=$('detail-body');$('detail-title').textContent=item.title;$('detail-id').textContent='';body.replaceChildren();
 if(!progress){$('detail-status').textContent=item.error?'线路状态读取失败：'+item.error:'正在读取节点状态…';return}
 $('detail-status').textContent='';
 const summary=document.createElement('div');summary.className='task-summary';summary.setAttribute('aria-label','任务摘要');
 const status=document.createElement('span');status.className='state';status.dataset.state=progress.run_status||item.run_status;status.textContent=runStatusLabel(item);summary.append(status);
 for(const [label,value] of [['目标',progress.counts.target],['有效',progress.counts.accepted],['缺口',progress.counts.gap]]){const part=document.createElement('span');part.className='summary-count';part.textContent=label+' '+knownCount(value);summary.append(part)}
 const info=document.createElement('details');info.className='task-runtime';const caption=document.createElement('summary');caption.textContent='运行信息';info.append(caption);
 const facts=document.createElement('div');facts.className='runtime-facts';info.append(facts);
 for(const text of ['需求编号 '+requestNumber(item),'完整 ID：'+item.task_id,`正式版本 ${progress.revision} · generation ${progress.run_generation}`,'创建于 '+date(item.created_at)]){const line=document.createElement('p');line.textContent=text;facts.append(line)}
 for(const node of progress.nodes){const line=document.createElement('p');line.textContent=`${node.label} · ${node.mode==='manual'?'人工确认':'自动推进'} · ${node.detail||'暂无记录'}`;facts.append(line)}
 body.append(summary,info);
 const nodes=document.createElement('nav');nodes.className='detail-nodes';nodes.setAttribute('aria-label','生产节点导航');
 for(const [index,key] of Object.keys(nodeNames).entries()){
  const node=progress.nodes.find(part=>part.node===key)||{node:key,label:nodeNames[key],node_status:'pending',detail:''};
  const box=document.createElement('a'),badge=document.createElement('span'),title=document.createElement('strong');box.className='detail-node';box.dataset.node=node.node;box.dataset.status=node.node_status;box.href=nodeURL(item.task_id,node.node);
  const stopped=node.node==='refill'&&node.detail.includes('自动补数已停止');badge.className='node-status';badge.textContent=stopped?'已停止':node.gate_status==='awaiting_approval'?'待确认':(node.node_status==='running'&&progress.run_status==='PLANNING')?'规划中':(({succeeded:'已通过',running:'进行中',failed:'失败',cancelled:'已取消'})[node.node_status]||'待开始');
  title.textContent=`${index+1} ${node.node==='packaging'?'交付':nodeNames[key]}`;box.title=node.detail||badge.textContent;box.append(title,badge);nodes.append(box)
 }
 body.append(nodes);
}
async function loadTaskPage(epoch,cursor,replaceRows=false,commit=()=>{}){
 const query=new URLSearchParams({limit:'20'});if(cursor)query.set('cursor',cursor);
 const tasks=await api('/v1/retrieval/projects/demo/tasks?'+query);
 if(epoch!==listEpoch)return;
 const previous=new Map(rows.map(item=>[item.task_id,item]));
 const incoming=tasks.items.map(item=>({...item,title:item.raw_request||item.task_id,progress:item.progress||(previous.get(item.task_id)?.progress?.task_version===item.task_version?previous.get(item.task_id).progress:undefined)}));
 rows=replaceRows?incoming:[...rows,...incoming.filter(item=>!previous.has(item.task_id))];
 nextTaskCursor=tasks.next_cursor;commit();render();
 const missing=incoming.filter(item=>!item.progress);let index=0;async function worker(){while(index<missing.length&&epoch===listEpoch){const item=rows.find(row=>row.task_id===missing[index++].task_id);if(!item)continue;try{item.progress=await api(`/v1/retrieval/tasks/${encodeURIComponent(item.task_id)}/progress`);item.error=null}catch(error){item.error=error.message}if(epoch!==listEpoch)return;updateRow(item);if(item.task_id===selected)showDetail(item)}}
 await Promise.all([worker(),worker(),worker()]);
}
async function load(){const epoch=++listEpoch;taskPaging.busy=true;updateTaskPager();$('line-status').textContent='正在读取线路清单…';try{await loadTaskPage(epoch,null,true,()=>{taskPaging.index=0;taskPaging.cursors=[null]});if(epoch!==listEpoch)return;if(selected){let item=rows.find(row=>row.task_id===selected);if(!item){try{const task=await api(`/v1/retrieval/tasks/${encodeURIComponent(selected)}`);if(epoch!==listEpoch)return;item={...task,title:task.raw_request||task.task_id};rows.unshift(item);render()}catch{selected=null;$('line-detail').classList.add('hidden')}}if(item)selectLine(selected)}$('line-status').textContent=''}catch(error){if(epoch!==listEpoch)return;$('line-status').textContent='线路清单读取失败：'+error.message;for(const item of rows)item.error='清单刷新失败，状态可能滞后';for(const item of rows)updateRow(item)}finally{if(epoch===listEpoch){taskPaging.busy=false;updateTaskPager()}}}
async function moveTaskPage(delta){
 if(taskPaging.busy)return;const target=taskPaging.index+delta;if(target<0||delta>0&&!nextTaskCursor)return;
 const cursor=delta>0?nextTaskCursor:taskPaging.cursors[target],epoch=++listEpoch;taskPaging.busy=true;updateTaskPager();$('line-status').textContent='';
 try{await loadTaskPage(epoch,cursor,true,()=>{taskPaging.index=target;taskPaging.cursors[target]=cursor;taskPaging.cursors.length=target+1});if(epoch===listEpoch){$('line-status').textContent='';$('line-rows').closest('.table-wrap')?.scrollTo({top:0})}}
 catch(error){if(epoch===listEpoch)$('line-status').textContent='翻页失败，保留当前页：'+error.message}
 finally{if(epoch===listEpoch){taskPaging.busy=false;updateTaskPager()}}
}
$('line-search').oninput=render;$('line-filter').onchange=render;$('line-node-filter').onchange=render;$('line-target-min').oninput=render;$('line-gap-filter').onchange=render;$('line-prev').onclick=()=>moveTaskPage(-1);$('line-next').onclick=()=>moveTaskPage(1);
$('detail-close').onclick=()=>{selected=null;detailEpoch++;$('line-detail').classList.add('hidden');history.replaceState(null,'','/production');render()};
'''

from .production_cleaning_ui import CLEANING_HTML, CLEANING_CSS, CLEANING_JS

BOARD_HTML = BOARD_HTML.replace('<div id="detail-body"></div></section>', '<div id="detail-body"></div>' + CLEANING_HTML + '</section>')
BOARD_HTML = BOARD_HTML.replace('<button id="detail-close"', '<a id="detail-chat" href="/chat">继续此 Chat</a><button id="detail-close"')
BOARD_CSS += CLEANING_CSS
BOARD_JS += CLEANING_JS

from .production_outputs_ui import OUTPUT_CSS, OUTPUT_JS

# Keep the overview and bookmarkable task output in separate navigations.
BOARD_CSS += OUTPUT_CSS
BOARD_JS += OUTPUT_JS

from .production_experience_ui import EXPERIENCE_HTML, EXPERIENCE_CSS, EXPERIENCE_JS

BOARD_HTML = BOARD_HTML.replace('</main>', EXPERIENCE_HTML + '</main>')
BOARD_CSS += EXPERIENCE_CSS
BOARD_JS += EXPERIENCE_JS + '\ninitializeAssetViewer();\nstartProduction();\n'

from .ui_typography import TYPOGRAPHY_CSS, PRODUCTION_TYPOGRAPHY_CSS

BOARD_CSS += TYPOGRAPHY_CSS + PRODUCTION_TYPOGRAPHY_CSS
