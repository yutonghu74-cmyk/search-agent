"""Local-only configuration UI, using the same demo identity as Chat."""
CATALOG_HTML = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>已有索引配置</title><link rel="stylesheet" href="/chat.css"></head><body><a class="skip-link" href="#main-content">跳到主要内容</a><main id="main-content" class="catalog-page">
<nav class="topbar" aria-label="主导航"><a class="brand" href="/" aria-label="返回 DSH 检索需求首页"><span class="brand-mark" aria-hidden="true">D</span><span>DSH Retrieval</span></a><a class="nav-link" href="/"><span aria-hidden="true">←</span> 返回 Chat</a></nav>
<header class="hero"><div><span class="eyebrow">CATALOG · LOCAL PREVIEW</span><h1>已有索引配置</h1><p class="hero-copy">用自然语言描述已有索引的业务用途，预览后再确认保存。</p></div><div class="project" aria-label="当前项目上下文"><span class="status-dot" aria-hidden="true"></span><span>项目 <strong>demo</strong></span></div></header>
<section class="notice" aria-label="配置安全边界"><span class="notice-icon" aria-hidden="true">i</span><div><strong>仅登记，不执行</strong><p>只配置已有索引，不创建物理库、不执行检索。文本会发送到所选 LLM，请勿填写密钥、内网地址或敏感数据；配置权限须单独授予。</p></div></section>
<div class="catalog-grid">
<section class="composer panel wide" aria-labelledby="catalog-input-heading"><div class="section-heading"><div><span class="section-kicker">STEP 01</span><h2 id="catalog-input-heading">描述索引用途</h2></div><span class="status-badge neutral">生成预览</span></div><div class="field-grid"><div class="field model-field"><label for="catalog-model"><span class="field-number" aria-hidden="true">AI</span><span>配置理解模型 <small>LLM</small></span></label><select id="catalog-model"></select><p class="helper">模型从服务端安全配置中读取。</p></div><div class="field"><label for="catalog-text">用途和索引选择</label><textarea id="catalog-text" rows="5" maxlength="4000" placeholder="例如：使用已有动物图片库查猫狗，增加宠物别名"></textarea></div></div><div class="form-footer"><span class="helper">不会立即保存或执行检索</span><button id="catalog-preview" disabled>生成配置预览 <span aria-hidden="true">→</span></button></div></section>
<section class="composer panel" aria-labelledby="catalog-proposal-heading"><div class="section-heading"><div><span class="section-kicker">STEP 02</span><h2 id="catalog-proposal-heading">待确认配置</h2></div><span class="status-badge">需确认</span></div><pre id="catalog-proposal">尚未生成预览</pre><button id="catalog-save" disabled>确认保存配置</button></section>
<section class="composer panel" aria-labelledby="catalog-current-heading"><div class="section-heading"><div><span class="section-kicker">CURRENT</span><h2 id="catalog-current-heading">已保存配置</h2></div><button class="secondary small" id="catalog-refresh">刷新</button></div><pre id="catalog-current">正在读取…</pre></section>
</div><p id="catalog-status" role="status" aria-live="polite"></p>
</main><script src="/catalog.js"></script></body></html>'''

CATALOG_JS = ''''use strict';
const el=id=>document.getElementById(id);
const base='/v1/retrieval/projects/demo/catalog-config';
let pending=null, busy=false, available=false;
function reset(){pending=null;el('catalog-proposal').textContent='';el('catalog-save').disabled=true;}
function controls(){el('catalog-preview').disabled=busy||!available||!el('catalog-model').value;el('catalog-save').disabled=busy||!pending;el('catalog-refresh').disabled=busy;el('catalog-text').disabled=busy;el('catalog-model').disabled=busy;}
async function api(path,body){const response=await fetch(path,{method:body?'POST':'GET',cache:'no-store',headers:{'Content-Type':'application/json','X-Tenant-Id':'tenant-a','X-User-Id':'user-a'},body:body?JSON.stringify(body):undefined});const data=await response.json();if(!response.ok){const error=new Error(`${data.code}: ${data.message}`);error.status=response.status;throw error;}return data;}
async function refresh(){try{const data=await api(base);available=true;el('catalog-current').textContent=data.config?JSON.stringify(data.config,null,2):'尚未保存配置';}catch(error){if(error.status===409){available=true;el('catalog-current').textContent='已保存配置已失效，请重新生成并确认。';}else{available=false;throw error;}}}
async function run(action){if(busy)return;busy=true;controls();try{await action();}catch(error){reset();el('catalog-status').textContent=error.message;}finally{busy=false;controls();}}
el('catalog-text').addEventListener('input',reset);el('catalog-model').addEventListener('change',()=>{reset();controls();});
el('catalog-preview').addEventListener('click',()=>run(async()=>{reset();const data=await api(base+'/preview',{text:el('catalog-text').value,llm_id:el('catalog-model').value});pending={proposal:data.proposal,proposal_digest:data.proposal_digest};el('catalog-proposal').textContent=JSON.stringify(data.proposal.entries,null,2);el('catalog-status').textContent='仅为预览，请核对后确认保存。';}));
el('catalog-save').addEventListener('click',()=>run(async()=>{const data=await api(base+'/confirm',pending);reset();el('catalog-status').textContent=`已保存配置版本 ${data.version}；未执行检索。`;await refresh();}));
el('catalog-refresh').addEventListener('click',()=>run(async()=>{reset();await refresh();el('catalog-status').textContent='已刷新保存配置。';}));
run(async()=>{const data=await api('/v1/retrieval/projects/demo/models');for(const model of data.items.filter(x=>x.kind==='llm')){const option=document.createElement('option');option.value=model.id;option.textContent=model.model+' ('+model.id+')';el('catalog-model').append(option);}await refresh();el('catalog-status').textContent=el('catalog-model').value?'可生成已有索引配置。':'未配置 LLM，暂不能生成。';});
'''
