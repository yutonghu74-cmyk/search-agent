"""Dependency-free local Chat preview assets. Never embed credentials or production endpoints."""

CHAT_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DSH 检索需求体验</title><link rel="stylesheet" href="/chat.css"></head>
<body><a class="skip-link" href="#main-content">跳到主要内容</a><main id="main-content">
  <nav class="topbar" aria-label="主导航"><a class="brand" href="/" aria-label="DSH 检索需求首页"><span class="brand-mark" aria-hidden="true">D</span><span>DSH Retrieval</span></a><a class="nav-link" href="/catalog">已有索引配置 <span aria-hidden="true">↗</span></a></nav>
  <div class="context-bar" aria-label="当前项目上下文"><span class="status-dot" aria-hidden="true"></span><span>项目 <strong>demo</strong></span><span class="project-divider" aria-hidden="true"></span><span>子维度 <strong>default</strong></span></div>
  <div class="app-shell"><aside class="history panel" aria-labelledby="history-heading"><div class="history-title"><h2 id="history-heading">历史 Chat</h2><button class="icon-button" id="refresh-history" aria-label="刷新历史 Chat" title="刷新历史 Chat">↻</button></div><div id="history-list" aria-live="polite"></div></aside>
  <div class="chat">
    <section class="conversation" aria-labelledby="conversation-heading"><div class="section-heading conversation-heading"><div><span class="section-kicker">CONVERSATION</span><h2 id="conversation-heading">对话</h2></div><span class="status-badge">先沟通，再检索</span></div><div id="timeline" class="timeline" role="log" aria-live="polite" aria-relevant="additions"><article class="message system"><h2>新建数据需求</h2><p>告诉我想找什么；不清楚的地方我会先问，确认检索词后才开始。</p></article></div></section>
    <section class="chat-composer panel" id="new-request" aria-labelledby="request-heading"><h2 class="sr-only" id="request-heading">与检索 Agent 对话</h2><label class="sr-only" for="request-text">输入消息</label><textarea id="request-text" rows="3" placeholder="直接说想找什么，例如：找 20 张狗图片，横图，JPG 格式" required></textarea><div class="chat-composer-footer"><button id="insert-request-example" class="secondary small" type="button">参考说法</button><details class="advanced-request"><summary>更多设置</summary><div class="advanced-fields"><div class="field-grid"><div class="field"><label for="group-parent">上级分类 <span class="optional">可选</span></label><input id="group-parent" placeholder="例如：动物"></div><div class="field"><label for="target-count">目标数量 <span class="optional">也可在对话中说明</span></label><input id="target-count" type="number" min="1" max="10000" placeholder="例如：100"></div></div><div class="field"><label for="group-lines">交付分组 <span class="optional">可选，每行一组</span></label><textarea id="group-lines" rows="3" placeholder="狗 | 金毛，柯基&#10;猫 | 橘猫，布偶猫"></textarea></div></div></details><span id="composer-model-note" class="helper">正在检查模型…</span><button id="prepare-task" aria-label="发送消息">发送 <span aria-hidden="true">↑</span></button></div></section>
    <section class="chat-composer panel hidden" id="feedback-box" aria-labelledby="feedback-heading"><h2 class="sr-only" id="feedback-heading">继续对话</h2><label class="sr-only" for="feedback-text">输入消息</label><textarea id="feedback-text" rows="3" placeholder="输入消息"></textarea><span id="feedback-model-note" class="hidden" aria-hidden="true"></span><div class="chat-composer-footer"><details class="advanced-request search-diagnostics"><summary>检索工具</summary><div class="preview-actions" aria-label="检索工具"><button class="secondary" id="preview-branches">查看查询分支（未执行）</button><button class="secondary" id="preview-routing">查看选库结果（未执行）</button><button class="secondary" id="first-search">本地首次检索（需确认）</button><button class="secondary" id="first-search-candidates">查看原始候选</button></div></details><button class="secondary small" id="new-task">新建 Chat</button><button id="prepare-feedback" aria-label="发送消息">发送 <span aria-hidden="true">↑</span></button></div></section>
    <p id="status" role="status" aria-live="polite"></p>
  </div>
  <aside class="right-rail" aria-label="Chat 操作与模型设置"><button class="new-chat-button" id="new-chat" type="button"><span aria-hidden="true">＋</span><span>新建 Chat</span><kbd>⌘ K</kbd></button>
    <section class="rail-card hidden" id="task-sidebar" aria-labelledby="task-sidebar-heading"><div class="rail-heading"><div><span class="section-kicker">TASK</span><h2 id="task-sidebar-heading">需求进度</h2></div></div><div class="task-identity"><span>需求 ID</span><code id="task-sidebar-id"></code></div><p id="task-sidebar-summary" class="helper"></p><div id="task-sidebar-nodes" class="progress-nodes" aria-label="六节点进度"></div><p id="retrieval-live-status" class="helper" role="status"></p></section>
    <section class="rail-card model-config" aria-labelledby="model-heading"><div class="rail-heading"><div><span class="section-kicker">MODELS</span><h2 id="model-heading">模型设置</h2></div><span id="chat-model-badge" class="status-badge neutral">正在连接</span></div>
      <div class="field compact-field"><label for="llm-model">DSH 需求理解 / 扩词模型 <small>LLM</small></label><select id="llm-model"><option value="">模板 / 手动编辑（不调用模型）</option></select></div>
      <div class="field compact-field"><label for="vlm-model">清洗语义判定模型 <small>VLM</small></label><select id="vlm-model"><option value="">未选择</option></select></div>
      <p id="model-status" class="helper">正在加载可用模型…</p>
      <details class="api-config"><summary>使用 API 与密钥配置</summary><div class="api-fields">
        <div class="field"><label for="api-provider">接口类型</label><select id="api-provider"><option value="openai-compatible">OpenAI Compatible</option><option value="custom">自定义 API</option></select></div>
        <div class="field"><label for="api-base-url">API 地址</label><input id="api-base-url" type="url" placeholder="api.example.com/v1" autocomplete="url" spellcheck="false"></div>
        <div class="field"><label for="api-key">API 密钥</label><input id="api-key" type="password" placeholder="仅保留在当前页面会话" autocomplete="new-password" spellcheck="false"><p class="helper">密钥不写入浏览器存储；此处仅暂存页面信息，不启用后端模型。</p></div>
        <div class="field-grid"><div class="field"><label for="custom-llm-model">LLM 模型名</label><input id="custom-llm-model" placeholder="model-id" spellcheck="false"></div><div class="field"><label for="custom-vlm-model">VLM 模型名</label><input id="custom-vlm-model" placeholder="model-id" spellcheck="false"></div></div>
        <button id="save-api-config" type="button">暂存连接</button><p id="api-config-status" class="helper" role="status">此处仅暂存页面信息，不启用后端模型；实际调用使用上方服务端模型。</p>
      </div></details>
    </section>
    <details class="rail-card"><summary>体验环境说明</summary><p class="helper">当前使用模拟身份和本地账本。测试索引检索不等于真实素材授权或清洗通过；模型建议在确认前不会执行。</p></details>
  </aside></div>
</main><script src="/chat.js"></script></body></html>"""

CHAT_CSS = """
:root{font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;color:#202421;background:#f7f7f5;line-height:1.5;--ink:#202421;--muted:#6f756f;--line:#dedfdb;--panel:#fff;--green:#246646;--green-2:#347b55;--mint:#edf7f0;--shadow:0 10px 32px #1c2c2210}*{box-sizing:border-box}
body{margin:0;background:#f7f7f5}main{max-width:1500px;margin:auto;min-height:100vh;padding:16px 22px 64px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.skip-link{position:fixed;top:10px;left:10px;z-index:20;transform:translateY(-160%);background:#202421;color:white;padding:10px 14px;border-radius:8px}.skip-link:focus{transform:none}.topbar{display:flex;align-items:center;justify-content:space-between;height:44px}.brand{display:flex;align-items:center;gap:10px;color:var(--ink);font-weight:750;text-decoration:none}.brand-mark{display:grid;place-items:center;width:30px;height:30px;border-radius:8px;background:#202421;color:white;font-family:Georgia,serif}.nav-link{color:#4c554f;text-decoration:none;font-size:13px;font-weight:650;padding:8px 10px;border-radius:8px}.nav-link:hover{background:#eceeea}.context-bar{display:flex;align-items:center;gap:9px;width:max-content;margin:8px auto 14px;background:white;border:1px solid var(--line);border-radius:10px;padding:7px 11px;font-size:12px}.context-note{color:var(--muted);padding-left:9px;border-left:1px solid var(--line)}.status-dot{flex:0 0 auto;width:7px;height:7px;border-radius:50%;background:#43a56e;box-shadow:0 0 0 3px #dff1e5}.project-divider{width:1px;height:14px;background:var(--line)}
.app-shell{display:grid;grid-template-columns:240px minmax(460px,1fr) 300px;gap:16px;align-items:start}.panel,.rail-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}.history,.right-rail{position:sticky;top:14px;max-height:calc(100vh - 28px)}.history{padding:12px;overflow:auto}.history-title,.section-heading,.rail-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.history h2,.section-heading h2,.rail-heading h2{font-size:16px;line-height:1.25;margin:2px 0}.section-kicker{font-size:10px;font-weight:750;letter-spacing:.14em;color:#668071}.helper{font-size:11px;color:var(--muted);margin:6px 0}.history-section{font-size:10px;font-weight:750;letter-spacing:.1em;color:#7a817c;margin:16px 6px 5px;padding-top:10px;border-top:1px solid #ededea}.icon-button{display:grid;place-items:center;width:30px;height:30px;padding:0;border-radius:8px;background:#f0f1ee;color:#465049;font-size:16px}.history-entry{display:grid;grid-template-columns:minmax(0,1fr) 30px;gap:3px;align-items:center;margin-top:4px}.history-item{display:block;width:100%;min-width:0;text-align:left;background:transparent;color:#303631;border:1px solid transparent;border-radius:9px;padding:9px;box-shadow:none}.history-item:hover,.rename-chat:hover{background:#f1f3ef}.history-item.active{background:#e9eee9;border-color:#d8ded9}.history-item span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.history-item small{display:block;color:var(--muted);margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.rename-chat{display:grid;place-items:center;width:28px;height:28px;padding:0;border:0;background:transparent;color:#777f79}.empty{font-size:12px;color:var(--muted);padding:10px 7px}.chat{display:grid;gap:14px;min-width:0}
.conversation-heading{padding:2px 4px}.timeline{display:grid;gap:12px;margin-top:10px;padding:16px;border:1px solid var(--line);border-radius:14px;background:#f0f1ee;min-height:180px}.message{max-width:88%;padding:14px 16px;border:1px solid #e0e2de;border-radius:14px;background:white;box-shadow:0 5px 18px #33483b0a}.message.system{border-top-left-radius:4px}.message.assistant{border-left:3px solid #4f956b;background:#f8fcf9;border-top-left-radius:4px}.message.user{justify-self:end;background:#2f5945;color:white;border-color:#2f5945;border-top-right-radius:4px}.message h2{font-size:14px;margin:0 0 6px}.message p{font-size:13px;margin:0;white-space:pre-wrap}.analysis-details{margin-top:10px;border-top:1px solid #dce9df;padding-top:8px;font-size:12px}.analysis-details summary{cursor:pointer;color:#355a42;font-weight:700}.analysis-details ul{margin:8px 0 0;padding-left:18px}.analysis-details li{margin:5px 0;word-break:break-word}.card{border-left:3px solid #65a47c}.card dl{display:grid;grid-template-columns:118px minmax(0,1fr);gap:7px 12px;margin:10px 0 0;font-size:12px}.card dt{color:var(--muted)}.card dd{margin:0;word-break:break-word}.actions{display:flex;flex-wrap:wrap;gap:7px;margin-top:14px}
.composer{padding:18px;display:grid;gap:14px}.chat-composer{padding:12px;background:white}.chat-composer>textarea{border:0;box-shadow:none!important;min-height:74px;padding:8px}.chat-composer-footer{display:flex;align-items:end;justify-content:space-between;gap:10px;border-top:1px solid #eceeeb;padding-top:9px}.advanced-request{flex:1}.advanced-request summary{width:max-content;cursor:pointer;color:#5f6862;font-size:12px;font-weight:650;padding:6px 2px}.advanced-fields{display:grid;gap:10px;padding:12px 0 4px}.chat-composer-footer>button{border-radius:999px;padding:9px 14px}.field-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px}.field{display:grid;gap:5px}.field label{font-size:12px;font-weight:700;color:#3b443e}.field label small{color:var(--muted);font-weight:600}.optional{color:#7a817c;font-weight:500;margin-left:4px}.form-footer,.preview-actions{display:flex;align-items:center;justify-content:space-between;gap:10px}.preview-actions{justify-content:flex-start}.status-badge{display:inline-flex;align-items:center;border-radius:999px;padding:4px 8px;background:#e0f0e5;color:#245b3e;font-size:10px;font-weight:750;white-space:nowrap}.status-badge.neutral{background:#eef0ed;color:#69716c}
.search-diagnostics{border-top:1px solid #eceeeb;padding-top:8px}.chat-composer-footer .search-diagnostics{border-top:0;padding-top:0}.search-diagnostics summary{cursor:pointer;color:#6f756f;font-size:12px}.search-diagnostics .preview-actions{flex-wrap:wrap;justify-content:flex-start;margin-top:10px}
.search-choice{display:flex;align-items:center;gap:9px;margin-top:8px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:12px;cursor:pointer}.search-choice:focus-within{outline:2px solid var(--green);outline-offset:2px}.search-choice input{accent-color:var(--green)}
.progress-nodes{display:grid;grid-template-columns:1fr;gap:8px;padding:4px 1px 8px}.node-card{position:relative;background:white;border:1px solid #dce1dc;border-radius:12px;min-width:0;overflow:visible}.node-card.search-active{border-color:#78b18b;box-shadow:0 0 0 2px #d9eddf}.node-card.search-unavailable{border-color:#dfbd75;box-shadow:0 0 0 2px #f8ecd0}.node-card summary,.node-card .node-heading{display:grid;grid-template-columns:28px minmax(0,1fr) auto;align-items:center;gap:8px;padding:11px;list-style:none}.node-card summary{cursor:pointer}.node-card summary::-webkit-details-marker{display:none}.node-index{display:grid;place-items:center;width:26px;height:26px;border-radius:8px;background:var(--mint);color:var(--green);font-size:11px;font-weight:800}.node-title{min-width:0;font-size:12px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.node-status{font-size:11px;font-weight:750;color:#496257;background:#eef3ef;padding:3px 7px;border-radius:999px;white-space:nowrap}.node-body{border-top:1px solid #edf0ed;padding:10px;font-size:11px;color:var(--muted)}.node-body p{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.node-body p+p{margin-top:8px}.node-terms{max-height:340px;overflow:auto}.node-term-group{padding:7px 0;border-top:1px solid #edf0ed}.node-term-group:first-child{border-top:0;padding-top:0}.node-term-group strong{display:block;color:var(--ink);font-size:11px;margin-bottom:3px}.node-candidates{max-height:340px;overflow:auto;margin-top:8px}.node-candidates .search-hit{padding:8px 0}
.right-rail{display:grid;gap:10px;overflow:auto}.task-identity{display:grid;gap:3px;font-size:11px;color:var(--muted)}.task-identity code{color:var(--ink);font-size:11px;overflow-wrap:anywhere}.new-chat-button{display:flex;align-items:center;gap:8px;width:100%;padding:11px 12px;background:#202421;box-shadow:0 6px 18px #17211d18}.new-chat-button span:nth-child(2){flex:1;text-align:left}.new-chat-button kbd{font:600 10px inherit;color:#c8cec9;border:1px solid #626963;border-radius:5px;padding:2px 5px}.rail-card{padding:14px;display:grid;gap:12px}.rail-heading{margin-bottom:2px}.compact-field select{font-size:12px}.api-config{border-top:1px solid #eceeeb;padding-top:10px}.api-config summary{cursor:pointer;font-size:12px;font-weight:700;color:#3f4b43}.api-fields{display:grid;gap:10px;padding-top:12px}.rail-note{display:flex;gap:10px;padding:12px;color:#5f6862;background:#f0f2ef;border-radius:12px}.rail-note strong{font-size:12px}.rail-note p{font-size:11px;margin:2px 0 0}.rail-note .status-dot{margin-top:5px}
textarea,input,select{width:100%;font:inherit;color:var(--ink);border:1px solid #c7ccc7;border-radius:9px;padding:9px 10px;background:white;outline:none;transition:border-color .15s,box-shadow .15s}textarea{resize:vertical;min-height:48px}textarea:hover,input:hover,select:hover{border-color:#98a39b}textarea:focus,input:focus,select:focus{border-color:var(--green-2);box-shadow:0 0 0 3px #347b5520}button{font:650 13px inherit;border:1px solid transparent;border-radius:9px;padding:9px 13px;background:var(--green);color:white;cursor:pointer;transition:transform .12s,background .12s}button:hover:not(:disabled){background:#1d5239;transform:translateY(-1px)}button.secondary{background:#edf1ed;color:#33453a;border-color:#dbe1dc}button.small{padding:6px 9px;font-size:11px}button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #e4b83f;outline-offset:2px}button:disabled{opacity:.55;cursor:wait;transform:none}.hidden{display:none!important}#status{position:sticky;bottom:10px;z-index:4;color:#7f3026;background:#fff7f5;border-radius:9px;min-height:0;margin:0;padding:0 10px}#status:not(:empty){padding:8px 10px;border:1px solid #ecc7c1;box-shadow:0 8px 24px #6d2d2515}pre{white-space:pre-wrap;word-break:break-word;font-size:12px;background:#f4f6f3;padding:12px;border-radius:10px;border:1px solid #e1e5e1}.catalog-page{max-width:1000px}.catalog-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px}.catalog-grid .wide{grid-column:1/-1}
@media(max-width:1180px){main{padding-inline:12px}.app-shell{grid-template-columns:170px minmax(0,1fr) 220px;gap:10px}.right-rail{grid-template-columns:1fr}.rail-note{display:none}.history{padding:9px}.history-entry{grid-template-columns:minmax(0,1fr) 26px}.history-item{padding:8px 6px}.rename-chat{width:26px}.rail-card{padding:11px}}
@media(max-width:680px){main{padding:14px 14px 56px}.app-shell{grid-template-columns:1fr}.chat{grid-column:1;order:1}.right-rail{position:static;max-height:none;grid-column:1;order:2;grid-template-columns:1fr}.history{position:static;max-height:none;grid-column:1;order:3}.context-bar{width:100%;justify-content:center}.context-note{display:none}.progress-nodes{grid-template-columns:1fr;overflow:visible}.node-card:not(:last-child)::after{display:none}}
@media(max-width:560px){.field-grid,.catalog-grid{grid-template-columns:1fr}.catalog-grid .wide{grid-column:auto}.message{max-width:100%}.timeline{padding:10px}.card dl{grid-template-columns:1fr;gap:2px}.card dd{margin-bottom:6px}.form-footer,.preview-actions{align-items:stretch;flex-direction:column}.form-footer button,.preview-actions button{width:100%}.section-heading{align-items:flex-start}.composer{padding:15px}.brand span:last-child{display:none}}
.advanced-settings{border-top:1px solid var(--line);padding-top:8px}.advanced-settings summary{cursor:pointer;color:var(--muted);font-size:12px}.advanced-settings>div{margin-top:10px}.message.assistant .actions{margin-top:12px}.message.assistant textarea{margin-top:10px;min-height:105px;font-size:12px}.search-hit{display:grid;gap:5px;padding:9px 0;border-top:1px solid #dce9df;font-size:12px}.search-hit strong{font-size:12px}.search-hit small{color:var(--muted)}.search-tags{display:flex;flex-wrap:wrap;gap:5px}.search-tag{display:inline-flex;max-width:100%;overflow-wrap:anywhere;padding:2px 7px;border:1px solid #d4e5d8;border-radius:999px;background:#eaf5ec;color:#245b3e;font-size:11px}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
"""

CHAT_JS = r"""
const state={task:null,draft:null,feedback:null,feedbackReviewBubble:null,feedbackManualCard:null,expansionPreviewBubble:null,expansionPreviewToken:null,expansionPreviewPlan:null,confirmedRevision:null,conversationId:null,resumeDraft:null,connection:null,firstSearchPreview:null,firstSearchProposal:null,firstSearchChoices:[],searchAttempt:null,targetCountTouched:false,modelChoiceTouched:false,proposedExpansion:null,confirmedCount:null,guidedResultBubble:null,guidedPhase:'input',dialogueId:null,dialogueVersion:0,dialogueStage:'request'};
const headers={'Content-Type':'application/json','X-Tenant-Id':'tenant-a','X-User-Id':'user-a'};
const $=id=>document.getElementById(id); const timeline=$('timeline');
const key=prefix=>prefix+'-'+crypto.randomUUID();
async function api(path,{method='GET',body,keyValue}={}){const h={...headers};if(keyValue)h['Idempotency-Key']=keyValue;const r=await fetch(path,{method,headers:h,body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok){const error=new Error(`${data.code||r.status}: ${data.message||'请求失败'}`);error.code=data.code||String(r.status);error.detail=data.message||'请求失败';throw error}return data}
function message(kind,title,text){const a=document.createElement('article');a.className='message '+kind;if(title){const h=document.createElement('h2');h.textContent=title;a.append(h)}const p=document.createElement('p');p.textContent=text;a.append(p);timeline.append(a);a.scrollIntoView({behavior:'smooth',block:'end'});return a}
function card(title,rows,actions=[]){const a=document.createElement('article');a.className='message card';const h=document.createElement('h2');h.textContent=title;a.append(h);const dl=document.createElement('dl');for(const [name,value] of rows){const dt=document.createElement('dt');dt.textContent=name;const dd=document.createElement('dd');dd.textContent=String(value);dl.append(dt,dd)}a.append(dl);if(actions.length){const box=document.createElement('div');box.className='actions';for(const [label,fn,secondary] of actions){const b=document.createElement('button');b.textContent=label;if(secondary)b.className='secondary';b.addEventListener('click',fn);box.append(b)}a.append(box)}timeline.append(a);a.scrollIntoView({behavior:'smooth',block:'end'});return a}
function draftBody(text,count,groupedExpansion){const query={schema_version:'query-spec@2',raw_request:text,media_type:'image',target_count:count,business_domain:'local_preview',intra_batch_dedup:{policy_ref:'exact-content',config_version:'1'},cross_batch_dedup:{scope:'none',references:[],comparison_methods:[],reference_digest:null},refill_policy:{mode:'input_only',target_count:count,approved_library_ids:[],progression:'manual'}};if(groupedExpansion)query.grouped_expansion=groupedExpansion;return{dimension_id:'default',query_spec:query,node_modes:{expansion:'manual',retrieval:'manual',cleaning:'manual',refill:'manual',packaging:'manual'}}}

function groupPreviewBody(text,count){const body=groupInputBody(text),llm=$('llm-model')?.value,vlm=$('vlm-model')?.value;if(llm&&vlm){body.model_selection={llm,vlm};if(body.raw_request)body.target_count=count}return body}
function groupInputBody(text,lines=$('group-lines').value.trim()){if(!lines)return{raw_request:text,dimension_id:'default'};const groups=lines.split(/\n/).filter(line=>line.trim()).map(line=>{const parts=line.split('|');if(parts.length>2)throw new Error('每行仅使用一个 | 分隔主标签和召回词');return{primary_label:parts[0].trim(),auxiliary_terms:(parts[1]||'').split(/[，,、]/).map(t=>t.trim()).filter(Boolean)}});return{dimension_id:'default',parent_label:$('group-parent').value.trim()||null,groups}}
function editProposal(){if(!state.draft||state.task)return;state.draft=null;timeline.replaceChildren();$('new-request').classList.remove('hidden');$('request-text').focus()}
function retireDraftActions(){state.draft=null;timeline.querySelectorAll('.actions').forEach(box=>box.remove())}
function showSavedGroups(plan){if(!plan)return;if(plan.model_binding)card('已确认模型选择',[['扩词 LLM',plan.model_binding.llm],['清洗 VLM',plan.model_binding.vlm],['配置摘要',plan.model_binding.config_digest]]);for(const group of plan.groups){card('已确认分组 · '+group.primary_label,[['上级分类',plan.parent_label||'无'],['召回词',group.auxiliary_terms.join('、')||'仅使用主标签'],['交付设置',group.package_name+' · 独立包（待执行）'],['词来源',plan.generator==='llm@1'?'LLM 建议':plan.generator==='user-edited@1'?'用户编辑':'模板建议']])}}

async function previewBranches(){if(!state.task)return;setBusy(true);try{const data=await api(`/v1/retrieval/tasks/${state.task.task_id}/query-branches`);card('查询分支预览',[['绑定',`revision ${data.revision} · generation ${data.run_generation} · task version ${data.task_version}`],['分支数量',data.preview.branches.length],['预览摘要',data.preview.preview_digest],['状态','未选库 · 未批准 · 未执行']]);for(const branch of data.preview.branches){card('查询 · '+branch.query_role,[['检索文本',branch.query_text],['来源',branch.origins.map(o=>`${o.query_role}: ${o.source_term} (${o.skill_version})`).join('；')],['约束引用',branch.constraint_refs.join('、')||'无结构化约束；原文条件不代表已编译规则'],['指纹',branch.query_fingerprint]])}message('system','执行边界',data.preview.note)}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
function setBusy(v){document.querySelectorAll('button,#llm-model,#vlm-model,#target-count').forEach(b=>b.disabled=v);const activity=typeof timeline==='undefined'?null:timeline;if(activity)activity.setAttribute('aria-busy',String(v));if(v)$('status').textContent=''}
async function previewRouting(){if(!state.task)return;const taskId=state.task.task_id;setBusy(true);try{const data=await api(`/v1/retrieval/tasks/${taskId}/query-routing`);if(state.task?.task_id!==taskId)return;card('选库结果 · 未批准',[['任务版本',`revision ${data.revision} · generation ${data.generation} · version ${data.task_version}`],['路由摘要',data.routing_digest],['配置摘要',data.catalog_config_digest||'无独立配置版本'],['执行边界',data.note]]);for(const route of data.routes){card('查询 · '+route.query_text,[['分支',route.branch_id],['可用索引',route.options.length?route.options.map(o=>`${o.library_id} / ${o.binding_id} · ${o.index_version}\n编码器 ${o.encoder_ref} · ${o.vector_dimension} 维 · ${o.metric} · 每批上限 ${o.max_batch_size}`).join('\n\n'):'无匹配的已授权索引或已注册查询能力；请检查索引配置和别名']])}}catch(e){$('status').textContent=e.message+'；请先在“已有索引配置”中确认配置，管理员还需登记查询能力。'}finally{setBusy(false)}}
async function previewFirstSearch(){if(!state.task)return;const taskId=state.task.task_id;setBusy(true);try{const data=await api(`/v1/retrieval/tasks/${taskId}/first-search`);if(state.task?.task_id!==taskId)return;state.firstSearchPreview=data;state.firstSearchProposal=null;state.firstSearchChoices=[];card('本地首次检索 · 选索引',[['模式','LOCAL_EXACT · 本地测试向量'],['计划状态','未批准、未执行'],['说明',data.note]]);for(const route of data.routes){const section=card('查询分支 · '+route.query_text,[['分支',route.branch_id],['可选索引',route.options.length]]);for(const option of route.options){const label=document.createElement('label');label.className='search-choice';const box=document.createElement('input');box.type='checkbox';box.setAttribute('aria-label',`选择 ${option.library_id} / ${option.binding_id}`);box.addEventListener('change',()=>{const key=route.branch_id+'|'+option.binding_id;state.firstSearchChoices=state.firstSearchChoices.filter(row=>row.key!==key);if(box.checked)state.firstSearchChoices.push({key,branch_id:route.branch_id,binding_id:option.binding_id,batch_size:Math.min(2,option.max_batch_size)})});const text=document.createElement('span');text.textContent=`${option.library_id} / ${option.binding_id} · ${option.index_version} · ${option.encoder_ref} · ${option.vector_dimension}维 · ${option.metric}`;label.append(box,text);section.append(label)}}card('下一步',[['说明','勾选一个或多个索引，再生成完整确认卡；当前不会发起检索。']],[['生成检索确认卡',prepareFirstSearch,false]])}catch(e){$('status').textContent=e.message+'；本地首次检索需要服务端显式启用测试夹具。'}finally{setBusy(false)}}
async function prepareFirstSearch(){const preview=state.firstSearchPreview;if(!state.task||!preview||!state.firstSearchChoices.length){$('status').textContent='请先选择至少一个索引';return}setBusy(true);try{const choices=state.firstSearchChoices.map(({branch_id,binding_id,batch_size})=>({branch_id,binding_id,batch_size}));const proposal=await api(`/v1/retrieval/tasks/${state.task.task_id}/first-search/prepare`,{method:'POST',body:{routing_digest:preview.routing_digest,choices}});state.firstSearchProposal=proposal;card('本地检索确认卡 · 尚未执行',[['分支与索引',proposal.members.map(m=>`${m.branch_id}: ${m.library_id} / ${m.binding_id} (${m.index_version})`).join('\n')],['每页数量',proposal.members.map(m=>`${m.binding_id}: ${m.batch_size}`).join('；')],['方案摘要',proposal.proposal_digest],['说明',proposal.note]],[['确认整组检索方案',confirmFirstSearch,false]])}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
async function confirmFirstSearch(){const proposal=state.firstSearchProposal;if(!state.task||!proposal)return;setBusy(true);try{const taskId=state.task.task_id;const confirmed=await api(`/v1/retrieval/tasks/${taskId}/first-search/confirm`,{method:'POST',keyValue:key('first-search-confirm'),body:{proposal_digest:proposal.proposal_digest,expected_task_version:proposal.expected_task_version,action:'confirm'}});state.task=await api(`/v1/retrieval/tasks/${taskId}`);card('检索方案已确认 · 未执行',[['摘要',confirmed.set_digest],['模式',confirmed.mode],['说明',confirmed.note]],[['执行下一批',runFirstSearch,false]])}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
function aggregateLocalCandidates(items){
  const groups=new Map();
  for(const item of items){
    // Only a resolved media unit may be folded across bindings. A missing or
    // conflicting mapping stays scoped to its physical index and vector ID.
    const mapped=item.status==='resolved'&&item.media_unit_id&&item.asset_version;
    const key=JSON.stringify(mapped?['media',item.media_unit_id,item.asset_version]:['vector',item.library_id,item.binding_id,item.index_version,typeof item.vector_id,item.vector_id,item.status]);
    let group=groups.get(key);
    if(!group){group={item,sources:[],indexes:[],vectors:[],rawHits:0,_sourceKeys:new Set(),_indexKeys:new Set(),_vectorKeys:new Set()};groups.set(key,group)}
    group.rawHits++;
    const sourceKey=JSON.stringify([item.branch_id,item.query_text,item.query_role,item.source_term]);
    if(!group._sourceKeys.has(sourceKey)){group._sourceKeys.add(sourceKey);group.sources.push({query_text:item.query_text,query_role:item.query_role,source_term:item.source_term})}
    const indexKey=JSON.stringify([item.library_id,item.binding_id,item.index_version]);
    if(!group._indexKeys.has(indexKey)){group._indexKeys.add(indexKey);group.indexes.push(item.library_id+'/'+item.binding_id+'（'+item.index_version+'）')}
    const vectorKey=JSON.stringify([item.binding_id,item.index_version,typeof item.vector_id,item.vector_id]);
    if(!group._vectorKeys.has(vectorKey)){group._vectorKeys.add(vectorKey);group.vectors.push(item.binding_id+':'+item.vector_id)}
  }
  return [...groups.values()];
}
function appendLocalCandidateGroups(bubble,items){
  const groups=aggregateLocalCandidates(items);
  for(const group of groups){
    const item=group.item,row=document.createElement('div');row.className='search-hit';
    const title=document.createElement('strong');title.textContent=item.status==='resolved'?'素材：'+item.media_unit_id+'（'+item.asset_version+'）':'未映射向量：'+item.binding_id+' / '+item.vector_id;
    const tags=document.createElement('div');tags.className='search-tags';tags.setAttribute('aria-label','命中检索词');for(const source of group.sources){const tag=document.createElement('span');tag.className='search-tag';tag.textContent=source.query_text;tags.append(tag)}
    const detail=document.createElement('small');detail.textContent='词来源：'+group.sources.map(source=>source.query_role+' / '+source.source_term).join('；')+' · 索引：'+group.indexes.join('、')+' · 向量：'+group.vectors.join('、')+' · 原始命中 '+group.rawHits+' 条 · 映射：'+item.status+' · 未接授权媒体网关';
    row.append(title,tags,detail);bubble.append(row);
  }
  return groups.length;
}
async function showFirstSearchCandidates(){if(!state.task)return;const data=await api(`/v1/retrieval/tasks/${state.task.task_id}/first-search/candidates`);const bubble=message('system','本地原始候选 · 非合格结果','原始向量命中 '+data.raw_hit_count+' 条；本次读取 '+data.returned+' 条。有映射素材按版本合并，缺映射向量按索引分开；清洗合格数量仍未知。');appendLocalCandidateGroups(bubble,data.items);if(data.truncated)message('system','候选展示上限','本次最多展示 200 条原始命中；未展示部分不等于无结果。')}
async function viewFirstSearchCandidates(){setBusy(true);try{await showFirstSearchCandidates()}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
async function runFirstSearch(){if(!state.task)return;setBusy(true);try{const taskId=state.task.task_id;const result=await api(`/v1/retrieval/tasks/${taskId}/first-search/tick`,{method:'POST',body:{max_requests:2}});card('本地检索批次',[['网络请求',result.network_attempts],['成员进度',result.members.map(m=>`${m.plan_id.slice(0,12)}: ${m.committed_pages} 页 / ${m.stop_reason||'可继续'}`).join('\n')],['说明',result.note]],result.pagination_stopped?[]:[['继续下一批',runFirstSearch,false]]);await showFirstSearchCandidates()}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
function resetTimeline(title='新建数据需求',text='说说你想找什么图片、需要多少张。'){state.guidedResultBubble=null;state.feedback=null;state.feedbackReviewBubble=null;state.feedbackManualCard=null;state.expansionPreviewBubble=null;state.expansionPreviewToken=null;state.expansionPreviewPlan=null;state.confirmedRevision=null;state.searchAttempt=null;state.proposedExpansion=null;state.targetCountTouched=false;timeline.replaceChildren();$('task-sidebar').classList.add('hidden');$('task-sidebar-nodes').replaceChildren();$('retrieval-live-status').textContent='';message('system',title,text)}
function savedChatNames(){try{return JSON.parse(localStorage.getItem('dsh-chat-names')||'{}')}catch{return{}}}
function renameChat(id,current){const next=prompt('重命名 Chat',current);if(next===null)return;const value=next.trim();if(!value)return;const names=savedChatNames();names[id]=value.slice(0,80);localStorage.setItem('dsh-chat-names',JSON.stringify(names));refreshHistory()}
function historyEntry({id,title,meta,active,onOpen}){const row=document.createElement('div');row.className='history-entry';const open=document.createElement('button');open.className='history-item'+(active?' active':'');const label=document.createElement('span');label.textContent=title;const detail=document.createElement('small');detail.textContent=meta;open.append(label,detail);open.addEventListener('click',onOpen);const rename=document.createElement('button');rename.className='rename-chat';rename.type='button';rename.textContent='✎';rename.title='重命名 '+title;rename.setAttribute('aria-label','重命名 '+title);rename.addEventListener('click',()=>renameChat(id,title));row.append(open,rename);return row}
async function refreshHistory(){const box=$('history-list');try{const [conversations,tasks]=await Promise.all([api('/v1/retrieval/projects/demo/conversations'),api('/v1/retrieval/projects/demo/tasks')]);const names=savedChatNames(),taskByConversation=new Map(tasks.items.filter(item=>item.conversation_id).map(item=>[item.conversation_id,item]));box.replaceChildren();const section=label=>{const h=document.createElement('div');h.className='history-section';h.textContent=label;box.append(h)};section('Chats');if(!conversations.items.length){const p=document.createElement('p');p.className='empty';p.textContent='还没有历史 Chat';box.append(p)}for(const item of conversations.items){const task=taskByConversation.get(item.conversation_id),fallback=task?.raw_request||`Chat ${item.conversation_id.slice(-6)}`;box.append(historyEntry({id:item.conversation_id,title:names[item.conversation_id]||fallback,meta:task?`revision ${task.confirmed_revision} · ${task.run_status}`:`${item.message_count} 条消息 · 草稿`,active:state.conversationId===item.conversation_id,onOpen:()=>openConversation(item.conversation_id)}))}const legacy=tasks.items.filter(item=>!item.conversation_id);if(legacy.length){section('早期需求');for(const item of legacy){box.append(historyEntry({id:item.task_id,title:names[item.task_id]||item.raw_request,meta:`revision ${item.confirmed_revision} · ${item.run_status}`,active:state.task?.task_id===item.task_id,onOpen:()=>openTask(item.task_id)}))}}}catch(e){box.replaceChildren();const p=document.createElement('p');p.className='empty';p.textContent='历史记录加载失败：'+e.message;box.append(p)}}
function fillDraftForm(draft){const query=draft.proposal.query_spec;$('request-text').value=query.raw_request;$('target-count').value=query.target_count;const grouped=query.grouped_expansion;$('group-parent').value=grouped?.parent_label||'';$('group-lines').value=grouped?.groups?.map(group=>group.primary_label+' | '+group.auxiliary_terms.join('，')).join('\n')||''}
async function openConversation(conversationId){setBusy(true);try{const data=await api(`/v1/retrieval/conversations/${conversationId}`);state.conversationId=conversationId;state.task=null;state.resumeDraft=null;resetTimeline('历史 Chat','可以在下方继续这段对话。草稿消息不会进入业务执行时间线。');for(const item of data.messages){message('user','',item.text)}for(const item of data.confirmations){card(`方案已确认 · revision ${item.revision}`,[['任务',item.task_id],['确认时间',item.confirmed_at]])}if(data.confirmations.length){const latest=data.confirmations[data.confirmations.length-1];const [task,progress,results]=await Promise.all([api(`/v1/retrieval/tasks/${latest.task_id}`),api(`/v1/retrieval/tasks/${latest.task_id}/progress`),api(`/v1/retrieval/tasks/${latest.task_id}/results?view=accepted&limit=20`)]);state.task=task;localStorage.setItem('dsh-task-id',task.task_id);showProgress(progress,results);showResults(results,task.task_id);$('new-request').classList.add('hidden');$('feedback-box').classList.remove('hidden');await refreshTaskSidebar();await offerExistingSearch(task.task_id)}else{const last=data.messages[data.messages.length-1],draft=await api(`/v1/retrieval/projects/demo/drafts/${last.draft_id}`);state.draft=draft;state.resumeDraft={draft_id:draft.draft_id,draft_version:draft.draft_version};fillDraftForm(draft);$('request-heading').textContent='继续这个 Chat';$('new-request').classList.remove('hidden');$('feedback-box').classList.add('hidden')}await refreshHistory()}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
function confirmedQuery(progress){const current=state.confirmedRevision;return current?.taskId===progress.task_id&&current.revision===progress.revision?current.proposal?.query_spec:null}
function nodeHeader(number,label,status,tag='summary'){const header=document.createElement(tag);const index=document.createElement('span');index.className='node-index';index.textContent=String(number).padStart(2,'0');const title=document.createElement('span');title.className='node-title';title.textContent=label;const badge=document.createElement('span');badge.className='node-status';badge.textContent=status;header.append(index,title,badge);return header}
function demandCard(progress){const query=confirmedQuery(progress);const card=document.createElement('details');card.className='node-card';card.append(nodeHeader(1,'诉求确认',query?'已完成':'进行中'));const body=document.createElement('div');body.className='node-body';const summary=document.createElement('p');const labels=query?.grouped_expansion?.groups?.map(group=>group.primary_label).filter(Boolean)||[];summary.textContent=query?`目标 ${query.target_count} 张 · 内容 ${labels.join('、')||'见诉求原文'}`:'正在读取已确认诉求…';body.append(summary);if(query?.raw_request){const original=document.createElement('p');original.textContent='诉求原文：'+query.raw_request;body.append(original)}card.append(body);return card}
function nodeCard(node,index,progress,results,searchProgress=null,candidates=null){
  const currentSearch=index===1&&searchProgress?.status==='approved'&&searchProgress.revision===progress.revision&&searchProgress.generation===progress.run_generation&&searchProgress.task_version===progress.task_version;
  const attempt=index===1&&state.searchAttempt?.taskId===progress.task_id&&state.searchAttempt.revision===progress.revision?state.searchAttempt:null;
  const statusText=currentSearch?(searchProgress.pagination_stopped?'已完成':'进行中'):attempt?.status==='unavailable'?'不可用':attempt?.status==='checking'?'进行中':index===0&&progress.revision>0&&node.node_status==='running'?'已完成':({pending:'未开始',running:'进行中',succeeded:'已完成',failed:'失败',cancelled:'已取消'}[node.node_status]||'未开始');
  const card=document.createElement(index<=1?'details':'div');card.className='node-card';
  if(currentSearch||attempt?.status==='checking')card.classList.add('search-active');else if(attempt?.status==='unavailable')card.classList.add('search-unavailable');
  const header=nodeHeader(index+2,node.label,statusText,index>1?'div':'summary');if(index>1)header.className='node-heading';card.append(header);
  if(index>1)return card;
  if(index===0){const body=document.createElement('div');body.className='node-body node-terms';const groups=confirmedQuery(progress)?.grouped_expansion?.groups||[];for(const group of groups){const row=document.createElement('div');row.className='node-term-group';const label=document.createElement('strong');label.textContent=group.primary_label;const terms=document.createElement('p');terms.textContent=group.auxiliary_terms?.join('、')||'仅主标签';row.append(label,terms);body.append(row)}if(!groups.length){const note=document.createElement('p');note.textContent='暂无已确认检索词。';body.append(note)}card.append(body);return card}
  const body=document.createElement('div');body.className='node-body';const note=document.createElement('p');
  note.textContent=currentSearch?`原始候选 ${candidates?.raw_hit_count??'读取中'} 条 · 未清洗`:attempt?.status==='unavailable'?attempt.reason:attempt?.status==='checking'?'正在检查授权索引…':'尚未开始检索。';body.append(note);
  if(currentSearch&&candidates){const list=document.createElement('div');list.className='node-candidates';list.setAttribute('aria-label','逐词检索结果');if(!appendLocalCandidateGroups(list,candidates.items)){const empty=document.createElement('p');empty.textContent='暂无原始候选。';list.append(empty)}body.append(list)}
  card.append(body);return card;
}
function showProgress(progress,results,searchProgress=null,candidates=null){$('task-sidebar').classList.remove('hidden');$('task-sidebar-id').textContent=state.task?.task_id||progress.task_id||'';$('task-sidebar-summary').textContent=`revision ${progress.revision} · generation ${progress.run_generation} · 目标 ${progress.counts.target} · 有效 ${progress.counts.accepted===null?'未知':progress.counts.accepted}`;const nodes=$('task-sidebar-nodes');nodes.replaceChildren();nodes.append(demandCard(progress));progress.nodes.forEach((node,index)=>nodes.append(nodeCard(node,index,progress,results,searchProgress,candidates)))}
async function refreshTaskSidebar(){
  if(!state.task)return;
  const taskId=state.task.task_id;$('task-sidebar').classList.remove('hidden');$('task-sidebar-id').textContent=taskId;
  try{
    const [progress,results,searchProgress]=await Promise.all([api(`/v1/retrieval/tasks/${taskId}/progress`),api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=1`),api(`/v1/retrieval/tasks/${taskId}/first-search/progress`).catch(()=>null)]);
    const currentSearch=searchProgress?.status==='approved'&&searchProgress.revision===progress.revision&&searchProgress.generation===progress.run_generation&&searchProgress.task_version===progress.task_version;
    const revisionIsCached=state.confirmedRevision?.taskId===taskId&&state.confirmedRevision.revision===progress.revision;
    const [candidates,revisions]=await Promise.all([currentSearch?api(`/v1/retrieval/tasks/${taskId}/first-search/candidates`).catch(()=>null):null,revisionIsCached?null:api(`/v1/retrieval/tasks/${taskId}/revisions`)]);
    if(state.task?.task_id!==taskId||state.task.confirmed_revision!==progress.revision||state.task.task_version!==progress.task_version)return;
    if(revisions){const revision=revisions.items.find(item=>item.revision===progress.revision);if(!revision)throw new Error('已确认版本不可读');state.confirmedRevision={taskId,revision:progress.revision,proposal:revision.proposal}}
    showProgress(progress,results,searchProgress,candidates);
  }catch(error){if(state.task?.task_id===taskId)$('task-sidebar-summary').textContent='节点状态暂不可读；需求 ID 已保存。'}
}
function showResults(results,taskId){if(!results.available){message('system','结果尚未发布',results.note);return}card('权威结果统计',[['绑定',`revision ${results.revision} · generation ${results.run_generation}`],['结果版本',results.result_set_digest],['有效',results.counts.accepted],['备选',results.counts.reserve],['重复',results.counts.duplicate],['淘汰',results.counts.failed],['待复核',results.counts.pending_review],['缺口',results.counts.gap],['停止原因',results.stop_reason||'无']]);for(const item of results.items){card('结果素材',[['素材标识',item.candidate_id],['素材版本',item.asset_version],['预览',item.preview_status==='unavailable'?'尚未接入授权媒体网关':item.preview_status]])}if(results.next_cursor){card('更多结果',[['说明','列表固定在当前结果版本，不会自动跳动']],[['加载更多',()=>loadMoreResults(taskId,results.next_cursor),false]])}message('system','结果说明',results.note)}
async function loadMoreResults(taskId,cursor){setBusy(true);try{const results=await api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=20&cursor=${encodeURIComponent(cursor)}`);showResults(results,taskId)}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
async function openTask(taskId){setBusy(true);try{const [task,revisions,progress,results]=await Promise.all([api(`/v1/retrieval/tasks/${taskId}`),api(`/v1/retrieval/tasks/${taskId}/revisions`),api(`/v1/retrieval/tasks/${taskId}/progress`),api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=20`)]);state.task=task;state.conversationId=task.conversation_id||null;localStorage.setItem('dsh-task-id',taskId);resetTimeline('历史需求',`任务 ${taskId}\n正式版本 ${task.confirmed_revision} · 状态 ${task.run_status}`);showProgress(progress,results);showResults(results,taskId);for(const revision of revisions.items){card(`正式版本 ${revision.revision}`,[['需求',revision.proposal.query_spec.raw_request],['方案摘要',revision.proposal_digest]]);showSavedGroups(revision.proposal.query_spec.grouped_expansion)}if(task.pending_draft){state.feedback=task.pending_draft;const bubble=message('assistant','有一条未确认的反馈','“'+state.feedback.feedback_text+'”仍是草稿，尚未改变当前需求。');guidedAction(bubble,'重新理解并处理',()=>routeTaskFollowup(state.feedback.feedback_text));guidedAction(bubble,'重新描述',editFeedback,true);guidedAction(bubble,'不采用',()=>decideFeedback('reject'),true);state.feedbackReviewBubble=bubble}$('new-request').classList.add('hidden');$('feedback-box').classList.remove('hidden');await refreshTaskSidebar();await offerExistingSearch(taskId);await refreshHistory();return true}catch(e){$('status').textContent=e.message;return false}finally{setBusy(false)}}
async function offerExistingSearch(taskId){
  try{
    const progress=await api(`/v1/retrieval/tasks/${taskId}/first-search/progress`);
    if(state.task?.task_id!==taskId)return;
    if(progress.status==='unapproved'){
      const bubble=message('assistant','这个需求尚未检索','它是之前确认的需求，不会因打开历史记录而自动执行。点击下方按钮后，会检查每个词的授权索引并开始本地测试检索。');
      guidedAction(bubble,'开始逐词检索',guidedStartSearch);
      const preview=await api(`/v1/retrieval/tasks/${taskId}/first-search`).catch(()=>null);
      if(state.task?.task_id===taskId&&preview){const unavailable=preview.routes.filter(route=>!route.options.length).map(route=>route.query_text);if(unavailable.length){state.searchAttempt={taskId,revision:state.task.confirmed_revision,status:'unavailable',reason:'当前授权测试索引不支持：'+unavailable.join('、')+'。尚未执行检索。'};await refreshTaskSidebar()}}
      return;
    }
    const data=await api(`/v1/retrieval/tasks/${taskId}/first-search/candidates`);
    if(state.task?.task_id!==taskId)return;
    const bubble=message('assistant','已检索的本地候选','原始命中 '+data.raw_hit_count+' 条，当前展示 '+data.returned+' 条；尚未清洗，不是合格图片。');
    if(!appendLocalCandidateGroups(bubble,data.items)){const note=document.createElement('small');note.textContent='还没有可展示的原始候选。';bubble.append(note)}
    if(progress.pagination_stopped){$('retrieval-live-status').textContent='本地检索完成 · 原始命中 '+data.raw_hit_count+' 条';$('feedback-heading').textContent='根据检索结果提出清洗建议'}
    else guidedAction(bubble,'继续检索',async()=>{setBusy(true);try{await guidedTick()}catch(error){$('status').textContent=error.message}finally{setBusy(false)}});
  }catch(error){if(state.task?.task_id===taskId)$('status').textContent='检索进度暂不可读：'+error.message}
}
function guidedAction(bubble,label,handler,secondary=false){const box=bubble.querySelector('.actions')||document.createElement('div');box.className='actions';const button=document.createElement('button');button.textContent=label;if(secondary)button.className='secondary';button.addEventListener('click',handler);box.append(button);bubble.append(box);return button}
function showTermReview(proposal,intro='我建议用这些检索词：'){
  const version=proposal.version||1;
  const bubble=message('assistant','',intro+'\n'+proposal.lines+'\n\n可以继续告诉我怎么改；只有点击下方按钮才会开始检索。');
  guidedAction(bubble,'确认检索词并开始检索',async()=>{
    if(state.proposedExpansion!==proposal||state.task||version!==(proposal.version||1)){$('status').textContent='这组检索词已变化，请确认最新词表。';return}
    await confirmGuidedExpansion(proposal,{value:proposal.lines,disabled:false});
  });
}
async function prepareGuidedExpansion(raw,count){
  const expansion=await api('/v1/retrieval/projects/demo/expansions',{method:'POST',body:groupPreviewBody(raw,count)});
  if(!expansion.recognized||!expansion.plan||!expansion.plan.groups.length||expansion.plan.groups.every(group=>['图片','图像','素材','数据'].includes(group.primary_label))){message('assistant','','要找什么主体或场景？例如“狗”或“客厅”。');return}
  const lines=expansion.plan.groups.map(group=>group.primary_label+' | '+group.auxiliary_terms.join('，')).join('\n');
  state.proposedExpansion={raw,count,plan:expansion.plan,lines,version:1};state.guidedPhase='expansion';saveDialogueSession();
  showTermReview(state.proposedExpansion);
}
function saveDialogueSession(){if(state.dialogueId)sessionStorage.setItem('dsh-dialogue',JSON.stringify({id:state.dialogueId,proposal:state.proposedExpansion}));else sessionStorage.removeItem('dsh-dialogue')}
function insertRequestExample(){
  const input=$('request-text');
  if(input.value.trim()){$('status').textContent='输入框已有内容，保留你的文字；清空后可填入参考说法。';input.focus();return}
  input.value='找【内容】图片，需要【数量】张；画幅或文件格式不限。';
  const start=input.value.indexOf('【内容】');input.focus();input.setSelectionRange(start,start+'【内容】'.length);
}
async function restoreDialogueSession(){
  let saved;try{saved=JSON.parse(sessionStorage.getItem('dsh-dialogue')||'null')}catch{return}
  if(!saved?.id)return;
  try{
    const current=await api('/v1/retrieval/projects/demo/dialogue-sessions/'+encodeURIComponent(saved.id));
    state.dialogueId=current.session_id;state.dialogueVersion=current.version;state.dialogueStage=current.stage;
    if(current.stage==='terms'&&saved.proposal){state.proposedExpansion=saved.proposal;state.guidedPhase='expansion'}
    for(const item of current.messages){if(item.role==='assistant')message('assistant','',item.text);else message('user','',item.text)}
    if(current.stage==='terms'&&!state.proposedExpansion&&current.request.target_count){await prepareGuidedExpansion(current.request.raw_request,current.request.target_count);return}
    if(state.proposedExpansion)showTermReview(state.proposedExpansion);
    else message('assistant','',current.summary+'。'+(current.question==='confirm_summary'?'对吗？':'请继续补充。'));
  }catch{sessionStorage.removeItem('dsh-dialogue')}
}
async function prepareTask(){
  const text=$('request-text').value.trim();if(!text)return;
  if(state.task){$('status').textContent='当前任务已确认；请在下方继续对话或新建 Chat。';return}
  $('request-text').value='';message('user','',text);setBusy(true);
  try{
    const llm=$('llm-model').value;if(!llm){message('assistant','','请先在右侧选择 LLM，我才能理解你的需求。');return}
    const proposal=state.proposedExpansion;
    if(proposal&&state.targetCountTouched)throw new Error('词表已生成；如需改变图片数量，请直接在对话中说明');
    const hint=state.targetCountTouched&&$('target-count').value?Number($('target-count').value):null;
    if(hint!==null&&(!Number.isInteger(hint)||hint<1||hint>10000))throw new Error('目标数量须为 1 到 10000 张图片');
    const currentGroups=proposal?groupInputBody(proposal.raw,proposal.lines).groups:null;
    const turn=await api('/v1/retrieval/projects/demo/dialogue-turn',{method:'POST',body:{session_id:state.dialogueId,expected_version:state.dialogueVersion,message:text,llm_id:llm,phase:state.dialogueStage,count_hint:hint,...(currentGroups?{current_groups:currentGroups,current_target_count:proposal.count}:{})}});
    state.targetCountTouched=false;
    state.dialogueId=turn.session_id;state.dialogueVersion=turn.version;state.dialogueStage=turn.stage;saveDialogueSession();
    if(turn.action==='answer'||turn.action==='clarify'){message('assistant','',turn.reply);return}
    if(turn.action==='confirm'&&turn.request.media_type==='image'&&turn.request.target_count){
      await prepareGuidedExpansion(turn.request.raw_request,turn.request.target_count);return;
    }
    if(turn.stage==='terms'&&!proposal&&turn.request.target_count){
      await prepareGuidedExpansion(turn.request.raw_request,turn.request.target_count);return;
    }
    if(turn.action==='confirm_terms'&&proposal){showTermReview(proposal,'检索词还需要你亲自确认：');return}
    if(turn.action==='revise_terms'&&proposal){await reviseGuidedExpansion(proposal,{value:proposal.lines},{value:text},turn);return}
    if(turn.action==='replace'){state.proposedExpansion=null;state.guidedPhase='input';state.resumeDraft=null;state.draft=null;saveDialogueSession()}
    if(turn.stage==='request'&&proposal){state.proposedExpansion=null;state.guidedPhase='input';saveDialogueSession()}
    message('assistant','',turn.reply);
  }catch(e){
    if(e.code==='MODEL_OUTPUT_INVALID')message('assistant','',e.detail||'这次生成的内容未通过校验，请重试。');
    else if(e.code==='MODEL_REQUEST_FAILED')message('assistant','','模型服务暂时没有返回可用结果，请重试这条消息。');
    else $('status').textContent=e.message;
    $('request-text').value=text+' '+$('request-text').value;
  }
  finally{setBusy(false)}
}
async function reviseGuidedExpansion(proposal,editor,feedbackEditor,turn){
  if(state.proposedExpansion!==proposal||state.task)return;
  const feedback=feedbackEditor.value.trim();if(!feedback){$('status').textContent='请先写下希望如何调整检索词';return}
  const llm=$('llm-model').value,vlm=$('vlm-model').value;if(!llm||!vlm){$('status').textContent='按反馈重新扩词需要已配置的 LLM 和 VLM 模型选择';return}
  setBusy(true);
  try{
    const current=groupInputBody(proposal.raw,editor.value.trim()).groups;
    if(!current?.length)throw new Error('请先保留至少一组可调整的检索词');
    const count=turn?.request.target_count||proposal.count;
    const instruction=turn?.expansion_instruction||feedback;
    const edits=(turn?.count_edits||[]).filter(edit=>edit.target==='terms');
    const updated=await api('/v1/retrieval/projects/demo/expansions',{method:'POST',body:{raw_request:proposal.raw,dimension_id:'default',target_count:count,model_selection:{llm,vlm},feedback_text:instruction,current_groups:current,count_edits:edits,current_target_count:proposal.count}});
    if(state.proposedExpansion!==proposal||!updated.recognized||!updated.plan)throw new Error('扩词建议已过期或未通过校验');
    const before=new Set(proposal.plan.groups.flatMap(group=>group.auxiliary_terms.map(term=>group.primary_label+'：'+term)));
    const after=new Set(updated.plan.groups.flatMap(group=>group.auxiliary_terms.map(term=>group.primary_label+'：'+term)));
    const added=[...after].filter(term=>!before.has(term)),removed=[...before].filter(term=>!after.has(term));
    proposal.raw+='\n扩词调整：'+feedback;proposal.count=count;proposal.plan=updated.plan;proposal.lines=updated.plan.groups.map(group=>group.primary_label+' | '+group.auxiliary_terms.join('，')).join('\n');proposal.version=(proposal.version||1)+1;
    saveDialogueSession();
    editor.value=proposal.lines;feedbackEditor.value='';
    const changeNote='目标 '+count+' 张图片；检索词新增 '+added.length+' 个、去掉 '+removed.length+' 个。'+(added.length?'例如：'+added.slice(0,3).join('；')+'。':'');
    showTermReview(proposal,'已按你的建议调整。'+changeNote+'\n完整词表：');
  }catch(error){
    const explanation=error.code==='EXPANSION_UNCHANGED'?'这次模型没有按你的建议改出新词，原词表已保留。可以重试这条建议。':'重新扩词未完成：'+(error.detail||error.message);
    $('status').textContent=explanation;
    const bubble=message('assistant','',explanation);
    if(['EXPANSION_UNCHANGED','TERM_COUNT_MISMATCH','MODEL_OUTPUT_INVALID','MODEL_REQUEST_FAILED'].includes(error.code))guidedAction(bubble,'重试这条建议',()=>reviseGuidedExpansion(proposal,editor,{value:feedback},turn));
  }
  finally{setBusy(false)}
}
async function confirmGuidedExpansion(proposal,editor){
  if(state.proposedExpansion!==proposal||state.task)return;
  setBusy(true);
  try{
    let plan=proposal.plan;
    if(editor.value.trim()!==proposal.lines.trim()){
      $('group-lines').value=editor.value.trim();
      const changed=await api('/v1/retrieval/projects/demo/expansions',{method:'POST',body:groupPreviewBody(proposal.raw,proposal.count)});
      if(!changed.recognized||!changed.plan)throw new Error('修改后的扩词未通过校验');
      plan=changed.plan;
    }
    const body=draftBody(proposal.raw,proposal.count,plan);
    const path=state.resumeDraft?'/v1/retrieval/projects/demo/drafts/'+state.resumeDraft.draft_id:'/v1/retrieval/projects/demo/drafts';
    if(state.resumeDraft)body.expected_draft_version=state.resumeDraft.draft_version;
    state.draft=await api(path,{method:'POST',keyValue:key('draft'),body});
    state.conversationId=state.draft.conversation_id;
    state.resumeDraft={draft_id:state.draft.draft_id,draft_version:state.draft.draft_version};
    state.task=await api('/v1/retrieval/tasks',{method:'POST',keyValue:key('task'),body:{draft_id:state.draft.draft_id,expected_draft_version:state.draft.draft_version,proposal_digest:state.draft.proposal_digest,action:'confirm'}});
    sessionStorage.removeItem('dsh-dialogue');
    localStorage.setItem('dsh-task-id',state.task.task_id);
    await refreshTaskSidebar();
    state.guidedPhase='search';state.proposedExpansion=null;editor.disabled=true;retireDraftActions();
    $('new-request').classList.add('hidden');$('feedback-box').classList.add('hidden');
    message('assistant','','已开始检索。需求 ID：'+state.task.task_id+'；目标 '+proposal.count+' 张。结果会在下方陆续出现。');
    await refreshHistory();await guidedStartSearch();
  }catch(e){
    const explanation=e.code==='FEATURE_NOT_SUPPORTED'?'页面还是旧版本，请刷新后再次确认检索词。词表仍在，检索没有启动。':state.task?'需求已保存，但检索没能启动。请从历史 Chat 打开这条需求后重试。':'暂时没能开始检索。请刷新页面后再确认一次检索词。';
    $('status').textContent=explanation;message('assistant','检索还没开始',explanation);
  }
  finally{setBusy(false)}
}
async function guidedStartSearch(){
  if(!state.task)return;
  const taskId=state.task.task_id,revision=state.task.confirmed_revision;
  state.searchAttempt={taskId,revision,status:'checking',reason:''};await refreshTaskSidebar();
  try{
    const data=await api('/v1/retrieval/tasks/'+taskId+'/first-search');
    if(state.task?.task_id!==taskId||state.task.confirmed_revision!==revision)return;
    state.firstSearchPreview=data;state.firstSearchChoices=[];
    const unsupported=data.routes.filter(route=>!route.options.length).map(route=>route.query_text);
    if(!data.routes.length||unsupported.length){
      const withoutIndex=data.routes.filter(route=>route.availability==='no_available_index').map(route=>route.query_text);
      const withoutEncoding=data.routes.filter(route=>route.availability==='query_encoding_unavailable').map(route=>route.query_text);
      const reasons=[];
      if(withoutIndex.length)reasons.push('无匹配的已授权索引：'+withoutIndex.join('、'));
      if(withoutEncoding.length)reasons.push('测试索引无法编码：'+withoutEncoding.join('、'));
      const reason=(reasons.join('；')||'没有可执行的检索词')+'。本地测试只支持登记的词规则，不会跳过词或伪造结果；真实任意词检索仍需匹配的 embedding 与索引。';state.searchAttempt={taskId,revision,status:'unavailable',reason};await refreshTaskSidebar();message('assistant','检索暂不可用',reason);$('feedback-box').classList.remove('hidden');return}
    const ambiguous=data.routes.filter(route=>route.options.length>1);
    state.firstSearchChoices=data.routes.filter(route=>route.options.length===1).map(route=>({branch_id:route.branch_id,binding_id:route.options[0].binding_id,batch_size:Math.min(2,route.options[0].max_batch_size)}));
    if(!ambiguous.length){await guidedConfirmAndSearch();return}
    const bubble=message('assistant','请选择有歧义的索引','已有 '+data.routes.length+' 个检索词；其中 '+ambiguous.length+' 个词匹配多个授权索引。其余词已自动选库；请为下面每个词选择一个索引。');
    for(const route of ambiguous){
      const heading=document.createElement('strong');heading.textContent='检索词：'+route.query_text;bubble.append(heading);
      for(const option of route.options){
        const label=document.createElement('label');label.className='search-choice';
        const box=document.createElement('input');box.type='radio';box.name='guided-index-'+route.branch_id;box.setAttribute('aria-label','为 '+route.query_text+' 选择 '+option.library_id);
        const choice={branch_id:route.branch_id,binding_id:option.binding_id,batch_size:Math.min(2,option.max_batch_size)};
        box.addEventListener('change',()=>{state.firstSearchChoices=state.firstSearchChoices.filter(row=>row.branch_id!==choice.branch_id);if(box.checked)state.firstSearchChoices.push(choice)});
        const labelText=document.createElement('span');labelText.textContent=option.library_id+' / '+option.binding_id+'（'+option.index_version+'）';label.append(box,labelText);bubble.append(label);
      }
    }
    guidedAction(bubble,'按所选索引开始检索',guidedConfirmAndSearch);
  }catch(e){if(state.task?.task_id!==taskId||state.task.confirmed_revision!==revision)return;const reason='当前测试索引不能逐词完成编码或路由：'+e.message+'。已确认的需求仍保留，但没有执行检索。';state.searchAttempt={taskId,revision,status:'unavailable',reason};await refreshTaskSidebar();message('assistant','检索暂不可用',reason);$('feedback-box').classList.remove('hidden')}
}
async function guidedConfirmAndSearch(){
  const preview=state.firstSearchPreview;
  if(!state.task||!preview)return;
  if(preview.routes.some(route=>!state.firstSearchChoices.some(choice=>choice.branch_id===route.branch_id))){$('status').textContent='每个检索词至少选择一个索引';return}
  setBusy(true);
  try{
    const proposal=await api('/v1/retrieval/tasks/'+state.task.task_id+'/first-search/prepare',{method:'POST',body:{routing_digest:preview.routing_digest,choices:state.firstSearchChoices}});
    state.firstSearchProposal=proposal;
    await api('/v1/retrieval/tasks/'+state.task.task_id+'/first-search/confirm',{method:'POST',keyValue:key('guided-search-confirm'),body:{proposal_digest:proposal.proposal_digest,expected_task_version:proposal.expected_task_version,action:'confirm'}});
    state.task=await api('/v1/retrieval/tasks/'+state.task.task_id);
    await refreshTaskSidebar();
    message('assistant','开始逐词检索','已批准 '+proposal.members.length+' 个词/索引组合。本地测试向量逐分支请求，下面仅展示授权原始命中，不代表已清洗合格。');
    await guidedTick();
  }catch(e){$('status').textContent=e.message;state.searchAttempt={taskId:state.task.task_id,revision:state.task.confirmed_revision,status:'unavailable',reason:e.message+'。未返回的词不显示为已有结果。'};await refreshTaskSidebar();message('assistant','检索未完成',e.message+'。未返回的词不显示为已有结果。');$('feedback-box').classList.remove('hidden')}
  finally{setBusy(false)}
}
async function guidedTick(){
  const taskId=state.task.task_id;
  for(let batch=1;batch<=32;batch++){
    const result=await api('/v1/retrieval/tasks/'+taskId+'/first-search/tick',{method:'POST',body:{max_requests:Math.min(8,Math.max(1,state.firstSearchChoices.length))}});
    const data=await api('/v1/retrieval/tasks/'+taskId+'/first-search/candidates');
    if(state.task?.task_id!==taskId)return;
    if(state.guidedResultBubble?.isConnected)state.guidedResultBubble.remove();
    const completed=result.members.filter(member=>member.stop_reason).length;
    const bubble=message('assistant',result.pagination_stopped?'检索完成 · 本地测试':'检索中 · 本地测试','已完成 '+completed+'/'+result.members.length+' 个词/索引分支；累计原始命中 '+data.raw_hit_count+' 条，当前展示 '+aggregateLocalCandidates(data.items).length+' 个候选。它们尚未清洗，也未接授权图片预览。');
    state.guidedResultBubble=bubble;
    const shown=appendLocalCandidateGroups(bubble,data.items);
    if(!shown){const note=document.createElement('small');note.textContent='目前没有可展示的原始候选；不表示所有词都已召回到素材。';bubble.append(note)}
    if(data.truncated){const note=document.createElement('small');note.textContent='候选列表最多展示 200 条，超出部分未展示。';bubble.append(note)}
    $('retrieval-live-status').textContent=(result.pagination_stopped?'本地检索完成':'本地检索进行中')+' · '+completed+'/'+result.members.length+' 分支 · 原始命中 '+data.raw_hit_count+' 条';
    await refreshTaskSidebar();
    if(result.pagination_stopped){const note=document.createElement('small');note.textContent='你可以查看各检索词对应的候选，并在下方提出清洗建议；合格数量尚未处理。';bubble.append(note);$('feedback-heading').textContent='根据检索结果提出清洗建议';$('feedback-box').classList.remove('hidden');return}
    if(!result.network_attempts||result.outcomes.some(outcome=>outcome.status==='retry_pending')||batch===32){
      const note=document.createElement('small');note.textContent='本轮自动检索已暂停，未完成的分支仍保留，可继续。';bubble.append(note);
      guidedAction(bubble,'继续检索',async()=>{setBusy(true);try{await guidedTick()}catch(e){$('status').textContent=e.message}finally{setBusy(false)}});
      return;
    }
    await new Promise(resolve=>setTimeout(resolve,0));
  }
}
async function confirmTask(){if(!state.draft||state.task)return;setBusy(true);try{state.task=await api('/v1/retrieval/tasks',{method:'POST',keyValue:key('task'),body:{draft_id:state.draft.draft_id,expected_draft_version:state.draft.draft_version,proposal_digest:state.draft.proposal_digest,action:'confirm'}});retireDraftActions();localStorage.setItem('dsh-task-id',state.task.task_id);await refreshTaskSidebar();message('system','提单成功',`任务 ${state.task.task_id}\n正式版本 ${state.task.confirmed_revision} · 状态 ${state.task.run_status}`);$('feedback-box').classList.remove('hidden');await refreshHistory()}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
async function prepareFeedback(){
  const text=$('feedback-text').value.trim();if(!text){$('status').textContent='请输入消息';return}
  await routeTaskFollowup(text);
}
async function routeTaskFollowup(text){
  if(!state.task)return;
  const llm=$('llm-model').value;if(!llm){$('status').textContent='请先选择 LLM，让模型判断这条消息要修改检索还是清洗';return}
  const taskId=state.task.task_id,taskVersion=state.task.task_version;
  setBusy(true);
  try{
    const preview=state.expansionPreviewPlan?.taskId===taskId&&state.expansionPreviewPlan?.baseVersion===taskVersion?state.expansionPreviewPlan:null;
    if(state.followupContext?.taskId!==taskId||state.followupContext?.version!==taskVersion)state.followupContext={taskId,version:taskVersion,messages:[]};
    const intent=await api(`/v1/retrieval/tasks/${taskId}/followup-intent`,{method:'POST',body:{text,llm_id:llm,expected_revision:state.task.confirmed_revision,expected_task_version:taskVersion,recent_messages:state.followupContext.messages.slice(-20),...(preview?{draft_preview:{groups:preview.plan.groups.map(group=>({primary_label:group.primary_label,auxiliary_terms:group.auxiliary_terms})),target_count:preview.count}}:{})}});
    if(state.task?.task_id!==taskId||state.task.task_version!==taskVersion)return;
    state.followupContext.messages.push({role:'user',text},...(intent.reply?[{role:'assistant',text:intent.reply}]:[]));
    state.followupContext.messages=state.followupContext.messages.slice(-20);
    if(intent.kind==='expansion'){await showPostTaskExpansion(text,intent);return}
    if(intent.kind==='answer'||intent.kind==='clarify'){message('user','',text);message('assistant','',intent.reply||'你希望调整哪些内容？');return}
    if(state.feedback){
      if(state.feedback.feedback_text===text){await showCleaningSuggestion();return}
      $('status').textContent='请先处理上一条未确认反馈，再提交新的清洗条件。';return;
    }
    message('user','',text);
    state.feedback=await api(`/v1/retrieval/tasks/${state.task.task_id}/feedback`,{method:'POST',keyValue:key('feedback'),body:{text,scope:'all',expected_revision:state.task.confirmed_revision,expected_task_version:state.task.task_version}});
    $('feedback-box').classList.add('hidden');
    await showCleaningSuggestion();
  }catch(e){$('status').textContent='尚未理解这条消息：'+e.message}
  finally{setBusy(false)}
}
async function showPostTaskExpansion(text,intent){
  if(!state.task)return;
  const taskId=state.task.task_id,baseVersion=state.task.task_version,llm=$('llm-model').value,vlm=$('vlm-model').value;
  if(!llm||!vlm){$('status').textContent='重新扩词需要已配置的 LLM 和 VLM 模型选择';return}
  setBusy(true);
  try{
    message('user','',text);
    const revisions=await api(`/v1/retrieval/tasks/${taskId}/revisions`);
    const revision=revisions.items.find(item=>item.revision===state.task.confirmed_revision),spec=revision?.proposal?.query_spec;
    if(!spec?.grouped_expansion?.groups?.length)throw new Error('当前需求没有可调整的已确认扩词分组');
    let previous=state.expansionPreviewPlan?.taskId===taskId&&state.expansionPreviewPlan?.baseVersion===baseVersion?state.expansionPreviewPlan:null;
    if(!previous&&state.feedback?.feedback_text&&state.feedback.feedback_text!==text){
      const earlier=await api(`/v1/retrieval/tasks/${taskId}/followup-intent`,{method:'POST',body:{text:state.feedback.feedback_text,llm_id:llm,expected_revision:state.task.confirmed_revision,expected_task_version:baseVersion}});
      if(earlier.kind==='expansion')previous={count:earlier.target_count,requestedTerms:earlier.requested_terms_per_group,feedbackText:state.feedback.feedback_text};
    }
    const count=intent.target_count||previous?.count||spec.target_count;
    const countEdits=(intent.count_edits||[]).filter(edit=>edit.target==='terms');
    const requestedTerms=countEdits.length?null:intent.requested_terms_per_group||previous?.requestedTerms||null;
    const feedbackText=previous?.feedbackText&&previous.feedbackText!==text?previous.feedbackText+'\n后续调整：'+text:text;
    const source=state.expansionPreviewPlan?.taskId===taskId&&state.expansionPreviewPlan?.baseVersion===baseVersion?state.expansionPreviewPlan.plan.groups:spec.grouped_expansion.groups;
    const current=source.map(group=>({primary_label:group.primary_label,auxiliary_terms:group.auxiliary_terms}));
    const updated=await api('/v1/retrieval/projects/demo/expansions',{method:'POST',body:{raw_request:spec.raw_request,dimension_id:revision.proposal.dimension_id,target_count:count,current_target_count:previous?.count||spec.target_count,model_selection:{llm,vlm},feedback_text:intent.expansion_instruction||feedbackText,current_groups:current,...(requestedTerms?{requested_terms_per_group:requestedTerms}:{count_edits:countEdits})}});
    if(state.task?.task_id!==taskId||state.task.task_version!==baseVersion)throw new Error('需求版本已改变，请刷新后重试');
    if(!updated.recognized||!updated.plan?.groups?.length)throw new Error('模型没有给出可用检索词');
    const lines=updated.plan.groups.map(group=>group.primary_label+' | '+group.auxiliary_terms.join('，')).join('\n');
    if(state.expansionPreviewBubble?.isConnected)state.expansionPreviewBubble.querySelector('.actions')?.remove();
    const token=key('expansion-preview');state.expansionPreviewToken=token;state.expansionPreviewPlan={taskId,baseVersion,plan:updated.plan,count,requestedTerms,feedbackText};
    const countNote=intent.target_count||previous?.count?'目标 '+count+' 张图':'目标沿用原需求的 '+count+' 张图；如需改数量，请补充具体张数';
    const bubble=message('assistant','重新扩词结果',countNote+'\n'+lines);state.expansionPreviewBubble=bubble;
    guidedAction(bubble,'确认新版词表',async()=>{
      if(state.expansionPreviewToken!==token||state.task?.task_id!==taskId||state.task.task_version!==baseVersion)return;
      setBusy(true);
      try{
        const pending=state.feedback;
        state.feedback=await api(`/v1/retrieval/tasks/${taskId}/feedback${pending?'/'+pending.draft_id:''}`,{method:'POST',keyValue:key('expansion-feedback'),body:{text:feedbackText,scope:'all',expected_revision:state.task.confirmed_revision,expected_task_version:baseVersion,...(pending?{expected_draft_version:pending.draft_version}:{}),target_count:count,grouped_expansion:updated.plan}});
        await decideFeedback('confirm',true);
      }catch(error){$('status').textContent='新版词表尚未确认：'+error.message}
      finally{setBusy(false)}
    });
    guidedAction(bubble,'继续调整',()=>{$('feedback-text').focus()},true);
    $('feedback-text').value='';
  }catch(error){$('status').textContent='重新扩词未完成：'+error.message}
  finally{setBusy(false)}
}
async function decideFeedback(action,expansion=false){setBusy(true);try{const result=await api(`/v1/retrieval/tasks/${state.task.task_id}/confirmations`,{method:'POST',keyValue:key(action),body:{draft_id:state.feedback.draft_id,expected_draft_version:state.feedback.draft_version,proposal_digest:state.feedback.proposal_digest,expected_task_version:state.feedback.base_task_version,action}});state.task=result.task;state.feedback=null;if(state.feedbackReviewBubble?.isConnected)state.feedbackReviewBubble.querySelector('.actions')?.remove();if(state.feedbackManualCard?.isConnected)state.feedbackManualCard.querySelector('.actions')?.remove();if(state.expansionPreviewBubble?.isConnected)state.expansionPreviewBubble.querySelector('.actions')?.remove();state.expansionPreviewToken=null;state.expansionPreviewPlan=null;await refreshTaskSidebar();message('assistant',action==='confirm'?(expansion?'新版词表已确认':'反馈已确认'):'已忽略这条反馈',action==='confirm'?(expansion?'已保存为同一需求 ID 的新版本，正在检查索引并逐词检索。':'需求已更新为新版本；当前尚未创建或执行清洗管线。'):'原需求保持不变。');$('feedback-text').value='';$('feedback-box').classList.remove('hidden');await refreshHistory();if(action==='confirm'&&expansion)await guidedStartSearch()}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
async function editFeedback(){
  const text=state.feedback?.feedback_text||'';
  await decideFeedback('reject');
  if(state.feedback)return;
  $('feedback-text').value=text;$('feedback-text').focus();
}
function showFeedbackReview(suggestion){
  if(state.feedbackReviewBubble?.isConnected)state.feedbackReviewBubble.remove();
  if(state.feedbackManualCard?.isConnected)state.feedbackManualCard.remove();
  $('feedback-box').classList.add('hidden');
  const sentence=text=>{const copy=text.trim().replace(/^请用户/, '请你').replace(/^建议用户/, '建议你');return /[。！？!?]$/.test(copy)?copy:copy+'。'};
  let bubble;
  if(!suggestion){
    bubble=message('assistant','请审阅这条反馈','模型暂时无法判断这条反馈是否形成清洗条件。你可以重新描述，或手动核对完整需求后确认；目前没有自动执行。');
    guidedAction(bubble,'查看完整需求',()=>{if(state.feedbackManualCard?.isConnected)state.feedbackManualCard.remove();state.feedbackManualCard=card('完整反馈草稿 · 手动确认',[['原反馈',state.feedback.feedback_text],['完整需求',state.feedback.proposal.query_spec.raw_request],['执行说明',state.feedback.execution_note]],[['仅保存反馈文本',()=>decideFeedback('confirm'),false],['不采用',()=>decideFeedback('reject'),true]])});
  }else if(suggestion.feedback_status==='ready'){
    bubble=message('assistant','我理解你的反馈','我建议把这条反馈理解为：“'+suggestion.requirement+'”。'+sentence(suggestion.rationale)+'目前确认只保存你的反馈原文；这条模型建议尚未写入清洗规则，也不会自动执行。');
    guidedAction(bubble,'仅保存反馈文本',()=>decideFeedback('confirm'));
  }else{
    const unrelated=suggestion.feedback_status==='unrelated';
    bubble=message('assistant',unrelated?'这条反馈还不能用于清洗':'请再补充一点',sentence(suggestion.rationale)+'当前需求不会改变。');
  }
  guidedAction(bubble,'重新描述',editFeedback,true);
  guidedAction(bubble,'不采用',()=>decideFeedback('reject'),true);
  state.feedbackReviewBubble=bubble;
}
async function showCleaningSuggestion(){
  const llm=$('llm-model').value,vlm=$('vlm-model').value;
  if(!llm||!vlm){showFeedbackReview(null);return}
  try{
    const suggestion=await api(`/v1/retrieval/tasks/${state.task.task_id}/feedback-suggestions`,{method:'POST',body:{draft_id:state.feedback.draft_id,expected_draft_version:state.feedback.draft_version,proposal_digest:state.feedback.proposal_digest,model_selection:{llm,vlm}}});
    showFeedbackReview(suggestion);
  }catch(e){showFeedbackReview(null);$('status').textContent='模型暂时无法判断：'+e.message}
}
$('preview-branches').addEventListener('click',previewBranches);
$('preview-routing').addEventListener('click',previewRouting);
$('first-search').addEventListener('click',previewFirstSearch);
$('first-search-candidates').addEventListener('click',viewFirstSearchCandidates);
function startNewChat(){localStorage.removeItem('dsh-task-id');sessionStorage.removeItem('dsh-dialogue');location.reload()}
function saveApiConfig(){const baseUrl=$('api-base-url').value.trim(),apiKey=$('api-key').value.trim(),llm=$('custom-llm-model').value.trim(),vlm=$('custom-vlm-model').value.trim();try{if(!baseUrl||!apiKey||!llm||!vlm)throw new Error('请填写 API 地址、密钥以及 LLM/VLM 模型名');const parsed=new URL(baseUrl);if(!['http:','https:'].includes(parsed.protocol))throw new Error('API 地址必须使用 http 或 https');state.connection={provider:$('api-provider').value,baseUrl:parsed.toString(),apiKey,llm,vlm};$('api-config-status').textContent='连接信息仅暂存于页面，刷新即清除；不会改变当前服务端模型，也不会在此发送密钥。';$('api-key').value=''}catch(e){$('api-config-status').textContent=e.message}}
$('prepare-task').addEventListener('click',prepareTask);$('request-text').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();prepareTask()}});$('prepare-feedback').addEventListener('click',prepareFeedback);$('feedback-text').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();prepareFeedback()}});$('new-task').addEventListener('click',startNewChat);$('new-chat').addEventListener('click',startNewChat);$('refresh-history').addEventListener('click',refreshHistory);$('save-api-config').addEventListener('click',saveApiConfig);document.addEventListener('keydown',event=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();startNewChat()}});
$('insert-request-example').addEventListener('click',insertRequestExample);
function updateModelMode(){
  const llm=$('llm-model'),enabled=!!llm.value;
  const modelName=llm.selectedOptions[0]?.textContent||llm.value;
  $('chat-model-badge').textContent=enabled?'LLM 已连接':'模板模式';
  $('chat-model-badge').classList.toggle('neutral',!enabled);
  $('composer-model-note').textContent=enabled?'':'请先在右侧选择 LLM';
  $('feedback-model-note').textContent=enabled?'发送后先判断是调整检索方案还是筛选已找到的图片。':'请先选择 LLM 以理解后续消息。';
}
$('llm-model').addEventListener('change',()=>{state.modelChoiceTouched=true;updateModelMode()});
$('vlm-model').addEventListener('change',()=>{state.modelChoiceTouched=true;updateModelMode()});
$('target-count').addEventListener('input',()=>{state.targetCountTouched=true});
async function loadModels(){
  try{
    const data=await api('/v1/retrieval/projects/demo/models');
    for(const model of data.items){if(!['llm','vlm'].includes(model.kind))continue;const option=document.createElement('option');option.value=model.id;option.textContent=`${model.provider} / ${model.model} (${model.id})`;$(model.kind+'-model').append(option)}
    const llms=data.items.filter(model=>model.kind==='llm'),vlms=data.items.filter(model=>model.kind==='vlm');
    if(!state.modelChoiceTouched&&llms.length===1)$('llm-model').value=llms[0].id;
    if(!state.modelChoiceTouched&&vlms.length===1)$('vlm-model').value=vlms[0].id;
    $('model-status').textContent=llms.length?'模型已就绪':'尚未配置服务端 LLM';
    updateModelMode();
  }catch(e){$('model-status').textContent='模型目录加载失败：'+e.message;updateModelMode()}
}
(async function initialize(){await loadModels();await refreshHistory();const taskId=localStorage.getItem('dsh-task-id');if(taskId){if(!await openTask(taskId))localStorage.removeItem('dsh-task-id')}else await restoreDialogueSession()})();
"""
